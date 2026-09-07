# render — brief em JSON → carrossel pronto para publicar

```bash
pip install jinja2 playwright
playwright install chromium

python render.py briefs/exemplo-bh.json
```

Saída em `out/<marca>/<data>/`: os slides numerados em JPEG 1080×1350 e um
`manifest.json` com a legenda e a ordem dos arquivos — que é exatamente o que
o publicador consome.

## Estrutura

```
brands/<marca>.json      cores e tipografia do perfil
templates/slide.html.j2  um template, cinco tipos de slide
briefs/<pauta>.json      o conteúdo de um post
render.py                brief -> JPEGs + manifest
out/                     saída
```

**Adicionar o perfil nº 12 é criar um arquivo em `brands/`.** Se exigir mexer
no template ou no `render.py`, a separação foi mal feita.

## Tipos de slide

| tipo | uso |
|---|---|
| `capa` | gancho + subtítulo + número de destaque |
| `grafico` | barras divergentes com rótulo direto em cada valor |
| `lista` | itens numerados |
| `texto` | um bloco de leitura |
| `fechamento` | CTA |

## Decisões que não são arbitrárias

**HTML + Playwright, não Pillow.** Layout de verdade — quebra de linha,
kerning, flexbox. O template vira arquivo versionado e trocar a identidade de
um perfil é editar CSS. Cada slide leva ~200ms.

**JPEG direto, não PNG convertido.** A API do Instagram só aceita JPEG para
imagem; converter depois é um passo a mais para errar.

**Gráfico em SVG inline, gerado em Python.** Nada de screenshot de matplotlib:
o SVG herda as cores da marca e as fontes da página.

**Escala.** O canvas tem 1080px mas é visto a ~400px no feed — escala visual
de ~2,7×. Por isso a barra de 52px aqui equivale a uma marca fina de ~19px na
tela, e os corpos de texto parecem enormes no arquivo e corretos no celular.

**Todo valor tem rótulo direto.** Em dashboard a regra é rotular
seletivamente, porque o hover cobre o resto. Numa imagem estática não existe
hover: valor sem rótulo é valor inacessível.

**Paleta divergente validada.** Azul `#3987e5` para alta, vermelho `#e66767`
para queda, sobre a superfície `#141518` — o par passa em todos os testes de
separação para daltonismo e de contraste. A cor nunca é o único canal: o sinal
aparece no rótulo (`+3,8%` / `−2,3%`) e a legenda nomeia os dois lados.

**Rótulo de valor com espaço reservado de cada lado.** Sem isso o número da
barra negativa colide com o nome do bairro — aconteceu na primeira versão.

## compose — facts → brief

```bash
export ANTHROPIC_API_KEY=...
python compose.py facts/exemplo-bh.json metro-bh
python compose.py facts/exemplo-bh.json metro-bh --dry-run   # sem chamar o modelo
```

O modelo escreve; **os validadores decidem**. Se o brief for reprovado, o erro
volta para o modelo corrigir, até 3 vezes. Se não passar, nada é gravado — o
perfil fica um ciclo sem post, de propósito.

### A checagem que sustenta o projeto: ancoragem numérica

Todo número que aparece no texto tem que existir nos `facts` de origem. Num
perfil de dado a proposta inteira é "o número é verificável" — um único número
inventado destrói isso de um jeito que nenhuma cadência conserta.

O validador extrai os números do texto publicado, normaliza (`3,8` / `3.8` /
`14.820` / `14 820`) e confere contra tudo que existe nos facts, com tolerância
para arredondamento honesto. Anos e números triviais passam direto.

O modelo pode escolher o ângulo, a ordem, as palavras e **quais** números
destacar. Não pode produzir número que não esteja nos facts, usar expressão
banida da marca, nem fugir do schema.

```bash
python test_validators.py
```

Vinte e três casos, incluindo os adversariais: número inventado no slide,
preço inventado na legenda, expressão de vendedor no CTA, título que estoura o
slide, carrossel sem capa.

## A cadeia completa

```
facts/exemplo-bh.json      dado bruto da fonte
        ↓  compose.py      LLM escreve, validadores aprovam
briefs/<marca>-<data>.json
        ↓  render.py       HTML -> Playwright -> JPEG
out/<marca>/<data>/        01.jpg ... 05.jpg + manifest.json
        ↓  ig_publisher.py container -> FINISHED -> publish
```

## Próximos passos

- `ingest`: alimentar `facts/` a partir da fonte real, com dedup por hash
- ligar o `manifest.json` ao `ig_publisher.py`
- reaproveitar os mesmos slides como Reels via `ffmpeg`
- fila de revisão humana entre o brief aprovado e a publicação
