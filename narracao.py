"""
narracao.py — texto -> WAV em pt-BR, com a voz declarada.

    python narracao.py "Belo Horizonte fechou o ano quase de lado."

O QUE ISSO RESOLVE

Os tres reels observados em claude/14-templates-observados.md tem narracao com
legenda queimada. O reel.py de hoje produz 14s de silencio absoluto. Um reel
silencioso disputa espaco com reels narrados -- e essa e a diferenca mais
visivel entre o que performa e o que este pipeline gera.

AS TRES VOZES, E POR QUE A ORDEM IMPORTA

  piper  — TTS neural, offline, voz boa. Precisa baixar o modelo (~60MB) do
           HuggingFace. Roda no GitHub Actions; NAO roda no ambiente desta
           sessao, onde o proxy bloqueia huggingface.co.
  gtts   — voz do Google Translate, aceitavel. Precisa de rede no momento da
           geracao. Roda no Actions; bloqueada nesta sessao.
  espeak — offline, sempre disponivel, e ROBOTICA. Serve para testar a
           sincronia e o encaixe do audio. Nao serve para publicar.

O PORTEIRO

`espeak` e marcado `producao=False`. Quem for publicar tem de conferir
`ok_para_publicar()` e recusar. Sem isso o comportamento seria: o modelo bom
falha em baixar, o fallback entra calado, e o perfil passa a publicar voz de
robo sem nada ficar vermelho. E o defeito recorrente deste projeto na forma
mais classica -- fallback que degrada em silencio.

O QUE EU NAO CONSIGO CHECAR

Eu nao ouco. Dá para conferir que o WAV existe, tem a duracao esperada, e que
legenda e audio comecam no mesmo instante. Se a voz e aceitavel para o perfil
e julgamento de quem ouve.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).parent
TAXA = 22050          # Hz do WAV intermediario


@dataclass
class Voz:
    nome: str
    producao: bool
    detalhe: str = ""


def _dur_wav(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def _piper_modelo() -> Path | None:
    """O .onnx de pt-BR, se ja estiver em disco.

    NAO baixa aqui. Baixar dentro da sintese esconderia uma chamada de rede
    no meio da geracao do reel: no dia em que o HuggingFace estiver fora, o
    job falharia num lugar que nao parece ter nada a ver com rede. O download
    e passo explicito do workflow.
    """
    for cand in [
        os.environ.get("PIPER_MODELO", ""),
        str(RAIZ / "vozes" / "pt_BR-faber-medium.onnx"),
    ]:
        if cand and Path(cand).is_file():
            return Path(cand)
    return None


def _sintetizar_piper(texto: str, destino: Path) -> None:
    modelo = _piper_modelo()
    if modelo is None:
        raise RuntimeError("modelo do piper ausente")
    # --config explicito. O piper acha o .onnx.json ao lado sozinho, mas
    # "acha sozinho" e suposicao: se o nome divergir, ele cai num default e
    # a voz sai em outra lingua sem erro nenhum.
    cfg = modelo.with_suffix(modelo.suffix + ".json")
    if not cfg.is_file():
        raise RuntimeError(f"config do modelo ausente: {cfg.name}")
    subprocess.run(
        [sys.executable, "-m", "piper", "--model", str(modelo),
         "--config", str(cfg), "--output_file", str(destino)],
        input=texto.encode("utf-8"), check=True, capture_output=True)


def _sintetizar_gtts(texto: str, destino: Path) -> None:
    from gtts import gTTS
    mp3 = destino.with_suffix(".mp3")
    gTTS(texto, lang="pt", tld="com.br").save(str(mp3))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp3),
         "-ar", str(TAXA), "-ac", "1", str(destino)], check=True)
    mp3.unlink(missing_ok=True)


def _sintetizar_espeak(texto: str, destino: Path) -> None:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("espeak-ng ausente")
    # -s 165: um pouco mais devagar que o padrao. Numero falado precisa de
    # tempo, e o ouvinte tem de casar o que ouve com o que le na tela.
    subprocess.run([exe, "-v", "pt-br", "-s", "165", "-w", str(destino), texto],
                   check=True, capture_output=True)


_ENGINES = [
    ("piper", True, _sintetizar_piper),
    ("gtts", True, _sintetizar_gtts),
    ("espeak", False, _sintetizar_espeak),
]


def escolher_voz(preferida: str | None = None) -> Voz:
    """A primeira que realmente funciona, em ordem de qualidade.

    Testa sintetizando de verdade, num arquivo temporario. Perguntar "o
    pacote esta instalado?" nao e a mesma pergunta que "ele produz audio
    aqui?" -- o gtts esta instalado nesta sessao e falha na rede.
    """
    import tempfile

    ordem = _ENGINES
    if preferida:
        ordem = [e for e in _ENGINES if e[0] == preferida]
        if not ordem:
            raise SystemExit(f"voz desconhecida: {preferida} "
                             f"({[e[0] for e in _ENGINES]})")

    tentativas: list[str] = []
    for nome, producao, fn in ordem:
        with tempfile.TemporaryDirectory() as d:
            alvo = Path(d) / "t.wav"
            try:
                fn("teste de voz", alvo)
                if not alvo.is_file() or alvo.stat().st_size < 1000:
                    raise RuntimeError("saida vazia")
                return Voz(nome, producao,
                           f"{_dur_wav(alvo):.2f}s para 'teste de voz'")
            except Exception as e:  # noqa: BLE001
                tentativas.append(f"{nome}: {' '.join(str(e).split())[:110]}")

    raise SystemExit("nenhuma voz funcionou:\n  " + "\n  ".join(tentativas))


def ok_para_publicar(v: Voz) -> None:
    """Recusa voz de teste no caminho de publicacao."""
    if not v.producao:
        raise SystemExit(
            f"a voz disponivel e '{v.nome}', que e voz de TESTE — robotica. "
            f"Publicar com ela e pior que publicar em silencio. "
            f"Instale o modelo do piper (vozes/pt_BR-faber-medium.onnx) ou "
            f"garanta rede para o gtts. Para gravar so para conferir "
            f"sincronia, use --permitir-voz-de-teste.")


# ---------------------------------------------------------------------------
# Numero falado
# ---------------------------------------------------------------------------

def pct_falado(v: float) -> str:
    """+4,16% -> 'mais quatro vírgula dezesseis por cento'.

    Por que verbalizar em vez de entregar '4,16%' ao TTS: cada engine le
    simbolo de um jeito, e algumas leem "4.16" como "quatro ponto um seis".
    Verbalizado, o texto falado fica igual em qualquer voz -- e testavel.

    O zero a esquerda na casa decimal e lido digito por digito: 0,01 e "zero
    vírgula zero um", nao "zero vírgula um". Sem isso a narracao diria um
    numero dez vezes maior que o da tela.
    """
    from num2words import num2words

    sinal = "menos" if v < 0 else "mais"
    inteiro = int(abs(v))
    # duas casas, como o quadro mostra
    centesimos = int(round((abs(v) - inteiro) * 100))
    if centesimos == 100:          # 3,999 -> 4,00
        inteiro += 1
        centesimos = 0

    partes = [sinal, num2words(inteiro, lang="pt_BR")]
    if centesimos:
        partes.append("vírgula")
        if centesimos < 10:                    # 0,07 -> "zero sete"
            partes += ["zero", num2words(centesimos, lang="pt_BR")]
        elif centesimos % 10 == 0:             # 0,50 -> "cinquenta"
            partes.append(num2words(centesimos, lang="pt_BR"))
        else:
            partes.append(num2words(centesimos, lang="pt_BR"))
    partes.append("por cento")
    return " ".join(partes)


def ordinal_falado(n: int) -> str:
    from num2words import num2words
    return num2words(n, lang="pt_BR", to="ordinal")


# ---------------------------------------------------------------------------
# Sintese de uma fala
# ---------------------------------------------------------------------------

def falar(texto: str, destino: Path, voz: Voz) -> float:
    """Grava o WAV e devolve a duracao real, medida do arquivo.

    A duracao MEDIDA e o insumo da linha do tempo. Estimar por numero de
    silabas daria uma legenda que anda junto do audio por acidente e
    desencontra dele quando o texto muda.
    """
    fn = dict((n, f) for n, _, f in _ENGINES)[voz.nome]
    destino.parent.mkdir(parents=True, exist_ok=True)
    bruto = destino.with_name(destino.stem + "-bruto.wav")
    fn(texto, bruto)
    if not bruto.is_file() or bruto.stat().st_size < 500:
        raise SystemExit(f"a sintese de {texto!r} saiu vazia")

    # NORMALIZA PARA UM FORMATO SO. Cada engine escreve na taxa que quer
    # (o piper na taxa do modelo, o gtts em mp3). A montagem do audio final
    # junta os trechos amostra por amostra com o modulo `wave`, e juntar
    # trechos de taxas diferentes daria uma narracao acelerada em parte do
    # video -- do tipo que sai no arquivo e nao aparece em nenhum log.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(bruto),
         "-ar", str(TAXA), "-ac", "1", "-c:a", "pcm_s16le", str(destino)],
        check=True)
    bruto.unlink(missing_ok=True)

    with wave.open(str(destino), "rb") as w:
        if w.getframerate() != TAXA or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(
                f"{destino.name}: esperava {TAXA}Hz mono 16 bits, veio "
                f"{w.getframerate()}Hz {w.getnchannels()}ch "
                f"{w.getsampwidth()*8} bits")
    return _dur_wav(destino)


def main() -> int:
    texto = " ".join(sys.argv[1:]) or "Teste de narração em português do Brasil."
    v = escolher_voz(os.environ.get("VOZ") or None)
    print(f"voz: {v.nome}  producao={v.producao}  ({v.detalhe})")
    alvo = RAIZ / "out" / "_narracao-teste.wav"
    d = falar(texto, alvo, v)
    print(f"{alvo}  {d:.2f}s")
    print(f"exemplo de numero: {pct_falado(-0.08)}")
    print(f"exemplo de numero: {pct_falado(4.16)}")
    print(f"exemplo de numero: {pct_falado(0.01)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
