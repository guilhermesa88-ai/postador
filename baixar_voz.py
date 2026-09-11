"""
baixar_voz.py — traz o modelo de voz do piper para vozes/.

    python baixar_voz.py

Passo EXPLICITO do workflow, nao um download escondido dentro da geracao do
reel. Se o HuggingFace estiver fora, o job tem de falhar aqui, num passo
chamado "Baixar a voz", e nao la na frente com um erro que nao parece ter nada
a ver com rede.

NAO TESTADO NO AMBIENTE DA SESSAO: o proxy daqui bloqueia huggingface.co.
Por isso o workflow roda `python narracao.py` como passo de fumaca logo depois
deste -- quem prova que a voz funciona e o pipeline, a cada rodada, e nao a
minha suposicao de que a URL esta certa.

Nao ha conferencia de hash porque eu nao tenho o hash de referencia: inventar
um seria pior que nao ter. A conferencia que da para fazer de verdade e a que
importa -- o arquivo abre, declara pt_BR, e o piper sintetiza audio audivel
com ele.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path

RAIZ = Path(__file__).parent
VOZES = RAIZ / "vozes"

# pt_BR-faber-medium: voz masculina, qualidade "medium" (22050 Hz).
# Trocavel por variavel de ambiente sem editar codigo.
NOME = "pt_BR-faber-medium"
BASE = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "pt/pt_BR/faber/medium/")

MIN_ONNX = 5_000_000        # o modelo medium passa de 60MB; 5MB ja denuncia erro
MAX_ONNX = 400_000_000


def baixar(url: str, destino: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "postador/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        dados = r.read()
    destino.write_bytes(dados)
    return len(dados)


def main() -> int:
    import os
    nome = os.environ.get("PIPER_VOZ", NOME)
    base = os.environ.get("PIPER_BASE", BASE)

    VOZES.mkdir(parents=True, exist_ok=True)
    onnx = VOZES / f"{nome}.onnx"
    cfg = VOZES / f"{nome}.onnx.json"

    if onnx.is_file() and cfg.is_file() and onnx.stat().st_size > MIN_ONNX:
        print(f"{onnx.name} ja esta em disco ({onnx.stat().st_size/1e6:.1f} MB)")
    else:
        for arq, alvo in ((f"{nome}.onnx.json", cfg), (f"{nome}.onnx", onnx)):
            n = baixar(base + arq, alvo)
            print(f"{arq}: {n/1e6:.1f} MB")

    # 1. O tamanho denuncia pagina de erro salva como se fosse modelo.
    tam = onnx.stat().st_size
    if not (MIN_ONNX < tam < MAX_ONNX):
        raise SystemExit(
            f"{onnx.name} tem {tam} bytes, fora da faixa esperada "
            f"({MIN_ONNX}-{MAX_ONNX}). Provavelmente nao e o modelo.")

    # 2. O config abre e declara a lingua que eu espero.
    try:
        meta = json.loads(cfg.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{cfg.name} nao e JSON valido: {e}")
    lingua = (meta.get("language") or {}).get("code") or meta.get("language")
    taxa = (meta.get("audio") or {}).get("sample_rate")
    print(f"config: lingua={lingua} taxa={taxa}")
    if not str(lingua).lower().startswith("pt"):
        raise SystemExit(
            f"o modelo declara lingua {lingua!r}, nao portugues. Narrar em "
            f"outra lingua e pior que narrar mal.")

    # 3. A CHECAGEM QUE VALE: sintetizar de verdade e ouvir o arquivo existir
    #    com duracao plausivel. As duas de cima olham metadado; esta olha o
    #    resultado.
    teste = VOZES / "_fumaca.wav"
    frase = "Belo Horizonte ficou em quarto lugar entre vinte e duas capitais."
    subprocess.run(
        [sys.executable, "-m", "piper", "--model", str(onnx),
         "--config", str(cfg), "--output_file", str(teste)],
        input=frase.encode("utf-8"), check=True, capture_output=True)

    with wave.open(str(teste), "rb") as w:
        dur = w.getnframes() / float(w.getframerate())
        canais, larg = w.getnchannels(), w.getsampwidth()
    print(f"fumaca: {dur:.2f}s, {canais}ch, {larg*8} bits")

    # Uma frase dessas nao sai em menos de 2s nem passa de 15s. Fora disso,
    # algo saiu errado de um jeito que metadado nao mostra.
    if not (2.0 < dur < 15.0):
        raise SystemExit(
            f"a sintese de fumaca deu {dur:.2f}s para uma frase de "
            f"{len(frase.split())} palavras — implausivel.")
    teste.unlink(missing_ok=True)

    print(f"\nvoz pronta: {onnx.relative_to(RAIZ)}")
    print("o quadro.py encontra sozinho (vozes/<nome>.onnx) ou por PIPER_MODELO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
