"""
reel.py — brief JSON -> MP4 vertical 1080x1920 pronto para publicar como Reel.

    python reel.py briefs/metro-de-bh-2026-09-07.json
    python reel.py --ultimo metro-de-bh

Saida em out/<slug>/<data>/reel.mp4 + capa.jpg, e um manifest.json com
formato "reel", que e o que o publicador consome.

POR QUE UM REEL E NAO SO O CARROSSEL

Carrossel entrega bem para quem ja tem audiencia; Reel e o que a plataforma
usa para mostrar a conta a quem ainda nao segue. Num perfil novo o carrossel
alcanca quase so quem ja esta la -- isto e, quase ninguem.

POR QUE GRAFICO ANIMADO E NAO SLIDESHOW

Um slideshow de imagens paradas e um Reel ruim: nao ha tempo de tela a ganhar
com uma imagem que nao muda. Aqui a animacao E o dado -- a barra cresce ate o
valor real. Isso da movimento sem inventar nada, que e exatamente a linha que
este projeto nao pode cruzar.

COMO O VIDEO E GRAVADO

O Playwright grava a pagina rodando animacoes CSS (webm), e o ffmpeg converte
para H.264. Nada de renderizar frame a frame: alem de lento, sairia travado.
A trilha e silencio de verdade -- musica com direito autoral esta fora, e um
Reel sem faixa de audio nenhuma da problema em parte dos aparelhos.

Dependencias: jinja2, playwright, ffmpeg no PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).parent
W, H = 1080, 1920
FPS = 30

# Linha do tempo, em segundos. Curto de proposito: o sinal que a plataforma
# mais pesa e retencao, e e mais facil segurar 14s inteiros do que 30 pela
# metade.
CENAS_S = {
    "gancho": 2.8,
    "grafico": 6.6,
    "leitura": 2.6,
    "fecho": 2.0,
}
FADE_S = 0.45          # entrada/saida de cada cena
BARRA_ATRASO_S = 0.10  # escalonamento entre barras

# Geometria do grafico, em px do canvas -- o CSS usa % do trilho, entao a
# conversao tem que ser feita aqui, com os mesmos numeros do template.
PAD_X = 90
NOME_W = 260
NOME_GAP = 26
TRILHO_PX = W - 2 * PAD_X - NOME_W - NOME_GAP

# Espaco reservado para o rotulo de valor, de cada lado que exista.
#
# Precisa ser medido em PIXEL, nao em porcentagem da amplitude: o rotulo tem
# largura fixa (~110px) independente do tamanho da barra. Na primeira versao
# a reserva era proporcional, e com o lado negativo pequeno (-0,25% contra
# +1,31%) o "-0,2%" da Curitiba invadiu a coluna de nomes.
VAL_PX = 150
VAL_PCT = VAL_PX / TRILHO_PX * 100


def _linha_do_tempo() -> tuple[list[dict], float]:
    """Converte a duracao de cada cena em porcentagens de keyframe.

    O CSS anima em % da duracao total, entao a conta tem que ser feita aqui.
    Fazer isso em Python e nao no template mantem o template legivel e a
    aritmetica testavel.
    """
    total = sum(CENAS_S.values())
    cenas, t = [], 0.0
    nomes = list(CENAS_S)
    for i, nome in enumerate(nomes):
        dur = CENAS_S[nome]
        ultima = i == len(nomes) - 1
        ini, fim = t, t + dur
        cenas.append({
            "nome": nome,
            "ini_s": ini,
            "p_oculta_antes": ini / total * 100,
            "p_visivel_ini": min(ini + FADE_S, fim) / total * 100,
            "p_visivel_fim": (100.0 if ultima else max(fim - FADE_S, ini) / total * 100),
            "p_oculta_depois": None if ultima else fim / total * 100,
            "ultima": ultima,
        })
        t = fim
    return cenas, total


def _barras(dados: list[dict], inicio_s: float, total_s: float) -> list[dict]:
    """Geometria e tempo de cada barra, em % -- divergente em torno do zero."""
    valores = [d["valor"] for d in dados]
    v_neg = abs(min(valores)) if min(valores) < 0 else 0.0
    v_pos = max(valores) if max(valores) > 0 else 0.0
    amplitude = (v_neg + v_pos) or 1.0

    # Mesma logica do render.py: reserva o rotulo primeiro, uma vez por lado
    # que exista, e divide o resto pela amplitude. A barra negativa mais longa
    # termina exatamente na borda da reserva -- por isso ela e a que dita o
    # tamanho de VAL_PX.
    esquerda = VAL_PCT if v_neg else 0.0
    reserva = VAL_PCT * ((1 if v_neg else 0) + (1 if v_pos else 0))
    util = 100.0 - reserva
    zero = esquerda + (v_neg / amplitude) * util

    saida = []
    for i, d in enumerate(dados):
        v = d["valor"]
        larg = abs(v) / amplitude * util
        atraso = inicio_s + 0.5 + i * BARRA_ATRASO_S
        saida.append({
            "label": d["label"],
            "valor": v,
            "texto": f"{v:+.1f}".replace(".", ",") + "%",
            "positiva": v >= 0,
            "largura_pct": larg,
            "esquerda_pct": zero if v >= 0 else zero - larg,
            "atraso_pct": atraso / total_s * 100,
            "atraso_s": atraso,
        })
    return saida, zero


def _texto_da_leitura(brief: dict) -> str:
    """A frase que fica na tela depois do grafico.

    Prioriza o slide de texto (a leitura do dado); cai para o primeiro item
    da lista. Nao inventa: se nao houver nenhum dos dois, devolve vazio e a
    cena some.
    """
    for s in brief.get("slides", []):
        if s.get("tipo") == "texto" and s.get("titulo"):
            return s["titulo"]
    for s in brief.get("slides", []):
        if s.get("tipo") == "lista" and s.get("itens"):
            return s["itens"][0]
    return ""


def montar_html(brief: dict, marca: dict) -> tuple[str, float]:
    cenas, total = _linha_do_tempo()
    por_nome = {c["nome"]: c for c in cenas}

    grafico = next((s for s in brief["slides"] if s.get("tipo") == "grafico"), None)
    if not grafico or not grafico.get("dados"):
        raise SystemExit("o brief nao tem slide de grafico com dados — sem isso nao ha Reel")

    barras, zero = _barras(grafico["dados"], por_nome["grafico"]["ini_s"], total)

    capa = brief["slides"][0]
    fecho = next((s for s in brief["slides"] if s.get("tipo") == "fechamento"), {})

    env = Environment(
        loader=FileSystemLoader(RAIZ / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("reel.html.j2").render(
        W=W, H=H, c=marca["cores"], t=marca["tipografia"], marca=marca,
        cenas=por_nome, total_s=total, barras=barras, zero_pct=zero,
        gancho=capa.get("titulo", ""),
        gancho_sub=capa.get("sub", ""),
        grafico_titulo=grafico.get("titulo", ""),
        leitura=_texto_da_leitura(brief),
        cta=fecho.get("cta", ""),
        selo_ia=brief.get("selo_ia"),
    )
    return html, total


def gravar(html: str, total_s: float, destino: Path) -> tuple[Path, Path]:
    """Grava o webm pelo Playwright e converte para MP4 com o ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg nao encontrado no PATH — sem ele nao ha MP4")

    bruto = destino / "_bruto"
    bruto.mkdir(parents=True, exist_ok=True)
    for antigo in bruto.glob("*.webm"):
        antigo.unlink()

    capa = destino / "capa.jpg"

    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = navegador.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=1,
            record_video_dir=str(bruto),
            record_video_size={"width": W, "height": H},
        )
        pagina = ctx.new_page()
        pagina.set_content(html, wait_until="networkidle")

        # A capa e o primeiro frame com o gancho ja legivel: e a miniatura
        # que aparece no perfil, entao nao pode ser tela preta de fade-in.
        pagina.wait_for_timeout(int(FADE_S * 1000) + 250)
        pagina.screenshot(path=str(capa), type="jpeg", quality=90)

        restante = total_s - (FADE_S + 0.25) + 0.4
        pagina.wait_for_timeout(int(restante * 1000))

        ctx.close()          # o webm só é escrito quando o contexto fecha
        navegador.close()

    webms = list(bruto.glob("*.webm"))
    if not webms:
        raise SystemExit("o Playwright nao gravou nenhum webm")

    mp4 = destino / "reel.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(webms[0]),
        # faixa de audio silenciosa: sem ela o Reel falha em parte dos aparelhos
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black",
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart",
        "-t", f"{total_s:.2f}",
        str(mp4),
    ]
    subprocess.run(cmd, check=True)
    shutil.rmtree(bruto, ignore_errors=True)
    return mp4, capa


