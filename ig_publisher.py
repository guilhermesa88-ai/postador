"""
ig_publisher.py — publicação no Instagram via Graph API (Content Publishing).

Implementação de referência do módulo mais arriscado do pipeline: é onde
o dinheiro e a conta estão em jogo. Cobre imagem única, carrossel e Reels,
com polling de processamento, checagem de rate limit, retry com backoff
e erros tipados.

Dependência única: requests.

    pip install requests

Uso:

    pub = InstagramPublisher(ig_user_id="1784140...", access_token=TOKEN)
    media_id = pub.publish_carousel(
        image_urls=[url1, url2, url3],
        caption="...",
    )

Notas de contrato da API (verificadas em set/2026):
  - Só contas *Business* publicam Reels de forma confiável. Creator dá
    problema. Use Business.
  - Toda mídia precisa estar em URL pública no momento da criação do
    container. A Meta faz o download; o seu storage precisa aguentar.
  - Imagem: JPEG apenas.
  - Container expira em 24h. Não crie container adiantado.
  - Limite: 100 posts publicados por API por conta / 24h (janela móvel).
    Carrossel conta como 1.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import requests

log = logging.getLogger(__name__)

API_VERSION = "v23.0"
BASE_URL = f"https://graph.instagram.com/{API_VERSION}"

# A Meta recomenda 1 poll/min por até 5 min. Encurtamos o intervalo inicial
# porque a maioria dos vídeos curtos fica pronta em 30-60s, e mantemos o
# teto de 5 min.
POLL_INTERVAL_S = 15
POLL_TIMEOUT_S = 300

# Margem de segurança: para de publicar antes de encostar no teto, para
# deixar folga para republicação manual.
PUBLISH_LIMIT = 100
PUBLISH_LIMIT_HEADROOM = 5

MediaFormat = Literal["image", "carousel", "reel"]


class PublishError(RuntimeError):
    """Falha de publicação. Base de todos os erros do módulo."""


class RateLimitReached(PublishError):
    """Cota de 24h da conta esgotada. Não é retryable no curto prazo."""


class MediaProcessingError(PublishError):
    """A Meta rejeitou o processamento da mídia (codec, tamanho, formato)."""


class ContainerExpired(PublishError):
    """Container passou das 24h antes de ser publicado."""


class TransientAPIError(PublishError):
    """Erro de rede ou 5xx da Meta. Retryable."""


@dataclass
class PublishResult:
    media_id: str
    container_id: str
    format: MediaFormat
    attempts: int
    elapsed_s: float
    permalink: str | None = None


@dataclass
class InstagramPublisher:
    ig_user_id: str
    access_token: str
    session: requests.Session = field(default_factory=requests.Session)
    timeout_s: int = 30
    max_retries: int = 3

    # ------------------------------------------------------------------
    # HTTP com retry
    # ------------------------------------------------------------------

    def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{BASE_URL}/{path.lstrip('/')}"
        payload = {**(params or {}), "access_token": self.access_token}

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if method == "GET":
                    resp = self.session.get(url, params=payload, timeout=self.timeout_s)
                else:
                    resp = self.session.post(url, data=payload, timeout=self.timeout_s)
            except requests.RequestException as exc:
                last_exc = exc
                self._backoff(attempt, reason=str(exc))
                continue

            if resp.status_code >= 500:
                last_exc = TransientAPIError(f"{resp.status_code}: {resp.text[:400]}")
                self._backoff(attempt, reason=f"HTTP {resp.status_code}")
                continue

            try:
                body = resp.json()
            except ValueError:
                raise PublishError(
                    f"Resposta não-JSON da Meta ({resp.status_code}): {resp.text[:400]}"
                ) from None

            if resp.status_code >= 400 or "error" in body:
                self._raise_for_api_error(body, resp.status_code)

            return body

        raise TransientAPIError(
            f"Falhou após {self.max_retries} tentativas: {last_exc}"
        ) from last_exc

    @staticmethod
    def _backoff(attempt: int, reason: str) -> None:
        delay = 2**attempt
        log.warning("Retry %s em %ss (%s)", attempt, delay, reason)
        time.sleep(delay)

    @staticmethod
    def _raise_for_api_error(body: dict[str, Any], status: int) -> None:
        err = body.get("error", {})
        code = err.get("code")
        subcode = err.get("error_subcode")
        msg = err.get("message", str(body)[:400])

        # 4 = rate limit da app; 9 = rate limit de publicação da conta.
        if code in (4, 9, 17, 32, 613):
            raise RateLimitReached(f"[{code}/{subcode}] {msg}")
        if code == 190:
            raise PublishError(f"Token inválido ou expirado [{code}]: {msg}")
        if status >= 500:
            raise TransientAPIError(f"[{code}] {msg}")
        raise PublishError(f"[{code}/{subcode}] {msg}")

    # ------------------------------------------------------------------
    # Guardas
    # ------------------------------------------------------------------

    def quota_used(self) -> int:
        """Posts já publicados na janela móvel de 24h."""
        body = self._request(
            "GET",
            f"{self.ig_user_id}/content_publishing_limit",
            {"fields": "quota_usage"},
        )
        data = body.get("data") or [{}]
        return int(data[0].get("quota_usage", 0))

    def assert_quota_available(self) -> None:
        used = self.quota_used()
        if used >= PUBLISH_LIMIT - PUBLISH_LIMIT_HEADROOM:
            raise RateLimitReached(
                f"Cota em {used}/{PUBLISH_LIMIT} (margem de {PUBLISH_LIMIT_HEADROOM})."
            )

    @staticmethod
    def assert_media_reachable(url: str) -> None:
        """A Meta baixa a mídia por conta dela. Se a URL não responder,
        o erro volta como 'processamento falhou', que é muito mais caro
        de diagnosticar do que um HEAD aqui."""
        try:
            resp = requests.head(url, timeout=15, allow_redirects=True)
        except requests.RequestException as exc:
            raise PublishError(f"Mídia inacessível em {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise PublishError(f"Mídia retornou HTTP {resp.status_code}: {url}")

    # ------------------------------------------------------------------
    # Containers
    # ------------------------------------------------------------------

    def _create_container(self, params: dict[str, Any]) -> str:
        body = self._request("POST", f"{self.ig_user_id}/media", params)
        container_id = body.get("id")
        if not container_id:
            raise PublishError(f"Container sem id na resposta: {body}")
        return str(container_id)

    def _wait_until_ready(self, container_id: str) -> None:
        """Espera o container ficar FINISHED.

        Vale para TODOS os formatos, imagem inclusive. A premissa de que só
        vídeo precisa esperar é falsa e custa caro: a Meta baixa a mídia da
        URL por conta dela, e até terminar o `media_publish` devolve

            code 9007 / subcode 2207027 — "Media ID is not available"

        que parece erro de permissão e não é: é só pressa.
        """
        deadline = time.monotonic() + POLL_TIMEOUT_S
        while True:
            body = self._request(
                "GET", container_id, {"fields": "status_code,status"}
            )
            status = body.get("status_code")

            if status == "FINISHED":
                return
            if status == "ERROR":
                raise MediaProcessingError(
                    f"Container {container_id}: {body.get('status', 'sem detalhe')}"
                )
            if status == "EXPIRED":
                raise ContainerExpired(f"Container {container_id} expirou.")
            if status == "PUBLISHED":
                return  # já publicado; idempotência

            if time.monotonic() > deadline:
                raise MediaProcessingError(
                    f"Container {container_id} ainda em {status} após "
                    f"{POLL_TIMEOUT_S}s."
                )
            time.sleep(POLL_INTERVAL_S)

    def _publish_container(self, container_id: str) -> str:
        body = self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            {"creation_id": container_id},
        )
        media_id = body.get("id")
        if not media_id:
            raise PublishError(f"media_publish sem id: {body}")
        return str(media_id)

    def _permalink(self, media_id: str) -> str | None:
        try:
            body = self._request("GET", media_id, {"fields": "permalink"})
            return body.get("permalink")
        except PublishError:
            return None  # cosmético; nunca deve derrubar a publicação

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def publish_image(
        self, image_url: str, caption: str = "", alt_text: str | None = None
    ) -> PublishResult:
        started = time.monotonic()
        self.assert_quota_available()
        self.assert_media_reachable(image_url)

        params: dict[str, Any] = {"image_url": image_url, "caption": caption}
        if alt_text:
            params["alt_text"] = alt_text

        container_id = self._create_container(params)
        self._wait_until_ready(container_id)
        media_id = self._publish_container(container_id)
        return PublishResult(
            media_id=media_id,
            container_id=container_id,
            format="image",
            attempts=1,
            elapsed_s=time.monotonic() - started,
            permalink=self._permalink(media_id),
        )

    def publish_carousel(
        self, image_urls: Iterable[str], caption: str = ""
    ) -> PublishResult:
        started = time.monotonic()
        urls = list(image_urls)
        if not 2 <= len(urls) <= 10:
            raise ValueError(f"Carrossel aceita de 2 a 10 itens, recebeu {len(urls)}.")

        self.assert_quota_available()
        for url in urls:
            self.assert_media_reachable(url)

        children = [
            self._create_container({"image_url": url, "is_carousel_item": "true"})
            for url in urls
        ]

        for child in children:
            self._wait_until_ready(child)

        container_id = self._create_container(
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": caption,
            }
        )
        self._wait_until_ready(container_id)
        media_id = self._publish_container(container_id)
        return PublishResult(
            media_id=media_id,
            container_id=container_id,
            format="carousel",
            attempts=1,
            elapsed_s=time.monotonic() - started,
            permalink=self._permalink(media_id),
        )

    def publish_reel(
        self,
        video_url: str,
        caption: str = "",
        cover_url: str | None = None,
        share_to_feed: bool = True,
    ) -> PublishResult:
        started = time.monotonic()
        self.assert_quota_available()
        self.assert_media_reachable(video_url)
        if cover_url:
            self.assert_media_reachable(cover_url)

        params: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if cover_url:
            params["cover_url"] = cover_url

        container_id = self._create_container(params)
        self._wait_until_ready(container_id)
        media_id = self._publish_container(container_id)
        return PublishResult(
            media_id=media_id,
            container_id=container_id,
            format="reel",
            attempts=1,
            elapsed_s=time.monotonic() - started,
            permalink=self._permalink(media_id),
        )

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    def fetch_insights(self, media_id: str, metrics: list[str]) -> dict[str, int]:
        """Nomes de métrica mudam de versão para versão — a Meta aposentou
        `impressions` e `plays` em abr/2025 em favor de `views`. Confirme a
        lista contra a versão da API que você fixou antes de confiar."""
        body = self._request(
            "GET", f"{media_id}/insights", {"metric": ",".join(metrics)}
        )
        out: dict[str, int] = {}
        for item in body.get("data", []):
            values = item.get("values") or [{}]
            out[item["name"]] = values[0].get("value", 0)
        return out


METRICS_FEED = ["views", "reach", "likes", "comments", "saved", "shares",
                "total_interactions"]
METRICS_REEL = METRICS_FEED + ["ig_reels_avg_watch_time"]


def metrics_for(fmt: MediaFormat) -> list[str]:
    return METRICS_REEL if fmt == "reel" else METRICS_FEED
