"""
compose.py — facts do banco -> brief JSON, com o modelo escrevendo e os
validadores decidindo se aquilo pode virar post.

    export ANTHROPIC_API_KEY=...
    python compose.py facts/exemplo-bh.json metro-bh

    # sem chamar o modelo, so para exercitar a validacao:
    python compose.py facts/exemplo-bh.json metro-bh --dry-run

Desenho: o modelo NAO tem a ultima palavra. Ele escreve, os validadores
reprovam, e o erro volta para ele corrigir -- ate `MAX_TENTATIVAS`. Se nao
passar, nada e gravado. Um post que inventa um numero custa mais caro que um
dia sem post.

O que o modelo pode e nao pode:
  - pode escolher o angulo, a ordem e as palavras
  - pode escolher QUAIS numeros dos facts destacar
  - nao pode produzir nenhum numero que nao esteja nos facts (checado)
  - nao pode usar as expressoes banidas da marca (checado)
  - nao pode fugir do formato dos slides (checado por schema)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from validators import BRIEF_SCHEMA, validar

RAIZ = Path(__file__).parent
MODELO = os.environ.get("COMPOSE_MODEL", "claude-sonnet-4-5")
MAX_TENTATIVAS = 3

INSTRUCOES = """Você escreve carrosséis para um perfil de Instagram de dados.

REGRA INVIOLÁVEL: todo número que você escrever tem que existir nos FACTS.
Você pode escolher quais destacar, arredondar para uma casa decimal e calcular
diferenças que os próprios FACTS já expõem. Não pode estimar, projetar nem
completar com conhecimento de fora. Se um número não está nos FACTS, ele não
existe.

SEGUNDA REGRA INVIOLÁVEL: não afirme exclusividade, liderança ou extremo — "a
única", "a que mais caiu", "a pior", "a primeira" — a menos que os FACTS
provem, listando ou contando. Os FACTS trazem a lista ordenada completa e os
agregados já calculados; leia antes de escrever. Uma frase assim não tem
número, então nenhum validador vai barrá-la: é você quem responde por ela.

Já aconteceu de uma capa dizer que Belo Horizonte foi "a única grande capital
com preço negativo no mês" quando eram quatro em queda — e uma delas aparecia
no gráfico do próprio post. Se não tem como conferir, escreva o dado direto:
"BH caiu 0,08% enquanto a média subiu 0,53%" diz o mesmo e é verdade.

O gancho da capa é uma leitura do dado que o leitor não teria sozinho — nunca
um adjetivo. "A média da cidade esconde dois mercados" é um gancho. "Confira
os dados incríveis do mercado" não é.

Escreva em português do Brasil, com acentuação correta.

Responda APENAS com o JSON do brief, sem cercas de código e sem comentários."""


def montar_prompt(facts: dict, marca: dict) -> str:
    voz = marca.get("voz", {})
    pauta = marca.get("pauta", {})
    return f"""{INSTRUCOES}

## PERFIL
Nome: {marca['nome']} ({marca['handle']})
Público: {pauta.get('publico', '')}
Formato: {pauta.get('formato_padrao', 'carrossel de 5 slides')}

## VOZ
Registro: {voz.get('registro', '')}
Sempre: {json.dumps(voz.get('sempre', []), ensure_ascii=False)}
Nunca: {json.dumps(voz.get('nunca', []), ensure_ascii=False)}
Expressões proibidas: {json.dumps(voz.get('banidas', []), ensure_ascii=False)}

## FACTS
{json.dumps(facts, ensure_ascii=False, indent=2)}

## SCHEMA DO BRIEF
{json.dumps(BRIEF_SCHEMA, ensure_ascii=False)}

Campos além do schema: "marca" deve ser "{marca['slug']}". A legenda fecha com
a fonte do dado e 4 a 6 hashtags. Nos textos você pode usar <strong> e <b>."""


def extrair_json(texto: str) -> dict:
    """O modelo às vezes embrulha em cerca de código mesmo pedindo para não."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.rsplit("```", 1)[0]
    ini, fim = t.find("{"), t.rfind("}")
    if ini == -1 or fim == -1:
        raise ValueError("nenhum objeto JSON na resposta do modelo")
    return json.loads(t[ini:fim + 1])


def compor(facts: dict, marca: dict, dry_run: bool = False) -> tuple[dict | None, list[str]]:
    """Devolve (brief, log). brief é None se não passou na validação."""
    log: list[str] = []
    voz = marca.get("voz", {})

    if dry_run:
        brief = json.loads((RAIZ / "briefs" / "exemplo-bh.json").read_text(encoding="utf-8"))
        r = validar(brief, facts, voz)
        log.append(f"[dry-run] validação do brief de exemplo:\n{r}")
        return (brief if r.ok else None), log

    from anthropic import Anthropic

    cliente = Anthropic()
    mensagens = [{"role": "user", "content": montar_prompt(facts, marca)}]

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        resp = cliente.messages.create(
            model=MODELO, max_tokens=4000, messages=mensagens,
        )
        bruto = resp.content[0].text

        try:
            brief = extrair_json(bruto)
        except (ValueError, json.JSONDecodeError) as e:
            log.append(f"tentativa {tentativa}: JSON inválido — {e}")
            mensagens += [
                {"role": "assistant", "content": bruto},
                {"role": "user", "content": f"Isso não é JSON válido: {e}. "
                                            "Responda apenas com o objeto JSON."},
            ]
            continue

        brief.setdefault("marca", marca["slug"])
        brief.setdefault("data", date.today().isoformat())
        brief.setdefault("selo_ia", "Conteúdo produzido com apoio de IA")

        r = validar(brief, facts, voz)
        log.append(f"tentativa {tentativa}:\n{r}")
        if r.ok:
            return brief, log

        mensagens += [
            {"role": "assistant", "content": bruto},
            {"role": "user", "content":
                "O brief foi reprovado na validação:\n"
                + "\n".join(f"- {e}" for e in r.erros)
                + "\n\nCorrija e responda apenas com o JSON completo."},
        ]

    log.append(f"reprovado após {MAX_TENTATIVAS} tentativas — nada gravado")
    return None, log


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("uso: python compose.py <facts.json> <marca> [--dry-run]")

    facts = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    marca = json.loads((RAIZ / "brands" / f"{sys.argv[2]}.json").read_text(encoding="utf-8"))
    dry = "--dry-run" in sys.argv

    brief, log = compor(facts, marca, dry_run=dry)
    for linha in log:
        print(linha)

    if brief is None:
        print("\nNada gravado. O perfil fica um ciclo sem post — de propósito.")
        return 1

    destino = RAIZ / "briefs" / f"{marca['slug']}-{brief['data']}.json"
    destino.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nBrief aprovado: {destino}")
    print(f"Renderize com:  python render.py {destino.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
