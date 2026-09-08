"""
publicar.py — o ultimo elo: manifest.json -> post no ar.

    python publicar.py out/metro-bh/2026-09-07
    python publicar.py out/metro-bh/2026-09-07 --dry-run   # sobe a midia, nao posta

Faz, nesta ordem:
  1. sobe os JPEGs para o GitHub Pages e espera cada URL responder
  2. cria os containers (filhos + pai, no caso de carrossel)
  3. espera status FINISHED  -- o passo que faltava e gerava o erro 9007
  4. publica
  5. grava resultado.json ao lado do manifest, com media_id e permalink

Variaveis de ambiente:
    IG_USER_ID, IG_ACCESS_TOKEN     conta de destino
    GITHUB_TOKEN                    PAT com Contents:write no repo de midia
    GH_OWNER, GH_REPO               onde a midia e hospedada

Idempotencia: se ja existir resultado.json com media_id, nao republica. Um cron
que roda duas vezes nao gera dois posts.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ig_publisher import InstagramPublisher, PublishError, metrics_for
from storage_github import GitHubPages


def env(nome: str) -> str:
    v = os.environ.get(nome)
    if not v:
        raise SystemExit(f"falta a variavel de ambiente {nome}")
    return v


def publicar(pasta: Path, dry_run: bool = False) -> int:
    manifest = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    resultado_path = pasta / "resultado.json"

    if resultado_path.exists():
        anterior = json.loads(resultado_path.read_text(encoding="utf-8"))
        if anterior.get("media_id"):
            print(f"Ja publicado: {anterior['media_id']}")
            print(f"  {anterior.get('permalink', '')}")
            return 0

    arquivos = [pasta / n for n in manifest["arquivos"]]
    faltando = [f.name for f in arquivos if not f.exists()]
    if faltando:
        raise SystemExit(f"arquivos ausentes na pasta: {faltando}")

    # 1) midia publica -----------------------------------------------------
    storage = GitHubPages(owner=env("GH_OWNER"), repo=env("GH_REPO"))
    prefixo = f"midia/{manifest['marca']}/{manifest['data']}"
    print(f"Subindo {len(arquivos)} arquivo(s) para {prefixo}/ ...")
    urls = storage.enviar_lote(arquivos, prefixo)
    for u in urls:
        print(f"  {u}")

    print("Aguardando o Pages servir as URLs...")
    storage.esperar_publicacao(urls)
    print("  todas acessiveis.")

    if dry_run:
        print("\n[dry-run] midia no ar, publicacao nao executada.")
        return 0

    # 2-4) publicar --------------------------------------------------------
    pub = InstagramPublisher(ig_user_id=env("IG_USER_ID"),
                             access_token=env("IG_ACCESS_TOKEN"))
    legenda = manifest["legenda"]

    try:
        if manifest["formato"] == "reel":
            # O manifest nomeia video e capa; nao dependo da ordem da lista,
            # que ja mudou uma vez e levaria o publicador a mandar o JPEG
            # como video_url sem reclamar.
            por_nome = {f.name: u for f, u in zip(arquivos, urls)}
            video = por_nome[manifest["video"]]
            capa = por_nome.get(manifest.get("capa", ""))
            print(f"Publicando Reel de {manifest.get('duracao_s', '?')}s...")
            r = pub.publish_reel(video, caption=legenda, cover_url=capa)
        elif manifest["formato"] == "carousel":
            print(f"Publicando carrossel de {len(urls)} imagens...")
            r = pub.publish_carousel(urls, caption=legenda)
        else:
            print("Publicando imagem unica...")
            r = pub.publish_image(urls[0], caption=legenda)
    except PublishError as e:
        print(f"\nFALHOU: {e}")
        return 1

    print(f"\nPUBLICADO em {r.elapsed_s:.1f}s")
    print(f"  media_id : {r.media_id}")
    if r.permalink:
        print(f"  link     : {r.permalink}")

    # 5) registrar ---------------------------------------------------------
    try:
        metricas = pub.fetch_insights(r.media_id, metrics_for(r.format))
    except PublishError:
        metricas = {}

    resultado_path.write_text(json.dumps({
        "media_id": r.media_id,
        "container_id": r.container_id,
        "permalink": r.permalink,
        "formato": r.format,
        "urls": urls,
        "publicado_em": datetime.now(timezone.utc).isoformat(),
        "metricas_iniciais": metricas,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  registro : {resultado_path.name}")
    return 0


def ultima_pasta(marca: str) -> Path:
    """A pasta de saida mais recente da marca. Mesmo motivo do render.py:
    tirar `$(ls -td ...)` do YAML, onde o escape ja falhou uma vez."""
    raiz = Path(__file__).parent / "out" / marca
    candidatos = sorted((p for p in raiz.glob("*") if (p / "manifest.json").exists()),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidatos:
        raise SystemExit(f"nenhuma pasta renderizada em out/{marca}/ — rode render.py antes")
    return candidatos[0]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("uso: python publicar.py <pasta> | --ultima <marca>  [--dry-run]")

    if sys.argv[1] == "--ultima":
        if len(sys.argv) < 3:
            raise SystemExit("uso: python publicar.py --ultima <marca> [--dry-run]")
        pasta = ultima_pasta(sys.argv[2])
        print(f"pasta: {pasta}")
    else:
        pasta = Path(sys.argv[1])

    sys.exit(publicar(pasta, dry_run="--dry-run" in sys.argv))
