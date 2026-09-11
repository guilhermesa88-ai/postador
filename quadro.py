"""
quadro.py — T1, o quadro progressivo.

    python quadro.py facts/fipezap-capitais.json metro-de-bh --horizonte ano
    python quadro.py facts/fipezap-capitais.json metro-de-bh --so-selecao

O QUE E O T1

Template observado em dois reels do mesmo criador (109 mil e 142 mil views,
94s e 81s). Registro em claude/14-templates-observados.md. Tres coisas que so
aparecem olhando frame a frame:

  1. o quadro entra VAZIO, com os numeros das posicoes ja visiveis. O
     espectador ve quantas revelacoes faltam antes de ver conteudo. E isso que
     sustenta 94 segundos.
  2. revela do 1 para o N, nao o contrario. A retencao nao vem de suspense
     sobre o topo -- vem do espectador procurando a cidade dele.
  3. ~8s por item. A duracao e consequencia de N, nao uma escolha.

POR QUE O CODIGO PREENCHE OS DADOS, E NAO O MODELO

Aqui a lista ordenada ja existe nos facts. Deixar o modelo copia-la seria
abrir superficie de alucinacao de graca: ele podia trocar uma cidade de lugar
ou arredondar errado, e a ancoragem numerica nao pegaria a TROCA (os dois
numeros existem nos facts, so nao naquela ordem).

Entao: o pipeline levanta a lista, calcula posicoes e empates, e escreve a
frase de fecho. O modelo escreve o gancho e a legenda -- texto, nao dado.
Isso ENCOLHE o que o modelo pode inventar em vez de aumentar.

E mesmo assim a ancoragem roda. "Veio do codigo" e suposicao ate ser checada,
e um bug no seletor e exatamente como um numero errado entraria.

O EMPATE

No informe de 2026-08, no acumulado do ano, Belo Horizonte e Porto Alegre
estao as duas em +0,01%. "A capital que menos valorizou" seria falso, e nenhum
validador numerico barraria a frase -- ela nao tem numero novo. Por isso o
fecho e gerado por codigo a partir dos dados, que sabem do empate. Superlativo
nao se checa depois; se evita na origem.
"""

from __future__ import annotations

import json
import os
import re
import sys
import wave
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

RAIZ = Path(__file__).parent
W, H = 1080, 1920

# Medidos nos reels observados: ~8s por item, ~94s para dez itens.
SEG_POR_ITEM = 8.0
SEG_ABERTURA = 2.2      # quadro vazio no ar antes da primeira revelacao
N_ITENS = 10            # o tamanho observado. 22 capitais dariam ~3min.

# DEPOIS da ultima revelacao, nao depois do ultimo INTERVALO.
#
# A primeira versao somava n*SEG_POR_ITEM e punha o fecho no fim disso. Mas a
# n-esima revelacao acontece no inicio do n-esimo intervalo, nao no fim: sobrava
# um vao morto de 8 segundos entre a ultima cidade aparecer e a frase entrar, e
# a frase ainda ficava legivel por ~1,5s antes do corte. Oito segundos de tela
# parada num formato cujo unico ativo e retencao.
#
# Agora a conta e explicita: abertura + (n-1) intervalos ate a ultima
# revelacao + este tempo de leitura. O numero e generoso de proposito -- o
# video gravado pelo Playwright atrasa ~2% em relacao ao relogio, e o fecho
# tem de sobrar legivel mesmo assim.
# Geometria da faixa de legenda, em px do quadro de 1920.
# A conta: 120 (topo) + 218 (cabeca) + 76 (regra) + 1086 (10x96 + 9x14) = 1500.
# O rodape fica a 52px do fim. A faixa ocupa o vao entre os dois, e
# conferir_altura() mede se e verdade em vez de eu confiar nesta soma.
# 120 (topo) + 290 (cabeca, fixa) + 76 (regra) + 1048 (10x94 + 9x12) = 1534.
# O rodape fica a 52px do fim. conferir_altura() mede se esta soma e verdade
# em vez de eu confiar nela -- e ja pegou dois erros meus aqui.
ALT_CABECA = 290
TOPO_FAIXA = 1534
ALT_FAIXA = 250


# ---------------------------------------------------------------------------
# O template, como esqueleto
# ---------------------------------------------------------------------------

# REGRA DE claude/13-motor-de-modelagem.md: o que cruza da pesquisa para a
# geracao e so a forma. Sem digito, sem @ de origem, sem nome proprio da fonte.
# O validador abaixo REPROVA -- nao confia.
TEMPLATE_T1 = {
    "id": "T1-quadro-progressivo",
    "observado_em": 2,
    "sem_rosto": True,
    "beats": [
        {"t": "abertura", "tipo": "encenacao",
         "mostra": "titulo + quadro vazio com as posicoes numeradas"},
        {"t": "revelacao", "tipo": "slot_dado", "repete": "n_itens",
         "slot": "lista_ordenada"},
        {"t": "fecho", "tipo": "slot_dado", "slot": "posicao_da_marca"},
    ],
    "exige": ["lista_ordenada", "localidade_da_marca_na_lista"],
    "retencao": "contagem restante visivel desde o primeiro segundo",
}

_DIGITO = re.compile(r"\d")
_ARROBA = re.compile(r"@\w")


