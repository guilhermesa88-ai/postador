"""
saude_token.py — o token que publica ainda vale, e por quanto tempo?

    python saude_token.py

O QUE ESTAVA ERRADO ANTES

O `renovar-token.yml` chamava `refresh_access_token`, lia o `expires_in` da
RESPOSTA e anunciava "token renovado, valido por 60 dias". Só que o token novo
que veio na resposta era descartado -- o secret `IG_ACCESS_TOKEN` nunca era
atualizado. Ou seja: o número reportado era a validade de um token que ninguém
ia usar, não a do token que está no secret e publica de verdade.

O job ficava verde todo dia até o dia em que o token vencesse, e aí tudo parava
em silêncio. É a mesma assinatura de defeito que já custou caro neste projeto:
uma checagem que não pode falhar. (O `ffmpeg -version | head -1`, o gráfico que
não era validado, o `setdefault` da procedência.)

O QUE ESTE SCRIPT FAZ

1. Confere se o token ARMAZENADO funciona agora (`/me`). Falha dura se não.
2. Renova e COMPARA o token devolvido com o armazenado. Essa comparação é o
   diagnóstico que faltava:
     - iguais   -> a renovação estende o token no lugar. Nada a guardar, o
                   prazo se renova sozinho todo dia. Problema resolvido.
     - difere   -> o secret continua no relógio original e vai morrer na data
                   dele. Aí é preciso guardar o token novo no secret à mão
                   (ou automatizar com um PAT).
   O script nunca imprime token nenhum -- só se são iguais ou não.
3. Mantém o prazo em `estado/saude-token.json`, identificando o token por uma
   IMPRESSÃO (sha256 truncado), nunca pelo valor. Quando você troca o secret a
   impressão muda, o script percebe e reinicia a contagem sozinho.
4. FALHA o job abaixo de MINIMO_DIAS. Notice ninguém lê; e-mail de job vermelho
   se lê.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

GRAPH = "https://graph.instagram.com"
RAIZ = Path(__file__).parent
REGISTRO = RAIZ / "estado" / "saude-token.json"

# Abaixo disso o job fica vermelho. 14 dias dão folga para renovar sem correria
# mesmo se o aviso cair numa semana cheia.
MINIMO_DIAS = 14

# Validade padrão de um token longo do Instagram, usada quando só sabemos
# quando vimos a impressão pela primeira vez.
VALIDADE_PADRAO_DIAS = 60


def env(nome: str) -> str:
    v = os.environ.get(nome)
    if not v:
        raise SystemExit(f"falta a variavel de ambiente {nome}")
    return v


def impressao(token: str) -> str:
    """Identifica o token sem guardá-lo. Digest de mão única, truncado: serve
    para dizer 'é o mesmo de ontem?' e para nada mais."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def funciona(token: str) -> tuple[bool, str]:
    """O teste que importa: este token publica hoje?"""
    try:
        r = requests.get(f"{GRAPH}/v23.0/me",
                         params={"fields": "id,username", "access_token": token},
                         timeout=30)
    except requests.RequestException as e:
        return False, f"erro de rede: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, r.json().get("username", "?")


def renovar(token: str) -> tuple[str | None, int, str]:
    """Devolve (token_novo, expires_in, erro)."""
    try:
        r = requests.get(f"{GRAPH}/refresh_access_token",
                         params={"grant_type": "ig_refresh_token",
                                 "access_token": token},
                         timeout=30)
    except requests.RequestException as e:
        return None, 0, f"erro de rede: {e}"
    if r.status_code != 200:
        return None, 0, f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    return body.get("access_token"), int(body.get("expires_in", 0)), ""


def ler_registro() -> dict:
    if REGISTRO.exists():
        return json.loads(REGISTRO.read_text(encoding="utf-8"))
    return {}


def gravar_registro(dados: dict) -> None:
    REGISTRO.parent.mkdir(exist_ok=True)
    REGISTRO.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def main() -> int:
    token = env("IG_ACCESS_TOKEN")
    agora = datetime.now(timezone.utc)
    imp = impressao(token)

    # 1) o token armazenado funciona?
    ok, detalhe = funciona(token)
    if not ok:
        print(f"::error::o token do secret NAO funciona — {detalhe}")
        print("Gere um token novo e atualize o secret IG_ACCESS_TOKEN.")
        return 1
    print(f"token do secret responde: @{detalhe} (impressao {imp})")

    # 2) renova e compara
    novo, expires_in, erro = renovar(token)
    if erro:
        print(f"::warning::nao foi possivel renovar: {erro}")
        renova_no_lugar = None
    else:
        renova_no_lugar = (novo == token)
        if renova_no_lugar:
            print(f"a renovacao ESTENDE o token no lugar — "
                  f"validade agora {expires_in / 86400:.0f} dias, sem nada a guardar.")
        else:
            print("::warning::a renovacao devolveu um token DIFERENTE: o secret "
                  "continua no relogio original e nao foi estendido.")

    # 3) prazo do token ARMAZENADO
    reg = ler_registro()
    if reg.get("impressao") != imp:
        print("token novo no secret (impressao mudou) — reiniciando a contagem.")
        reg = {"impressao": imp, "visto_em": agora.isoformat(timespec="seconds")}

    visto = datetime.fromisoformat(reg["visto_em"])

    if renova_no_lugar:
        # A renovação de hoje vale para o token que está no secret.
        expira = agora + timedelta(seconds=expires_in)
        base = "renovado hoje"
    else:
        # Só sabemos desde quando vimos esta impressão. A estimativa é otimista
        # se o token já era velho quando começamos a acompanhar — por isso o
        # limiar de 14 dias, e por isso isto está escrito aqui.
        expira = visto + timedelta(days=VALIDADE_PADRAO_DIAS)
        base = f"estimativa: primeira vez visto em {visto.date()} + {VALIDADE_PADRAO_DIAS}d"

    dias = (expira - agora).total_seconds() / 86400
    reg.update({
        "expira_em": expira.isoformat(timespec="seconds"),
        "dias_restantes": round(dias, 1),
        "renova_no_lugar": renova_no_lugar,
        "base_do_calculo": base,
        "conferido_em": agora.isoformat(timespec="seconds"),
    })
    gravar_registro(reg)

    print(f"prazo do token armazenado: {dias:.0f} dias ({base})")

    # 4) falhar cedo
    if dias < MINIMO_DIAS:
        print(f"::error::o token vence em {dias:.0f} dias (limite {MINIMO_DIAS}). "
              "Gere um token longo novo e atualize o secret IG_ACCESS_TOKEN "
              "antes que o pipeline pare.")
        return 1
    if dias < MINIMO_DIAS * 2:
        print(f"::warning::token vence em {dias:.0f} dias — vale renovar esta semana.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
