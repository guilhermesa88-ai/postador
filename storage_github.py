"""
storage_github.py — publica as midias num repositorio do GitHub Pages.

A Meta baixa a midia por conta dela, entao a imagem precisa estar numa URL
publica ANTES de criar o container. O repositorio que ja serve a politica de
privacidade serve tambem para isso: e gratuito, HTTPS, sem redirect e sem
header exigido -- exatamente o que a API pede.

Usa a Contents API do GitHub, entao nao precisa de git instalado.

Token: um fine-grained PAT com permissao de Contents: read and write apenas
neste repositorio. Vem de GITHUB_TOKEN no ambiente -- nunca hardcoded.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import requests

API = "https://api.github.com"


class StorageError(RuntimeError):
    pass


class GitHubPages:
    def __init__(self, owner: str, repo: str, branch: str = "main",
                 token: str | None = None, pages_url: str | None = None):
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise StorageError("falta GITHUB_TOKEN no ambiente")
        self.pages_url = (pages_url or f"https://{owner}.github.io/{repo}").rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    # -- interno ----------------------------------------------------------

    def _sha_existente(self, caminho: str) -> str | None:
        r = self.s.get(f"{API}/repos/{self.owner}/{self.repo}/contents/{caminho}",
                       params={"ref": self.branch}, timeout=30)
        return r.json().get("sha") if r.status_code == 200 else None

    # -- publico ----------------------------------------------------------

    def enviar(self, local: Path, caminho_remoto: str) -> str:
        """Sobe um arquivo e devolve a URL publica dele."""
        conteudo = base64.b64encode(local.read_bytes()).decode()
        corpo = {
            "message": f"midia: {caminho_remoto}",
            "content": conteudo,
            "branch": self.branch,
        }
        sha = self._sha_existente(caminho_remoto)
        if sha:
            corpo["sha"] = sha   # sobrescrever exige o sha atual

        r = self.s.put(f"{API}/repos/{self.owner}/{self.repo}/contents/{caminho_remoto}",
                       json=corpo, timeout=60)
        if r.status_code not in (200, 201):
            raise StorageError(f"upload de {caminho_remoto} falhou "
                               f"[{r.status_code}]: {r.text[:300]}")
        return f"{self.pages_url}/{caminho_remoto}"

    def enviar_lote(self, arquivos: list[Path], prefixo: str) -> list[str]:
        urls = []
        for f in arquivos:
            urls.append(self.enviar(f, f"{prefixo}/{f.name}"))
            time.sleep(0.4)   # respeita o rate limit da Contents API
        return urls

    def esperar_publicacao(self, urls: list[str], timeout_s: int = 180) -> None:
        """O Pages leva de segundos a ~1min para servir o arquivo novo.

        Publicar antes disso faz a Meta receber 404 e devolver o erro como
        'falha de processamento', que aponta para o lugar errado. Melhor
        esperar aqui do que diagnosticar la.
        """
        limite = time.time() + timeout_s
        pendentes = list(urls)
        while pendentes and time.time() < limite:
            ainda = []
            for u in pendentes:
                try:
                    if requests.head(u, timeout=15, allow_redirects=True).status_code < 400:
                        continue
                except requests.RequestException:
                    pass
                ainda.append(u)
            pendentes = ainda
            if pendentes:
                time.sleep(5)
        if pendentes:
            raise StorageError(
                "estas URLs nao ficaram publicas a tempo:\n  " + "\n  ".join(pendentes))
