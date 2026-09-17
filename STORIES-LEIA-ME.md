# Stories EstiloVidya — sequências (versão 2)

## Operação
- Até **8 sequências por dia**, com 1 a 10 imagens JPEG RGB 1080×1920 em cada uma.
- Uma sequência por execução. Imagens enviadas individualmente em ordem; cada uma aparece como um Story do Instagram.
- Horários de Brasília: **08h15, 10h15, 12h15, 14h15, 16h15, 18h15, 20h15 e 22h15**. GitHub Actions pode atrasar.
- O limite conta sequências iniciadas no dia de Brasília, não o número de imagens. Uma sequência iniciada antes da meia-noite pertence àquele dia.
- Dez imagens é um limite operacional desta implementação, não uma afirmação sobre o limite do Instagram. A cota real da Meta é consultada antes do envio, com margem de cinco publicações.
- Todas as imagens e seus hashes públicos são conferidos antes do primeiro envio. Hash do conjunto também protege a ordem aprovada.
- A versão anterior (arte.jpg + story.json) continua funcionando e seus recibos são preservados.
- Uma tentativa parcial/incerta bloqueia toda a fila. Não há repetição automática de POST nem retomada automática das imagens restantes.
- Não gera novas peças automaticamente. Somente manifestos aprovados são processados. Não inclui música, links clicáveis, vídeo ou stickers.

## Preparar uma sequência
Na raiz do Postador:

    python preparar_story.py EV-S002 primeira.jpg segunda.jpg terceira.jpg --after 2026-09-18T08:00:00-03:00 --before 2026-09-18T09:30:00-03:00

A ordem dos argumentos define 01.jpg, 02.jpg, 03.jpg e a ordem de publicação. O manifesto nasce com approved=false. Revisar as imagens e preencher approved, approved_by e approved_at conforme autorização real. Não alterar imagem, ordem ou hash depois da aprovação; gerar novo ID para uma nova versão.

Use janelas de elegibilidade por sequência para distribuir o conteúdo ao longo do dia. Até oito é um teto: sem oito sequências aprovadas e elegíveis não haverá oito envios. Não acumula compensação automática de dias sem conteúdo.

## Verificação e disparo

    python -m unittest discover -s tests_stories -v
    python stories.py

O workflow Stories EstiloVidya, com publish=false, executa testes e valida a fila sem publicar. Com publish=true e enabled=true, publica no máximo uma sequência elegível. Usa exclusivamente IG_USER_ID_ESTILOVIDYA e IG_ACCESS_TOKEN_ESTILOVIDYA.

## Recibos e atualização
`estado/stories-estilovidya.json` mantém um registro por sequência e um recibo por imagem dentro de frames. O recibo antigo EV-S001 deve permanecer intacto. **Nunca enviar o arquivo vazio do pacote inicial sobre o estado remoto.**

Atualizações desta versão alteram somente stories.py, preparar_story.py, este manual, config/stories-estilovidya.json, tests_stories/test_sequences.py e .github/workflows/publicar-stories.yml. Não alteram a fila, os recibos, Reels ou outras marcas.

Se houver falha, consultar o estado remoto e o artifact antes de repetir. Cada imagem em creating/processing/publishing pode exigir conciliação na Meta. Mesmo quando todas as imagens aparecem publicadas e falta apenas finalizar a sequência, conciliar o registro sob supervisão; não apagar o recibo para destravar.

## Evidência anterior
EV-S001 foi publicado em 16/09/2026 às 23h18 Brasília, media_id 17950140543257236, e conferido visualmente. Execução: https://github.com/guilhermesa88-ai/postador/actions/runs/35173881616.
