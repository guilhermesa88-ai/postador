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
p.add_argument("jpeg", type=Path)
p.add_argument("--after", required=True, help="ISO com fuso, ex. 2026-09-18T09:00:00-03:00")
p.add_argument("--before", required=True, help="Prazo final de publicacao, ISO com fuso")
a = p.parse_args()
if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", a.id):
    p.error("ID invalido")
if stories.date(a.before) <= stories.date(a.after):
    p.error("Prazo final precisa ser posterior ao inicio")
data = a.jpeg.read_bytes()
stories.validate_image(data)
folder = stories.QUEUE / a.id
folder.mkdir(parents=True, exist_ok=False)
shutil.copyfile(a.jpeg, folder / "arte.jpg")
manifest = {"id": a.id, "approved": False, "approved_by": "",
            "approved_at": "", "publish_after": a.after,
            "publish_before": a.before, "sha256": hashlib.sha256(data).hexdigest()}
(folder / "story.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Preparado: {folder}. Revisar arte e registrar aprovacao antes de publicar.")
