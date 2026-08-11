"""FUMAÇA CONTRA PRODUÇÃO — uma geração de verdade, ponta a ponta.

Por que existe (19/07): o `gate.sh --smoke` chamava este arquivo e ele
NÃO EXISTIA. A etapa mais cara do gate — a única que exercita o produto
inteiro contra o servidor real — falhava por ausência, e ninguém tinha
rodado com --smoke desde que a linha foi escrita. Etapa de gate que
aponta para o vazio é pior que etapa ausente: o script promete cobertura
que não tem.

O que ele prova, e as asserções estáticas NÃO provam: que a extração do
núcleo não mudou o caminho da cliente. AST diz que o corte está limpo;
só uma geração real diz que o relatório continua saindo.

Manda para marcia.fervienza@gmail.com — destinatário de teste combinado.
Nunca mande para outro endereço a partir daqui.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("API_BASE", "https://web-production-6c77f.up.railway.app")
KEY = os.environ.get("API_SECRET_KEY", "")
DESTINO = "marcia.fervienza@gmail.com"

falhas = []


def checa(nome, cond, detalhe=""):
    print(f"{'OK   ' if cond else 'FALHA'} {nome}" + (f"   → {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


def _post(rota, corpo, timeout=600):
    req = urllib.request.Request(
        BASE + rota, data=json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as ex:
        try:
            return ex.code, json.load(ex)
        except Exception:
            return ex.code, {"raw": ex.read().decode()[:400]}


if not KEY:
    print("FALHA  API_SECRET_KEY não está no ambiente — sem ela não dá para "
          "falar com produção.")
    sys.exit(1)

# Dados de nascimento CRUS, sem chart pré-calculado: assim a fumaça
# exercita geocoding + Kerykeion + regra dos 5° + filtro de aspectos, e
# não só a metade de texto do caminho.
PAYLOAD = {
    "name": "Helena Penteado",
    "gender": "feminino",
    "email": DESTINO,
    "birth_date": "1992-09-18",
    "birth_time": "09:50",
    "birth_city": "Belo Horizonte, MG, Brasil",
    "report_for": "O mapa é meu",
}

print("=" * 66)
print(f"FUMAÇA CONTRA {BASE}")
print("=" * 66)

t0 = time.time()
code, body = _post("/generate-report", dict(PAYLOAD))
dur = time.time() - t0
meta = body.get("meta") or {}

print(f"\nHTTP {code} em {dur:.0f}s\n")

checa("geração responde 200", code == 200,
      f"code={code} msg={str(body.get('message'))[:200]}")
checa("relatório veio com texto", len(body.get("report") or "") > 5000,
      f"{len(body.get('report') or '')} caracteres")
checa("PDF gerado", bool(body.get("pdf_base64")),
      f"pdf_error={meta.get('pdf_error')!r}")
checa("e-mail enviado", meta.get("email_sent") is True,
      f"email_error={meta.get('email_error')!r}")
checa("sem falha fechada de língua", not meta.get("falha_lingua"),
      str(meta.get("falha_lingua"))[:200])
checa("lint do PDF vazio", not meta.get("pdf_lint"),
      f"{len(meta.get('pdf_lint') or [])} violação(ões)")
checa("repetição entre seções vazia", not meta.get("repetition_lint"),
      f"{len(meta.get('repetition_lint') or [])} ocorrência(s)")
checa("verificador EXECUTOU", bool(meta.get("verifier")),
      "verifier_log vazio é ambíguo entre 0 violações e crash")
checa("geocoding acertou a cidade",
      abs((meta.get("latitude") or 0) + 19.92) < 0.3
      and (meta.get("timezone") == "America/Sao_Paulo"),
      f"lat={meta.get('latitude')} tz={meta.get('timezone')}")
checa("voz reconhecida (report_for por extenso)",
      meta.get("report_for_reconhecido") is True
      and (meta.get("voice") or {}).get("person") == "segunda",
      f"bruto={meta.get('report_for_bruto')!r} "
      f"voz={(meta.get('voice') or {}).get('person')!r}")

# Margem contra o corte do proxy (300s). Não é falha — é o número que
# decide quando o assíncrono deixa de ser opcional.
print(f"\n  tempo: {dur:.0f}s   margem até o corte do proxy (300s): "
      f"{300 - dur:.0f}s")

# ------------------------------------------------------------------
# CAMINHO ASSÍNCRONO — 202 + /status, forçado pelo corpo.
# Deixa um trabalho PENDENTE se ainda não houver worker de pé; é
# barato e some no primeiro worker que subir.
print()
code2, body2 = _post("/generate-report", dict(PAYLOAD, assincrono=True), timeout=60)
checa("assíncrono responde 202", code2 == 202,
      f"code={code2} {str(body2)[:200]}")
tid = body2.get("id")
if tid:
    req = urllib.request.Request(f"{BASE}/status/{tid}",
                                 headers={"X-API-Key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        st = json.load(r)
    checa("/status devolve o trabalho", st.get("id") == tid,
          f"estado={st.get('estado')!r}")
    print(f"  trabalho {tid} em estado {st.get('estado')!r} "
          f"(fica PENDENTE até o worker subir)")

print()
if falhas:
    print(f">>> {len(falhas)} FALHOU: {falhas}")
    sys.exit(1)
print(f">>> fumaça OK — geração real em {dur:.0f}s, e-mail enviado")
