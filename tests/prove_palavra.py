"""LINT DE PALAVRA — morde nas corrupções que o GPT achou (19/07)?

As sete, e o que cada regra cobre. A sétima ("como você mente pensa") é
erro de SINTAXE, não de palavra: nenhuma regra lexical alcança, e está
registrada como tal em vez de fingida como coberta.
"""
import warnings; warnings.filterwarnings("ignore")
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from word_lint import word_lint

falhas = 0


def chk(rot, cond):
    global falhas
    if cond:
        print(f"  OK      {rot}")
    else:
        falhas += 1
        print(f"  ERRADO  {rot}")


REAIS = [
    ("o idealismo pode se voltarcontra você", "palavra:colada"),
    ("a clareza de o que você pode dar sem se perder", "palavra:contracao_faltando"),
    ("a rigidez saturninan gravada na casa 4", "palavra:corrompida"),
    ("A retrogradiação de Saturno sugere internalização", "palavra:corrompida"),
    ("expandindo a zona de conforto arianas", "palavra:concordancia_adjetivo"),
    ("sobre onde e com quem você quer fincar ancoras", "palavra:acento_faltando"),
]
print("=" * 62)
print("A) AS SEIS QUE O LINT COBRE — todas do texto REAL")
print("=" * 62)
for frase, esperado in REAIS:
    kinds = [x["kind"] for x in word_lint(frase)]
    chk(f"{esperado:32s} {frase[:42]!r}", esperado in kinds)

print()
print("=" * 62)
print("B) INVENTADAS — a classe, não só o exemplo")
print("=" * 62)
INVENTADAS = [
    ("o medo pode se virarcontra você", "palavra:colada"),
    ("a raiz de o que sustenta", "palavra:contracao_faltando"),
    ("uma postura leoninan firme", "palavra:corrompida"),
    ("a energia de conforto leoninas", "palavra:concordancia_adjetivo"),
]
for frase, esperado in INVENTADAS:
    kinds = [x["kind"] for x in word_lint(frase)]
    chk(f"{esperado:32s} {frase[:42]!r}", esperado in kinds)

print()
print("=" * 62)
print("C) NEGATIVOS — o que NÃO pode acender")
print("=" * 62)
LIMPAS = [
    "autoafirmação e autoexigência caminham juntas",          # prefixo produtivo
    "um raciocínio contraintuitivo mas correto",              # prefixo produtivo
    "você precisa planejar e registrar as seções",            # pt-BR fora do dic. europeu
    "o contato harmônico com o fato de que a ação é sua",     # pt-BR acentual
    "formas que você não reconheça como leoninas",            # núcleo plural distante
    "olhar para o que ficou para trás",                       # 'para' não vira 'pará'
    "de um jeito ou de outro, você chega lá",                 # 'de um' não é contração
]
for frase in LIMPAS:
    r = word_lint(frase)
    chk(f"limpo: {frase[:46]!r}", not r)
    for x in r:
        print(f"            acusou {x['kind']} {x['match']!r}")

print()
print("=" * 62)
print("D) O QUE O LINT NÃO ALCANÇA — registrado, não escondido")
print("=" * 62)
nao_pega = "há uma tensão entre como você mente pensa e o que você busca"
chk("'como você mente pensa' NÃO é pego (é sintaxe, não palavra)",
    not word_lint(nao_pega))
print("          → precisa da passada de revisão de língua (item 5 da Márcia)")

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("LINT DE PALAVRA 19/07: TUDO PROVADO")


# ============================================================
# ENCANAMENTO DE LÍNGUA (19/07): detectar → regenerar → redetectar →
# falhar fechado. A passada de REESCRITA ficou de fora por medição:
# corrige 5 dos 16 e precisou de seis guardas; o detector acha 16 com
# risco zero.
# ============================================================
print()
print("=" * 62)
print("ENCANAMENTO: TETO DE TENTATIVAS E FALHA FECHADA")
print("=" * 62)
import re as _re
import revisao_lingua as _rl
import app as _app
import inspect as _insp

