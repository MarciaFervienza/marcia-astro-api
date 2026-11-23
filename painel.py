#!/usr/bin/env python3
"""PAINEL — o que saiu, o que foi recusado, e o que chegou sinalizado.

    python3 painel.py

Lê o registro do lado do SERVIDOR (as 25 últimas gerações). Esse registro
sobrevive à desconexão do cliente: se o proxy cortar a resposta aos 300s,
o relatório ainda sai e aparece aqui.

Isto existe para a Márcia continuar sendo a editora do produto, e não a
espectadora dele. O sistema decide o encanamento — tentar de novo,
enfileirar, entregar. O julgamento continua dela, e julgamento precisa de
informação na mão.

O que cada coluna responde:

  desfecho    saiu, foi recusado, ou nem respondeu
  2a          o relatório só saiu porque houve segunda tentativa?
  rodadas     quantas voltas o encanamento de língua precisou
  sinaliz.    quantas sinalizações CHEGARAM ao cliente sem segurar o
              relatório — é aqui que aparece uma palavra inventada como
              o "retrograu"
"""
import json
import os
import sys
import urllib.error
import urllib.request


def _le_env():
    try:
        for linha in open(".env", encoding="utf-8"):
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except FileNotFoundError:
        pass


_le_env()
BASE = os.environ.get("API_BASE", "https://web-production-6c77f.up.railway.app")
KEY = os.environ.get("API_SECRET_KEY", "")

if not KEY:
    sys.exit("API_SECRET_KEY não está no .env nem no ambiente.")

req = urllib.request.Request(BASE + "/ultimas-geracoes",
                             headers={"X-API-Key": KEY})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        dados = json.load(r)
except urllib.error.HTTPError as ex:
    sys.exit(f"o servidor respondeu {ex.code}. Se for 401, a chave do .env "
             f"não é a que está no Railway.")
except Exception as ex:
    sys.exit(f"não consegui falar com o servidor: {str(ex)[:160]}")

gs = dados.get("geracoes") or []
if not gs:
    print("Nenhuma geração registrada ainda.")
    print("(O registro fica no banco do servidor e sobrevive aos deploys.)")
    sys.exit(0)

print("=" * 78)
print(f"ÚLTIMAS {len(gs)} GERAÇÕES")
print("=" * 78)
print(f"{'dia':<7}{'hora':<10}{'quem':<22}{'desfecho':<18}{'eq':<4}{'2a':<5}{'rod':<5}{'sinaliz.':<9}")
print("-" * 78)

recusados, recuperados, sinalizados = [], [], []
for g in gs:
    nome = str(g.get("nome") or "—")[:21]
    desf = str(g.get("desfecho") or "—")
    seg = "SIM" if g.get("segunda_tentativa") else ""
    rod = g.get("rodadas")
    sin = g.get("sinalizacoes")
    print(f"{str(g.get('dia') or ''):<7}{str(g.get('quando') or '')[:8]:<10}{nome:<22}"
          f"{desf[:17]:<18}{('EQ' if g.get('equipe') else ''):<4}{seg:<5}{str(rod if rod is not None else '—'):<5}"
          f"{str(sin if sin is not None else '—'):<9}")
    # "falh" pega falha_fechada_lingua E email_falhou (23/11): relatório
    # escrito e não entregue também é um mapa que não saiu.
    if "falh" in desf:
        recusados.append(g)
    if g.get("segunda_tentativa"):
        recuperados.append(g)
    if g.get("sinalizado"):
        sinalizados.append(g)

print("-" * 78)
ok = sum(1 for g in gs if g.get("desfecho") == "ok")
_eq = sum(1 for g in gs if g.get("equipe"))
print(f"com a senha da equipe (coluna eq): {_eq}")
print(f"entregues: {ok}    recusados: {len(recusados)}    "
      f"recuperados pela 2a tentativa: {len(recuperados)}")

# O QUE CHEGOU AO CLIENTE SINALIZADO. É a parte que mais merece o olho
# dela: não segurou o relatório, mas foi entregue assim mesmo.
if sinalizados:
    print("\n" + "=" * 78)
    print("CHEGOU AO CLIENTE COM SINALIZAÇÃO — vale o teu olho")
    print("=" * 78)
    for g in sinalizados:
        print(f"\n{g.get('nome')}:")
        for s in g.get("sinalizado") or []:
            print(f"  · {s}")

# E o que foi recusado, com a frase que segurou.
if recusados:
    print("\n" + "=" * 78)
    print("RECUSADOS — o relatório não saiu")
    print("=" * 78)
    for g in recusados:
        seg = " (já na 2a tentativa)" if g.get("segunda_tentativa") else ""
        print(f"\n{g.get('nome')}{seg}:")
        for p in (g.get("pendencias") or [])[:6]:
            # Formato novo (17/11): dicionário com frase, motivo e seção.
            # Formato antigo: só a string da frase. Os dois são aceitos
            # porque o registro vive na memória do servidor e, logo depois
            # de um deploy, ainda pode conter linhas do formato velho.
            if isinstance(p, dict):
                print(f"  · {p.get('frase', '')}")
                print(f"    POR QUE: {p.get('motivo') or '(motivo não registrado)'}"
                      + (f"   [seção: {p['secao']}]" if p.get("secao") else ""))
            else:
                print(f"  · {p}")
                print("    POR QUE: (motivo não registrado — versão antiga)")

print()
