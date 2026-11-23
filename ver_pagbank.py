#!/usr/bin/env python3
"""O QUE O SERVIDOR ENXERGA NO PAGBANK.

    python3 ver_pagbank.py

Não emite código e não manda e-mail: só olha. Responde três coisas:

  1. o token que está no Railway serve para o PagBank?
  2. o servidor consegue falar com o PagBank?
  3. como vem escrita a descrição de cada venda, e o servidor reconhece
     qual é de curso?

Não mostra nome nem e-mail de ninguém: só data, valor, status, a
descrição do item e se a venda veio com e-mail.
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

horas = int(sys.argv[1]) if len(sys.argv) > 1 else 72
req = urllib.request.Request(
    BASE + "/pagbank-diag", data=json.dumps({"horas": horas}).encode(),
    headers={"X-API-Key": KEY, "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
except urllib.error.HTTPError as ex:
    sys.exit(f"o servidor respondeu {ex.code}. Se for 404, o deploy novo "
             f"ainda não entrou; se for 401, a chave do .env não confere.")
except Exception as ex:
    sys.exit(f"não consegui falar com o servidor: {str(ex)[:160]}")

STATUS = {"1": "aguardando", "2": "em análise", "3": "PAGA",
          "4": "PAGA (disponível)", "5": "em disputa", "6": "devolvida",
          "7": "cancelada"}

print("=" * 74)
print("PAGBANK, VISTO PELO SERVIDOR")
print("=" * 74)
print(f"e-mail da conta no Railway: {'sim' if d.get('tem_email') else 'NÃO'}")
print(f"token no Railway:           {'sim' if d.get('tem_token') else 'NÃO'}"
      f"  ({d.get('tamanho_token')} caracteres)")
s = d.get("status")
if s == "sem_credenciais":
    sys.exit("\nFaltam PAGBANK_EMAIL e/ou PAGBANK_TOKEN no Railway.")
if s == "falha_de_rede":
    sys.exit(f"\nO servidor NÃO conseguiu falar com o PagBank: "
             f"{d.get('detalhe')}")
if s == "pagbank_recusou":
    print(f"\nO PagBank RECUSOU a consulta: {d.get('detalhe')}")
    sys.exit("HTTP 401 quer dizer que esse token não vale para consultas "
             "(pode ser uma credencial só do Wix).")

print(f"\nO token SERVE. Vendas nas últimas {d.get('horas')} horas: "
      f"{d.get('total')}")
print("-" * 74)
for v in d.get("vendas") or []:
    st = STATUS.get(v.get("status"), v.get("status"))
    print(f"{v.get('data')}  R$ {v.get('valor'):>9}  {st}")
    if "detalhe_falhou" in v:
        print(f"    (não consegui abrir os detalhes: {v['detalhe_falhou']})")
        continue
    for it in v.get("itens") or ["(sem descrição)"]:
        print(f"    item: {it}")
    print(f"    veio com e-mail: {'sim' if v.get('tem_email') else 'NÃO'}"
          f"    reconhecido como: {v.get('curso') or 'não é curso'}")
print()
