"""
validators.py — o portão entre o que o modelo escreveu e o que vai ao ar.

A checagem que importa é a **ancoragem numérica**: todo número que aparece no
texto tem que existir nos `facts` de origem. Num perfil de dado, a proposta
inteira é "o número é verificável"; um único número inventado destrói isso de
um jeito que nenhuma cadência conserta.

As outras checagens são higiene: schema, palavras banidas, limites de tamanho.

Nada aqui depende de LLM — é tudo determinístico e testável, e por isso é onde
vale confiar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

# ---------------------------------------------------------------------------
# Contrato do brief
# ---------------------------------------------------------------------------

BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["marca", "legenda", "slides"],
    "properties": {
        "marca": {"type": "string", "minLength": 1},
        "data": {"type": "string"},
        "selo_ia": {"type": "string"},
        "legenda": {"type": "string", "minLength": 40, "maxLength": 2200},
        "slides": {
            "type": "array",
            "minItems": 3,
            "maxItems": 10,
            "items": {
                "type": "object",
                "required": ["tipo", "eyebrow", "titulo"],
                "properties": {
                    "tipo": {"enum": ["capa", "grafico", "lista", "texto", "fechamento"]},
                    "eyebrow": {"type": "string", "minLength": 2, "maxLength": 40},
                    "titulo": {"type": "string", "minLength": 3, "maxLength": 70},
                    "sub": {"type": "string", "maxLength": 160},
                    "corpo": {"type": "string", "maxLength": 420},
                    "cta": {"type": "string", "maxLength": 120},
                    "rodape_destaque": {"type": "string", "maxLength": 160},
                    "unidade": {"type": "string", "maxLength": 4},
                    "fonte": {"type": "string", "maxLength": 80},
                    "itens": {
                        "type": "array", "minItems": 2, "maxItems": 5,
                        "items": {"type": "string", "minLength": 10, "maxLength": 220},
                    },
                    "dados": {
                        "type": "array", "minItems": 3, "maxItems": 10,
                        "items": {
                            "type": "object",
                            "required": ["label", "valor"],
                            "properties": {
                                "label": {"type": "string", "minLength": 1, "maxLength": 24},
                                "valor": {"type": "number"},
                            },
                        },
                    },
                },
            },
        },
    },
}


@dataclass
class Resultado:
    ok: bool = True
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def falhar(self, msg: str) -> None:
        self.ok = False
        self.erros.append(msg)

    def avisar(self, msg: str) -> None:
        self.avisos.append(msg)

    def __str__(self) -> str:
        linhas = ["OK" if self.ok else "REPROVADO"]
        linhas += [f"  ERRO   {e}" for e in self.erros]
        linhas += [f"  aviso  {a}" for a in self.avisos]
        return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Números
# ---------------------------------------------------------------------------

# Casa 1.234,56 / 1234.56 / 12 / -3,8 — com ou sem separador de milhar.
_NUM = re.compile(r"-?\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?")

# Números que não precisam existir nos facts: fazem parte da língua, não do dado.
_TOLERADOS = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0,
    100.0, 1000.0,
}


# 14.820 / 1.234.567 — ponto como separador de milhar (convenção pt-BR).
# Distinguir de 6.1 (decimal) importa: sem isso R$ 14.820 vira 14,82 e a
# ancoragem reprova um número que está correto.
_MILHAR_PONTO = re.compile(r"^-?\d{1,3}(?:\.\d{3})+$")


def _para_float(txt: str) -> float | None:
    t = txt.replace(" ", "")
    if "," in t and "." in t:          # 1.234,56 -> milhar ponto, decimal vírgula
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    elif _MILHAR_PONTO.match(t):       # 14.820 -> milhar, não decimal
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def extrair_numeros(texto: str) -> set[float]:
    """Números mencionados num texto, normalizados para float."""
    achados: set[float] = set()
    for m in _NUM.finditer(texto):
        v = _para_float(m.group())
        if v is not None:
            achados.add(round(abs(v), 4))
    return achados


def numeros_dos_facts(facts: Any) -> set[float]:
    """Todo número presente na estrutura de facts, em qualquer profundidade."""
    achados: set[float] = set()

    def anda(no: Any) -> None:
        if isinstance(no, bool):
            return
        if isinstance(no, (int, float)):
            achados.add(round(abs(float(no)), 4))
        elif isinstance(no, str):
            achados.update(extrair_numeros(no))
        elif isinstance(no, dict):
            for v in no.values():
                anda(v)
        elif isinstance(no, list):
            for v in no:
                anda(v)

    anda(facts)
    return achados


def _textos_do_brief(brief: dict) -> list[tuple[str, str]]:
    """(origem, texto) de tudo que vira palavra publicada."""
    saida: list[tuple[str, str]] = [("legenda", brief.get("legenda", ""))]
    for i, s in enumerate(brief.get("slides", []), start=1):
        for campo in ("eyebrow", "titulo", "sub", "corpo", "cta", "rodape_destaque"):
            if s.get(campo):
                saida.append((f"slide {i}.{campo}", s[campo]))
        for j, it in enumerate(s.get("itens", []) or [], start=1):
            saida.append((f"slide {i}.item {j}", it))
    return saida


def checar_ancoragem(brief: dict, facts: Any, tolerancia: float = 0.051) -> Resultado:
    """Todo número publicado tem que existir nos facts.

    `tolerancia` cobre arredondamento honesto (3,81 nos facts vira 3,8 no post).
    Números triviais e anos passam direto — ver `_TOLERADOS`.
    """
    r = Resultado()
    base = numeros_dos_facts(facts)

    # os valores do próprio gráfico entram na base: vieram dos facts via seleção
    for s in brief.get("slides", []):
        for d in s.get("dados", []) or []:
            base.add(round(abs(float(d["valor"])), 4))

    for origem, texto in _textos_do_brief(brief):
        for n in extrair_numeros(texto):
            if n in _TOLERADOS or 1900 <= n <= 2100:
                continue
            if any(abs(n - b) <= tolerancia for b in base):
                continue
            r.falhar(f"{origem}: número {n:g} não existe nos facts de origem")
    return r


# ---------------------------------------------------------------------------
# Voz e forma
# ---------------------------------------------------------------------------

def checar_voz(brief: dict, voz: dict) -> Resultado:
    r = Resultado()
    banidas = [p.lower() for p in voz.get("banidas", [])]
    for origem, texto in _textos_do_brief(brief):
        baixo = texto.lower()
        for p in banidas:
            if p in baixo:
                r.falhar(f'{origem}: expressão banida "{p}"')
    return r


def checar_estrutura(brief: dict) -> Resultado:
    r = Resultado()
    try:
        jsonschema.validate(brief, BRIEF_SCHEMA)
    except jsonschema.ValidationError as e:
        caminho = "/".join(str(p) for p in e.absolute_path) or "(raiz)"
        r.falhar(f"schema em {caminho}: {e.message}")
        return r

    slides = brief["slides"]
    if slides[0]["tipo"] != "capa":
        r.falhar("o primeiro slide precisa ser do tipo 'capa'")
    if slides[-1]["tipo"] != "fechamento":
        r.avisar("o último slide não é 'fechamento' — o carrossel termina sem CTA")

    for i, s in enumerate(slides, start=1):
        if s["tipo"] == "grafico" and not s.get("dados"):
            r.falhar(f"slide {i}: tipo 'grafico' sem 'dados'")
        if s["tipo"] == "lista" and not s.get("itens"):
            r.falhar(f"slide {i}: tipo 'lista' sem 'itens'")
        if s["tipo"] == "fechamento" and not s.get("cta"):
            r.falhar(f"slide {i}: tipo 'fechamento' sem 'cta'")

    if not any(s["tipo"] == "grafico" for s in slides):
        r.avisar("nenhum slide de gráfico — num perfil de dado isso é incomum")
    return r


def validar(brief: dict, facts: Any, voz: dict) -> Resultado:
    """Roda tudo e junta o resultado."""
    total = Resultado()
    for parcial in (checar_estrutura(brief), checar_voz(brief, voz),
                    checar_ancoragem(brief, facts)):
        if not parcial.ok:
            total.ok = False
        total.erros += parcial.erros
        total.avisos += parcial.avisos
    return total
