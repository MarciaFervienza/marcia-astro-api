"""FILA PERSISTIDA — as propriedades que a fila TEM de garantir (19/07).

Rodam contra SQLite, que é a MESMA lógica que roda em Postgres. A única
divergência é a cláusula de reivindicação (FOR UPDATE SKIP LOCKED), e ela
está isolada num ramo — este arquivo confere que o ramo existe.
"""
import warnings; warnings.filterwarnings("ignore")
import os
import sys
import tempfile
import threading
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fila as F

falhas = 0


def chk(rot, cond, det=""):
    global falhas
    if cond:
        print(f"  OK      {rot}")
    else:
        falhas += 1
        print(f"  ERRADO  {rot}" + (f"  → {det}" if det else ""))


def nova():
    d = tempfile.mkdtemp()
    f = F.Fila(os.path.join(d, "fila.db"))
    f.criar_tabelas()
    return f


print("=" * 68)
print("A) SEM DATABASE_URL A FILA NÃO ABRE — falha alta, sem fallback")
print("=" * 68)
_bak = os.environ.pop("DATABASE_URL", None)
try:
    F.Fila()
    chk("levanta sem DATABASE_URL", False, "abriu sem banco")
except RuntimeError as e:
    chk("levanta sem DATABASE_URL", True)
    chk("a mensagem explica por que não há fallback",
        "perde o trabalho no restart" in str(e))
finally:
    if _bak:
        os.environ["DATABASE_URL"] = _bak

print()
print("=" * 68)
print("B) CICLO DE VIDA")
print("=" * 68)
f = nova()
tid = f.enfileirar({"name": "Teste", "birth_city": "Rodeio, SC"},
                   nome="Teste", email="t@x.com")
chk("enfileira e devolve id", bool(tid))
chk("nasce PENDENTE", f.buscar(tid)["estado"] == F.PENDENTE)
j = f.reivindicar("w1")
chk("worker reivindica", j is not None and j["id"] == tid)
chk("payload volta inteiro", j["payload"]["birth_city"] == "Rodeio, SC")
chk("passa a PROCESSANDO", f.buscar(tid)["estado"] == F.PROCESSANDO)
chk("conta a tentativa", f.buscar(tid)["tentativas"] == 1)
chk("fila vazia não devolve nada", f.reivindicar("w2") is None)
f.concluir(tid, markdown="# Mapa\n\ntexto", chart={"points": {"sun": {}}},
           meta={"pdf_bytes": 1})
b = f.buscar(tid)
chk("conclui como OK", b["estado"] == F.OK)
chk("guarda o MARKDOWN (o degrau 3 vive disto)", b["markdown"].startswith("# Mapa"))
chk("guarda o CHART", b["chart"]["points"] == {"sun": {}})

print()
print("=" * 68)
print("C) DOIS WORKERS NÃO PEGAM O MESMO TRABALHO")
print("=" * 68)
f2 = nova()
for i in range(6):
    f2.enfileirar({"i": i})
pegos, erros, lock = [], [], threading.Lock()


def _worker(wid):
    # QUALQUER exceção é registrada. Sem isto o teste PASSOU com três das
    # quatro threads mortas — a sobrevivente fez tudo e "6 de 6" não
    # provou concorrência nenhuma.
    try:
        while True:
            j = f2.reivindicar(wid)
            if not j:
                return
            with lock:
                pegos.append(j["id"])
            time.sleep(0.01)
    except Exception as exc:
        with lock:
            erros.append(f"{wid}: {type(exc).__name__}: {exc}")


ths = [threading.Thread(target=_worker, args=(f"w{i}",)) for i in range(4)]
[t.start() for t in ths]
[t.join() for t in ths]
chk(f"NENHUM worker morreu (morreram {len(erros)})", not erros,
    "; ".join(erros[:2]))
chk(f"6 trabalhos, 6 reivindicações (deu {len(pegos)})", len(pegos) == 6)
chk("NENHUM trabalho pego duas vezes", len(set(pegos)) == len(pegos),
    f"duplicados: {len(pegos) - len(set(pegos))}")
import inspect
chk("no Postgres usa FOR UPDATE SKIP LOCKED",
    "FOR UPDATE SKIP LOCKED" in inspect.getsource(F.Fila.reivindicar))

