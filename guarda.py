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

Hoje so existe um angulo, entao na pratica so publica quando muda o informe.
Quando o motor de angulos existir, ele passa `--angulo <nome>` e a mesma
guarda libera varios posts do mesmo informe, cada um respondendo outra coisa.
O seam ja esta aqui de proposito: e onde a cadencia diaria deixa de ser
repeticao e vira serie.
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
    angulo = "padrao"
    if "--angulo" in sys.argv:
        angulo = sys.argv[sys.argv.index("--angulo") + 1]

    anterior = ja_publicado(facts, marca, angulo)

    if anterior and "--forcar" not in sys.argv:
        print(f"NADA NOVO: o informe {facts.get('periodo')} "
              f"(hash {facts.get('hash_pdf')}) ja virou post no angulo '{angulo}'.")
        print(f"  publicado em {anterior.get('data')}  {anterior.get('permalink') or ''}")
        print("  Nao vou repetir. Rode com --forcar, ou traga um angulo novo.")
        return SEM_NOVIDADE

    if anterior:
        print(f"AVISO: repetindo (dado, angulo) ja publicado — --forcar foi usado.")

    print(f"Pode publicar: informe {facts.get('periodo')}, angulo '{angulo}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
