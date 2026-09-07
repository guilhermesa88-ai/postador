"""
ig_auth.py — OAuth e ciclo de vida de token (Business Login for Instagram).

Cobre as quatro operações que você vai repetir uma vez por perfil e depois
todo dia para sempre:

    1. montar a URL de autorização
    2. trocar o `code` por token de 1 hora
    3. trocar o token de 1 hora por token de 60 dias
    4. renovar o token de 60 dias antes que ele morra

Ciclo de vida (confirmado set/2026):
    short-lived  = 1 hora
    long-lived   = 60 dias
    refresh      = só funciona se o token tiver >= 24h de idade E não
                   estiver expirado. Fora dessa janela ou não muda nada,
                   ou falha de vez — e aí só refazendo o OAuth na mão.

É por isso que o job de refresh roda **diário**, não mensal: a janela é
generosa, mas quem só lembra no dia 59 descobre o problema no dia 61.

CLI:

    python ig_auth.py url
    python ig_auth.py exchange <code>
    python ig_auth.py refresh <long_lived_token>
    python ig_auth.py whoami <token>
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

AUTH_BASE = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"

# ATENÇÃO — a armadilha que custa uma tarde:
#
# `client_id` e `client_secret` aqui são o **ID e a chave secreta do app do
# INSTAGRAM**, que aparecem na página "Configuração da API com login do
# Instagram". NÃO são o App ID e o App Secret do app da Meta. São números
# diferentes, e usar o da Meta devolve um erro de OAuth genérico que não
# diz nada sobre a causa.
#
#   App da Meta   : 4820596794889466   ← NÃO é este
#   App do Instagram: 1042005951977262 ← é este que vai em IG_CLIENT_ID

# Escopos exatamente como o painel da Meta os gera para este app.
# `manage_insights` é o que habilita a coleta de métricas por post.
SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
]

# Renove com folga. 7 dias é o alarme; 50 é quando o job age.
REFRESH_AFTER_DAYS = 50
ALERT_BEFORE_DAYS = 7


class AuthError(RuntimeError):
    pass


@dataclass
class Token:
    access_token: str
    expires_in: int
    obtained_at: dt.datetime

    @property
    def expires_at(self) -> dt.datetime:
        return self.obtained_at + dt.timedelta(seconds=self.expires_in)

    @property
    def days_left(self) -> float:
        return (self.expires_at - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400

    @property
    def needs_refresh(self) -> bool:
        age_days = (dt.datetime.now(dt.timezone.utc) - self.obtained_at).days
        return age_days >= 1 and self.days_left <= (60 - REFRESH_AFTER_DAYS)

    @property
    def is_critical(self) -> bool:
        return self.days_left <= ALERT_BEFORE_DAYS

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "expires_in": self.expires_in,
                "obtained_at": self.obtained_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
            },
            indent=2,
        )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _check(resp: requests.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        raise AuthError(f"Resposta não-JSON ({resp.status_code}): {resp.text[:300]}")
    if resp.status_code >= 400 or "error" in body or "error_message" in body:
        raise AuthError(f"[{resp.status_code}] {body}")
    return body


# ----------------------------------------------------------------------
# 1. URL de autorização
# ----------------------------------------------------------------------

def authorization_url(client_id: str, redirect_uri: str, state: str = "") -> str:
    """Abra no navegador logado na conta que você quer conectar.

    O painel do Meta App também gera essa URL pronta em
    'Business login settings' — se divergir do que sai aqui, confie no
    painel: ele reflete a configuração real do seu app.
    """
    params = {
        "force_reauth": "true",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(SCOPES),
    }
    if state:
        params["state"] = state
    return f"{AUTH_BASE}?{urlencode(params)}"


# ----------------------------------------------------------------------
# 2 e 3. Code -> token curto -> token longo
# ----------------------------------------------------------------------

def exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> tuple[str, str]:
    """Retorna (short_lived_token, instagram_user_id)."""
    # O Instagram anexa '#_' no fim do code na barra de endereços.
    code = code.split("#")[0].strip()

    body = _check(
        requests.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=30,
        )
    )
    token = body.get("access_token")
    user_id = str(body.get("user_id", ""))
    if not token:
        raise AuthError(f"Sem access_token na resposta: {body}")
    return token, user_id


def to_long_lived(short_token: str, client_secret: str) -> Token:
    body = _check(
        requests.get(
            f"{GRAPH}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": client_secret,
                "access_token": short_token,
            },
            timeout=30,
        )
    )
    return Token(
        access_token=body["access_token"],
        expires_in=int(body.get("expires_in", 5_184_000)),
        obtained_at=_now(),
    )


# ----------------------------------------------------------------------
# 4. Refresh
# ----------------------------------------------------------------------

def refresh(long_token: str) -> Token:
    body = _check(
        requests.get(
            f"{GRAPH}/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": long_token,
            },
            timeout=30,
        )
    )
    return Token(
        access_token=body["access_token"],
        expires_in=int(body.get("expires_in", 5_184_000)),
        obtained_at=_now(),
    )


def whoami(token: str) -> dict:
    return _check(
        requests.get(
            f"{GRAPH}/me",
            params={
                "fields": "id,username,account_type,media_count",
                "access_token": token,
            },
            timeout=30,
        )
    )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Falta a variável de ambiente {name}.")
    return value


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1]

    if cmd == "url":
        print(
            authorization_url(
                _env("IG_CLIENT_ID"), _env("IG_REDIRECT_URI"), state="bootstrap"
            )
        )
        print(
            "\nAbra no navegador logado na conta Business. Depois de autorizar,"
            "\ncopie o valor de ?code= da URL de retorno e rode:"
            "\n\n    python ig_auth.py exchange <code>\n"
        )
        return 0

    if cmd == "exchange":
        if len(argv) < 3:
            raise SystemExit("Uso: python ig_auth.py exchange <code>")
        short, user_id = exchange_code(
            argv[2],
            _env("IG_CLIENT_ID"),
            _env("IG_CLIENT_SECRET"),
            _env("IG_REDIRECT_URI"),
        )
        token = to_long_lived(short, _env("IG_CLIENT_SECRET"))
        print(f"instagram_user_id: {user_id}")
        print(token.to_json())
        print(f"\nGuarde no banco. Expira em {token.days_left:.0f} dias.")
        return 0

    if cmd == "refresh":
        if len(argv) < 3:
            raise SystemExit("Uso: python ig_auth.py refresh <token>")
        token = refresh(argv[2])
        print(token.to_json())
        return 0

    if cmd == "whoami":
        if len(argv) < 3:
            raise SystemExit("Uso: python ig_auth.py whoami <token>")
        print(json.dumps(whoami(argv[2]), indent=2))
        return 0

    print(f"Comando desconhecido: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
