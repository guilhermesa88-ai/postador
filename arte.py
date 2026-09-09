"""
arte.py — publica arte pronta. Você entrega o arquivo, o pipeline entrega o post.

    python arte.py preparar estilovidya     # escolhe a pasta, valida, monta o manifest
    python arte.py registrar estilovidya    # grava no estado, depois de publicar
    python arte.py listar estilovidya       # o que está na fila

COMO ENTREGAR O CONTEUDO

Uma pasta por post, dentro de `entrada/<marca>/`:

    entrada/estilovidya/2026-09-10-camisa-proposito/
        legenda.txt      obrigatorio
        01.jpg           1 imagem = post simples; 2 a 10 = carrossel
        02.jpg           a ORDEM do carrossel e a ordem alfabetica dos nomes
        ...
    entrada/estilovidya/2026-09-12-reel-lente/
        legenda.txt
        video.mp4        1 mp4 = Reel
        capa.jpg         opcional, vira a capa do Reel

O formato NAO precisa ser declarado: sai do que esta na pasta. Menos um campo
para preencher errado.

POR QUE ESTE CAMINHO E DIFERENTE DO metro-de-bh

Aquele perfil nasce de um PDF: ingere, extrai fatos, pede texto ao modelo e
ancora todo numero publicado nos fatos de origem. Aqui nao ha fonte de dados --
o conteudo e seu. A ancoragem numerica ficaria sem nada para conferir, e rodar
uma checagem que nao pode reprovar seria teatro. Ela nao roda.

O que roda no lugar sao as restricoes REAIS do Instagram, conferidas aqui
embaixo em vez de descobertas como erro cru da API depois do upload: JPEG que e
mesmo JPEG, proporcao dentro da faixa aceita, carrossel de 2 a 10, legenda ate
2200 caracteres, ate 30 hashtags, tamanho de arquivo, duracao do video.

O selo de IA NAO e carimbado automaticamente aqui. Se a arte e sua, dizer que
foi produzida com apoio de IA seria declarar algo falso. Se for gerada por IA,
escreva o selo na propria legenda.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).parent
ENTRADA = RAIZ / "entrada"
SAIDA = RAIZ / "out"
ESTADO = RAIZ / "estado"
BRANDS = RAIZ / "brands"

FUSO_BR = timezone(timedelta(hours=-3))
DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]

# Restricoes do Instagram (Content Publishing API, v23.0).
MAX_LEGENDA = 2200
MAX_HASHTAGS = 30
MAX_IMAGEM_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
CARROSSEL_MIN, CARROSSEL_MAX = 2, 10
# Feed aceita de 4:5 (retrato) a 1.91:1 (paisagem).
PROPORCAO_MIN, PROPORCAO_MAX = 0.8, 1.91
REEL_MIN_S, REEL_MAX_S = 3, 15 * 60

IMAGENS = {".jpg", ".jpeg"}
VIDEOS = {".mp4"}


class ArteInvalida(SystemExit):
    pass


def falhar(msg: str) -> None:
    raise ArteInvalida(f"arte invalida: {msg}")


# ---------------------------------------------------------------------------
# Leitura dos arquivos — sem confiar na extensao
# ---------------------------------------------------------------------------

def _e_jpeg(caminho: Path) -> bool:
    """Extensao mente. PNG renomeado para .jpg e o erro mais comum aqui, e a
    API devolve um erro cru bem depois do upload."""
    with caminho.open("rb") as f:
        return f.read(3) == b"\xff\xd8\xff"


def _e_mp4(caminho: Path) -> bool:
    with caminho.open("rb") as f:
        cabeca = f.read(12)
    return cabeca[4:8] == b"ftyp"


def dimensoes_jpeg(caminho: Path) -> tuple[int, int]:
    """Le largura e altura direto dos marcadores SOF do JPEG. Sem Pillow: uma
    dependencia a menos no runner, e o formato aqui e sempre JPEG."""
    with caminho.open("rb") as f:
        if f.read(2) != b"\xff\xd8":
            falhar(f"{caminho.name}: nao comeca com SOI de JPEG")
        while True:
            b = f.read(1)
            if not b:
                falhar(f"{caminho.name}: fim do arquivo sem encontrar as dimensoes")
            if b != b"\xff":
                continue
            marcador = f.read(1)
            while marcador == b"\xff":
                marcador = f.read(1)
            m = marcador[0]
            # SOF0..SOF15, menos os marcadores que nao carregam dimensao
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                f.read(3)  # tamanho do segmento (2) + precisao (1)
                altura = int.from_bytes(f.read(2), "big")
                largura = int.from_bytes(f.read(2), "big")
                return largura, altura
            tamanho = int.from_bytes(f.read(2), "big")
            f.seek(tamanho - 2, 1)


def duracao_mp4(caminho: Path) -> float | None:
    """Usa ffprobe. Devolve None se a ferramenta nao existir -- e quem chama
    decide se isso e aceitavel; aqui nao se finge que conferiu."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(caminho)],
            capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Validacao
