#!/usr/bin/env python3
"""EMITIR ACESSO — o que a Marcelle roda depois de cada venda.

    python3 emitir.py

Pergunta e-mail, nome e produto, emite o código de uso único e manda o
e-mail de boas-vindas com o link do formulário. No combo, emite também o
voucher de presente e acrescenta o bloco encaminhável.

POR QUE UM ARQUIVO, e não um comando colado no Terminal: um `python3 -c`
de várias linhas se quebra na primeira quebra de linha e executa pela
metade. Aconteceu duas vezes em 17/11, uma de manhã e uma à tarde.

POR QUE ELE NÃO EMITE NADA SOZINHO: a emissão mora no servidor, no
/emitir-acesso. Este arquivo só pergunta e mostra. Se a emissão morasse
aqui, o PagBank (que vai chamar o mesmo endpoint) e a Marcelle estariam
mandando e-mails diferentes, e ninguém descobriria até uma cliente
comparar os dois.
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

PRODUTOS = {
    "1": ("leitura", "Curso de Leitura de Mapa Natal"),
    "2": ("preditivas", "Curso de Técnicas de Astrologia Preditiva"),
    "3": ("combo", "Combo (os dois cursos)"),
}

if not KEY:
    sys.exit("API_SECRET_KEY não está no .env nem no ambiente.")

print("=" * 66)
print("EMITIR ACESSO — depois de confirmar o pagamento no PagBank")
print("=" * 66)
print()

email = input("E-mail da aluna: ").strip()
if "@" not in email:
    sys.exit("Isso não parece um e-mail. Nada foi emitido.")

nome = input("Nome dela (como ela escreveu): ").strip()

print()
for k, (_, rotulo) in sorted(PRODUTOS.items()):
    print(f"  {k}. {rotulo}")
escolha = input("\nO que ela comprou? (1, 2 ou 3): ").strip()
if escolha not in PRODUTOS:
    sys.exit("Opção inválida. Nada foi emitido.")
produto = PRODUTOS[escolha][0]

print()
print("Cole aqui as linhas de acesso ao curso (o que você já manda hoje).")
print("Pode deixar em branco se for mandar o acesso separado.")
print("Termine com uma linha vazia:")
linhas = []
while True:
    try:
        linha = input()
    except EOFError:
        break
    if not linha.strip():
        break
    linhas.append(linha)
acesso = "\n".join(linhas)

print()
print("-" * 66)
print(f"  para:    {email}")
print(f"  nome:    {nome or '(sem nome)'}")
print(f"  produto: {PRODUTOS[escolha][1]}")
print(f"  acesso:  {'sim, ' + str(len(linhas)) + ' linha(s)' if linhas else 'não'}")
print("-" * 66)
if input("\nConfirma e manda? (s/n): ").strip().lower() not in ("s", "sim"):
    sys.exit("Nada foi emitido.")

corpo = {"email": email, "nome": nome, "produto": produto, "acesso": acesso}
req = urllib.request.Request(
    BASE + "/emitir-acesso", data=json.dumps(corpo, ensure_ascii=False).encode(),
    headers={"Content-Type": "application/json", "X-API-Key": KEY})

print("\nemitindo…", flush=True)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
except urllib.error.HTTPError as ex:
    try:
        det = json.load(ex)
    except Exception:
        det = {"raw": ex.read().decode()[:400]}
    sys.exit(f"\nO servidor respondeu {ex.code}: "
             f"{det.get('message') or det.get('raw')}")
except Exception as ex:
    sys.exit(f"\nNão consegui falar com o servidor: {str(ex)[:160]}")

print()
print("=" * 66)
if res.get("email_enviado"):
    print("E-MAIL ENVIADO")
else:
    print("O CÓDIGO FOI EMITIDO, MAS O E-MAIL NÃO SAIU")
    print(f"motivo: {res.get('detalhe_email') or 'não informado'}")
    print("Copie o texto abaixo e mande do seu e-mail.")
print("=" * 66)
print(f"  código da aluna:    {res.get('codigo_aluna')}")
if res.get("codigo_presente"):
    print(f"  voucher de presente: {res.get('codigo_presente')}")
if res.get("reaproveitado"):
    print("  (esta compra já tinha código; reaproveitei em vez de emitir outro)")
print()
print("-" * 66)
print(res.get("texto") or "")
print("-" * 66)
