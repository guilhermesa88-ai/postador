# Stories automáticos — EstiloVidya

## Situação desta entrega
Implementação local, ainda não instalada no GitHub e ainda sem publicação real.
O pacote acrescenta arquivos ao Postador existente; não substitui os publicadores
de Reels/feed, nem as filas de outras marcas. A configuração começa desativada e
a fila está vazia. Não há automação rodando localmente ou em segundo plano.

## Comportamento
- JPEG RGB, 1080 × 1920, até 8 MB, para as novas artes de Stories.
- Até um Story por execução e três por dia, em horário de Brasília.
- Workflow preparado para 09:15, 14:15 e 19:15. GitHub Actions pode atrasar.
- Só processa manifestos aprovados, dentro da janela definida.
- Valida o hash da arte local e da cópia pública imutável antes do envio.
- Usa exclusivamente IG_USER_ID_ESTILOVIDYA e IG_ACCESS_TOKEN_ESTILOVIDYA.
- Confere @estilovidya e conta Business; falha se a cota não puder ser validada.
- Publica com media_type=STORIES. Não vira imagem no feed.
- Mantém recibos por ID, conteúdo e etapa. Uma tentativa incerta bloqueia a fila.
- Não repete POST após timeout e não apaga automaticamente a mídia da fila.
- Conteúdo repetido (mesmo hash) não é republicado.
- Esta versão não inclui vídeo, música da biblioteca, link clicável ou stickers
  interativos. Texto e elementos visuais devem estar incorporados no JPEG.

## Instalação
Copiar o conteúdo deste pacote para a raiz de guilhermesa88-ai/postador,
preservando os arquivos existentes, revisar o diff e enviar para main.
Nunca sobrescrever um estado/stories-estilovidya.json já usado: esse arquivo
contém recibos e evita duplicatas. A configuração de enabled começa false.

Os secrets já usados pela marca devem estar disponíveis no workflow. Não inserir
tokens no código, no manifesto ou na documentação. O token precisa permitir
publicação e a conta precisa ser Business. A documentação da Meta registra essa
restrição: https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login

Testar antes de ativar:
    pip install -r requirements-stories.txt
    python -m unittest discover -s tests_stories -v
    python stories.py

## Preparar uma arte
    python preparar_story.py EV-S001 caminho/arte.jpg --after 2026-09-18T09:00:00-03:00 --before 2026-09-19T20:00:00-03:00

Isso cria entrada-stories/estilovidya/EV-S001/arte.jpg e story.json.
O manifesto nasce SEM aprovação. Após revisão/autorização real, preencher:
- approved: true
- approved_by: nome da pessoa que autorizou
- approved_at: data/hora ISO com fuso da aprovação

Não mudar uma arte já aprovada silenciosamente. Criar nova versão/ID quando
houver alteração e fazer nova revisão. O hash corresponde exatamente ao JPEG.

## Primeiro teste real e ativação
1. Instalar os arquivos e uma única arte de teste autorizada na fila.
2. Rodar manualmente Stories EstiloVidya com publish=false para validar.
3. Com a arte de teste como único item elegível, definir enabled=true e rodar
   manualmente com publish=true. A autorização de implementação e teste de
   Guilherme já existe; não é necessária nova autorização para esse mesmo teste.
4. Verificar o media_id no recibo e conferir o Story no Instagram. O retorno da API
   não substitui a revisão visual final.
5. Só alimentar os próximos itens depois de confirmar o piloto. Os três horários
   passam a consumir a fila aprovada. Para pausar, definir enabled=false.

## Falhas e conciliação
Estados creating, processing ou publishing significam tentativa que exige
conciliação; o próximo disparo falha sem fazer novo POST. Consultar primeiro o
recibo em estado/stories-estilovidya.json e o artifact da execução que falhou.
Se o push final falhar, o artifact pode conter media_id ausente no repositório.

Conferir container_id na Meta e procurar a publicação na conta. Se publicada,
completar status=published, media_id e published_at com evidência real.
Não apagar registros ou reencaminhar o mesmo conteúdo para destravar a fila.
Se houver falha comprovada sem publicação, documentar a investigação e fazer
recuperação supervisionada; não há comando automático de reenvio.

O workflow usa uma trava exclusiva para Stories. Os recibos são enviados ao
GitHub antes da criação e antes da publicação; conflito ou falha de sincronização
interrompe o fluxo. O estado pendente remoto impede repetição mesmo se o processo
for encerrado depois de publicar e antes de gravar o resultado.

## Limites do teste local
Testes cobrem seleção, janela, aprovação, integridade, limites, corpo STORIES,
erros da API simulados, prevenção de repetição e persistência com Git real local.
Não comprovam token, permissões, alcance público aceito pela Meta, execução do
Actions nem postagem na conta. Isso depende do piloto remoto acima.