print()
print("=" * 68)
print("D) WORKER MORREU NO MEIO — retomada COM TETO")
print("=" * 68)
f3 = nova()
t3 = f3.enfileirar({"x": 1})
f3.reivindicar("w-morto")            # tentativas = 1
ret, des = f3.retomar_orfaos(agora=time.time() + F.HEARTBEAT_MORTO_SEGS + 10)
chk("heartbeat velho volta para PENDENTE", ret == [t3] and not des)
chk("estado é PENDENTE de novo", f3.buscar(t3)["estado"] == F.PENDENTE)
f3.reivindicar("w-morto-2")          # tentativas = 2 == TETO
ret2, des2 = f3.retomar_orfaos(agora=time.time() + F.HEARTBEAT_MORTO_SEGS + 10)
chk(f"no teto de {F.TETO_RETOMADAS} DESISTE em vez de retomar",
    des2 == [t3] and not ret2)
chk("vira FALHOU, não fica em laço", f3.buscar(t3)["estado"] == F.FALHOU)
chk("o motivo nomeia o teto", "teto" in (f3.buscar(t3)["motivo_falha"] or ""))

f4 = nova()
t4 = f4.enfileirar({"x": 2})
f4.reivindicar("w-vivo")
f4.heartbeat(t4, "w-vivo")
ret3, _ = f4.retomar_orfaos(agora=time.time() + 10)
chk("worker VIVO não é retomado", ret3 == [])

print()
print("=" * 68)
print("E) RESUMO DIÁRIO — a única rede sem a resposta HTTP")
print("=" * 68)
f5 = nova()
a = f5.enfileirar({"x": 1}, nome="Ana", email="ana@x.com")
b = f5.enfileirar({"x": 2}, nome="Bia", email="bia@x.com")
f5.reivindicar("w"); f5.falhar(a, "falha fechada de língua",
                               markdown="# texto que falhou")
f5.reivindicar("w"); f5.concluir(b, markdown="# ok")
res = f5.falhados_desde(time.time() - 3600)
chk("lista só os que FALHARAM", len(res) == 1 and res[0]["id"] == a)
chk("carrega nome e e-mail da cliente",
    res[0]["nome"] == "Ana" and res[0]["email"] == "ana@x.com")
chk("carrega o motivo", "língua" in res[0]["motivo"])
chk("a falha guarda o markdown (degrau 3 depois de falhar)",
    (f5.buscar(a)["markdown"] or "").startswith("# texto"))
chk("janela antiga não traz nada", f5.falhados_desde(time.time() + 60) == [])
chk("contagem por estado", f5.contagem_por_estado().get(F.FALHOU) == 1)

print()
# ==================================================================
# DEVOLVER — o diagnóstico não pode custar nada a quem está na fila.
#
# Defeito real (19/07): o /diag-fila roda 4 threads chamando
# `reivindicar`, que devolve QUALQUER pendente — inclusive pedido de
# cliente. A thread via que o id não era dela e retornava seco, deixando
# o trabalho preso em PROCESSANDO sem worker atrás. Dois pedidos reais
# travaram assim, e só sairiam quando um worker subisse e o
# `retomar_orfaos` os alcançasse 5 minutos depois.
# ==================================================================
print()
print("--- devolver (trabalho tocado por engano) ---")
_f2 = F.Fila(":memory:")
_f2.criar_tabelas()
_real = _f2.enfileirar({"cliente": True}, nome="Cliente Real")

_t = _f2.reivindicar("diag-0")
chk("reivindicar pega o trabalho do cliente", _t["id"] == _real)
chk("e INCREMENTA a tentativa", _f2.buscar(_real)["tentativas"] == 1)

_f2.devolver(_real, "diag-0")
_b = _f2.buscar(_real)
chk("devolver: volta para PENDENTE", _b["estado"] == F.PENDENTE)
chk("devolver: DESFAZ a tentativa", _b["tentativas"] == 0)
chk("devolver: solta o worker_id", not _b.get("worker_id"))

# Sem desfazer a tentativa, TRÊS diagnósticos matariam um trabalho que
# nunca falhou — o teto de retomadas é 2.
for _ in range(3):
    _f2.reivindicar("diag-x")
    _f2.devolver(_real, "diag-x")
