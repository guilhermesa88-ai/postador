"""
render.py — briefing em JSON -> JPEGs 1080x1350 prontos para a API do Instagram.

    python render.py briefs/exemplo-bh.json

Saida em out/<slug>/<data>/ : os slides numerados + manifest.json com a
legenda e a lista ordenada de arquivos, que e o que o publicador consome.

Por que HTML + Playwright e nao Pillow: layout de verdade (flexbox, quebra de
linha, kerning), template versionado em arquivo, e trocar a identidade visual
de um perfil vira editar CSS. Um slide leva ~200ms.

Por que JPEG: a API do Instagram so aceita JPEG para imagem. Renderizar PNG e
converter depois e um passo a mais para errar.

Escala: o canvas tem 1080px de largura mas e visto a ~400px no feed. A escala
visual efetiva e ~2.7x, entao as specs de marca fina (barra <=24px num
dashboard) viram ~44px aqui para dar o mesmo peso visual.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).parent
W, H = 1080, 1350
PAD = 76
JPEG_QUALITY = 90

# ---------------------------------------------------------------------------
# Grafico: barras horizontais divergentes, em SVG inline
# ---------------------------------------------------------------------------

BAR_H = 52          # ~19px na escala de exibicao: marca fina
BAR_GAP = 30
BAR_RADIUS = 10     # ponta arredondada; base quadrada
LABEL_COL = 300     # coluna de rotulos de bairro, alinhada a direita
LABEL_GAP = 34      # respiro entre o rotulo e o inicio da area de plotagem
VAL_GAP = 18        # respiro entre a ponta da barra e o valor
VAL_W = 130         # espaco reservado para o rotulo de valor de cada lado
ALTURA_GRAFICO = 700  # px do slide reservados para a area do grafico


def _bar_path(x0: float, y: float, largura: float, altura: float, r: float,
              para_direita: bool) -> str:
    """Barra com as duas quinas arredondadas apenas na ponta do dado."""
    r = min(r, abs(largura))
    if para_direita:
        x1 = x0 + largura
        return (f"M{x0},{y} H{x1 - r} Q{x1},{y} {x1},{y + r} "
                f"V{y + altura - r} Q{x1},{y + altura} {x1 - r},{y + altura} "
                f"H{x0} Z")
    x1 = x0 - largura
    return (f"M{x0},{y} H{x1 + r} Q{x1},{y} {x1},{y + r} "
            f"V{y + altura - r} Q{x1},{y + altura} {x1 + r},{y + altura} "
            f"H{x0} Z")


def _fmt(v: float, unidade: str) -> str:
    txt = f"{v:+.1f}".replace('.', ',')
    return f"{txt}{unidade}"


def grafico_svg(dados: list[dict], cores: dict, unidade: str = "%") -> str:
    """Barras divergentes. `dados` = [{"label": str, "valor": float}, ...]

    Forma escolhida porque o trabalho do dado e POLARIDADE (subiu/caiu), nao
    so magnitude. Paleta divergente validada: azul x vermelho com base neutra.
    Todo valor recebe rotulo direto -- numa imagem estatica nao existe hover,
    entao a regra de "rotular seletivamente" nao se aplica: sem rotulo o dado
    fica inacessivel.
    """
    n = len(dados)
    largura_util = W - 2 * PAD

    # A area de plotagem comeca depois da coluna de rotulos e vai ate a borda.
    plot_x0 = PAD + LABEL_COL + LABEL_GAP
    plot_x1 = W - PAD
    plot_w = plot_x1 - plot_x0

    valores = [d["valor"] for d in dados]
    v_neg = abs(min(valores)) if min(valores) < 0 else 0.0
    v_pos = max(valores) if max(valores) > 0 else 0.0
    total = (v_neg + v_pos) or 1.0

    # Escala das barras.
    #
    # A versao anterior repartia a largura proporcionalmente aos dois lados e so
    # depois descontava VAL_W de cada um. Quando o lado negativo era pequeno --
    # -0,25% contra +1,31% no lado positivo -- o espaco proporcional dele ficava
    # menor que o rotulo, o desconto ia a zero, e o `min()` levava a escala do
    # grafico inteiro junto: as barras sairam com 50px num canvas de 1080.
    #
    # Agora e o contrario: reserva-se o rotulo primeiro, uma vez por lado que
    # exista, e o que sobra e dividido pela amplitude total. A escala passa a
    # depender do dado, nao do lado mais apertado.
    reserva = VAL_W * ((1 if v_neg else 0) + (1 if v_pos else 0))
    util = max(plot_w - reserva, 50)
    escala = util / total
    zero_x = plot_x0 + (VAL_W if v_neg else 0) + v_neg * escala

    # O SVG e desenhado num viewBox de W de largura mas exibido com
    # `largura_util`, entao o conteudo encolhe nessa proporcao. A altura do
    # elemento precisa encolher junto, senao ele ocupa no layout mais do que
    # desenha e o rodape do slide sai cortado -- foi o que aconteceu com um
    # grafico de 10 barras.
    esc_svg = largura_util / W
    gap = BAR_GAP if n <= 8 else 22
    disponivel = ALTURA_GRAFICO / esc_svg
    bar_h = min(BAR_H, max((disponivel - 20 - (n - 1) * gap) / n, 24))

    altura = n * bar_h + (n - 1) * gap
    partes: list[str] = [
        f'<svg width="{largura_util}" height="{(altura + 20) * esc_svg:.0f}" '
        f'viewBox="0 0 {W} {altura + 20}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Poppins, DejaVu Sans, sans-serif">'
    ]

    # linha de base (zero) — hairline recessiva, nunca tracejada
    partes.append(
        f'<line x1="{zero_x:.1f}" y1="-6" x2="{zero_x:.1f}" y2="{altura + 6}" '
        f'stroke="{cores["baseline"]}" stroke-width="2"/>'
    )

    for i, d in enumerate(dados):
        y = i * (bar_h + gap)
        v = d["valor"]
        comprimento = max(abs(v) * escala, 6)
        cor = cores["positivo"] if v >= 0 else cores["negativo"]
        direita = v >= 0

        partes.append(
            f'<path d="{_bar_path(zero_x, y, comprimento, bar_h, BAR_RADIUS, direita)}" '
            f'fill="{cor}"/>'
        )

        # rotulo do bairro: ink secundario, nunca a cor da serie
        partes.append(
            f'<text x="{PAD + LABEL_COL}" y="{y + bar_h * 0.72}" text-anchor="end" '
            f'font-size="32" font-weight="400" fill="{cores["ink_secundario"]}">'
            f'{d["label"]}</text>'
        )

        # valor na ponta, tambem em ink
        vx = zero_x + comprimento + VAL_GAP if direita else zero_x - comprimento - VAL_GAP
        anchor = "start" if direita else "end"
        partes.append(
            f'<text x="{vx:.1f}" y="{y + bar_h * 0.72}" text-anchor="{anchor}" '
            f'font-size="32" font-weight="600" fill="{cores["ink"]}">'
            f'{_fmt(v, unidade)}</text>'
        )

    partes.append("</svg>")
    return "".join(partes)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(brief_path: Path) -> Path:
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    marca = json.loads((RAIZ / "brands" / f"{brief['marca']}.json").read_text(encoding="utf-8"))
    cores = marca["cores"]

    env = Environment(
        loader=FileSystemLoader(RAIZ / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    tpl = env.get_template("slide.html.j2")

    hoje = brief.get("data") or date.today().isoformat()
    # A pasta carrega o angulo: o mesmo informe rende varios posts, e sem isso
    # o segundo do dia sobrescreveria o manifest do primeiro.
    destino = RAIZ / "out" / marca["slug"] / f"{hoje}-{brief.get('angulo', 'padrao')}"
    destino.mkdir(parents=True, exist_ok=True)

    slides = brief["slides"]
    arquivos: list[str] = []

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(viewport={"width": W, "height": H},
                                    device_scale_factor=1)
        for i, s in enumerate(slides):
            svg = grafico_svg(s["dados"], cores, s.get("unidade", "%")) \
                if s["tipo"] == "grafico" else ""

            html = tpl.render(
                s=s, marca=marca, c=cores, t=marca["tipografia"],
                W=W, H=H, PAD=PAD, idx=i, total=len(slides),
                chart_svg=svg, selo_ia=brief.get("selo_ia"),
            )
            pagina.set_content(html, wait_until="networkidle")
            caminho = destino / f"{i+1:02d}.jpg"
            pagina.screenshot(path=str(caminho), type="jpeg", quality=JPEG_QUALITY)
            arquivos.append(caminho.name)
            print(f"  slide {i+1}/{len(slides)}  {s['tipo']:12s} -> {caminho.name}")

        navegador.close()

    manifest = {
        "marca": marca["slug"],
        "data": hoje,
        "formato": "carousel" if len(slides) > 1 else "image",
        "legenda": brief["legenda"],
        "arquivos": arquivos,
        "periodo": brief.get("periodo"),
        "hash_pdf": brief.get("hash_pdf"),
        "angulo": brief.get("angulo", "padrao"),
        "gerado_em": date.today().isoformat(),
    }
    (destino / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return destino


def ultimo_brief(marca: str) -> Path:
    """O brief mais recente da marca.

    Existe para o workflow nao precisar de `$(ls -t ... | head -1)`: aspas e
    cifrao dentro de YAML dentro de shell ja quebraram este pipeline uma vez.
    Aqui a mesma logica e Python, testavel e sem camada de escape.
    """
    candidatos = sorted((RAIZ / "briefs").glob(f"{marca}-*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidatos:
        raise SystemExit(f"nenhum brief de '{marca}' em briefs/ — rode compose.py antes")
    return candidatos[0]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("uso: python render.py <brief.json> | --ultimo <marca>")

    if sys.argv[1] == "--ultimo":
        if len(sys.argv) < 3:
            raise SystemExit("uso: python render.py --ultimo <marca>")
        brief = ultimo_brief(sys.argv[2])
        print(f"brief: {brief.relative_to(RAIZ)}")
    else:
        brief = Path(sys.argv[1])

    saida = render(brief)
    print(f"\nPronto: {saida}")
