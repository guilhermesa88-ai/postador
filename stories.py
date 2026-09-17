"""Publisher de Stories EstiloVidya. Sem retries automáticos de POST."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from PIL import Image

UTC = timezone.utc
BR = timezone(timedelta(hours=-3))
CONFIG = Path("config/stories-estilovidya.json")
STATE = Path("estado/stories-estilovidya.json")
QUEUE = Path("entrada-stories/estilovidya")
REPO = "guilhermesa88-ai/postador"


class Blocked(RuntimeError):
    pass


def stamp():
    return datetime.now(UTC).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def date(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise Blocked("Horario precisa incluir fuso.")
    return result


def validate_image(data):
    if not data or len(data) > 8 * 1024 * 1024:
        raise Blocked("JPEG vazio ou acima de 8 MB.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "JPEG" or image.size != (1080, 1920) or image.mode != "RGB":
                raise Blocked("Use JPEG RGB 1080x1920.")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Blocked:
        raise
    except Exception:
        raise Blocked("JPEG invalido.") from None


def load_queue(root):
    result = []
    for manifest in sorted((root / QUEUE).glob("*/story.json")):
        item = read_json(manifest)
        sid = item.get("id", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", sid) or manifest.parent.name != sid:
            raise Blocked("ID invalido ou diferente da pasta.")
        if item.get("approved") is not True:
            continue
        if not item.get("approved_by") or not item.get("approved_at"):
            raise Blocked(f"{sid}: aprovacao sem autoria/data.")
        date(item["approved_at"])
        start, end = date(item["publish_after"]), date(item["publish_before"])
        if end <= start:
            raise Blocked(f"{sid}: janela de publicacao invalida.")
        media = manifest.parent / "arte.jpg"
        if media.is_symlink() or manifest.is_symlink() or manifest.parent.is_symlink():
            raise Blocked("Links simbolicos nao sao aceitos na fila.")
        data = media.read_bytes()
        validate_image(data)
        sha = hashlib.sha256(data).hexdigest()
        if sha != item.get("sha256"):
            raise Blocked(f"{sid}: imagem mudou depois da aprovacao.")
        result.append({**item, "path": media.relative_to(root).as_posix()})
    return sorted(result, key=lambda x: (date(x["publish_after"]), x["id"]))


def select(items, state, now, daily_limit):
    entries = state["items"]
    if any(v["status"] != "published" for v in entries.values()):
        raise Blocked("Tentativa pendente/incerta: conciliar recibo antes de continuar.")
    today = now.astimezone(BR).date()
    count = sum(date(v["published_at"]).astimezone(BR).date() == today
                for v in entries.values())
    if count >= daily_limit:
        return None
    hashes = {v["sha256"] for v in entries.values()}
    for item in items:
        if item["id"] in entries or item["sha256"] in hashes:
            continue
        if date(item["publish_after"]) <= now < date(item["publish_before"]):
            return item
    return None


class Meta:
    def __init__(self, account_id, token, version="v23.0", opener=urlopen):
        if not account_id.isdigit() or not token:
            raise Blocked("Secrets EstiloVidya ausentes ou ID invalido.")
        if not re.fullmatch(r"v\d+\.\d+", version):
            raise Blocked("Versao API invalida.")
        self.account_id, self.token, self.opener = account_id, token, opener
        self.base = f"https://graph.instagram.com/{version}"

    def request(self, method, path, **params):
        # Token nunca aparece na URL, no recibo ou na mensagem de erro.
        url = f"{self.base}/{path}"
        data = None
        if method == "GET":
            url += "?" + urlencode(params)
        else:
            data = urlencode(params).encode()
        request = Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener(request, timeout=45) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise Blocked(f"Meta HTTP {exc.code}; sem repeticao automatica.") from None
        except (URLError, OSError, ValueError):
            raise Blocked("Resposta Meta ausente/invalida; sem repeticao automatica.") from None
        if not isinstance(result, dict) or "error" in result:
            raise Blocked("Meta retornou erro; verificar acesso sem repetir POST.")
        return result

    def check(self):
        info = self.request("GET", self.account_id, fields="id,username,account_type")
        if str(info.get("username", "")).lower() != "estilovidya":
            raise Blocked("Conta diferente de @estilovidya.")
        if info.get("account_type") != "BUSINESS":
            raise Blocked("Stories exigem conta Business; confirmar tipo e acesso.")
        quota = self.request("GET", f"{self.account_id}/content_publishing_limit",
                             fields="quota_usage,config")
        try:
            row = quota["data"][0]
            usage = row["quota_usage"]
            limit = row["config"]["quota_total"]
            if type(usage) is not int or type(limit) is not int or not 0 <= usage < limit - 5:
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise Blocked("Cota indisponivel ou sem margem para publicar.") from None

    def create(self, image_url):
        data = self.request("POST", f"{self.account_id}/media",
                            media_type="STORIES", image_url=image_url)
        return self.require_id(data)

    @staticmethod
    def require_id(data):
        value = str(data.get("id", ""))
        if not value.isdigit():
            raise Blocked("Resposta sem ID; nao repetir a tentativa.")
        return value

    def ready(self, container_id):
        for attempt in range(6):
            result = self.request("GET", container_id, fields="status_code")
            status = result.get("status_code")
            if status == "FINISHED":
                return
            if status != "IN_PROGRESS":
                raise Blocked("Container nao esta pronto; conciliar antes de repetir.")
            if attempt < 5:
                time.sleep(60)
        raise Blocked("Processamento excedeu 5 minutos; conciliacao necessaria.")

    def publish(self, container_id):
        return self.require_id(self.request(
            "POST", f"{self.account_id}/media_publish", creation_id=container_id))


class Journal:
    """Persistencia remota antes de cada mutacao. Somente arquivo STATE."""
    def __init__(self, root):
        self.root = root

    def git(self, *args):
        proc = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        if proc.returncode:
            # Nao expor output: git pode incluir URL autenticada.
            raise Blocked("Falha ao sincronizar recibo GitHub; publicacao interrompida.")
        return proc.stdout.strip()

    def check(self):
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise Blocked("Publicacao real somente pelo workflow GitHub.")
        if os.environ.get("GITHUB_REPOSITORY") != REPO:
            raise Blocked("Repositorio incorreto.")
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise Blocked("Publicacao real somente em main.")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise Blocked("Checkout alterado; iniciar execucao limpa.")

    def save(self, state):
        path = self.root / STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(path)
        self.git("add", "--", STATE.as_posix())
        self.git("commit", "-m", "chore(stories): registrar etapa de publicacao",
                 "--", STATE.as_posix())
        # Outro workflow pode ter atualizado arquivos distintos.
        for _ in range(3):
            self.git("pull", "--rebase", "origin", "main")
            try:
                self.git("push", "origin", "HEAD:main")
                return
            except Blocked:
                continue
        raise Blocked("Recibo nao confirmado no GitHub; nao repetir publicacao.")


def media_url(root, item, revision):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise Blocked("Commit de origem invalido.")
    return f"https://raw.githubusercontent.com/{REPO}/{revision}/{quote(item['path'])}"


def check_remote(url, sha):
    try:
        with urlopen(url, timeout=30) as response:
            data = response.read(8 * 1024 * 1024 + 1)
    except (OSError, URLError):
        raise Blocked("Arte ainda nao esta acessivel publicamente.") from None
    if hashlib.sha256(data).hexdigest() != sha:
        raise Blocked("Arte remota difere da aprovada.")
    validate_image(data)


def publish_one(item, state, api, save, url, now):
    # Antes da primeira mutacao ja existe uma reserva persistida remotamente.
    receipt = {
        "status": "creating", "sha256": item["sha256"],
        "started_at": now.isoformat(), "image_url": url,
        "approved_by": item["approved_by"], "approved_at": item["approved_at"],
    }
    state["items"][item["id"]] = receipt
    save(state)
    receipt["container_id"] = api.create(url)
    receipt["status"] = "processing"
    save(state)
    api.ready(receipt["container_id"])
    receipt["status"] = "publishing"
    save(state)
    receipt["media_id"] = api.publish(receipt["container_id"])
    receipt["status"] = "published"
    receipt["published_at"] = stamp()
    save(state)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    config = read_json(root / CONFIG)
    if config.get("expected_username") != "estilovidya":
        raise Blocked("Configuracao nao pertence a EstiloVidya.")
    limit = config.get("daily_limit")
    if type(limit) is not int or not 1 <= limit <= 3:
        raise Blocked("Limite diario deve estar entre 1 e 3.")
    state = read_json(root / STATE)
    if state.get("schema") != 1 or not isinstance(state.get("items"), dict):
        raise Blocked("Estado invalido.")
    items = load_queue(root)
    now = datetime.now(UTC)
    item = select(items, state, now, limit)
    if not args.publish:
        print(json.dumps({"mode": "dry-run", "approved": len(items),
                          "next_id": item["id"] if item else None,
                          "enabled": config.get("enabled") is True}))
        return
    if config.get("enabled") is not True:
        print("Stories desativados na configuracao; nenhuma publicacao.")
        return
    if not item:
        print("Sem Story elegivel ou limite diario atingido.")
        return
    journal = Journal(root)
    journal.check()
    revision = journal.git("rev-parse", "HEAD")
    url = media_url(root, item, revision)
    check_remote(url, item["sha256"])
    api = Meta(os.environ.get("IG_USER_ID_ESTILOVIDYA", ""),
               os.environ.get("IG_ACCESS_TOKEN_ESTILOVIDYA", ""), config["api_version"])
    api.check()
    receipt = publish_one(item, state, api, journal.save, url, now)
    print(json.dumps({"published": item["id"], "media_id": receipt["media_id"]}))


if __name__ == "__main__":
    try:
        main()
    except Blocked as exc:
        print(f"BLOQUEADO: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("BLOQUEADO: falha inesperada; conferir recibo antes de repetir.", file=sys.stderr)
        sys.exit(1)