def validar_template(t: dict) -> list[str]:
    """Reprova vazamento de substancia da fonte para dentro do esqueleto.

    Um template e forma. Se tem digito, tem dado de outra pessoa; se tem @,
    tem a fonte. Qualquer um dos dois significa que a abstracao falhou --
    e 'eu nao copiei' e suposicao vestida de verificacao.
    """
    erros: list[str] = []

    def anda(no, caminho: str) -> None:
        if isinstance(no, str):
            if _DIGITO.search(no):
                erros.append(f"{caminho}: digito no template — {no!r}")
            if _ARROBA.search(no):
                erros.append(f"{caminho}: @ de origem no template — {no!r}")
        elif isinstance(no, dict):
            for k, v in no.items():
                # Metadado do processo, nao conteudo. A lista e curta de
                # proposito: cada nome aqui e um lugar onde a checagem deixa
                # de valer, e lista de excecao que cresce e checagem que deixa
                # de poder falhar. `id` entra porque o proprio nome do
                # template carrega o numero dele ("T1"); `observado_em` porque
                # e a contagem de quantos reels fundamentaram o esqueleto.
                if k in ("id", "observado_em"):
                    continue
                anda(v, f"{caminho}.{k}")
        elif isinstance(no, list):
            for i, v in enumerate(no):
                anda(v, f"{caminho}[{i}]")

    anda(t, t.get("id", "template"))
    return erros


# ---------------------------------------------------------------------------
# Selecao: o codigo levanta a lista dos facts
# ---------------------------------------------------------------------------

def selecionar(facts: dict, horizonte: str, localidade: str,
               n: int = N_ITENS) -> dict:
    """Devolve a janela de `n` itens que CONTEM a localidade da marca.

    Ordena crescente pelo valor -- o extremo primeiro, como no observado --
    e pega a janela do inicio. Se a localidade nao cair nela, e erro, nao
    ajuste silencioso: trocar a janela para caber a marca mudaria a afirmacao
    do post sem ninguem ver.
    """
    h = (facts.get("horizontes") or {}).get(horizonte)
    if not h or not h.get("capitais"):
        raise SystemExit(
            f"facts sem horizontes.{horizonte}.capitais — "
            f"disponiveis: {sorted((facts.get('horizontes') or {}))}")

    todas = h["capitais"]
    total = len(todas)

    # Crescente: quem menos variou primeiro.
    ordenadas = sorted(todas, key=lambda c: c["variacao_pct"])
    janela = ordenadas[:n]

    achou = [i for i, c in enumerate(janela, start=1)
             if localidade.lower() in c["cidade"].lower()]
    if not achou:
        pos_geral = [i for i, c in enumerate(ordenadas, start=1)
                     if localidade.lower() in c["cidade"].lower()]
        raise SystemExit(
            f"'{localidade}' nao esta nos {n} primeiros deste horizonte "
            f"(posicao {pos_geral[0] if pos_geral else '?'} de {total}). "
            f"Nao vou trocar a janela para caber a marca — isso mudaria a "
            f"afirmacao do post. Escolha outro horizonte ou outro n.")

    pos_na_janela = achou[0]
    item = janela[pos_na_janela - 1]
    valor = item["variacao_pct"]

    # Empates no MESMO valor, na lista inteira. Sem isso o fecho viraria
    # superlativo falso -- e superlativo nao tem numero novo, entao nenhum
    # validador numerico o barraria.
    empatadas = [c["cidade"] for c in todas
                 if abs(c["variacao_pct"] - valor) < 1e-9
                 and localidade.lower() not in c["cidade"].lower()]

    return {
        "horizonte": horizonte,
        "indice_geral_pct": h.get("indice_geral_pct"),
        "total_capitais": total,
        "itens": [{"label": c["cidade"], "valor": c["variacao_pct"]}
                  for c in janela],
        "localidade": item["cidade"],
        "posicao_na_janela": pos_na_janela,
        "posicao_geral": pos_na_janela,   # janela comeca no inicio da ordem
        "valor_da_marca": valor,
        "empatadas_com_a_marca": empatadas,
    }


ROTULO_HORIZONTE = {
    "mensal": "no mes",
    "ano": "no acumulado do ano",
    "doze_meses": "em doze meses",
}


def _pt(v: float) -> str:
    return f"{v:+.2f}".replace(".", ",") + "%"


def frase_de_fecho(sel: dict, quando: str | None = None) -> str:
    """Escrita por codigo, a partir dos dados. Nao pelo modelo.

    Toda a diferenca entre uma frase verdadeira e um superlativo falso esta
    em saber do empate -- e quem sabe do empate e quem leu os facts.
    """
    # `quando` vem de periodo_em_palavras(): "em agosto de 2026" e nao "no
    # mes". Mesmo motivo do titulo -- o espectador precisa saber de que
    # periodo e o numero, e o fecho e a frase que ele le por ultimo.
    quando = quando or ROTULO_HORIZONTE.get(sel["horizonte"], sel["horizonte"])
    loc = sel["localidade"]
    val = _pt(sel["valor_da_marca"])
    emp = sel["empatadas_com_a_marca"]
    total = sel["total_capitais"]
    pos = sel["posicao_geral"]

    if emp:
        if len(emp) == 1:
            return (f"{loc} fechou {quando} em {val} — empatada com "
                    f"{emp[0]} entre as {total} capitais.")
        return (f"{loc} fechou {quando} em {val} — empatada com "
                f"{len(emp)} outras capitais.")
    return (f"{loc} ficou em {pos}º lugar entre {total} capitais "
            f"{quando}: {val}.")


# ---------------------------------------------------------------------------
# Linha do tempo
# ---------------------------------------------------------------------------