chk(f"3 diagnósticos NÃO consomem o teto de {F.TETO_RETOMADAS} retomadas",
    _f2.buscar(_real)["tentativas"] == 0)

# Devolver não pode roubar trabalho de OUTRO worker.
_f2.reivindicar("worker-legitimo")
_f2.devolver(_real, "diag-intruso")
chk("devolver de outro worker NÃO tem efeito",
    _f2.buscar(_real)["estado"] == F.PROCESSANDO)

print()
# ==================================================================
# FILA PARADA — ninguém consumindo (11/08, episódio real).
#
# O serviço worker subiu com o comando herdado do railway.json da raiz
# (`gunicorn app:app`) e virou uma SEGUNDA CÓPIA DA API: verde no painel,
# healthcheck passando, e nunca tocou na fila. Dois trabalhos ficaram
# presos 2,8 dias e um novo esperou 140s sem ninguém pegar.
#
# `retomar_orfaos` NÃO cobre este caso — ele conserta worker que morreu no
# MEIO do trabalho. Worker que nunca existiu deixa tudo em PENDENTE, sem
# heartbeat, invisível para ele por definição.
# ==================================================================
print()
print("--- fila parada (ninguém consumindo) ---")
_f3 = F.Fila(":memory:")
_f3.criar_tabelas()
chk("fila vazia não acusa espera", _f3.espera_do_mais_antigo() is None)

_t3 = _f3.enfileirar({"cliente": True}, nome="Helena")
chk("pedido recém-enfileirado tem espera ~0",
    0 <= (_f3.espera_do_mais_antigo() or -1) < 5)

_f3.con().cursor().execute("UPDATE trabalhos SET criado_em=?",
                           (time.time() - 3600,))
_esp = _f3.espera_do_mais_antigo()
chk("pedido de 1h é reportado como 3600s", 3595 < _esp < 3605, f"{_esp:.0f}s")

# Reivindicado deixa de ser pendente: a espera mede quem AINDA não foi
# pego, não quem está sendo processado. Um worker lento não é fila parada.
_f3.reivindicar("w-vivo")
chk("trabalho já reivindicado NÃO conta como fila parada",
    _f3.espera_do_mais_antigo() is None)

# E o par com retomar_orfaos: os dois cobrem casos DIFERENTES.
_f4 = F.Fila(":memory:")
_f4.criar_tabelas()
_t4 = _f4.enfileirar({"x": 1})
_f4.con().cursor().execute("UPDATE trabalhos SET criado_em=?",
                           (time.time() - 99999,))
chk("PENDENTE velho: retomar_orfaos NÃO vê (não é o caso dele)",
    _f4.retomar_orfaos() == ([], []))
chk("mas espera_do_mais_antigo VÊ",
    (_f4.espera_do_mais_antigo() or 0) > 99000)

if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("FILA 19/07: TUDO PROVADO")


print()
print("=" * 68)
print("F) DEGRAU 3 — /remontar-pdf")
print("=" * 68)
import inspect as _insp
import app as _app

_src = _insp.getsource(_app.remontar_pdf_endpoint)
chk("existe a rota /remontar-pdf",
    any(r.rule == "/remontar-pdf" for r in _app.app.url_map.iter_rules()))
chk("exige chave de API", "unauthorized" in _src)
chk("recebe {id, markdown}", '"id"' in _src and '"markdown"' in _src)
chk("NÃO regera texto (não chama generate_report)",
    "generate_report" not in _src and "rg.generate" not in _src)
chk("reusa o CHART guardado — mandala e tabelas idênticas",
    'chart.get("points"' in _src and "_generate_chart_svg" in _src)
chk("recusa se não houver chart guardado", "409" in _src)
chk("guarda contra colagem errada (invariantes)",
    "divergencia_de_invariante" in _src)
chk("a guarda NÃO bloqueia se a Márcia forçar", 'body.get("forcar")' in _src)
chk("envia para o e-mail da CLIENTE", "send_report_email" in _src)
chk("registra o desfecho", "remontado_a_mao" in _src)
chk("falha ALTA se a fila não abrir", "_fila_ou_erro" in _src)

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("FILA + DEGRAU 3 19/07: TUDO PROVADO")
