# Setup no GitHub — o que falta

O pipeline vai viver no repositório que você já tem: **`guilhermesa88-ai/postador`**.
Ele já serve a política de privacidade pelo Pages; passa a servir também a
mídia dos posts e a rodar o cron.

---

## 1. Subir os arquivos

Descompacte o zip e, em <https://github.com/guilhermesa88-ai/postador/upload/main>,
**arraste a pasta inteira** para a área de upload. O GitHub preserva a
estrutura de diretórios em arrastar-e-soltar (não preserva se você selecionar
arquivo por arquivo — arraste a pasta).

Commit direto na `main`.

> Não suba `token.json` nem `token-*.json`. Eles têm o token de 60 dias e o
> repositório é público. O `.gitignore` já bloqueia, mas confira antes.

---

## 2. Dois secrets

*Settings → Secrets and variables → Actions → aba **Secrets** → New repository secret*

| Nome | Valor |
|---|---|
| `IG_USER_ID` | `28476280395340157` |
| `IG_ACCESS_TOKEN` | o `access_token` de dentro do `token-metro_de_bh.json` |

E mais um, para o `compose` escrever:

| Nome | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | sua chave da API |

**Só três.** O `MEDIA_TOKEN` que eu tinha previsto saiu: o workflow escreve no
próprio repositório usando o token que o Actions já fornece, então não há PAT
para criar nem para rotacionar.

---

## 3. Duas variables

*Mesma tela, aba **Variables***

| Nome | Valor |
|---|---|
| `MARCA` | `metro-de-bh` |
| `FACTS_ATUAL` | `exemplo-bh.json` |

---

## 4. Testar sem publicar

*Actions → Publicar carrossel → Run workflow → marque **dry run** → Run*

Ele instala tudo, roda os testes dos validadores, compõe o brief com o modelo,
renderiza os cinco slides, sobe as imagens para o Pages e **para antes de
publicar**. Se as URLs responderem, a cadeia está inteira.

O resultado fica em *Artifacts*, no fim da página da execução: os JPEGs e o
`manifest.json`, para você olhar antes de deixar postar de verdade.

---

## 5. Ligar o cron

Se o dry run passou, rode uma vez **sem** dry run para publicar o primeiro
post de verdade. Depois disso o cron assume: segunda a sexta, 7h30 de
Brasília.

O `renovar-token.yml` roda diário e avisa quando faltarem 7 dias para o token
vencer. **Esse é o job que impede o perfil de voltar ao OAuth manual** — se o
token expirar, é senha de novo.

---

## Onde mexer depois

| Quero mudar | Arquivo |
|---|---|
| o dado que vira post | `facts/*.json` |
| a voz, as cores, a bio | `brands/metro-de-bh.json` |
| o layout dos slides | `templates/slide.html.j2` |
| o horário do post | `cron` em `.github/workflows/publicar.yml` |
| o que é proibido publicar | `validators.py` |

Perfil número dois é um arquivo novo em `brands/` — mais o onboarding da conta
descrito em `ONBOARDING-PERFIL.md`.