_T = ("# Mapa Natal — Teste\n\n## Abertura\n\nTexto bom aqui, com frases "
      "inteiras que fazem sentido.\n\n## Lua: suas raízes\n\nA frase está "
      "quebrada aqui e não fecha.\n\n## Saturno\n\nOutro texto bom.\n")

chk("mapeia as seções do relatório",
    [t for t, _, _ in _rl._mapa_de_secoes(_T)] ==
    ["Abertura", "Lua: suas raízes", "Saturno"])
_loc = _rl.secao_da_frase(_T, "A frase está quebrada aqui")
chk("localiza a frase apontada na seção certa",
    _loc is not None and _loc[0] == "Lua: suas raízes")

_orig_det = _rl.detectar_sem_sentido
try:
    # defeito TEIMOSO: o detector sempre acha a 1ª frase da seção Lua
    def _teimoso(t, call_claude_fn=None, max_workers=6, granularidade="paragrafo"):
        m = _re.search(r"## Lua[^\n]*\n\n([^\n]+)", t)
        return [{"frase": m.group(1), "motivo": "não fecha"}] if m else []
    _rl.detectar_sem_sentido = _teimoso
    _n = {"i": 0}

    def _regen(_t):
        _n["i"] += 1
        return f"Tentativa {_n['i']}: continua quebrada e sem sentido."

    _, _lg, _fal = _rl.pipeline_lingua(_T, None, _regen,
                                       call_claude_fn=lambda *a, **k: "OK",
                                       max_tentativas=3)
    chk("defeito teimoso FALHA FECHADO", bool(_fal))
    chk(f"para no teto de 3 rodadas, sem loop (deu {len(_lg['rodadas'])})",
        len(_lg["rodadas"]) == 3)
    chk(f"regenera 2 vezes, não infinitas (deu {_lg['regeneracoes']})",
        _lg["regeneracoes"] == 2)
    chk("o motivo da falha nomeia a frase pendente",
        "seguem quebradas" in (_fal or ""))

    # teto configurável
    _n["i"] = 0
    _, _lg2, _fal2 = _rl.pipeline_lingua(_T, None, _regen,
                                         call_claude_fn=lambda *a, **k: "OK",
                                         max_tentativas=2)
    chk(f"teto=2 para em 2 rodadas (deu {len(_lg2['rodadas'])})",
        len(_lg2["rodadas"]) == 2 and bool(_fal2))

    # caminho feliz
    _c = {"i": 0}

    def _cura(t, call_claude_fn=None, max_workers=6, granularidade="paragrafo"):
        _c["i"] += 1
        return ([{"frase": "A frase está quebrada aqui", "motivo": "x"}]
                if _c["i"] == 1 else [])
    _rl.detectar_sem_sentido = _cura
    _novo, _lg3, _fal3 = _rl.pipeline_lingua(
        _T, None, lambda t: "Texto novo, limpo e inteiro.",
        call_claude_fn=lambda *a, **k: "OK")
    chk("seção regenerada limpa NÃO falha fechado", not _fal3)
    chk("a seção foi de fato substituída",
        "Texto novo, limpo e inteiro" in _novo)
    chk("as outras seções ficaram intactas",
        "Texto bom aqui" in _novo and "Outro texto bom" in _novo)
finally:
    _rl.detectar_sem_sentido = _orig_det

# A falha fechada tem de vir ANTES de qualquer artefato ou envio.
_src = _insp.getsource(_app.generate_report_endpoint)
_i = _src.index("FALHA FECHADA DE LÍNGUA")
for _marca, _rot in (("_apply_moon_note", "nota da Lua"),
                     ("_generate_chart_svg", "SVG da mandala"),
                     ("pg.generate_pdf", "geração do PDF"),
                     ("send_report_email", "envio do e-mail")):
    chk(f"falha fechada vem ANTES de: {_rot}", _src.index(_marca) > _i)
_bloco = _src[_i:_src.index("_apply_moon_note", _i)]
chk("responde 422, não 200", "422" in _bloco)
chk("alerta para o executivo@ com seção e frase",
    "_send_failure_alert" in _bloco and '"secao"' in _bloco
    and '"frase"' in _bloco)

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("ENCANAMENTO DE LÍNGUA 19/07: TUDO PROVADO")
