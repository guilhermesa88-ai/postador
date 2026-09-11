"""
limpar_fila.py — tira de entrada/ o que ja foi publicado.

    python limpar_fila.py <marca>              # a pasta da ultima publicacao
    python limpar_fila.py <marca> --todas      # tudo que o estado diz publicado
    python limpar_fila.py <marca> --todas --so-listar

O PROBLEMA

O `publicar-arte.yml` nunca removia a pasta publicada. A guarda impede
republicar, entao nada saia errado -- mas `entrada/` crescia para sempre. Com
4 pastas ninguem nota. Com 50 reels de video, cada `actions/checkout` das
quatro workflows (uma delas 4x/dia) passa a carregar tudo isso, e o
`actions/checkout` vem com `fetch-depth: 1`: o peso que importa e o da arvore
de trabalho, exatamente o que este script remove.

O QUE IMPEDE ESTE SCRIPT DE APAGAR ARTE NAO PUBLICADA

Tres condicoes, todas obrigatorias, cada uma capaz de reprovar:

  1. existe registro em estado/<marca>.json com angulo "arte:<pasta>";
  2. esse registro tem `media_id` -- ou seja, o post existe no Instagram;
  3. o caminho a remover esta DENTRO de entrada/<marca>/.

Sem as tres, o script recusa e sai 1. Preferi condicoes explicitas a confiar
na ordem dos passos do workflow: apagar arte que nao foi publicada e perda de
trabalho seu, nao um job vermelho.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).parent
ENTRADA = RAIZ / "entrada"
ESTADO = RAIZ / "estado"
SAIDA = RAIZ / "out"


def publicados_com_media(marca: str) -> dict[str, str]:
    """{nome_da_pasta: media_id} do que o estado diz que foi ao ar."""
    caminho = ESTADO / f"{marca}.json"
    if not caminho.exists():
        return {}
    pubs = json.loads(caminho.read_text(encoding="utf-8")).get("publicados", [])
    saida: dict[str, str] = {}
    for r in pubs:
        ang = str(r.get("angulo") or "")
        if not ang.startswith("arte:"):
            continue
        if not r.get("media_id"):
            # Registro sem media_id nao prova publicacao. Nao apaga.
            continue
        saida[ang[len("arte:"):]] = r["media_id"]
    return saida


def _dentro_da_fila(pasta: Path, marca: str) -> bool:
    """A pasta esta mesmo em entrada/<marca>/? Barreira contra caminho torto."""
    try:
        rel = pasta.resolve().relative_to((ENTRADA / marca).resolve())
    except ValueError:
        return False
    return len(rel.parts) == 1


def remover(marca: str, nomes: dict[str, str], so_listar: bool) -> int:
    base = ENTRADA / marca
    if not base.exists():
        print(f"entrada/{marca}/ nao existe — nada a limpar.")
        return 0

    removidas, bloqueadas = [], []
    for nome, media_id in sorted(nomes.items()):
        pasta = base / nome
        if not pasta.is_dir():
            continue                      # ja foi limpa numa rodada anterior
        if not _dentro_da_fila(pasta, marca):
            bloqueadas.append(f"{nome}: fora de entrada/{marca}/")
            continue
        tam = sum(f.stat().st_size for f in pasta.rglob("*") if f.is_file())
        if so_listar:
            print(f"  [listar] {nome}  {tam/1e6:.1f} MB  media_id {media_id}")
        else:
            shutil.rmtree(pasta)
            print(f"  removida {nome}  {tam/1e6:.1f} MB  (media_id {media_id})")
        removidas.append(nome)

    if bloqueadas:
        for b in bloqueadas:
            print(f"::error::{b}")
        raise SystemExit("caminho suspeito — nada mais foi removido")

    restantes = [p.name for p in sorted(base.glob("*")) if p.is_dir()]
    verbo = "a remover" if so_listar else "removida(s)"
    print(f"\n{len(removidas)} pasta(s) {verbo}; "
          f"{len(restantes)} ainda na fila de {marca}")
    if restantes:
        print("  fila: " + ", ".join(restantes[:8])
              + (f" ... (+{len(restantes)-8})" if len(restantes) > 8 else ""))
    return 0


def ultima(marca: str) -> dict[str, str]:
    """So a pasta da publicacao mais recente, conferida pelo resultado.json.

    Le do out/, nao do estado: e o caminho que o workflow acabou de usar, e o
    `origem` do manifest diz exatamente de qual pasta da fila aquela midia
    saiu -- sem precisar casar nomes por convencao.
    """
    raiz = SAIDA / marca
    if not raiz.exists():
        raise SystemExit(f"out/{marca}/ nao existe — nada publicado nesta rodada")
    cands = sorted((p for p in raiz.glob("*") if (p / "resultado.json").exists()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise SystemExit(f"nenhum resultado.json em out/{marca}/ — nada a limpar")
    pasta = cands[0]
    resultado = json.loads((pasta / "resultado.json").read_text(encoding="utf-8"))
    manifest = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))

    media_id = resultado.get("media_id")
    if not media_id:
        raise SystemExit(
            f"{pasta.name}: resultado.json sem media_id — o post NAO foi ao ar, "
            f"a pasta fica na fila")

    origem = str(manifest.get("origem") or "")
    if not origem:
        raise SystemExit(
            f"{pasta.name}: manifest sem `origem` — nao sei de qual pasta da "
            f"fila esta midia veio, e nao vou adivinhar")
    esperado = f"entrada/{marca}/"
    if not origem.replace("\\", "/").startswith(esperado):
        raise SystemExit(
            f"{pasta.name}: origem {origem!r} nao esta em {esperado} — recuso")
    return {Path(origem).name: media_id}


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("uso: python limpar_fila.py <marca> "
                         "[--todas] [--so-listar]")
    marca = sys.argv[1]
    so_listar = "--so-listar" in sys.argv

    if "--todas" in sys.argv:
        nomes = publicados_com_media(marca)
        print(f"estado/{marca}.json: {len(nomes)} arte(s) com media_id")
    else:
        nomes = ultima(marca)
        print(f"ultima publicacao: {list(nomes)[0]}")

    if not nomes:
        print("nada publicado para limpar.")
        return 0
    return remover(marca, nomes, so_listar)


if __name__ == "__main__":
    sys.exit(main())