def linha_do_tempo(n: int) -> tuple[list[dict], float]:
    """Um keyframe de revelacao por item, em % da duracao total.

    O CSS anima em % -- a conta fica aqui, onde da para testar.

    A ordem devolvida e por INDICE DE REVELACAO, nao por posicao no quadro;
    quem casa as duas coisas e `montar_html`. Ver a nota sobre revelacao
    reversa la.
    """
    total = SEG_ABERTURA + (n - 1) * SEG_POR_ITEM + SEG_APOS_ULTIMA
    revelacoes = []
    for i in range(n):
        t = SEG_ABERTURA + i * SEG_POR_ITEM
        revelacoes.append({
            "i": i + 1,
            "t_s": t,
            "p": t / total * 100,
            "p_fim": min(t + 0.55, total) / total * 100,
        })

    # CHECAGEM QUE PODE FALHAR. Um item sem keyframe nunca apareceria no
    # video, e o quadro terminaria com um buraco que ninguem notaria no log.
    if len(revelacoes) != n:
        raise SystemExit(f"linha do tempo com {len(revelacoes)} revelacoes "
                         f"para {n} itens")
    return revelacoes, total


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------

def montar_html(sel: dict, marca: dict, falas: list[dict], total: float,
                gancho: str, gancho_sub: str,
                selo_ia: str | None) -> str:
    """O HTML, com os keyframes tirados dos instantes REAIS das falas."""
    n = len(sel["itens"])
    valores = [i["valor"] for i in sel["itens"]]
    v_min, v_max = min(valores), max(valores)
    amplitude = (v_max - v_min) or 1.0

    por_pos = {f["pos"]: f for f in falas if f["papel"] == "item"}
    fala_fecho = next(f for f in falas if f["papel"] == "fecho")

    # REVELACAO REVERSA -- e um DESVIO do observado, de proposito.
    #
    # Nos dois reels observados a revelacao ia do 1 para o N e a cidade do
    # publico caia no N, no fim. As duas propriedades coincidiam porque o
    # ranking deles era de PRECO e a cidade estava no rodape do top 10.
    #
    # Aqui o ranking e de VARIACAO e a marca esta no extremo: em ordem
    # crescente, Belo Horizonte e a posicao 1. Revelar na ordem do ranking
    # entregaria a resposta no segundo dois -- e o publico deste perfil e
    # justamente de BH, ou seja, ele iria embora com o que veio buscar.
    #
    # Entao o quadro mantem a numeracao do ranking (1 = quem menos valorizou)
    # e preenche de baixo para cima. A posicao 1 fica visivelmente vazia ate
    # o fim: promessa a vista, que e o mecanismo que sustentava os 94s.
    #
    # Nao e observado. E modificacao com motivo, e o watch time dira se
    # estava certa. O roteiro() narra na mesma ordem -- as duas coisas leem a
    # mesma lista, entao nao ha como divergirem.
    linhas = []
    for k, item in enumerate(sel["itens"], start=1):
        f = por_pos[k]
        linhas.append({
            "pos": k,
            "label": item["label"],
            "texto": _pt(item["valor"]),
            "e_marca": sel["posicao_na_janela"] == k,
            # barra proporcional dentro da janela, so como apoio visual
            "largura_pct": 8 + (item["valor"] - v_min) / amplitude * 92,
            "p": f["ini_s"] / total * 100,
            "p_fim": min(f["ini_s"] + 0.55, total) / total * 100,
            "t_s": f["ini_s"],
        })

    # CHECAGEM QUE PODE FALHAR. Se dois itens ganharem o mesmo instante, um
    # deles pisa no outro e o quadro termina com um buraco -- no video, nao
    # no log. Ja aconteceu o equivalente neste projeto com o `setdefault`.
    instantes = sorted(l["t_s"] for l in linhas)
    if len(set(instantes)) != n:
        raise SystemExit(f"instantes de revelacao repetidos: {instantes}")

    # As legendas: uma faixa por fala, no intervalo exato do audio dela.
    # A legenda NAO e derivada do texto falado -- para os itens ela e a versao
    # curta ("10. Campo Grande  +4,16%"), porque legenda de reel e para bater
    # o olho, nao para ler frase inteira.
    legendas = [{
        "texto": f.get("legenda") or f["texto"],
        "item": f["papel"] == "item",
        "p": f["ini_s"] / total * 100,
        "p_fim": min(f["ini_s"] + 0.25, total) / total * 100,
        "p_sai": min(f["fim_s"] + 0.15, total) / total * 100,
        "i": i,
    } for i, f in enumerate(falas)]

    env = Environment(
        loader=FileSystemLoader(RAIZ / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("quadro.html.j2").render(
        W=W, H=H, c=marca["cores"], t=marca["tipografia"], marca=marca,
        total_s=total, linhas=linhas, n=n, legendas=legendas,
        TOPO_FAIXA=TOPO_FAIXA, ALT_FAIXA=ALT_FAIXA,
        ALT_CABECA=ALT_CABECA,
        gancho=gancho, gancho_sub=gancho_sub,
        p_fecho=fala_fecho["ini_s"] / total * 100,
        selo_ia=selo_ia,
    )


MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def periodo_em_palavras(facts: dict, horizonte: str) -> tuple[str, str]:
    """(escrito, falado) do periodo coberto por este horizonte.

    `periodo` nos facts e o mes do informe ("2026-08"). O horizonte diz o que
    a variacao acumula a partir dele -- e sao coisas diferentes: o mesmo
    informe de agosto traz "agosto", "o ano ate agosto" e "doze meses ate
    agosto". Dizer so "2026" no titulo do acumulado do ano seria dar a
    entender ano fechado, que nao e o caso em setembro.
    """
    from num2words import num2words

    per = str(facts.get("periodo") or "")
    if not re.fullmatch(r"\d{4}-\d{2}", per):
        raise SystemExit(
            f"periodo dos facts em formato inesperado: {per!r}. O titulo diz "
            f"o periodo ao espectador — sem saber qual e, nao publico.")
    ano_n, mes_n = int(per[:4]), int(per[5:])
    mes = MESES[mes_n - 1]
    ano_falado = num2words(ano_n, lang="pt_BR")

    if horizonte == "mensal":
        return f"{mes} de {ano_n}", f"{mes} de {ano_falado}"
    if horizonte == "ano":
        return (f"acumulado de {ano_n}, até {mes}",
                f"acumulado de {ano_falado}, até {mes}")
    if horizonte == "doze_meses":
        return (f"12 meses até {mes} de {ano_n}",
                f"doze meses até {mes} de {ano_falado}")
    raise SystemExit(f"horizonte desconhecido: {horizonte}")


def titulo_e_sub(sel: dict, facts: dict) -> dict:
    """Titulo, subtitulo e abertura falada — escritos por codigo.

    DUAS COISAS QUE O TITULO TEM DE DIZER e a primeira versao nao dizia:
    o QUE valorizou menos, e em QUE periodo. "As 10 capitais que menos
    valorizaram" nao informa nem um nem outro: valorizaram o que? quando?
    Num perfil cuja proposta e o dado ser verificavel, titulo ambiguo e o
    mesmo problema de sempre numa forma mais barata de cometer.

    E o verbo segue o SINAL dos dados. "Menos subiu" com Curitiba a -0,25%
    na lista seria falso, e nenhum validador numerico pegaria -- a frase nao
    tem numero. No horizonte mensal tres das dez estao negativas.
    """
    # CONFERE A FONTE EM VEZ DE SUPOR. Se um dia o informe carregado for o de
    # LOCACAO, o titulo "preco de venda" viraria mentira sem nada falhar.
    fonte = str(facts.get("fonte") or "")
    if not ("venda" in fonte.lower() and "residencial" in fonte.lower()):
        raise SystemExit(
            f"a fonte dos facts e {fonte!r}, que nao declara venda residencial. "
            f"O titulo afirma 'preco de venda de imovel residencial' — "
            f"ajuste o texto antes de publicar com outra fonte.")

    n = len(sel["itens"])
    negativos = sum(1 for i in sel["itens"] if i["valor"] < 0)
    escrito, falado = periodo_em_palavras(facts, sel["horizonte"])

    if negativos == 0:
        titulo = f"As {n} capitais onde o imóvel menos subiu"
        falado_abre = (f"As {n} capitais onde o preço de venda do imóvel "
                       f"residencial menos subiu")
    elif negativos == n:
        titulo = f"As {n} capitais onde o imóvel caiu de preço"
        falado_abre = (f"As {n} capitais onde o preço de venda do imóvel "
                       f"residencial caiu")
    else:
        titulo = f"As {n} capitais com a menor variação de preço"
        falado_abre = (f"As {n} capitais com a menor variação no preço de "
                       f"venda do imóvel residencial")

    return {
        "titulo": titulo,
        "sub": f"Venda residencial · {escrito} · FipeZap",
        "abertura_falada": f"{falado_abre}. {falado.capitalize()}.",
        "negativos": negativos,
        # o fecho nasce aqui porque aqui estao as duas coisas de que ele
        # precisa: os dados (empate, posicao) e o periodo em palavras
        "fecho": frase_de_fecho(sel, f"em {escrito}"),
        "fecho_falado": frase_de_fecho(sel, f"em {falado}"),
    }


# ---------------------------------------------------------------------------
# Narracao: o roteiro e escrito por codigo, e a linha do tempo segue o audio
# ---------------------------------------------------------------------------

GAP_S = 0.55            # respiro entre falas

# O quadro vazio no ar ANTES da primeira palavra.
#
# Tinha dois motivos para existir e eu so tinha pensado num. O primeiro: a
# promessa (dez posicoes numeradas e vazias) precisa de um beat na tela antes
# de a narracao comecar, senao o espectador ouve antes de entender o que ve.
#
# O segundo apareceu quando conferir_legendas_no_video() reprovou o proprio
# reel: a checagem compara a tinta da faixa num instante VAZIO com a tinta no
# meio de cada fala, e com a primeira fala comecando em 0,0s nao existia
# instante vazio -- a referencia ja vinha com a legenda entrando. O conserto
# certo nao era afrouxar a comparacao, era fazer o instante vazio existir.
SEG_LEAD = 0.8
SEG_MIN_POR_ITEM = 3.2  # piso: revelacao que pisca nao da para ler
SEG_CAUDA = 1.2         # silencio no fim, para nao cortar seco


def roteiro(sel: dict, cab: dict) -> list[dict]:
    """As falas, na ORDEM EM QUE SAO REVELADAS (nao na ordem do ranking).

    Escrito por codigo, pelo mesmo motivo do fecho: quem sabe a posicao e o
    empate e quem leu os facts. O modelo nao entra aqui.
    """
    import narracao as nar

    n = len(sel["itens"])
    # A abertura FALA a frase completa (o que, e de que periodo) e MOSTRA o
    # titulo curto. O titulo ja esta no cabecalho persistente; repeti-lo
    # inteiro na legenda seria a mesma frase duas vezes na tela.
    falas = [{"papel": "abertura", "pos": None,
              "texto": cab["abertura_falada"],
              "legenda": cab["sub"]}]

    # posicao n primeiro, posicao 1 por ultimo -- ver a nota sobre revelacao
    # reversa em montar_html()
    for pos in range(n, 0, -1):
        item = sel["itens"][pos - 1]
        falas.append({
            "papel": "item", "pos": pos,
            "texto": (f"{nar.ordinal_falado(pos).capitalize()}: "
                      f"{item['label']}, {nar.pct_falado(item['valor'])}."),
            "legenda": f"{pos}. {item['label']}  {_pt(item['valor'])}",
        })

    falas.append({"papel": "fecho", "pos": None,
                  "texto": cab["fecho_falado"], "legenda": cab["fecho"]})
    return falas


def sintetizar_roteiro(falas: list[dict], destino: Path, voz) -> None:
    """Grava um WAV por fala e anota a duracao MEDIDA em cada uma."""
    import narracao as nar

    destino.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(falas):
        wav = destino / f"fala-{i:02d}-{f['papel']}.wav"
        f["wav"] = wav
        f["dur"] = nar.falar(f["texto"], wav, voz)


def linha_do_tempo_falada(falas: list[dict]) -> float:
    """Anota inicio e fim de cada fala. A duracao do video sai do audio.

    DESVIO DO OBSERVADO, anotado. Nos reels de referencia cada item ocupava
    ~8s porque duas pessoas conversavam em cima dele. A narracao daqui e
    seca: enunciar posicao, cidade e valor leva ~4s. Manter 8s fixos
    devolveria os mesmos vaos mortos que eu acabei de tirar da versao muda.
    Entao o slot segue a fala, com um piso para a linha nao piscar.
    """
    t = SEG_LEAD
    for f in falas:
        f["ini_s"] = t
        dur = f["dur"] + GAP_S
        if f["papel"] == "item":
            dur = max(dur, SEG_MIN_POR_ITEM)
        f["fim_s"] = t + dur
        t = f["fim_s"]
    return t + SEG_CAUDA


def montar_audio(falas: list[dict], total_s: float, destino: Path) -> Path:
    """Um WAV do tamanho do video, com cada fala no seu instante exato.

    Montado amostra por amostra em vez de com filtro de atraso do ffmpeg:
    aqui o deslocamento e aritmetica inteira sobre a taxa, entao a fala comeca
    no frame que a legenda entra -- por construcao, nao por aproximacao.
    """
    import narracao as nar

    taxa = nar.TAXA
    total_frames = int(round(total_s * taxa))
    trilha = bytearray(total_frames * 2)   # 16 bits mono, zerado = silencio

    for f in falas:
        with wave.open(str(f["wav"]), "rb") as w:
            dados = w.readframes(w.getnframes())
        ini = int(round(f["ini_s"] * taxa)) * 2
        fim = min(ini + len(dados), len(trilha))
        if fim <= ini:
            continue
        trilha[ini:fim] = dados[:fim - ini]

    alvo = destino / "narracao.wav"
    with wave.open(str(alvo), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(taxa)
        w.writeframes(bytes(trilha))
    return alvo


def juntar_audio(mp4: Path, wav: Path, total_s: float) -> Path:
    """Troca a faixa silenciosa do MP4 pela narracao."""
    import subprocess
    saida = mp4.with_name("reel-narrado.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-i", str(wav),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
         "-shortest", "-movflags", "+faststart", str(saida)],
        check=True)
    saida.replace(mp4)
    return mp4


def conferir_altura(html: str) -> dict:
    """Mede no navegador ANTES de gravar, e falha se algo cair fora da tela.

    Ajustar as medidas do CSS conserta a ocorrencia, nao a classe. Uma frase
    de legenda mais longa, um nome de capital maior ou a fonte caindo para o
    fallback voltam a empurrar conteudo para fora dos 1920px -- e a perda e
    invisivel: o video sai, o job fica verde, e a legenda simplesmente nao
    esta la. Foi assim que este bug apareceu, e so porque eu olhei a folha
    de frames.

    Entao a medida vira porteiro. Melhor um job vermelho que um reel mudo.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": W, "height": H},
                           device_scale_factor=1)
        pag.set_content(html, wait_until="networkidle")
        m = pag.evaluate("""() => {
            const r = el => { if (!el) return null;
                const b = el.getBoundingClientRect();
                return {topo: b.top, base: b.bottom, alt: b.height}; };
            const legs = [...document.querySelectorAll('.legenda')].map(el => {
                const b = el.getBoundingClientRect();
                return {base: b.bottom, alt: b.height,
                        txt: el.textContent.slice(0, 40)};
            });
            const cab = document.querySelector('.cabeca');
            return {
                rolagem: document.documentElement.scrollHeight,
                cabeca: cab ? {alt: cab.getBoundingClientRect().height,
                               conteudo: cab.scrollHeight} : null,
                quadro: r(document.querySelector('.quadro')),
                faixa: r(document.querySelector('.faixa')),
                rodape: r(document.querySelector('.rodape')),
                legendas: legs,
            };
        }""")
        nav.close()

    problemas: list[str] = []
    if m["rolagem"] > H + 2:
        problemas.append(f"a pagina rola: scrollHeight {m['rolagem']} > {H}")

    # O cabecalho tem altura fixa e `overflow: hidden` -- entao um titulo
    # longo demais seria CORTADO sem nada reclamar. A comparacao entre altura
    # da caixa e altura do conteudo e o que transforma isso em erro.
    cb = m.get("cabeca")
    if cb and cb["conteudo"] > cb["alt"] + 2:
        problemas.append(
            f"o titulo nao cabe no cabecalho: precisa de {cb['conteudo']:.0f}px "
            f"e a caixa tem {cb['alt']:.0f}px — sairia cortado")

    q, fx, rod = m["quadro"], m["faixa"], m["rodape"]
    if q and fx and q["base"] > fx["topo"] + 2:
        problemas.append(
            f"o quadro termina em {q['base']:.0f}px e invade a faixa de "
            f"legenda (topo {fx['topo']:.0f}px)")
    if fx and rod and fx["base"] > rod["topo"] + 2:
        problemas.append(
            f"a faixa de legenda termina em {fx['base']:.0f}px e invade o "
            f"rodape (topo {rod['topo']:.0f}px)")

    # Cada legenda, uma por uma: a mais comprida e que decide, e ela nao e
    # necessariamente a ultima.
    for g in m["legendas"]:
        if g["alt"] < 10:
            problemas.append(f"legenda com altura ~0: {g['txt']!r}")
        elif fx and g["base"] > fx["topo"] + fx["alt"] + 2:
            problemas.append(
                f"legenda passa da faixa ({g['base']:.0f}px): {g['txt']!r}")

    if problemas:
        for x in problemas:
            print(f"::error::{x}")
        raise SystemExit("layout estourou — nada gravado")

    mais_alta = max((g["alt"] for g in m["legendas"]), default=0)
    print(f"layout ok: quadro termina em {q['base']:.0f}px, faixa "
          f"{fx['topo']:.0f}-{fx['base']:.0f}px, legenda mais alta "
          f"{mais_alta:.0f}px de {fx['alt']:.0f}px")
    return m


def _tinta(mp4: Path, t: float, topo: int, altura: int) -> float:
    """Desvio padrao da luminancia numa faixa do frame em `t`.

    Faixa vazia e quase uniforme (desvio baixo); faixa com texto tem letra
    clara sobre fundo escuro (desvio alto). Sem Pillow: o ffmpeg escreve um
    PGM binario e o cabecalho P5 e tres campos de texto antes dos bytes.
    """
    import subprocess
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(mp4),
         "-frames:v", "1", "-vf", f"crop={W}:{altura}:0:{topo},format=gray",
         "-f", "image2pipe", "-vcodec", "pgm", "-"],
        capture_output=True, check=True).stdout

    partes, i = [], 0
    while len(partes) < 4 and i < len(out):      # P5, largura, altura, maxval
        while i < len(out) and out[i:i + 1].isspace():
            i += 1
        if out[i:i + 1] == b"#":
            while i < len(out) and out[i:i + 1] != b"\n":
                i += 1
            continue
        j = i
        while j < len(out) and not out[j:j + 1].isspace():
            j += 1
        partes.append(out[i:j]); i = j
    i += 1
    px = out[i:]
    if not px:
        raise SystemExit(f"nao consegui ler a faixa do frame em {t:.1f}s")
    n = len(px)
    media = sum(px) / n
    return (sum((p - media) ** 2 for p in px) / n) ** 0.5


def conferir_legendas_no_video(mp4: Path, falas: list[dict],
                               total_s: float) -> None:
    """As legendas estao LEGIVEIS no video, nao so no DOM.

    Esta checagem existe porque a de altura nao bastou. O layout media 1785px
    de 1920 e passava; o video saia com a frase aparecendo a 85s de 86,2s --
    um segundo e meio de leitura para tres linhas. O DOM dizia que cabia, e
    cabia. O que faltava era TEMPO, e nenhuma medida de altura ve tempo.

    Entao mede-se no artefato: no meio da fala a faixa tem de ter tinta; no
    vao entre a abertura e a primeira revelacao, nao. Amostra a primeira, uma
    do meio e a ultima -- checar as treze dobraria o tempo de verificacao sem
    cobrir nenhuma classe nova de erro.
    """
    alvos = [falas[0], falas[len(falas) // 2], falas[-1]]

    # A referencia sai do meio do lead-in, onde a faixa esta vazia POR
    # CONSTRUCAO: a primeira fala so comeca em SEG_LEAD. Ver a nota lá.
    vazio = _tinta(mp4, SEG_LEAD / 2, TOPO_FAIXA, ALT_FAIXA)
    print(f"tinta na faixa: {vazio:.2f} em {SEG_LEAD/2:.1f}s "
          f"(faixa vazia, por construcao)")
    if vazio > 8.0:
        raise SystemExit(
            f"a faixa de legenda deveria estar vazia em {SEG_LEAD/2:.1f}s e "
            f"tem tinta {vazio:.2f} — a referencia da checagem nao vale, "
            f"entao a checagem tambem nao")

    for f in alvos:
        meio = min(f["ini_s"] + f["dur"] * 0.5 + 0.3, total_s - 0.4)
        t = _tinta(mp4, meio, TOPO_FAIXA, ALT_FAIXA)
        rotulo = f.get("legenda") or f["texto"]
        print(f"  {meio:6.1f}s  tinta {t:6.2f}  {rotulo[:44]!r}")
        if t < vazio + 6.0:
            print(f"::error::a legenda de {f['papel']} nao aparece no video "
                  f"em {meio:.1f}s (tinta {t:.2f} contra {vazio:.2f} de faixa vazia)")
            raise SystemExit("reel gravado sem legenda — nao publicar")


def _sinalizar(tem_reel: bool) -> None:
    """Anota em GITHUB_OUTPUT se houve reel. Mesmo padrao do arte.py.

    Sem isso o passo de publicacao rodaria mesmo nas rodadas em que nao ha
    horizonte livre -- e o publicar.py pegaria "a ultima pasta", que seria a
    do post anterior. Republicar por acidente e pior que nao publicar.
    """
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"tem_reel={'true' if tem_reel else 'false'}\n")


def main() -> int:
    import narracao as nar

    if len(sys.argv) < 3:
        raise SystemExit(
            "uso: python quadro.py <facts.json> <marca> "
            "[--horizonte ano|mensal|doze_meses] [--so-selecao] "
            "[--permitir-voz-de-teste] [--voz piper|gtts|espeak]\n"
            "\nO sinal tem_reel vai para GITHUB_OUTPUT quando a variavel "
            "existe — nao ha flag para isso.")

    facts_path = Path(sys.argv[1])
    slug = sys.argv[2]
    horizonte = None
    if "--horizonte" in sys.argv:
        horizonte = sys.argv[sys.argv.index("--horizonte") + 1]
    # O workflow passa string VAZIA quando o usuario nao escolheu horizonte no
    # dispatch. Tratar "" como "nao informado" aqui evita que o YAML precise
    # de condicional -- e YAML com condicional foi de onde saiu metade dos
    # bugs deste repositorio.
    if "--horizonte-opcional" in sys.argv:
        v = sys.argv[sys.argv.index("--horizonte-opcional") + 1].strip()
        horizonte = v or None
    voz_pedida = None
    if "--voz" in sys.argv:
        voz_pedida = sys.argv[sys.argv.index("--voz") + 1]

    erros = validar_template(TEMPLATE_T1)
    if erros:
        for e in erros:
            print(f"::error::{e}")
        raise SystemExit("template reprovado — substancia da fonte vazou para o esqueleto")
    print(f"template {TEMPLATE_T1['id']}: esqueleto limpo")

    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    marca = json.loads((RAIZ / "brands" / f"{slug}.json").read_text(encoding="utf-8"))

    localidade = marca.get("localidade") or "Belo Horizonte"

    # QUAL HORIZONTE. Com cadencia de 6 horas isso nao pode ser escolha na
    # mao: sem guarda, o cron publicaria o MESMO reel quatro vezes por dia.
    # A guarda ja existe -- `guarda.ja_publicado` casa (periodo, hash_pdf,
    # angulo) contra o historico -- e o angulo do T1 carrega o horizonte.
    import guarda

    ordem = ["mensal", "ano", "doze_meses"]
    if horizonte:
        candidatos = [horizonte]
    else:
        candidatos = ordem

    horizonte = None
    for h in candidatos:
        anterior = guarda.ja_publicado(facts, slug, f"t1:{h}")
        if anterior:
            print(f"t1:{h} — ja publicado em {anterior.get('data', '?')}")
            continue
        horizonte = h
        break

    if horizonte is None:
        # Nada a publicar NAO e falha. O informe e mensal e o T1 rende tres
        # posts por informe; a maior parte do tempo a rodada de 6h nao tem o
        # que dizer, e inventar seria pior.
        print("\nnenhum horizonte do T1 disponivel para este informe — "
              "nada a publicar.")
        _sinalizar(False)
        return 0

    sel = selecionar(facts, horizonte, localidade)
    cab = titulo_e_sub(sel, facts)
    print(f"\ntitulo:    {cab['titulo']}")
    print(f"subtitulo: {cab['sub']}")
    print(f"abertura falada: {cab['abertura_falada']}")

    print(f"\nhorizonte: {horizonte} (indice geral {_pt(sel['indice_geral_pct'])})")
    print(f"janela de {len(sel['itens'])} entre {sel['total_capitais']} capitais, "
          f"crescente:")
    for k, it in enumerate(sel["itens"], start=1):
        marcador = "  <-- a marca" if k == sel["posicao_na_janela"] else ""
        print(f"  {k:2d}. {it['label']:<18} {_pt(it['valor']):>8}{marcador}")
    if sel["empatadas_com_a_marca"]:
        print(f"\nEMPATE no valor da marca: {sel['empatadas_com_a_marca']}")
        print("  -> o fecho nao pode afirmar posicao unica.")
    print(f"fecho (escrito por codigo): {cab['fecho']}")

    if "--so-selecao" in sys.argv:
        return 0

    voz = nar.escolher_voz(voz_pedida)
    print(f"\nvoz: {voz.nome}  producao={voz.producao}  ({voz.detalhe})")
    if not voz.producao and "--permitir-voz-de-teste" not in sys.argv:
        nar.ok_para_publicar(voz)
    if not voz.producao:
        print("::warning::gravando com VOZ DE TESTE — serve para conferir "
              "sincronia, nao para publicar")

    destino = RAIZ / "out" / slug / f"{date.today().isoformat()}-t1-{horizonte}"
    destino.mkdir(parents=True, exist_ok=True)

    falas = roteiro(sel, cab)
    sintetizar_roteiro(falas, destino / "_falas", voz)
    total = linha_do_tempo_falada(falas)

    itens = [f for f in falas if f["papel"] == "item"]
    slot_marca = next(i for i, f in enumerate(itens, start=1)
                      if f["pos"] == sel["posicao_na_janela"])
    ultima = itens[-1]
    print(f"\nordem de revelacao: reversa (posicao {len(itens)} primeiro, "
          f"posicao 1 por ultimo)")
    print(f"  {localidade} e revelada na {slot_marca}a de {len(itens)} "
          f"revelacoes; a ultima e {sel['itens'][ultima['pos'] - 1]['label']}")
    if slot_marca <= len(itens) / 2:
        print(f"::warning::a marca aparece na primeira metade — o pagamento "
              f"final do quadro vai ser "
              f"{sel['itens'][ultima['pos'] - 1]['label']}, nao {localidade}. "
              f"Num perfil de {localidade} isso enfraquece o fecho; considere "
              f"outro horizonte.")

    print(f"\nroteiro ({len(falas)} falas, {total:.1f}s):")
    for f in falas:
        print(f"  {f['ini_s']:6.1f}s +{f['dur']:4.1f}s  {f['papel']:<8} "
              f"{f['texto'][:52]}")

    html = montar_html(sel, marca, falas, total,
                       gancho=cab["titulo"], gancho_sub=cab["sub"],
                       selo_ia="Conteúdo produzido com apoio de IA")
    conferir_altura(html)

    from reel import gravar
    mp4, capa = gravar(html, total, destino)
    wav = montar_audio(falas, total, destino)
    juntar_audio(mp4, wav, total)
    print(f"\n{mp4}  {mp4.stat().st_size/1_000_000:.1f} MB")

    conferir_legendas_no_video(mp4, falas, total)
    conferir_audio_no_video(mp4, total)
    print("legenda e audio conferidos no video.")

    # O manifest e o contrato com publicar.py e guarda.py. `angulo` carrega o
    # horizonte -- e por isso que a guarda de repeticao funciona de graca:
    # (periodo, hash_pdf, "t1:ano") e uma assinatura distinta de
    # (periodo, hash_pdf, "t1:mensal").
    #
    # periodo e hash_pdf vem dos FACTS, atribuidos aqui. Nao ha modelo no
    # caminho do T1 para sugerir procedencia, mas a regra e a mesma do
    # compose.py: quem carimba procedencia e o pipeline.
    legenda_post = (
        f"{cab['titulo']} — {cab['sub']}.\n\n"
        f"{cab['fecho']}\n\n"
        f"Fonte: {facts.get('fonte')}. "
        f"Conteúdo produzido com apoio de IA.\n\n"
        f"#imoveis #belohorizonte #mercadoimobiliario #fipezap #dados"
    )
    (destino / "manifest.json").write_text(json.dumps({
        "marca": marca["slug"],
        "data": date.today().isoformat(),
        "formato": "reel",
        "legenda": legenda_post,
        "arquivos": [mp4.name, capa.name],
        "video": mp4.name,
        "capa": capa.name,
        "duracao_s": round(total, 1),
        "periodo": facts.get("periodo"),
        "hash_pdf": facts.get("hash_pdf"),
        "angulo": f"t1:{horizonte}",
        "voz": voz.nome,
        "gerado_em": date.today().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: angulo t1:{horizonte}, voz {voz.nome}")

    _sinalizar(True)
    return 0


def conferir_audio_no_video(mp4: Path, total_s: float) -> None:
    """O MP4 tem faixa de audio, com a duracao certa e nao muda.

    O `gravar()` do reel.py injeta uma faixa SILENCIOSA de proposito (Reel sem
    faixa nenhuma falha em parte dos aparelhos). Ou seja: se o `juntar_audio`
    falhasse, o arquivo continuaria tendo audio -- silencio -- e nada
    pareceria errado. Um reel "narrado" que sai mudo e exatamente o tipo de
    falha que este projeto ja cansou de levar.
    """
    import subprocess
    infos = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,duration",
         "-of", "default=nw=1:nk=1", str(mp4)],
        capture_output=True, text=True, check=True).stdout.split()
    if not infos:
        raise SystemExit("o MP4 saiu sem faixa de audio")
    # `duration` pode vir como "N/A" ou nem vir, dependendo do container.
    # Indexar infos[1] as cegas trocaria "audio sem duracao declarada" por
    # um IndexError sem relacao aparente com audio.
    codec = infos[0]
    dur_txt = infos[1] if len(infos) > 1 else "?"

    # Nivel medio: silencio digital da -91 dB; fala fica bem acima disso.
    saida = subprocess.run(
        # SEM -v error aqui de proposito: o volumedetect escreve a medida em
        # nivel `info`, e silenciar o ffmpeg silenciava justamente o numero
        # que esta checagem existe para ler. A primeira versao imprimia
        # "nivel medio None dB" e caia -- ruim por acidente, nao por defeito.
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(mp4),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    media = None
    for linha in saida.splitlines():
        if "mean_volume" in linha:
            media = float(linha.split(":")[1].strip().split()[0])
    print(f"audio: {codec}, duracao {dur_txt}s, nivel medio {media} dB")
    if media is None:
        raise SystemExit("nao consegui medir o nivel do audio")
    if media < -60.0:
        raise SystemExit(
            f"o audio do MP4 esta praticamente mudo ({media} dB) — "
            f"a narracao nao entrou")


if __name__ == "__main__":
    sys.exit(main())
