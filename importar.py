"""
importar.py — um zip vira fila.

    python importar.py            # procura zips e importa todos

O PROBLEMA QUE ISSO RESOLVE

Arrastar 20 arquivos soltos para a pagina de upload do GitHub perde a estrutura
de pastas: tudo cai na raiz e os nomes repetidos viram "01 (5).jpg",
"01 (10).jpg". Foi o que aconteceu em 09/09/2026. Pior: a numeracao que o
GitHub aplica NAO segue a ordem das pastas, entao remontar pelo nome teria
embaralhado os carrosseis em silencio -- so daria para perceber depois de
publicado, com as telas fora de ordem.

Um zip nao tem esse problema. E UM arquivo: nao ha o que embaralhar, nao ha
nome repetido para o navegador renomear. A estrutura viaja dentro dele.

COMO USAR

Arraste um zip com esta forma para a pagina de upload do repositorio:

    estilovidya/
        05-ev3-03-o-essencial/
            legenda.txt
            video.mp4
            capa.jpg

A pasta de primeiro nivel e a MARCA. Ela tem de existir em brands/, senao o
zip nao e tocado. Ao dar commit, o workflow `importar-zip.yml` dispara sozinho,
extrai para entrada/, confere a fila e apaga o zip.

O QUE FAZ FALHAR (de proposito)

- zip que sobrou sem ser importado -> vermelho. Zip parado que ninguem nota e
  conteudo que voce achou que estava na fila e nao esta.
- pasta de primeiro nivel que nao e uma marca conhecida -> nao mexe no zip e
  falha. Adivinhar a marca aqui e como adivinhar a ordem pelo nome do arquivo.
- caminho com `..` ou absoluto dentro do zip -> recusa antes de extrair.
- pasta que ja existe em entrada/ -> recusa. Sobrescrever fila em silencio
  perderia material aprovado.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).parent
ENTRADA = RAIZ / "entrada"
BRANDS = RAIZ / "brands"


def marcas_conhecidas() -> set[str]:
    return {p.stem for p in BRANDS.glob("*.json")}


def zips() -> list[Path]:
    """Zips na raiz e em entrada/. Nao varre o repositorio inteiro: material
    de importacao chega por um caminho combinado, nao de qualquer lugar."""
    achados = sorted(RAIZ.glob("*.zip")) + sorted(ENTRADA.glob("*.zip"))
    return [p for p in achados if p.is_file()]


def conferir(z: zipfile.ZipFile, caminho: Path) -> tuple[str, list[str]]:
    """Devolve (marca, membros) ou levanta SystemExit com o motivo."""
    membros = [n for n in z.namelist() if not n.endswith("/")]
    if not membros:
        raise SystemExit(f"{caminho.name}: zip vazio")

    for n in membros:
        p = Path(n)
        if p.is_absolute() or ".." in p.parts:
            raise SystemExit(f"{caminho.name}: caminho suspeito no zip: {n}")

    topos = {Path(n).parts[0] for n in membros}
    if len(topos) != 1:
        raise SystemExit(
            f"{caminho.name}: esperava UMA pasta de primeiro nivel (a marca), "
            f"achei {sorted(topos)}")
    marca = topos.pop()

    if marca not in marcas_conhecidas():
        raise SystemExit(
            f"{caminho.name}: '{marca}' nao e uma marca conhecida "
            f"({sorted(marcas_conhecidas())}). Nao vou adivinhar — o zip fica onde esta.")

    for n in membros:
        if len(Path(n).parts) < 3:
            raise SystemExit(
                f"{caminho.name}: '{n}' esta solto dentro de {marca}/. "
                "Cada post precisa da propria pasta.")

    pastas = {Path(n).parts[1] for n in membros}
    for pasta in sorted(pastas):
        destino = ENTRADA / marca / pasta
        if destino.exists():
            raise SystemExit(
                f"{caminho.name}: entrada/{marca}/{pasta} ja existe. "
                "Renomeie a pasta no zip ou remova a antiga antes de importar.")

    return marca, membros


def importar(caminho: Path) -> tuple[str, list[str]]:
    """Extrai para um lugar provisorio, VALIDA cada post, e so entao move para
    entrada/ e apaga o zip.

    A ordem importa. A versao anterior extraia direto e apagava o zip antes de
    olhar o conteudo: um JPEG que na verdade era PNG entraria na fila e so
    apareceria como erro no dia da publicacao, com o zip original ja perdido.
    Agora, se qualquer peca reprovar, nada entra na fila e o zip continua onde
    estava."""
    import shutil
    import tempfile
    import arte

    with zipfile.ZipFile(caminho) as z:
        marca, membros = conferir(z, caminho)
        temp = Path(tempfile.mkdtemp(prefix="importar-", dir=RAIZ))
        try:
            z.extractall(temp, members=[m for m in z.namelist()
                                        if not m.endswith("/")])
            pastas = sorted({Path(n).parts[1] for n in membros})

            # Mesma validacao que o publicador faz -- adiantada para o momento
            # da importacao, onde ainda da para consertar sem pressa.
            for pasta in pastas:
                m = arte.inspecionar(temp / marca / pasta)
                print(f"    {pasta}: {m['formato']}, {len(m['arquivos'])} arquivo(s)")

            destino = ENTRADA / marca
            destino.mkdir(parents=True, exist_ok=True)
            for pasta in pastas:
                shutil.move(str(temp / marca / pasta), str(destino / pasta))
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    caminho.unlink()
    return marca, pastas


def main() -> int:
    pendentes = zips()
    if not pendentes:
        print("nenhum zip para importar.")
        return 0

    print(f"{len(pendentes)} zip(s) encontrado(s).")
    marcas: set[str] = set()
    problemas: list[str] = []
    for z in pendentes:
        try:
            marca, pastas = importar(z)
        except SystemExit as e:
            print(f"::error::{e}")
            problemas.append(z.name)
            continue
        except zipfile.BadZipFile:
            print(f"::error::{z.name}: nao e um zip valido")
            problemas.append(z.name)
            continue
        marcas.add(marca)
        print(f"{z.name} -> entrada/{marca}/ ({len(pastas)} pasta(s))")
        for p in pastas:
            print(f"    {p}")

    if problemas:
        print(f"::error::zip(s) nao importado(s): {problemas}. "
              "Eles continuam no repositorio — resolva o motivo acima e rode de novo.")
        return 1

    print("\nmarcas afetadas: " + ", ".join(sorted(marcas)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