# ---------------------------------------------------------------------------

def validar_legenda(texto: str) -> None:
    if not texto.strip():
        falhar("legenda.txt esta vazio")
    if len(texto) > MAX_LEGENDA:
        falhar(f"legenda com {len(texto)} caracteres; o limite e {MAX_LEGENDA}")
    tags = re.findall(r"#\w+", texto)
    if len(tags) > MAX_HASHTAGS:
        falhar(f"{len(tags)} hashtags; o limite e {MAX_HASHTAGS}")


def validar_imagem(caminho: Path, exigir_proporcao: bool = True) -> None:
    if not _e_jpeg(caminho):
        falhar(f"{caminho.name} nao e JPEG de verdade (so a extensao diz que e). "
               "Exporte como JPEG — o Instagram nao aceita PNG pela API.")
    tamanho = caminho.stat().st_size
    if tamanho > MAX_IMAGEM_BYTES:
        falhar(f"{caminho.name} tem {tamanho / 1048576:.1f} MB; o limite e 8 MB")
    if not exigir_proporcao:
        return
    largura, altura = dimensoes_jpeg(caminho)
    prop = largura / altura
    if not (PROPORCAO_MIN <= prop <= PROPORCAO_MAX):
        falhar(f"{caminho.name} tem {largura}x{altura} (proporcao {prop:.2f}); "
               f"o feed aceita de {PROPORCAO_MIN} (4:5) a {PROPORCAO_MAX} (1.91:1)")


def validar_video(caminho: Path) -> float | None:
    if not _e_mp4(caminho):
        falhar(f"{caminho.name} nao parece um MP4 (sem caixa ftyp)")
    tamanho = caminho.stat().st_size
    if tamanho > MAX_VIDEO_BYTES:
        falhar(f"{caminho.name} tem {tamanho / 1048576:.0f} MB; o limite e 1 GB")
    dur = duracao_mp4(caminho)
    if dur is None:
        print(f"::warning::ffprobe indisponivel — a duracao de {caminho.name} "
              "nao foi conferida aqui e so o Instagram vai reclamar.")
        return None
    if not (REEL_MIN_S <= dur <= REEL_MAX_S):
        falhar(f"{caminho.name} dura {dur:.1f}s; o Reel aceita de "
               f"{REEL_MIN_S}s a {REEL_MAX_S // 60} min")
    return dur


# ---------------------------------------------------------------------------
# A fila
# ---------------------------------------------------------------------------

def _publicados(marca: str) -> set[str]:
    caminho = ESTADO / f"{marca}.json"
    if not caminho.exists():
        return set()
    hist = json.loads(caminho.read_text(encoding="utf-8")).get("publicados", [])
    return {r["angulo"] for r in hist if str(r.get("angulo", "")).startswith("arte:")}


def fila(marca: str) -> list[Path]:
    raiz = ENTRADA / marca
    if not raiz.exists():
        return []
    ja = _publicados(marca)
    pendentes = [p for p in sorted(raiz.iterdir())
                 if p.is_dir() and f"arte:{p.name}" not in ja]
    return pendentes


