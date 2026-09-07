"""
ingest_fipezap.py — extrai do informe do FipeZap a variacao de preco das 22
capitais, em tres horizontes, e grava em facts/fipezap-capitais.json.

    python ingest_fipezap.py            # mes mais recente disponivel
    python ingest_fipezap.py 2026 8     # mes especifico

HISTORICO QUE VALE LER ANTES DE MEXER

A primeira versao deste arquivo tentava extrair uma tabela de precos POR BAIRRO
de Belo Horizonte. Essa tabela nao existe. Ela apareceu num resumo de IA do PDF,
duas vezes seguidas, com numeros identicos -- o que parecia confirmacao e era o
mesmo modelo inventando a mesma coisa. Quatro informes reais foram baixados e
nenhum tinha uma linha sequer. O proprio PDF diz, por escrito, que "a Fipe nao
divulga informacoes detalhadas ou tabelas de preco medio por zona, distrito ou
bairro".

O que salvou foi o parser falhar alto em vez de aceitar o que encontrasse.
Mantenha assim: **se a extracao nao bater com o esperado, nada e gravado.**

O que o informe REALMENTE traz, e o que esta versao extrai: a variacao
percentual de cada uma das 22 capitais, em tres horizontes -- mes, acumulado no
ano e janela movel de 12 meses -- em paragrafos de texto corrido.

Dependencias: requests, pdfplumber
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pdfplumber
import requests

RAIZ = Path(__file__).parent
BASE = "https://downloads.fipe.org.br/indices/fipezap"
SUFIXOS = ["residencial-venda", "residencial-venda-pub", "residencial-venda-publico"]

# O servidor da Fipe recusa com 403 quem chega sem User-Agent de navegador --
# filtro generico de bot, nao politica contra acesso automatizado. O PDF e
# publico e linkado na propria pagina do indice.
HEADERS = {
    "User-Agent": ("metro-de-bh-bot/1.0 "
                   "(+https://github.com/guilhermesa88-ai/postador) Mozilla/5.0"),
    "Accept": "application/pdf,*/*",
}

# Vocabulario fechado: as 22 capitais da cesta do indice. Fechado de proposito --
# e o que impede o parser de "achar" cidade que nao existe.
CAPITAIS = [
    "Aracaju", "Belém", "Belo Horizonte", "Brasília", "Campo Grande", "Cuiabá",
    "Curitiba", "Florianópolis", "Fortaleza", "Goiânia", "João Pessoa", "Maceió",
    "Manaus", "Natal", "Porto Alegre", "Recife", "Rio de Janeiro", "Salvador",
    "São Luís", "São Paulo", "Teresina", "Vitória",
]

# Marcadores das tres secoes, ja sem espacos (o PDF extrai palavras coladas).
SECOES = [
    ("mensal",     "Análisedoúltimomês"),
    ("ano",        "Balançoparcialde"),
    ("doze_meses", "Análisedosúltimos12meses"),
]

MIN_CAPITAIS = 18          # abaixo disso o layout mudou; nao publique
VAR_MIN, VAR_MAX = -30.0, 60.0

# O informe compara a variacao do indice com a inflacao. O numero do IPCA e
# de terceiro (IBGE), citado dentro do texto -- entao nao invento pattern
# rigido: pego toda mencao a IPCA junto com o percentual mais proximo E o
# trecho literal em que ele aparece. O trecho e o que permite conferir a que
# horizonte aquele numero se refere, sem eu ter que adivinhar.
_IPCA = re.compile(r"IPCA[^.;]{0,120}?\(?([+-]?\d{1,2},\d{1,2})\s*%", re.I)
IPCA_MIN, IPCA_MAX = -5.0, 30.0


class IngestError(RuntimeError):
    pass


def baixar(ano: int, mes: int) -> tuple[bytes, str]:
    erros = []
    for s in SUFIXOS:
        u = f"{BASE}/fipezap-{ano}{mes:02d}-{s}.pdf"
        try:
            r = requests.get(u, timeout=90, headers=HEADERS)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                return r.content, u
            erros.append(f"{r.status_code} {u}")
        except requests.RequestException as e:
            erros.append(f"{e} {u}")
    raise IngestError(f"informe de {mes:02d}/{ano} nao baixado:\n  " + "\n  ".join(erros))


def texto_do_pdf(blob: bytes) -> str:
    caminho = RAIZ / ".fipezap.pdf"
    caminho.write_bytes(blob)
    try:
        with pdfplumber.open(caminho) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    finally:
        caminho.unlink(missing_ok=True)


def fatiar(texto: str) -> dict[str, str]:
    """Recorta as tres secoes de analise. Unica transformacao: tirar espaco.

    Nao normalizo acento: o PDF os preserva, e manter o texto o mais proximo
    possivel do original reduz a chance de casar coisa errada.
    """
    h = re.sub(r"\s+", "", texto)
    pos = {nome: h.find(marca) for nome, marca in SECOES}
    faltando = [n for n, p in pos.items() if p < 0]
    if faltando:
        raise IngestError(
            f"secoes nao encontradas no informe: {faltando}. "
            f"O texto do PDF mudou de formato. Nada foi gravado."
        )
    nomes = [n for n, _ in SECOES]
    fatias = {}
    for i, n in enumerate(nomes):
        fim = pos[nomes[i + 1]] if i + 1 < len(nomes) else len(h)
        fatias[n] = h[pos[n]:fim]
    return fatias


def capitais_da_fatia(fatia: str) -> dict[str, float]:
    achados: dict[str, float] = {}
    for cap in CAPITAIS:
        agulha = cap.replace(" ", "")
        m = re.search(re.escape(agulha) + r"\(([+-]?\d{1,2},\d{2})%\)", fatia, re.I)
        if not m:
            continue
        v = float(m.group(1).replace(",", "."))
        if VAR_MIN <= v <= VAR_MAX:
            achados[cap] = v
    return achados


def indice_geral(fatia: str, horizonte: str) -> float | None:
    """A variacao do indice como um todo, para comparar a capital com a media."""
    padroes = {
        "mensal": r"aumentomédiode([+-]?\d{1,2},\d{2})%",
        "ano": r"acumuloualtade([+-]?\d{1,2},\d{2})%",
        "doze_meses": r"registraraltade([+-]?\d{1,2},\d{2})%",
    }
    m = re.search(padroes[horizonte], fatia, re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def referencias_ipca(texto: str) -> list[dict]:
    """Mencoes ao IPCA no informe, com o trecho literal em volta.

    Devolve lista vazia se o informe nao citar o IPCA -- e isso e um resultado
    valido, nao um erro. O efeito de nao achar e o perfil nao poder comparar o
    imovel com a inflacao naquele mes. O efeito de eu "achar" um numero errado
    seria publicar uma comparacao falsa. Entre os dois, a lista vazia.
    """
    plano = re.sub(r"\s+", " ", texto)
    achados: list[dict] = []
    vistos: set[tuple[str, float]] = set()

    for m in _IPCA.finditer(plano):
        v = float(m.group(1).replace(",", "."))
        if not (IPCA_MIN <= v <= IPCA_MAX):
            continue
        ini = max(0, m.start() - 160)
        fim = min(len(plano), m.end() + 60)
        trecho = plano[ini:fim].strip()
        chave = (trecho[-90:], v)
        if chave in vistos:
            continue
        vistos.add(chave)
        achados.append({"variacao_pct": v, "trecho": trecho})

    return achados


def ingerir(ano: int, mes: int) -> Path:
    print(f"Buscando informe de {mes:02d}/{ano}...")
    blob, url = baixar(ano, mes)
    print(f"  {url}  ({len(blob)//1024} KB)")

    texto = texto_do_pdf(blob)
    fatias = fatiar(texto)
    horizontes: dict[str, dict] = {}

    for nome, fatia in fatias.items():
        caps = capitais_da_fatia(fatia)
        if len(caps) < MIN_CAPITAIS:
            raise IngestError(
                f"secao '{nome}': so {len(caps)} de 22 capitais reconhecidas "
                f"(minimo {MIN_CAPITAIS}). Formato mudou. Nada foi gravado.\n"
                f"Rode o workflow 'Inspecionar PDF' e olhe o texto real: {url}"
            )
        if "Belo Horizonte" not in caps:
            raise IngestError(f"secao '{nome}': Belo Horizonte ausente. Nada foi gravado.")

        ordenado = sorted(caps.items(), key=lambda x: -x[1])
        posicao = [c for c, _ in ordenado].index("Belo Horizonte") + 1
        horizontes[nome] = {
            "indice_geral_pct": indice_geral(fatia, nome),
            "capitais": [{"cidade": c, "variacao_pct": v} for c, v in ordenado],
            "bh_pct": caps["Belo Horizonte"],
            "bh_posicao": posicao,
            "total_capitais": len(caps),
        }

    facts = {
        "fonte": "FipeZap — Índice de Venda Residencial",
        "fonte_url": url,
        "descricao": ("Variação percentual do preço de venda de imóveis residenciais "
                      "nas 22 capitais monitoradas, em três horizontes"),
        "periodo": f"{ano}-{mes:02d}",
        "extraido_em": datetime.now(timezone.utc).isoformat(),
        "hash_pdf": hashlib.sha256(blob).hexdigest()[:16],
        "horizontes": horizontes,
    }

    ipca = referencias_ipca(texto)
    if ipca:
        facts["inflacao_ipca"] = {
            "fonte": "IPCA/IBGE, conforme citado no informe do FipeZap",
            "observacao": ("Cada item traz o trecho literal do informe. Use o "
                           "trecho para saber a que horizonte o numero se "
                           "refere; nao compare horizontes diferentes."),
            "mencoes": ipca,
        }

    destino = RAIZ / "facts" / "fipezap-capitais.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    for nome, h in horizontes.items():
        print(f"\n  {nome}: {h['total_capitais']} capitais, indice geral "
              f"{h['indice_geral_pct']:+.2f}%" if h["indice_geral_pct"] is not None
              else f"\n  {nome}: {h['total_capitais']} capitais")
        print(f"    Belo Horizonte {h['bh_pct']:+.2f}%  "
              f"(posicao {h['bh_posicao']} de {h['total_capitais']})")
        topo = h["capitais"][0]
        print(f"    topo: {topo['cidade']} {topo['variacao_pct']:+.2f}%")

    # Impresso no log de proposito: e assim que se confere o IPCA contra o PDF
    # antes de deixar o perfil comparar imovel com inflacao.
    print("\n  IPCA citado no informe:")
    if ipca:
        for m in ipca:
            print(f"    {m['variacao_pct']:+.2f}%  ...{m['trecho'][-150:]}")
    else:
        print("    nenhuma mencao encontrada — o perfil nao vai comparar com inflacao")

    print(f"\n  gravado em {destino}")
    return destino


def main() -> int:
    if len(sys.argv) >= 3:
        candidatos = [(int(sys.argv[1]), int(sys.argv[2]))]
    else:
        hoje = date.today()
        candidatos, a, m = [], hoje.year, hoje.month
        for _ in range(4):
            m -= 1
            if m == 0:
                m, a = 12, a - 1
            candidatos.append((a, m))

    ultimo = None
    for ano, mes in candidatos:
        try:
            ingerir(ano, mes)
            return 0
        except IngestError as e:
            ultimo = e
            print(f"  {mes:02d}/{ano}: {str(e).splitlines()[0]}")
    print(f"\nFALHOU: {ultimo}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
