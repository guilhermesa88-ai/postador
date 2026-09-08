"""
metricas.py — coleta a série de desempenho dos posts já publicados.

    python metricas.py metro-de-bh
    python metricas.py metro-de-bh --janela 60

POR QUE ISSO EXISTE

O plano do projeto define o critério de corte em duas linhas, nos dois
documentos: "medir retenção, saves e shares — não seguidores" e "métrica de
corte aos 90 dias: saves + shares por post". Até aqui nada disso era coletado.

O `publicar.py` chama `fetch_insights` alguns SEGUNDOS depois de publicar --
quando toda métrica é estruturalmente zero -- e grava o resultado em
`out/.../resultado.json`, que vai só para o artefato do run e expira. O que
sobrevive, `estado/<marca>.json`, guarda `media_id` e `permalink` e nenhum
número. Ou seja: a pergunta "algum post funcionou?" não tinha como ser
respondida.

Este script fecha esse buraco. Roda todo dia, lê os posts do estado, pergunta
as métricas de cada um e acumula uma SÉRIE em `metricas/<marca>.csv`.

FORMATO LONGO, DE PROPÓSITO

Uma linha por (data_coleta, media_id, metrica). Reel e carrossel têm conjuntos
de métricas diferentes, e a Meta aposenta nome de métrica de uma versão para
outra (`impressions` e `plays` morreram em abr/2025). Em formato largo, cada
mudança dessas quebra o cabeçalho e obriga a reescrever o histórico. Em formato
longo, métrica nova é linha nova e o passado continua legível.

IDEMPOTÊNCIA

A chave é (data_coleta, media_id, metrica). Rodar duas vezes no mesmo dia
atualiza a linha, não duplica. Um cron que dispara duas vezes não polui a série.

O QUE FAZ O JOB FALHAR

Se havia post elegível e NENHUMA linha foi coletada, sai com código 1. Um
coletor que não coleta e termina verde seria mais um "check que não pode
falhar" -- a família de defeito que já custou caro neste projeto (o
`ffmpeg -version | head -1`, o gráfico não validado, o renovador de token que
lê a validade do token que descarta). Falha parcial vira `::warning::` e a
coleta continua para os outros posts.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ig_publisher import InstagramPublisher, PublishError, metrics_for

RAIZ = Path(__file__).parent
ESTADO = RAIZ / "estado"
METRICAS = RAIZ / "metricas"

# Depois de ~30 dias a série de um post do Instagram está praticamente parada;
# continuar consultando gasta chamada sem acrescentar informação.
JANELA_DIAS = 30

COLUNAS = ["data_coleta", "media_id", "marca", "angulo", "formato",
           "publicado_em", "dias", "metrica", "valor", "coletado_em"]


def env(nome: str) -> str:
    v = os.environ.get(nome)
    if not v:
        raise SystemExit(f"falta a variavel de ambiente {nome}")
    return v


def _instante(reg: dict) -> datetime | None:
    """Quando o post foi ao ar. `registrado_em` é gravado pela guarda no momento
    da publicação e é o mais confiável; `data` é o fallback para os registros
    semeados a mão."""
    bruto = reg.get("registrado_em") or reg.get("data")
    if not bruto:
        return None
    try:
        d = datetime.fromisoformat(bruto)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def elegiveis(marca: str, janela: int) -> list[dict]:
    caminho = ESTADO / f"{marca}.json"
    if not caminho.exists():
        # Marca cadastrada que ainda nao publicou nada nao e falha -- e o estado
        # normal de um perfil novo. Antes isso derrubava o job e teria pintado
        # de vermelho a coleta das outras marcas.
        print(f"{caminho.relative_to(RAIZ)} ainda nao existe — nada publicado.")
        return []

    import json
    historico = json.loads(caminho.read_text(encoding="utf-8")).get("publicados", [])
    agora = datetime.now(timezone.utc)
    saida = []
    for reg in historico:
        if not reg.get("media_id"):
            # Registro semeado a mao, sem id: nao da para consultar. Nao e erro,
            # mas tem de aparecer -- silencio aqui viraria "a serie esta completa".
            print(f"  (sem media_id, fora da coleta: angulo {reg.get('angulo')})")
            continue
        quando = _instante(reg)
        if quando is None:
            print(f"::warning::registro sem data utilizavel: {reg.get('media_id')}")
            continue
        dias = (agora - quando).days
        if dias > janela:
            continue
        saida.append({**reg, "_quando": quando, "_dias": dias})
    return saida


def consultar(pub: InstagramPublisher, media_id: str, formato: str) -> dict[str, int]:
    """Uma métrica inválida derruba a requisição inteira na Graph API. Por isso,
    se o lote falhar, tenta uma a uma: perder uma métrica é aceitável, perder o
    post inteiro por causa dela não é."""
    metricas = metrics_for(formato)
    try:
        return pub.fetch_insights(media_id, metricas)
    except PublishError as e:
        print(f"::warning::lote falhou para {media_id} ({e}); tentando metrica a metrica")

    out: dict[str, int] = {}
    for m in metricas:
        try:
            out.update(pub.fetch_insights(media_id, [m]))
        except PublishError as e:
            print(f"::warning::  {media_id}.{m}: {e}")
    return out


def carregar_serie(caminho: Path) -> dict[tuple[str, str, str], dict]:
    if not caminho.exists():
        return {}
    with caminho.open(encoding="utf-8", newline="") as f:
        return {(r["data_coleta"], r["media_id"], r["metrica"]): r
                for r in csv.DictReader(f)}


def gravar_serie(caminho: Path, linhas: dict[tuple[str, str, str], dict]) -> None:
    caminho.parent.mkdir(exist_ok=True)
    ordenado = sorted(linhas.values(),
                      key=lambda r: (r["data_coleta"], r["media_id"], r["metrica"]))
    with caminho.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS)
        w.writeheader()
        w.writerows(ordenado)


def coletar(marca: str, janela: int = JANELA_DIAS) -> int:
    posts = elegiveis(marca, janela)
    if not posts:
        print(f"nenhum post de {marca} dentro da janela de {janela} dias.")
        return 0

    print(f"{len(posts)} post(s) na janela de {janela} dias.")
    pub = InstagramPublisher(ig_user_id=env("IG_USER_ID"),
                             access_token=env("IG_ACCESS_TOKEN"))

    agora = datetime.now(timezone.utc)
    hoje = agora.date().isoformat()
    caminho = METRICAS / f"{marca}.csv"
    serie = carregar_serie(caminho)
    novas = 0

    for p in posts:
        valores = consultar(pub, p["media_id"], p.get("formato", "carousel"))
        if not valores:
            print(f"  {p['media_id']} ({p.get('angulo')}): nenhuma metrica")
            continue
        resumo = " ".join(f"{k}={v}" for k, v in sorted(valores.items()))
        print(f"  d+{p['_dias']} {p.get('angulo')}: {resumo}")
        for metrica, valor in valores.items():
            serie[(hoje, p["media_id"], metrica)] = {
                "data_coleta": hoje,
                "media_id": p["media_id"],
                "marca": marca,
                "angulo": p.get("angulo", ""),
                "formato": p.get("formato", ""),
                "publicado_em": p["_quando"].date().isoformat(),
                "dias": p["_dias"],
                "metrica": metrica,
                "valor": valor,
                "coletado_em": agora.isoformat(timespec="seconds"),
            }
            novas += 1

    if novas == 0:
        print("::error::havia post elegivel e nenhuma metrica foi coletada — "
              "token invalido, conta errada ou nomes de metrica aposentados.")
        return 1

    gravar_serie(caminho, serie)
    print(f"\n{novas} valor(es) gravado(s) em {caminho.relative_to(RAIZ)} "
          f"({len(serie)} linhas no total).")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("uso: python metricas.py <marca> [--janela N]")
    janela = JANELA_DIAS
    if "--janela" in sys.argv:
        janela = int(sys.argv[sys.argv.index("--janela") + 1])
    sys.exit(coletar(args[0], janela))
