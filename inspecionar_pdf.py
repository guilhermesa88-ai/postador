"""
inspecionar_pdf.py — descoberta, nao producao.

Baixa o informe do FipeZap e imprime o texto extraido, para eu escrever o
parser contra a realidade em vez de contra o que imagino que esteja la.

Existe porque a rodada anterior falhou exatamente por isso: escrevi um parser
para uma tabela que um resumo de IA disse existir, e que os quatro PDFs reais
nao tinham. Descoberta e producao viraram passos separados de proposito.

    python inspecionar_pdf.py            # mes mais recente
    python inspecionar_pdf.py 2026 8
"""

from __future__ import annotations

import re
import sys
from datetime import date

import pdfplumber
import requests

BASE = "https://downloads.fipe.org.br/indices/fipezap"
SUFIXOS = ["residencial-venda", "residencial-venda-pub", "residencial-venda-publico"]
HEADERS = {
    "User-Agent": ("metro-de-bh-bot/1.0 "
                   "(+https://github.com/guilhermesa88-ai/postador) Mozilla/5.0"),
    "Accept": "application/pdf,*/*",
}

CAPITAIS = ["Belo Horizonte", "São Paulo", "Rio de Janeiro", "Curitiba",
            "Porto Alegre", "Recife", "Salvador", "Fortaleza", "Brasília",
            "Vitória", "Goiânia", "Florianópolis"]


def baixar(ano: int, mes: int):
    for s in SUFIXOS:
        u = f"{BASE}/fipezap-{ano}{mes:02d}-{s}.pdf"
        r = requests.get(u, timeout=90, headers=HEADERS)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return r.content, u
    return None, None


def main() -> int:
    if len(sys.argv) >= 3:
        alvos = [(int(sys.argv[1]), int(sys.argv[2]))]
    else:
        h = date.today()
        alvos, a, m = [], h.year, h.month
        for _ in range(4):
            m -= 1
            if m == 0:
                m, a = 12, a - 1
            alvos.append((a, m))

    for ano, mes in alvos:
        blob, url = baixar(ano, mes)
        if not blob:
            print(f"-- {mes:02d}/{ano}: nao encontrado")
            continue

        print("=" * 78)
        print(f"INFORME {mes:02d}/{ano}   {url}   {len(blob)//1024} KB")
        print("=" * 78)

        caminho = "/tmp/fipezap.pdf"
        open(caminho, "wb").write(blob)
        with pdfplumber.open(caminho) as pdf:
            print(f"paginas: {len(pdf.pages)}\n")
            for npag, pag in enumerate(pdf.pages, start=1):
                texto = pag.extract_text() or ""
                linhas = [l.rstrip() for l in texto.split("\n") if l.strip()]

                # so paginas que falam de capital com numero perto
                relevante = any(c.lower() in texto.lower() for c in CAPITAIS) and \
                            re.search(r"\d{1,2}\.\d{3}|\d,\d{1,2}\s*%", texto)
                if not relevante:
                    continue

                print("-" * 78)
                print(f"PAGINA {npag}")
                print("-" * 78)
                for l in linhas[:70]:
                    print(f"  {l}")
                print()

                # tabelas que o pdfplumber reconhece como grade
                for t in (pag.extract_tables() or [])[:3]:
                    print(f"  [TABELA {len(t)}x{len(t[0]) if t else 0}]")
                    for linha in t[:14]:
                        print("   |", " | ".join((c or "").replace("\n", " ")[:22]
                                                 for c in linha))
                    print()
        return 0

    print("nenhum informe baixado")
    return 1


if __name__ == "__main__":
    sys.exit(main())
