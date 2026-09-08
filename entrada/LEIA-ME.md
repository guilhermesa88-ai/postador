# entrada/ — a fila de arte pronta

Uma pasta por post, dentro de `entrada/<marca>/`. O nome da pasta é a
identidade do post: use algo como `2026-09-10-camisa-proposito`.

```
entrada/estilovidya/2026-09-10-camisa-proposito/
    legenda.txt      obrigatório
    01.jpg           1 imagem = post simples; 2 a 10 = carrossel
    02.jpg           a ORDEM do carrossel é a ordem alfabética dos nomes
```

```
entrada/lentesvidya/2026-09-12-como-ler-a-receita/
    legenda.txt
    video.mp4        1 mp4 = Reel
    capa.jpg         opcional, vira a capa do Reel
```

**O formato não é declarado** — sai do que está na pasta. Menos um campo para
preencher errado.

## O que é conferido antes de publicar

Aqui, não como erro cru da API depois do upload:

- JPEG que é mesmo JPEG (PNG renomeado para `.jpg` é o erro mais comum)
- proporção entre 4:5 e 1.91:1 no feed (a capa do Reel é exceção)
- carrossel de 2 a 10 imagens
- legenda até 2200 caracteres, até 30 hashtags
- imagem até 8 MB, vídeo até 1 GB
- Reel entre 3 s e 15 min

Arte inválida deixa o job **vermelho**. Fila vazia deixa **verde** — não ter o
que publicar não é falha.

## Como publicar

Actions → **Publicar arte** → escolha a marca → Run workflow.
Ele pega a pasta mais antiga que ainda não virou post, valida, hospeda a mídia,
publica e registra em `estado/<marca>.json`. A partir daí o coletor diário de
métricas acompanha o post sozinho.

`dry_run` valida e sobe a mídia sem publicar.

## O selo de IA

Não é carimbado automaticamente. Se a arte é sua, dizer que foi produzida com
apoio de IA seria declarar algo falso. Se for gerada por IA, escreva o selo na
própria legenda.
