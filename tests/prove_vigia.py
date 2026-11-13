"""VIGIA EXTERNA + SEGUNDO CANAL — provas comportamentais (13/11).

Por que comportamental e não asserção sobre a fonte: em 11/08 uma
salvaguarda ficou verde porque procurava uma substring que aparecia duas
vezes no caminho. Aqui tudo é exercitado de verdade — fila real em
sqlite, cliente HTTP do Flask, e o Telegram dublado no ponto de saída.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_d = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.path.join(_d, "vigia.db")
os.environ.setdefault("API_SECRET_KEY", "chave-de-teste-vigia")

import fila as F                                              # noqa: E402
import app as A                                               # noqa: E402

falhas = []


def chk(nome, cond, detalhe=""):
    print(f"{'OK   ' if cond else 'FALHA'} {nome}" + (f"   → {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


cli = A.app.test_client()
f = F.Fila(); f.criar_tabelas()

# ------------------------------------------------------------------ 1
print("\n1) /vigia com a fila limpa")
r = cli.get("/vigia")
chk("1a. responde 200 com fila vazia", r.status_code == 200, f"http={r.status_code}")
chk("1b. ok=True", (r.get_json() or {}).get("ok") is True)
chk("1c. sem autenticação (o monitor externo não guarda a chave)",
    r.status_code == 200)

# ------------------------------------------------------------------ 2
print("\n2) PROCESSANDO travado tem que derrubar para 503")
_NOME = "Zulmira Testemunha"
_MAIL = "zulmira.testemunha@exemplo.invalido"
tid = f.enfileirar({"name": _NOME, "email": _MAIL,
                    "birth_city": "Pelotas, RS, Brasil"},
                   nome=_NOME, email=_MAIL)
f.reivindicar("worker-de-teste")
r = cli.get("/vigia")
chk("2a. heartbeat fresco ainda é 200", r.status_code == 200, f"http={r.status_code}")

con = f.con(); cur = con.cursor()
cur.execute(f._q("UPDATE trabalhos SET heartbeat=%s WHERE id=%s"),
            (time.time() - F.HEARTBEAT_MORTO_SEGS - 600, tid))
if f.postgres:
    con.commit()
r = cli.get("/vigia")
b = r.get_json() or {}
chk("2b. heartbeat morto vira 503", r.status_code == 503, f"http={r.status_code}")
chk("2c. ok=False e o motivo nomeia o travamento",
    b.get("ok") is False and any("heartbeat morto" in m for m in b.get("motivos", [])),
    f"motivos={b.get('motivos')}")
chk("2d. conta o travado", b.get("travados") == 1, f"travados={b.get('travados')}")

# ------------------------------------------------------------------ 3
# O endpoint é aberto DE PROPÓSITO. Então ele não pode vazar nada.
print("\n3) sem autenticação exige ZERO dado pessoal")
_bruto = json.dumps(r.get_json(), ensure_ascii=False)
chk("3a. não vaza o nome", _NOME not in _bruto)
chk("3b. não vaza o e-mail", _MAIL not in _bruto)
chk("3c. não vaza a cidade", "Pelotas" not in _bruto)
chk("3d. não vaza o id do trabalho", str(tid) not in _bruto, f"corpo={_bruto[:160]}")

# ------------------------------------------------------------------ 4
print("\n4) PENDENTE envelhecido (worker que nunca existiu)")
f.devolver(tid, "worker-de-teste")
cur.execute(f._q("UPDATE trabalhos SET criado_em=%s, heartbeat=NULL WHERE id=%s"),
            (time.time() - A.FILA_PARADA_SEGS - 600, tid))
if f.postgres:
    con.commit()
r = cli.get("/vigia")
b = r.get_json() or {}
chk("4a. pendente parado além do teto vira 503", r.status_code == 503,
    f"http={r.status_code}")
chk("4b. o motivo diz que ninguém consome",
    any("ninguém consome" in m for m in b.get("motivos", [])),
    f"motivos={b.get('motivos')}")

# ------------------------------------------------------------------ 5
# A propriedade que a Márcia aprovou: o alerta não morre com o SendGrid.
print("\n5) segundo canal: alerta sobrevive ao e-mail morto")
_orig_tg, _orig_mail = A._telegram_enviar, A._enviar_email_bruto
_vistas = []
try:
    A._telegram_enviar = lambda t: (_vistas.append(t), True)[1]
    A._enviar_email_bruto = lambda *a, **k: False          # SendGrid morto
    _ok = A._alerta_com_retry("assunto de teste", "corpo de teste", tentativas=1)
    chk("5a. com e-mail morto e Telegram vivo, o alerta NÃO se perde",
        _ok is True, f"retorno={_ok}")
    chk("5b. e a mensagem saiu mesmo", len(_vistas) == 1 and "corpo de teste" in _vistas[0])

    _vistas.clear()
    A._telegram_enviar = lambda t: (_vistas.append(t), False)[1]
    _ok2 = A._alerta_com_retry("assunto 2", "corpo 2", tentativas=1)
    chk("5c. com os DOIS mortos, aí sim é alerta perdido", _ok2 is False)

    # _send_failure_alert tinha `return` antes de qualquer outro canal
    # quando o SendGrid não estava configurado. É o desenho que produziu
    # os três meses de silêncio.
    _vistas.clear()
    A._telegram_enviar = lambda t: (_vistas.append(t), True)[1]
    _bak_key = A.SENDGRID_API_KEY
    A.SENDGRID_API_KEY = ""
    try:
        A._send_failure_alert("estagio_de_teste", ValueError("defeito falso"),
                              {"name": "X", "email": "x@y.z"})
    finally:
        A.SENDGRID_API_KEY = _bak_key
    chk("5d. sem SendGrid configurado, _send_failure_alert AINDA usa o Telegram",
        len(_vistas) == 1, f"mensagens={len(_vistas)}")
finally:
    A._telegram_enviar, A._enviar_email_bruto = _orig_tg, _orig_mail

# ------------------------------------------------------------------ 6
print("\n6) Telegram não configurado degrada em silêncio")
_bt, _ci = A.TELEGRAM_BOT_TOKEN, A.TELEGRAM_CHAT_ID
try:
    A.TELEGRAM_BOT_TOKEN, A.TELEGRAM_CHAT_ID = "", ""
    chk("6a. sem token devolve False sem levantar",
        A._telegram_enviar("qualquer coisa") is False)
finally:
    A.TELEGRAM_BOT_TOKEN, A.TELEGRAM_CHAT_ID = _bt, _ci

print()
if falhas:
    print(f">>> {len(falhas)} FALHOU: {falhas}")
    raise SystemExit(1)
print(">>> VIGIA + SEGUNDO CANAL: TUDO PROVADO")
