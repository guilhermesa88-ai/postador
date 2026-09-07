"""
perfil.py — foto de perfil e bio de um perfil da rede.

    python perfil.py metro-bh

Gera em out/<marca>/perfil/:
    perfil.jpg    1080x1080, para subir no Instagram
    conferencia.png  a mesma marca nos tamanhos REAIS de exibicao

A conferencia e a parte que importa. A foto de perfil aparece a ~110px no
perfil e a ~32px ao lado de cada comentario e no anel de stories. Quase toda
foto de perfil ruim foi aprovada olhando o arquivo grande. Se a marca nao
sobrevive a 32px, ela nao existe -- e so olhando nesse tamanho para saber.

Dai as regras do desenho: nenhuma linha fina, nenhum texto pequeno, contraste
alto, forma unica reconhecivel pela silhueta.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).parent
LADO = 1080


def marca_svg(c: dict, altura: int = 690) -> str:
    """Barras de alturas diferentes: le como grafico e como skyline ao mesmo
    tempo. Formas cheias, sem detalhe fino -- e por isso que sobrevive a 32px.
    """
    larg = 132
    vao = 44
    alturas = [0.42, 0.72, 1.0, 0.58]     # o pico fora do centro evita simetria boba
    cores = [c["ink_secundario"], c["acento"], c["acento"], c["ink_secundario"]]
    total = len(alturas) * larg + (len(alturas) - 1) * vao

    partes = [f'<svg width="{total}" height="{altura}" viewBox="0 0 {total} {altura}" '
              f'xmlns="http://www.w3.org/2000/svg">']
    for i, (h, cor) in enumerate(zip(alturas, cores)):
        x = i * (larg + vao)
        alt = altura * h
        y = altura - alt
        partes.append(
            f'<rect x="{x}" y="{y:.0f}" width="{larg}" height="{alt:.0f}" '
            f'rx="20" fill="{cor}"/>'
        )
    partes.append("</svg>")
    return "".join(partes)


def html_perfil(marca: dict) -> str:
    c = marca["cores"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      *{{margin:0;padding:0;box-sizing:border-box}}
      body{{width:{LADO}px;height:{LADO}px;background:{c['surface']};
           display:flex;align-items:center;justify-content:center}}
    </style></head><body>{marca_svg(c)}</body></html>"""


def html_conferencia(marca: dict, data_uri: str) -> str:
    """A mesma imagem nos tamanhos reais, sobre fundo claro e escuro."""
    c = marca["cores"]
    tamanhos = [(150, "150px  foto ampliada"),
                (110, "110px  perfil"),
                (56, "56px  sugestao"),
                (32, "32px  comentario / anel de stories")]
    def bloco(fundo: str, cor_txt: str) -> str:
        itens = "".join(
            f'<div style="text-align:center">'
            f'<img src="{data_uri}" style="width:{px}px;height:{px}px;'
            f'border-radius:50%;display:block;margin:0 auto 14px">'
            f'<div style="font-size:13px;color:{cor_txt};opacity:.75">{rot}</div>'
            f'</div>' for px, rot in tamanhos)
        return (f'<div style="background:{fundo};padding:44px 40px;display:flex;'
                f'gap:44px;align-items:flex-end;justify-content:center">{itens}</div>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      *{{margin:0;padding:0;box-sizing:border-box}}
      body{{width:760px;font-family:{marca['tipografia']['familia']}}}
      h3{{font-size:14px;font-weight:600;padding:16px 40px 0;color:#666}}
    </style></head><body>
      <h3>como aparece de verdade</h3>
      {bloco('#ffffff', '#111')}
      {bloco(c['surface'], c['ink'])}
    </body></html>"""


def gerar(slug: str) -> Path:
    marca = json.loads((RAIZ / "brands" / f"{slug}.json").read_text(encoding="utf-8"))
    destino = RAIZ / "out" / slug / "perfil"
    destino.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        nav = p.chromium.launch()

        pg = nav.new_page(viewport={"width": LADO, "height": LADO}, device_scale_factor=1)
        pg.set_content(html_perfil(marca), wait_until="networkidle")
        foto = destino / "perfil.jpg"
        pg.screenshot(path=str(foto), type="jpeg", quality=92)

        import base64
        uri = "data:image/jpeg;base64," + base64.b64encode(foto.read_bytes()).decode()
        pg2 = nav.new_page(viewport={"width": 760, "height": 520}, device_scale_factor=2)
        pg2.set_content(html_conferencia(marca, uri), wait_until="networkidle")
        pg2.screenshot(path=str(destino / "conferencia.png"), full_page=True)

        nav.close()

    bio = marca.get("bio", "")
    (destino / "bio.txt").write_text(bio, encoding="utf-8")
    print(f"  perfil.jpg       {LADO}x{LADO}")
    print(f"  conferencia.png  tamanhos reais de exibicao")
    print(f"  bio.txt          {len(bio)} de 150 caracteres")
    return destino


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("uso: python perfil.py <marca>")
    print(f"\nGerando identidade de {sys.argv[1]}")
    print(f"\nPronto: {gerar(sys.argv[1])}\n")
