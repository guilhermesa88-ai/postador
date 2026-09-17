"""Gera manifesto SEM aprovacao. Nao publica."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
import stories

p = argparse.ArgumentParser()
p.add_argument("id")
p.add_argument("jpeg", type=Path, nargs="+", help="JPEGs na ordem de publicacao (1 a 10)")
p.add_argument("--after", required=True, help="ISO com fuso, ex. 2026-09-18T09:00:00-03:00")
p.add_argument("--before", required=True, help="Prazo final de publicacao, ISO com fuso")
a = p.parse_args()
if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", a.id):
    p.error("ID invalido")
if stories.date(a.before) <= stories.date(a.after):
    p.error("Prazo final precisa ser posterior ao inicio")
if not 1 <= len(a.jpeg) <= 10:
    p.error("Use de 1 a 10 imagens por sequencia")
images = []
for source in a.jpeg:
    data = source.read_bytes()
    stories.validate_image(data)
    images.append((source, hashlib.sha256(data).hexdigest()))
if len({sha for _, sha in images}) != len(images):
    p.error("Nao repetir imagens na mesma sequencia")
folder = stories.QUEUE / a.id
folder.mkdir(parents=True, exist_ok=False)
frames = []
for index, (source, sha) in enumerate(images, 1):
    name = f"{index:02d}.jpg"
    shutil.copyfile(source, folder / name)
    frames.append({"file": name, "sha256": sha})
sequence_hash = hashlib.sha256("\n".join(f["sha256"] for f in frames).encode()).hexdigest()
manifest = {"id": a.id, "approved": False, "approved_by": "",
            "approved_at": "", "publish_after": a.after,
            "publish_before": a.before, "sha256": sequence_hash, "images": frames}
(folder / "story.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Preparado: {folder}, {len(frames)} imagens. Revisar e aprovar antes de publicar.")
