"""
guarda.py — decide se HOJE tem o que dizer, antes de gastar o modelo.

    python guarda.py facts/fipezap-capitais.json metro-de-bh
    python guarda.py facts/fipezap-capitais.json metro-de-bh --forcar
    python guarda.py --registrar-ultima metro-de-bh

Sai com 0 se pode publicar, 78 se nao ha nada novo. O workflow le esse codigo
e encerra o job em sucesso quando for 78 -- dia sem post nao e falha.

POR QUE ISSO EXISTE

O cron roda de segunda a sexta, mas o informe do FipeZap sai uma vez por mes.
Sem guarda, o pipeline baixaria o mesmo PDF, escreveria um texto diferente
sobre os mesmos numeros e publicaria cinco vezes na mesma semana. Nao e
invencao -- os numeros continuariam certos -- mas e repeticao, e num perfil de
dado repeticao gasta a paciencia de quem segue tao rapido quanto erro.

A regra e a mesma ja escrita no compose.py: um dia sem post custa menos que um
post que nao devia existir.

O QUE CONTA COMO "NOVO"

A identidade de um post e (dado, angulo):
  - dado  = hash do PDF + periodo do informe
  - angulo = qual pergunta o post responde sobre esse dado

Os angulos vivem em pautas/<marca>.json. A cada rodada a guarda escolhe o
primeiro que ainda nao foi publicado PARA ESTE informe, e so para quando
todos acabarem -- ai o perfil fica quieto ate sair informe novo.

Um angulo so entra na fila se os fatos de que ele precisa existirem (campo
`exige`) e se as condicoes dele valerem (campo `so_se`). Isso e o que permite
publicar muito sem inventar nada: quando o dado nao sustenta a pergunta, o
angulo some sozinho, em vez de o modelo ter que "dar um jeito" -- que e
exatamente onde nasce superlativo inventado.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).parent
ESTADO = RAIZ / "estado"
SEM_NOVIDADE = 78          # codigo de saida combinado com o workflow


def _arquivo(marca: str) -> Path:
    return ESTADO / f"{marca}.json"


def _historico(marca: str) -> list[dict]:
    caminho = _arquivo(marca)
    if not caminho.exists():
        return []
    try:
        return json.loads(caminho.read_text(encoding="utf-8")).get("publicados", [])
    except json.JSONDecodeError:
        # Estado corrompido nao pode virar licenca para publicar duplicado.
        raise SystemExit(f"{caminho} ilegivel — conserte ou apague antes de rodar")


def assinatura(origem: dict, angulo: str, onde: str) -> dict:
    """Identidade de um post: (informe, angulo).

    Falha alto se a procedencia nao estiver la. Sem periodo e hash, a guarda
    nao consegue afirmar nem que e novo nem que e repetido -- e "nao sei
    dizer" nao pode virar "pode publicar". Na primeira versao esses campos
    entravam como None, dois None nao casavam com nada, e a guarda liberava
    tudo achando que estava conferindo.
    """
    faltando = [c for c in ("periodo", "hash_pdf") if not origem.get(c)]
    if faltando:
        raise SystemExit(
            f"{onde} sem {', '.join(faltando)} — nao da para saber de que informe "
            f"este post veio. Regere pelo pipeline atual antes de publicar."
        )
    return {
        "periodo": origem["periodo"],
        "hash_pdf": origem["hash_pdf"],
        "angulo": angulo,
    }


def ja_publicado(facts: dict, marca: str, angulo: str) -> dict | None:
    """Devolve o registro anterior se este (dado, angulo) ja foi ao ar."""
    alvo = assinatura(facts, angulo, "os facts")
    for reg in _historico(marca):
        if all(reg.get(k) == v for k, v in alvo.items()):
            return reg
    return None


def _valor(dados: dict, caminho: str):
    """Le um caminho pontilhado nos facts. Devolve None se faltar qualquer parte."""
    no = dados
    for parte in caminho.split("."):
        if not isinstance(no, dict) or parte not in no:
            return None
        no = no[parte]
    return no


def angulos_possiveis(facts: dict, marca: str) -> list[dict]:
    """Angulos que ESTE informe sustenta, na ordem da pauta."""
    caminho = RAIZ / "pautas" / f"{marca}.json"
    if not caminho.exists():
        raise SystemExit(f"sem pauta em {caminho.relative_to(RAIZ)} — nada a publicar")
    pauta = json.loads(caminho.read_text(encoding="utf-8"))

    servem = []
    for a in pauta.get("angulos", []):
        faltam = [c for c in a.get("exige", []) if _valor(facts, c) in (None, [], {})]
        if faltam:
            print(f"  angulo '{a['nome']}' fora: os facts nao tem {faltam}")
            continue
        reprovou = False
        for cond in a.get("so_se", []):
            v = _valor(facts, cond["caminho"])
            if not isinstance(v, (int, float)) or v < cond.get("minimo", 0):
                print(f"  angulo '{a['nome']}' fora: {cond['caminho']}={v} "
                      f"< minimo {cond.get('minimo')}")
                reprovou = True
        if not reprovou:
            servem.append(a)
    return servem


def _dias_desde_ultimo(marca: str) -> float | None:
    hist = _historico(marca)
    if not hist:
        return None
    ultimo = max((r.get("registrado_em") or "" for r in hist), default="")
    if not ultimo:
        return None
    try:
        quando = datetime.fromisoformat(ultimo)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - quando).total_seconds() / 86400


def proximo_angulo(facts: dict, marca: str) -> dict | None:
    """O primeiro angulo desta pauta que ainda nao virou post neste informe."""
    pauta_path = RAIZ / "pautas" / f"{marca}.json"
    pauta = json.loads(pauta_path.read_text(encoding="utf-8"))
    espera = pauta.get("min_dias_entre_posts", 0)
    desde = _dias_desde_ultimo(marca)
    if espera and desde is not None and desde < espera:
        print(f"  ultimo post foi ha {desde:.1f} dia(s); a pauta pede {espera}. "
              f"Segurando.")
        return None

    for a in angulos_possiveis(facts, marca):
        if not ja_publicado(facts, marca, a["nome"]):
            return a
    return None


def registrar(pasta: Path) -> None:
    """Grava o post publicado no historico. Roda depois do publicar.py."""
    manifest = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    resultado_path = pasta / "resultado.json"
    if not resultado_path.exists():
        print("sem resultado.json — nada publicado, nada a registrar")
        return

    resultado = json.loads(resultado_path.read_text(encoding="utf-8"))
    if not resultado.get("media_id"):
        print("resultado.json sem media_id — nada a registrar")
        return

    marca = manifest["marca"]
    ESTADO.mkdir(exist_ok=True)
    caminho = _arquivo(marca)
    dados = {"publicados": _historico(marca)}

    registro = assinatura(manifest, manifest.get("angulo", "padrao"),
                          f"{pasta.name}/manifest.json")
    dados["publicados"].append({
        **registro,
        "formato": manifest.get("formato"),
        "data": manifest.get("data"),
        "media_id": resultado["media_id"],
        "permalink": resultado.get("permalink"),
        "registrado_em": datetime.now(timezone.utc).isoformat(),
    })
    # Historico curto: o que interessa e o passado recente, e o arquivo e
    # commitado a cada rodada.
    dados["publicados"] = dados["publicados"][-60:]

    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"registrado em {caminho.relative_to(RAIZ)}: {resultado['media_id']}")


def main() -> int:
    if "--registrar-ultima" in sys.argv:
        # Reaproveita a mesma busca do publicador: um so lugar decide o que e
        # "a pasta mais recente", entao guarda e publicacao nunca divergem.
        from publicar import ultima_pasta
        registrar(ultima_pasta(sys.argv[sys.argv.index("--registrar-ultima") + 1]))
        return 0

    if len(sys.argv) < 3:
        raise SystemExit("uso: python guarda.py <facts.json> <marca> "
                         "[--angulo <nome>] [--forcar]")

    facts = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    marca = sys.argv[2]

    print(f"Informe {facts.get('periodo')} (hash {facts.get('hash_pdf')}).")
    servem = angulos_possiveis(facts, marca)
    usados = {r.get("angulo") for r in _historico(marca)
              if r.get("periodo") == facts.get("periodo")
              and r.get("hash_pdf") == facts.get("hash_pdf")}
    print(f"  {len(servem)} angulo(s) que o dado sustenta; "
          f"{len(usados & {a['nome'] for a in servem})} ja publicado(s) neste informe.")

    escolhido = proximo_angulo(facts, marca)

    if escolhido is None and "--forcar" in sys.argv:
        escolhido = servem[0] if servem else None
        if escolhido:
            print(f"AVISO: --forcar em uso; repetindo '{escolhido['nome']}'.")

    if escolhido is None:
        print("NADA NOVO: todos os angulos deste informe ja viraram post.")
        print("  Nao vou repetir. Espere o informe do mes que vem, "
              "acrescente um angulo na pauta, ou rode com --forcar.")
        return SEM_NOVIDADE

    print(f"Pode publicar: angulo '{escolhido['nome']}'")
    print(f"  pergunta: {escolhido['pergunta']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
