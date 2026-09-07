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

# O informe traz uma linha de tabela comparando o indice com a inflacao:
#
#   IPCA* IBGE  -0,40%  +0,07%  +3,02%  +4,14%  -
#
# nesta ordem de coluna: mes de referencia, mes anterior, acumulado no ano,
# ultimos 12 meses, preco medio (vazio para indice de inflacao).
#
# A primeira versao desta extracao pegava "IPCA seguido do percentual mais
# proximo" no texto corrido. Parecia funcionar e estava errada: num paragrafo
# o percentual mais proximo da palavra IPCA era o do IGP-M, e o numero teria
# ido para o campo rotulado como IPCA. Numero certo com rotulo errado e pior
# que numero ausente -- o validador nao pega, porque o numero existe mesmo.
# Por isso agora so a linha da tabela conta, com as quatro colunas de uma vez.
_IPCA_TABELA = re.compile(
    r"IPCA\s*\*?\s+IBGE\s+([+-]?\d{1,2},\d{2})%\s+([+-]?\d{1,2},\d{2})%\s+"
    r"([+-]?\d{1,2},\d{2})%\s+([+-]?\d{1,2},\d{2})%"
)
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


def inflacao_ipca(texto: str) -> dict | None:
    """A linha do IPCA na tabela comparativa do informe, com rotulo por coluna.

    Devolve None se a linha nao for encontrada -- e isso e um resultado valido,
    nao um erro. O efeito de nao achar e o perfil nao poder comparar imovel com
    inflacao naquele mes. O efeito de achar errado seria publicar uma
    comparacao falsa. Entre os dois, None.
    """
    plano = re.sub(r"\s+", " ", texto)
    m = _IPCA_TABELA.search(plano)
    if not m:
        return None

    vals = [float(g.replace(",", ".")) for g in m.groups()]
    if not all(IPCA_MIN <= v <= IPCA_MAX for v in vals):
        return None

    mensal, mes_anterior, ano, doze_meses = vals
    return {
        "fonte": "IPCA/IBGE, conforme a tabela comparativa do informe do FipeZap",
        "observacao": ("Cada campo e uma coluna da linha do IPCA na tabela do "
                       "informe. Compare cada horizonte com o horizonte "
                       "correspondente do indice; nunca cruze horizontes."),
        "mensal_pct": mensal,
        "mes_anterior_pct": mes_anterior,
        "ano_pct": ano,
        "doze_meses_pct": doze_meses,
        "trecho_literal": plano[m.start():m.end()],
    }


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

    ipca = inflacao_ipca(texto)
    if ipca:
        facts["inflacao_ipca"] = ipca

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
    print("\n  IPCA na tabela do informe:")
    if ipca:
        print(f"    mes {ipca['mensal_pct']:+.2f}%   mes anterior {ipca['mes_anterior_pct']:+.2f}%   "
              f"ano {ipca['ano_pct']:+.2f}%   12 meses {ipca['doze_meses_pct']:+.2f}%")
        print(f"    linha lida: {ipca['trecho_literal']}")
    else:
        print("    linha nao encontrada — o perfil nao vai comparar com inflacao")

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
