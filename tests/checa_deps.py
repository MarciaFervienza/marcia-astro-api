"""As versões instaladas são EXATAMENTE as que vão subir?

Em 19/07 o build do Railway quebrou num commit que toca só ESTADO.md.
Causa estrutural: `requirements.txt` tinha FAIXAS, nenhuma versão exata.
Cada build resolvia do zero no PyPI, e o gate rodava contra o que estava
instalado nesta máquina — anthropic 0.99.0 aqui contra 0.121.0 lá.

O gate nunca validou o conjunto que sobe. É a mesma família do NameError de
17/07: passou por tudo e só apareceu no deploy.
"""
import importlib.metadata as md
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
falhas = 0

faixas, pins = [], []
for linha in open(os.path.join(RAIZ, "requirements.txt"), encoding="utf-8"):
    linha = linha.strip()
    if not linha or linha.startswith("#"):
        continue
    if "==" not in linha:
        faixas.append(linha)
    else:
        # EXTRAS não fazem parte do nome do pacote: "psycopg[binary]" é o
        # pacote "psycopg" com um extra. Sem tirar, o verificador procurava
        # um pacote inexistente e acusava divergência que não existe.
        _p, _v = linha.split("==")
        pins.append((_p.split("[", 1)[0].strip(), _v.strip()))

if faixas:
    falhas += 1
    print(f"  ERRADO  {len(faixas)} dependência(s) sem versão exata:")
    for f in faixas:
        print(f"            {f}")
else:
    print(f"  OK      as {len(pins)} dependências têm versão exata")

drift = []
for pacote, versao in pins:
    try:
        inst = md.version(pacote)
    except Exception:
        drift.append(f"{pacote}: requirements pede {versao}, NÃO INSTALADO aqui")
        continue
    if inst != versao:
        drift.append(f"{pacote}: requirements {versao} × instalado {inst}")

if drift:
    falhas += 1
    print("  ERRADO  o gate roda contra versões DIFERENTES das que sobem:")
    for d in drift:
        print(f"            {d}")
else:
    print("  OK      o instalado é exatamente o que sobe")

if falhas:
    raise SystemExit(1)
print(f"DEPENDÊNCIAS: {len(pins)} fixas e conferidas")