def montar(brief_path: Path) -> Path:
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    marca = json.loads((RAIZ / "brands" / f"{brief['marca']}.json").read_text(encoding="utf-8"))

    hoje = brief.get("data") or date.today().isoformat()
    # A pasta carrega o angulo: o mesmo informe rende varios posts, e sem isso
    # o segundo do dia sobrescreveria o manifest do primeiro.
    destino = RAIZ / "out" / marca["slug"] / f"{hoje}-{brief.get('angulo', 'padrao')}"
    destino.mkdir(parents=True, exist_ok=True)

    html, total = montar_html(brief, marca)
    mp4, capa = gravar(html, total, destino)

    tamanho_mb = mp4.stat().st_size / 1_000_000
    print(f"  {mp4.name}  {total:.1f}s  {tamanho_mb:.1f} MB")
    print(f"  {capa.name}  (miniatura)")

    (destino / "manifest.json").write_text(json.dumps({
        "marca": marca["slug"],
        "data": hoje,
        "formato": "reel",
        "legenda": brief["legenda"],
        "arquivos": [mp4.name, capa.name],
        "video": mp4.name,
        "capa": capa.name,
        "duracao_s": round(total, 1),
        "periodo": brief.get("periodo"),
        "hash_pdf": brief.get("hash_pdf"),
        "angulo": brief.get("angulo", "padrao"),
        "gerado_em": date.today().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return destino


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("uso: python reel.py <brief.json> | --ultimo <marca>")

    if sys.argv[1] == "--ultimo":
        if len(sys.argv) < 3:
            raise SystemExit("uso: python reel.py --ultimo <marca>")
        from render import ultimo_brief
        alvo = ultimo_brief(sys.argv[2])
        print(f"brief: {alvo.relative_to(RAIZ)}")
    else:
        alvo = Path(sys.argv[1])

    print(f"\nPronto: {montar(alvo)}")
