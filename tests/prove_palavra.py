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
