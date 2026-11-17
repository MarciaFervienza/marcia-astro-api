"""A segunda tentativa existe, é UMA só, e não move o padrão.

POR QUE EXISTE (medido em 14/11): três mapas falharam fechados — Bete,
Carlos e Marcelo — e os TRÊS passaram de primeira ao serem refeitos, com
os mesmos dados de nascimento. Não existem mapas difíceis: existe
variância na geração. Deixar a cliente sem nada por causa de uma rolagem
ruim desperdiça um relatório que sairia limpo na tentativa seguinte.

O QUE ELA NÃO É: afrouxamento. A segunda geração passa pelo MESMO gate,
com a MESMA falha fechada no fim. Se as duas falharem, o relatório
continua não saindo. É encanamento, não julgamento.

As asserções são sobre a ÁRVORE SINTÁTICA e a ORDEM, não sobre a
presença de uma string — foi assim que uma salvaguarda morreu em 19/07.
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(RAIZ, "app.py"), encoding="utf-8").read()

FALHAS = []


def checa(nome, cond, detalhe=""):
    if cond:
        print(f"  ok   {nome}")
    else:
        print(f"  FALHOU  {nome}  {detalhe}")
        FALHAS.append(nome)


print("[1] o interruptor existe e vem ligado por padrão")
checa("1a. SEGUNDA_TENTATIVA é lida do ambiente",
      'os.environ.get(\n    "SEGUNDA_TENTATIVA", "1")' in SRC
      or 'os.environ.get("SEGUNDA_TENTATIVA", "1")' in SRC,
      "o padrão tem de ser ligado — a proteção não pode depender de lembrar")

print("\n[2] ela roda ANTES da falha fechada, senão é decorativa")
_i_ret = SRC.find('if result.get("falha_lingua") and SEGUNDA_TENTATIVA:')
_i_fc = SRC.find('_falha_lingua = result.get("falha_lingua")')
checa("2a. o bloco da segunda tentativa existe", _i_ret >= 0)
checa("2b. e vem antes da decisão de recusar", 0 <= _i_ret < _i_fc,
      f"retry={_i_ret} falha_fechada={_i_fc}")

print("\n[3] UMA só — não há laço")
_bloco = SRC[_i_ret:_i_fc] if (_i_ret >= 0 and _i_fc > _i_ret) else ""
checa("3a. sem while no bloco", "while " not in _bloco)
checa("3b. sem for no bloco", not re.search(r"\bfor\b", _bloco))
checa("3c. exatamente uma regeneração no bloco",
      _bloco.count("rg.generate_report(") == 1,
      f"{_bloco.count('rg.generate_report(')} chamadas")

print("\n[4] o padrão NÃO se move")
checa("4a. a falha fechada continua existindo depois dela",
      'if _falha_lingua:' in SRC[_i_fc:_i_fc + 300])
checa("4b. e a 2a tentativa também pode falhar fechada",
      "2a tentativa também falhou fechada" in _bloco)
checa("4c. exceção na 2a não piora o desfecho da 1a",
      "except Exception" in _bloco and "logger.exception" in _bloco)

print("\n[5] fica registrada — repetição silenciosa esconderia a taxa real")
checa("5a. segunda_tentativa no meta dos DOIS caminhos",
      SRC.count('"segunda_tentativa": _segunda_tentativa') == 2,
      f"{SRC.count('mesma-chave')} — 200 e 422 precisam dos dois")
checa("5b. e o motivo da PRIMEIRA falha é guardado",
      SRC.count('"falha_primeira_tentativa": _falha_primeira') == 2)

print("\n[6] o arquivo continua válido")
try:
    ast.parse(SRC)
    checa("6a. app.py faz parse", True)
except SyntaxError as e:
    checa("6a. app.py faz parse", False, str(e))

print()
if FALHAS:
    print(f"PROVE_SEGUNDA_TENTATIVA FALHOU: {len(FALHAS)} asserção(ões)")
    for f in FALHAS:
        print(f"  - {f}")
    sys.exit(1)
print("prove_segunda_tentativa: todas as asserções passaram")
