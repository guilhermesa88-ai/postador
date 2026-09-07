"""
ingest_fipezap.py — baixa o informe do FipeZap e extrai a tabela de bairros
de Belo Horizonte para facts/.

    python ingest_fipezap.py                 # mes mais recente disponivel
    python ingest_fipezap.py 2026 8          # mes especifico

Por que parser deterministico e nao "eu li o PDF e anotei": o perfil inteiro se
apoia em "todo numero com fonte". Numero que passa por leitura humana ou por
resumo de modelo pode chegar trocado sem ninguem perceber. Aqui o texto sai do
PDF por extracao, cada linha e conferida contra faixas plausiveis, e **se a
tabela nao for encontrada o programa falha em vez de inventar**.

Essa ultima parte e proposital. Ha uma nota no proprio informe dizendo que a
Fipe nao divulga tabela por bairro; se isso significar que a tabela nao existe
em algum mes, este script para e ninguem publica nada.

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

# Sufixos que a Fipe alterna entre meses. Tentados em ordem.
SUFIXOS = ["residencial-venda", "residencial-venda-pub", "residencial-venda-publico"]

# Faixas de sanidade. Nao sao chute: preco de m2 residencial em capital
# brasileira fora de 1.000-60.000 e erro de parse, nao mercado.
PRECO_MIN, PRECO_MAX = 1_000, 60_000
VAR_MIN, VAR_MAX = -50.0, 50.0
MIN_LINHAS = 5

# BAIRRO  R$ 17.432  -0,4%   (o R$ as vezes vem colado, as vezes ausente)
LINHA = re.compile(
    r"^([A-ZÀ-Ú][A-ZÀ-Ú\s\.\-']{2,34})\s+"       # nome em caixa alta
    r"R?\$?\s*([\d]{1,2}\.[\d]{3})\s+"            # 17.432
    r"([+-]?\d{1,2},\d)\s*%?$"                    # -0,4%
)


class IngestError(RuntimeError):
    pass


def url_do_informe(ano: int, mes: int) -> list[str]:
    ref = f"{ano}{mes:02d}"
    return [f"{BASE}/fipezap-{ref}-{s}.pdf" for s in SUFIXOS]


def baixar(ano: int, mes: int) -> tuple[bytes, str]:
    erros = []
    for u in url_do_informe(ano, mes):
        try:
            r = requests.get(u, timeout=90)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                return r.content, u
            erros.append(f"{r.status_code} {u}")
        except requests.RequestException as e:
            erros.append(f"{e} {u}")
    raise IngestError(f"informe de {mes:02d}/{ano} nao encontrado:\n  " + "\n  ".join(erros))


def texto_do_pdf(blob: bytes) -> str:
    caminho = RAIZ / ".fipezap.pdf"
    caminho.write_bytes(blob)
    try:
        with pdfplumber.open(caminho) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    finally:
        caminho.unlink(missing_ok=True)


def achar_secao_bh(texto: str) -> str:
    """Recorta o pedaco do documento que fala de Belo Horizonte."""
    linhas = texto.split("\n")
    ini = None
    for i, l in enumerate(linhas):
        if re.search(r"belo\s+horizonte", l, re.I):
            ini = i
            break
    if ini is None:
        raise IngestError("nenhuma mencao a Belo Horizonte no informe")
    # a tabela de bairros vem logo depois; 120 linhas cobrem com folga
    return "\n".join(linhas[ini:ini + 120])


def extrair_bairros(secao: str) -> list[dict]:
    achados: list[dict] = []
    vistos: set[str] = set()
    for linha in secao.split("\n"):
        m = LINHA.match(linha.strip())
        if not m:
            continue
        nome = " ".join(m.group(1).split()).title()
        preco = int(m.group(2).replace(".", ""))
        var = float(m.group(3).replace(",", "."))

        if not (PRECO_MIN <= preco <= PRECO_MAX):
            continue
        if not (VAR_MIN <= var <= VAR_MAX):
            continue
        if nome in vistos:
            continue
        vistos.add(nome)
        achados.append({"bairro": nome, "preco_m2": preco, "variacao_12m_pct": var})
    return achados


def ingerir(ano: int, mes: int) -> Path:
    print(f"Buscando informe de {mes:02d}/{ano}...")
    blob, url = baixar(ano, mes)
    print(f"  {url}  ({len(blob)//1024} KB)")

    texto = texto_do_pdf(blob)
    secao = achar_secao_bh(texto)
    bairros = extrair_bairros(secao)

    if len(bairros) < MIN_LINHAS:
        raise IngestError(
            f"so {len(bairros)} bairro(s) reconhecido(s) — abaixo do minimo de "
            f"{MIN_LINHAS}. O layout do informe pode ter mudado, ou a tabela por "
            f"bairro pode nao existir neste mes. Nada foi gravado.\n"
            f"Confira o PDF na mao antes de mexer no parser: {url}"
        )

    bairros.sort(key=lambda b: b["variacao_12m_pct"], reverse=True)
    variacoes = [b["variacao_12m_pct"] for b in bairros]

    facts = {
        "fonte": "FipeZap",
        "fonte_url": url,
        "descricao": ("Preço médio do m² anunciado e variação em 12 meses, por bairro, "
                      "em Belo Horizonte"),
        "periodo": f"{ano}-{mes:02d}",
        "unidade_variacao": "% em 12 meses",
        "extraido_em": datetime.now(timezone.utc).isoformat(),
        "hash_pdf": hashlib.sha256(blob).hexdigest()[:16],
        "series": bairros,
        "agregados": {
            "bairros_na_tabela": len(bairros),
            "maior_alta_pct": max(variacoes),
            "maior_queda_pct": min(variacoes),
            "amplitude_pp": round(max(variacoes) - min(variacoes), 1),
            "bairros_em_alta": sum(1 for v in variacoes if v > 0),
            "bairros_em_queda": sum(1 for v in variacoes if v < 0),
            "preco_maximo": max(b["preco_m2"] for b in bairros),
            "preco_minimo": min(b["preco_m2"] for b in bairros),
        },
    }

    destino = RAIZ / "facts" / "fipezap-bh.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n  {len(bairros)} bairros extraidos, periodo {facts['periodo']}")
    for b in bairros:
        print(f"    {b['bairro']:<20} R$ {b['preco_m2']:>7,}/m²   {b['variacao_12m_pct']:+.1f}%"
              .replace(",", "."))
    print(f"\n  amplitude: {facts['agregados']['amplitude_pp']} pontos percentuais")
    print(f"  gravado em {destino}")
    return destino


def main() -> int:
    if len(sys.argv) >= 3:
        ano, mes = int(sys.argv[1]), int(sys.argv[2])
        candidatos = [(ano, mes)]
    else:
        # o informe do mes corrente costuma sair no meio do mes seguinte;
        # tenta do mais recente para tras
        hoje = date.today()
        candidatos = []
        a, m = hoje.year, hoje.month
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