def inspecionar(pasta: Path) -> dict:
    """Le a pasta, valida tudo e devolve o manifest. O formato sai do conteudo:
    1 imagem = post simples, 2 a 10 = carrossel, 1 mp4 = Reel."""
    legenda_path = pasta / "legenda.txt"
    if not legenda_path.exists():
        falhar(f"{pasta.name}/legenda.txt nao existe")
    legenda = legenda_path.read_text(encoding="utf-8").strip()
    validar_legenda(legenda)

    arquivos = [p for p in sorted(pasta.iterdir())
                if p.is_file() and p.name != "legenda.txt"]
    imagens = [p for p in arquivos if p.suffix.lower() in IMAGENS]
    videos = [p for p in arquivos if p.suffix.lower() in VIDEOS]
    outros = [p for p in arquivos if p not in imagens and p not in videos]
    if outros:
        falhar(f"arquivos que nao sao JPEG nem MP4: {[p.name for p in outros]}")
    if len(videos) > 1:
        falhar(f"{len(videos)} videos na mesma pasta; um Reel por pasta")
    if not imagens and not videos:
        falhar(f"{pasta.name} nao tem nenhuma imagem nem video")

    manifest = {"marca": pasta.parent.name, "legenda": legenda,
                "data": datetime.now(timezone.utc).date().isoformat(),
                "angulo": f"arte:{pasta.name}", "origem": str(pasta.relative_to(RAIZ))}

    if videos:
        video = videos[0]
        dur = validar_video(video)
        capa = None
        if imagens:
            if len(imagens) > 1:
                falhar("um Reel aceita no maximo uma imagem de capa; "
                       f"achei {[p.name for p in imagens]}")
            capa = imagens[0]
            # A capa do Reel e vertical: nao vale a faixa do feed.
            validar_imagem(capa, exigir_proporcao=False)
        manifest.update({
            "formato": "reel",
            "video": video.name,
            "capa": capa.name if capa else None,
            "duracao_s": round(dur, 1) if dur else None,
            "arquivos": [video.name] + ([capa.name] if capa else []),
        })
        return manifest

    for img in imagens:
        validar_imagem(img)
    if len(imagens) == 1:
        manifest.update({"formato": "image", "arquivos": [imagens[0].name]})
    elif CARROSSEL_MIN <= len(imagens) <= CARROSSEL_MAX:
        manifest.update({"formato": "carousel",
                         "arquivos": [p.name for p in imagens]})
    else:
        falhar(f"{len(imagens)} imagens; um carrossel aceita de "
               f"{CARROSSEL_MIN} a {CARROSSEL_MAX}")
    return manifest


def _sinalizar(tem_post: bool) -> None:
    """Diz ao workflow se ha post nesta rodada, por GITHUB_OUTPUT.

    A guarda do metro-de-bh usa codigo de saida 78 para "nada novo", e o
    workflow le `outcome`. Aqui isso nao serve: `outcome` nao distingue
    "fila vazia" (que e normal e tem de ficar verde) de "arte invalida" (que
    e um problema seu e tem de ficar vermelho). Um flag explicito separa os
    dois -- e arte invalida sai com codigo 1, como qualquer erro."""
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"tem_post={'true' if tem_post else 'false'}\n")


