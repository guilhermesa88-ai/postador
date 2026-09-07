# Onboarding de um perfil novo

Resposta direta à pergunta "basta criar o perfil no Instagram?": **não.**
O app da Meta já está configurado e vale para todos os perfis — mas o **token
é por conta**, e cada conta nova tem um pedaço que só você pode fazer.

São seis passos. Cinco são cliques; um exige senha.

---

## O que você faz uma vez por perfil

### 1. Criar a conta no Instagram
Nome, e-mail, senha. Use um e-mail que você controle — a recuperação passa por
ele. **Não crie várias contas no mesmo dia**: criação em lote é o padrão que a
Meta usa para identificar rede artificial.

### 2. Converter para Business
*Configurações → Tipo de conta → Mudar para conta profissional → Empresa.*

**Business, não Creator.** Creator publica imagem normalmente e só quebra nos
Reels — semanas depois, quando você já esqueceu que essa escolha existiu.

### 3. Dar o papel de Testador do Instagram
No painel do app **Postador 01** (`4820596794889466`), aba **Funções** →
adicionar a conta como *Testador do Instagram*.

É esse papel que faz o Standard Access valer para a conta. Sem ele o OAuth até
completa, mas a publicação falha.

### 4. Aceitar o convite dentro do Instagram
Logado na conta nova: *Configurações → Site e permissões → Convites de
testador → aceitar.*

Fácil de esquecer, e o sintoma é o mesmo do passo anterior.

### 5. Rodar o OAuth  ← **o passo que exige você**

Abra a URL de autorização logado na conta nova, autorize, copie o `code` e
rode o `1-TROCAR-TOKEN.bat`.

**Esse passo não automatiza.** O Business Login for Instagram força
autenticação nova a cada autorização — mesmo com sessão ativa no navegador. É
o desenho do fluxo, não configuração. Cada perfil custa um minuto humano aqui,
uma vez.

### 6. Guardar o token
O `token.json` gerado tem `instagram_user_id` e o token de 60 dias. Nos
GitHub Secrets do repositório, cadastre:

| Secret | Valor |
|---|---|
| `IG_USER_ID` | o `instagram_user_id` |
| `IG_ACCESS_TOKEN` | o token de 60 dias |

Com mais de um perfil, isso vira uma tabela no banco em vez de secrets soltos
— mas para o primeiro, secrets bastam.

---

## O que é automático a partir daí

Nada acima se repete. O ciclo diário roda sozinho:

```
cron → compose (LLM escreve, validadores aprovam)
     → render (JPEGs)
     → upload para o GitHub Pages
     → publicar via API
```

**Com uma condição:** o job de renovação de token precisa rodar. Enquanto o
token for renovado dentro da janela de 60 dias, nunca mais se pede senha. Se
um token expirar, aquele perfil volta ao passo 5 — manual. Por isso o
`renovar-token.yml` roda diário e avisa aos 7 dias do vencimento.

---

## Configuração do repositório (uma vez, não por perfil)

**Secrets** (Settings → Secrets and variables → Actions → Secrets):

| Secret | O que é |
|---|---|
| `ANTHROPIC_API_KEY` | para o `compose` escrever |
| `IG_USER_ID` | conta de destino |
| `IG_ACCESS_TOKEN` | token de 60 dias |
| `MEDIA_TOKEN` | PAT fine-grained com *Contents: read and write* **apenas** no repo de mídia |

**Variables** (mesma tela, aba Variables):

| Variable | Exemplo |
|---|---|
| `MARCA` | `metro-bh` |
| `FACTS_ATUAL` | `exemplo-bh.json` |
| `MEDIA_REPO` | `postador` |

Nenhum desses valores passa pelo chat nem fica no seu disco.

---

## Por que GitHub Actions e não a sua máquina

O pipeline é Python + Playwright. Rodar local exige instalar os dois e manter
o computador ligado no horário do post. No Actions:

- o cron roda com o seu computador desligado
- os segredos ficam no cofre do GitHub, não em arquivo no Desktop
- já existe repositório, e o Pages dele já serve a mídia
- é gratuito nesse volume

O `concurrency: publicar` garante que duas execuções nunca postem ao mesmo
tempo, e o `publicar.py` é idempotente: se já existe `resultado.json` com
`media_id`, ele não republica. Um cron que dispara duas vezes não gera dois
posts.

---

## Teste antes do cron

Dispare o workflow na mão com **dry run** marcado (aba Actions → Publicar
carrossel → Run workflow). Ele compõe, renderiza, sobe a mídia e para antes de
publicar. Se as URLs responderem, o caminho está inteiro.
