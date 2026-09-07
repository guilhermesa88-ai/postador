"""
Testes dos validadores — casos adversariais.

O modelo é a parte não determinística do pipeline; estes testes provam que a
parte determinística barra o que não pode passar. Rode com:

    python test_validators.py
"""

from __future__ import annotations

import json
from pathlib import Path

from validators import (checar_ancoragem, checar_estrutura, checar_voz,
                        extrair_numeros, numeros_dos_facts, validar)

RAIZ = Path(__file__).parent
FACTS = json.loads((RAIZ / "facts" / "exemplo-bh.json").read_text(encoding="utf-8"))
BRIEF = json.loads((RAIZ / "briefs" / "exemplo-bh.json").read_text(encoding="utf-8"))
VOZ = json.loads((RAIZ / "brands" / "metro-de-bh.json").read_text(encoding="utf-8"))["voz"]

falhas: list[str] = []


def checa(nome: str, condicao: bool, detalhe: str = "") -> None:
    if condicao:
        print(f"  ok    {nome}")
    else:
        print(f"  FALHA {nome} {detalhe}")
        falhas.append(nome)


print("\nextração de números")
checa("decimal com vírgula", 3.8 in extrair_numeros("subiu 3,8% no trimestre"))
checa("negativo", 2.3 in extrair_numeros("caiu -2,3%"))
checa("milhar com ponto", 14820.0 in extrair_numeros("R$ 14.820 o m²"))
checa("milhar com espaço", 14820.0 in extrair_numeros("R$ 14 820 o m²"))
checa("decimal com ponto", 6.1 in extrair_numeros("amplitude de 6.1 pp"))
checa("ignora texto puro", extrair_numeros("sem número aqui") == set())

print("\nnúmeros dos facts")
base = numeros_dos_facts(FACTS)
checa("pega variação", 3.8 in base)
checa("pega preço", 14820.0 in base)
checa("pega agregado", 6.1 in base)
checa("pega negativo como absoluto", 2.3 in base)

print("\nbrief de exemplo (deve passar inteiro)")
r = validar(BRIEF, FACTS, VOZ)
checa("validação completa aprova", r.ok, str(r))

print("\nancoragem numérica — o teste que importa")

inventado = json.loads(json.dumps(BRIEF))
inventado["slides"][0]["sub"] = "A variação do m² subiu 47,2% em Belo Horizonte."
r = checar_ancoragem(inventado, FACTS)
checa("barra número inventado", not r.ok, str(r.erros))

inventado2 = json.loads(json.dumps(BRIEF))
inventado2["legenda"] = "O m² médio de BH chegou a R$ 22.400 neste trimestre."
r = checar_ancoragem(inventado2, FACTS)
checa("barra preço inventado na legenda", not r.ok)

arredondado = json.loads(json.dumps(BRIEF))
arredondado["slides"][0]["sub"] = "A média da cidade foi de 0,7%."
r = checar_ancoragem(arredondado, FACTS)
checa("aceita número real dos facts", r.ok, str(r.erros))

ano = json.loads(json.dumps(BRIEF))
ano["slides"][0]["sub"] = "Dados coletados em 2026."
r = checar_ancoragem(ano, FACTS)
checa("aceita ano", r.ok, str(r.erros))

trivial = json.loads(json.dumps(BRIEF))
trivial["slides"][0]["sub"] = "São 3 leituras possíveis."
r = checar_ancoragem(trivial, FACTS)
checa("aceita número trivial", r.ok, str(r.erros))

print("\nvoz")
vendedor = json.loads(json.dumps(BRIEF))
vendedor["slides"][4]["cta"] = "Oportunidade única, invista já!"
r = checar_voz(vendedor, VOZ)
checa("barra expressão banida", not r.ok, str(r.erros))
checa("aponta as duas ocorrências", len(r.erros) >= 2, str(r.erros))

print("\nestrutura")
sem_capa = json.loads(json.dumps(BRIEF))
sem_capa["slides"][0]["tipo"] = "texto"
r = checar_estrutura(sem_capa)
checa("exige capa no primeiro slide", not r.ok)

grafico_vazio = json.loads(json.dumps(BRIEF))
del grafico_vazio["slides"][1]["dados"]
r = checar_estrutura(grafico_vazio)
checa("exige dados no slide de gráfico", not r.ok)

titulo_longo = json.loads(json.dumps(BRIEF))
titulo_longo["slides"][0]["titulo"] = "T" * 200
r = checar_estrutura(titulo_longo)
checa("barra título que estoura o slide", not r.ok)

poucos = json.loads(json.dumps(BRIEF))
poucos["slides"] = poucos["slides"][:2]
r = checar_estrutura(poucos)
checa("barra carrossel curto demais", not r.ok)

print()
if falhas:
    print(f"{len(falhas)} falha(s): {falhas}")
    raise SystemExit(1)
print("todos os testes passaram")