def dia_de_publicar(marca: str) -> tuple[bool, str]:
    """A cadencia e config da MARCA, nao do workflow.

    O cron roda todo dia e cada perfil decide se hoje e dia dele, por
    `dias_da_semana` em brands/<marca>.json (0 = segunda). Assim dois perfis
    com ritmos diferentes convivem no mesmo agendamento, e mudar o ritmo de um
    nao mexe no outro nem exige editar YAML.

    Vale so para rodada agendada: disparo manual publica sempre. E a valvula de
    escape para quando voce quer que saia agora, num dia que nao e o do perfil.
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return True, "disparo manual — cadencia nao se aplica"
    caminho = BRANDS / f"{marca}.json"
    if not caminho.exists():
        return True, f"sem brands/{marca}.json — publica em toda rodada"
    dias = json.loads(caminho.read_text(encoding="utf-8")).get("dias_da_semana")
    if not dias:
        return True, "sem dias_da_semana — publica em toda rodada"
    hoje = datetime.now(FUSO_BR).weekday()
    if hoje in dias:
        return True, f"hoje e {DIAS[hoje]}, dia de {marca}"
    return False, (f"hoje e {DIAS[hoje]}; {marca} publica "
                   f"{'/'.join(DIAS[d] for d in sorted(dias))}")


def preparar(marca: str) -> int:
    pode, motivo = dia_de_publicar(marca)
    print(f"cadencia: {motivo}")
    if not pode:
        _sinalizar(False)
        return 0

    pendentes = fila(marca)
    if not pendentes:
        print(f"nada novo em entrada/{marca}/ — fila vazia.")
        _sinalizar(False)
        return 0

    pasta = pendentes[0]
    print(f"pasta: {pasta.relative_to(RAIZ)}")
    manifest = inspecionar(pasta)

    destino = SAIDA / marca / pasta.name
    destino.mkdir(parents=True, exist_ok=True)
    for nome in manifest["arquivos"]:
        (destino / nome).write_bytes((pasta / nome).read_bytes())
    (destino / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  formato : {manifest['formato']}")
    print(f"  arquivos: {', '.join(manifest['arquivos'])}")
    print(f"  legenda : {len(manifest['legenda'])} caracteres")
    print(f"  restam  : {len(pendentes) - 1} pasta(s) na fila")
    print(f"pronto em {destino.relative_to(RAIZ)}")
    _sinalizar(True)
    return 0


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

def registrar(marca: str) -> int:
    """Grava no mesmo estado/<marca>.json que a guarda e o coletor de metricas
    ja leem. Assim a arte entra na serie de desempenho sem codigo novo."""
    raiz = SAIDA / marca
    candidatos = sorted((p for p in raiz.glob("*") if (p / "resultado.json").exists()),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidatos:
        raise SystemExit(f"nenhum resultado.json em out/{marca}/ — publique antes")
    pasta = candidatos[0]
    manifest = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    resultado = json.loads((pasta / "resultado.json").read_text(encoding="utf-8"))

    if not resultado.get("media_id"):
        raise SystemExit("resultado.json sem media_id — nao houve publicacao")

    caminho = ESTADO / f"{marca}.json"
    ESTADO.mkdir(exist_ok=True)
    dados = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    historico = dados.get("publicados", [])

    conteudo = hashlib.sha256()
    for nome in manifest["arquivos"]:
        conteudo.update((pasta / nome).read_bytes())

    historico.append({
        "periodo": "arte",
        "hash_pdf": conteudo.hexdigest()[:16],
        "angulo": manifest["angulo"],
        "formato": manifest["formato"],
        "data": manifest["data"],
        "media_id": resultado["media_id"],
        "permalink": resultado.get("permalink"),
        "registrado_em": datetime.now(timezone.utc).isoformat(),
        "origem": manifest.get("origem", ""),
    })
    dados["publicados"] = historico[-60:]
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"registrado em {caminho.relative_to(RAIZ)}: {resultado['media_id']}")
    return 0


def listar(marca: str) -> int:
    pendentes = fila(marca)
    if not pendentes:
        print(f"fila de {marca} vazia.")
        return 0
    print(f"{len(pendentes)} pasta(s) na fila de {marca}:")
    for p in pendentes:
        try:
            m = inspecionar(p)
            print(f"  {p.name}: {m['formato']}, {len(m['arquivos'])} arquivo(s)")
        except SystemExit as e:
            print(f"  {p.name}: {e}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("uso: python arte.py preparar|registrar|listar <marca>")
    acao, marca = sys.argv[1], sys.argv[2]
    if acao == "preparar":
        sys.exit(preparar(marca))
    if acao == "registrar":
        sys.exit(registrar(marca))
    if acao == "listar":
        sys.exit(listar(marca))
    raise SystemExit(f"acao desconhecida: {acao}")
