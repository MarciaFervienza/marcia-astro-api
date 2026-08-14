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

# ==================================================================
# CONFIG DO RAILWAY (11/08). Duas causas de *failure to build*, as duas
# minhas:
#   · pus uma chave `_comentario` no JSON. JSON não tem comentário, e o
#     schema do Railway declara "additionalProperties": false na raiz —
#     conferido no schema real, não suposto. Chave desconhecida derruba.
#   · mandei apontar para `api/railway.worker.json`. A raiz do
#     repositório É a pasta api, então o caminho certo é
#     `railway.worker.json`, sem prefixo.
#
# Chaves conforme https://backboard.railway.app/railway.schema.json.
# ==================================================================
print()
print("--- config do Railway ---")
import json as _json

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOPO_OK = {"$schema", "build", "deploy", "environments"}
_BUILD_OK = {"builder", "watchPatterns", "buildCommand", "dockerfilePath",
             "nixpacksConfigPath", "nixpacksPlan", "nixpacksVersion",
             "railpackVersion"}
_DEPLOY_OK = {"startCommand", "preDeployCommand", "numReplicas",
              "healthcheckPath", "healthcheckTimeout", "sleepApplication",
              "runtime", "registryCredentials", "restartPolicyType",
              "restartPolicyMaxRetries", "cronSchedule", "region",
              "multiRegionConfig", "limitOverride", "requiredMountPath",
              "overlapSeconds", "drainingSeconds", "ipv6EgressEnabled"}

for _nome in ("railway.json", "railway.worker.json"):
    _cam = os.path.join(_RAIZ, _nome)
    chk(f"{_nome} existe na RAIZ do repo (sem prefixo api/)",
        os.path.isfile(_cam))
    with open(_cam, encoding="utf-8") as _fh:
        _cfg = _json.load(_fh)
    _extra = set(_cfg) - _TOPO_OK
    chk(f"{_nome}: nenhuma chave desconhecida na raiz", not _extra, str(_extra))
    _extra_b = set(_cfg.get("build", {})) - _BUILD_OK
    chk(f"{_nome}: nenhuma chave desconhecida em build", not _extra_b, str(_extra_b))
    _extra_d = set(_cfg.get("deploy", {})) - _DEPLOY_OK
    chk(f"{_nome}: nenhuma chave desconhecida em deploy", not _extra_d, str(_extra_d))

_w = _json.load(open(os.path.join(_RAIZ, "railway.worker.json"), encoding="utf-8"))
# `-X utf8` NÃO é enfeite (11/08). Sem LANG definido o contêiner cai em
# locale C, e toda escrita dependente de locale vira ASCII. Os três
# primeiros trabalhos morreram com "'ascii' codec can't encode character
# '”'". Reproduzido com LC_ALL=C PYTHONCOERCECLOCALE=0: sem a flag, a
# escrita de uma aspa curva levanta; com ela, passa.
chk("worker: startCommand é `python -X utf8 worker.py`",
    _w["deploy"]["startCommand"] == "python -X utf8 worker.py",
    _w["deploy"].get("startCommand"))
chk("worker: a flag -X utf8 está no comando",
    "-X utf8" in _w["deploy"]["startCommand"],
    "sem ela, locale C quebra acento e aspa curva")
# O worker não escuta porta: healthcheck herdado o mataria em laço, e o
# sintoma seria "reiniciando" em vez de "parado".
chk("worker: SEM healthcheckPath", "healthcheckPath" not in _w["deploy"])
# Sair com código 0 também deixa a fila sem ninguém.
chk("worker: restartPolicy ALWAYS, não ON_FAILURE",
    _w["deploy"]["restartPolicyType"] == "ALWAYS")

_r = _json.load(open(os.path.join(_RAIZ, "railway.json"), encoding="utf-8"))
chk("a raiz continua subindo a API (o worker não pode herdar isto)",
    "gunicorn" in _r["deploy"]["startCommand"])

# UnicodeEncodeError É subclasse de ValueError. O `except ValueError` do
# núcleo existe para entrada inválida e devolve 400 com a mensagem crua —
# engolia falha de CODIFICAÇÃO e a reportava como erro da cliente, sem
# traceback e sem alerta. Uma hora diagnosticando o lugar errado.
chk("UnicodeEncodeError é subclasse de ValueError (a armadilha)",
    issubclass(UnicodeEncodeError, ValueError))
import fonte_geracao as _fg2
_src_nu = _fg2.fonte(__import__("app"))
chk("o núcleo captura UnicodeError ANTES de ValueError",
    _src_nu.index("except UnicodeError") < _src_nu.index("except ValueError as e"))
chk("erro de codificação vira 500 com traceback, não 400",
    "generate_report_unicode" in _src_nu
    and "Erro de codificação no servidor" in _src_nu)
chk("o traceback vai para o motivo de falha da fila",
    "--- traceback ---" in open(
        os.path.join(_RAIZ, "app.py"), encoding="utf-8").read())

# O worker avisa se subir sem UTF-8 — para o dia em que alguém mudar o
# comando e o sintoma voltar como "erro de texto".
_srcw = open(os.path.join(_RAIZ, "worker.py"), encoding="utf-8").read()
chk("o worker loga a codificação no arranque", "utf8_mode" in _srcw)
chk("e ALERTA se subir sem UTF-8", "WORKER SEM UTF-8" in _srcw)

# ==================================================================
# REENFILEIRAR (11/08). O teto de retomadas é 2 por desenho — trabalho
# que falha em laço não pode tentar para sempre. Mas quando a causa foi
# AMBIENTE e não o pedido (locale ASCII no contêiner do worker), os
# trabalhos que esgotaram o teto estão corretos e só precisam rodar de
# novo, A PARTIR DO PAYLOAD GUARDADO — reconstruir à mão é como a fixture
# mentiu quatro vezes num dia só.
# ==================================================================
print()
print("--- reenfileirar ---")
import app as _appmod
_src_re = inspect.getsource(_appmod.reenfileirar_endpoint)
chk("existe a rota /reenfileirar",
    "/reenfileirar" in {r.rule for r in _appmod.app.url_map.iter_rules()})
chk("exige chave de API", "compare_digest" in _src_re)
chk("usa o PAYLOAD GUARDADO, não um reconstruído",
    'f.enfileirar(t["payload"]' in _src_re)
chk("recusa se não houver payload guardado", '"não tem payload guardado"' in _src_re
    or "não tem payload guardado" in _src_re)
# A guarda que importa: duplicar trabalho em curso = dois relatórios para
# a mesma cliente, que é contra o que o heartbeat existe.
chk("RECUSA duplicar trabalho ainda em curso", "ainda_em_curso" in _src_re)
chk("mas a Márcia pode forçar", '"forcar"' in _src_re)
chk("devolve 202 com o id novo e a origem",
    '"origem": tid' in _src_re and "202" in _src_re)

# ==================================================================
# SEGREDO MALFORMADO (11/08). A ANTHROPIC_API_KEY do worker foi colada
# com uma ASPA CURVA no fim. Cabeçalho HTTP é ASCII por especificação —
# `-X utf8` NÃO conserta, testado com e sem a flag. Toda chamada ao Claude
# morria com "'ascii' codec can't encode character '”' in position 109", a
# 16 chamadas de profundidade, dentro de uma thread, dentro do SDK.
#
# A posição é o TAMANHO DA CHAVE, e por isso era idêntica em todo trabalho.
# ==================================================================
print()
print("--- segredo malformado ---")
_orig_env = dict(os.environ)
try:
    _K = "sk-ant-api03-" + "A" * 95
    for _rot, _val, _deve in (
            ("chave limpa",             _K,              False),
            ("aspa curva no fim",       _K + "\u201d",   True),
            ("aspa curva no começo",    "\u201c" + _K,   True),
            ("entre aspas RETAS",       '"' + _K + '"',  True),
            ("com acento no meio",      _K[:50] + "ã" + _K[50:], True),
    ):
        os.environ["ANTHROPIC_API_KEY"] = _val
        _r = _appmod.chaves_malformadas()
        _achou = any(m["variavel"] == "ANTHROPIC_API_KEY" for m in _r)
        chk(f"{_rot}: {'acusa' if _deve else 'passa'}", _achou == _deve,
            str([m["nome_unicode"] for m in _r])[:70])

    # A mensagem tem de NOMEAR a variável e o caractere. Erro que não diz
    # onde procurar custou uma hora de diagnóstico por eliminação.
    os.environ["ANTHROPIC_API_KEY"] = _K + "\u201d"
    _m = _appmod.chaves_malformadas()[0]
    chk("a mensagem nomeia a variável", "ANTHROPIC_API_KEY" in _m["detalhe"])
    chk("e nomeia o caractere pelo nome Unicode",
        _m["nome_unicode"] == "RIGHT DOUBLE QUOTATION MARK", _m["nome_unicode"])
    chk("e diz a posição e o tamanho",
        str(_m["posicao"]) in _m["detalhe"] and str(len(_K) + 1) in _m["detalhe"])
finally:
    os.environ.clear()
    os.environ.update(_orig_env)

# O worker RECUSA subir; a API recusa a requisição. Nos dois casos com o
# nome da variável, em vez de morrer 16 chamadas depois dentro do SDK.
_srcw2 = open(os.path.join(_RAIZ, "worker.py"), encoding="utf-8").read()
chk("o worker detecta segredo malformado no arranque",
    "chaves_malformadas()" in _srcw2)
# NÃO pode sair: SystemExit + restartPolicy ALWAYS = laço de reinício, e
# cada volta seria mais um e-mail. Trocar um modo de falha por outro.
#
# Por AST, não por grep: a primeira versão procurava a string "SystemExit"
# na fonte e reprovava por causa do COMENTÁRIO que explica a decisão.
# Asserção que confunde comentário com código é a mesma classe da
# salvaguarda que procurava substring — texto não é comportamento.
import ast as _ast

_arv_w = _ast.parse(_srcw2)
_main_w = next(n for n in _arv_w.body
               if isinstance(n, _ast.FunctionDef) and n.name == "main")
_saidas = [n for n in _ast.walk(_main_w)
           if isinstance(n, _ast.Raise) and (
               (isinstance(n.exc, _ast.Name) and n.exc.id == "SystemExit")
               or (isinstance(n.exc, _ast.Call)
                   and isinstance(n.exc.func, _ast.Name)
                   and n.exc.func.id == "SystemExit"))]
chk("o worker NÃO sai (evita laço de reinício com restartPolicy ALWAYS)",
    not _saidas, f"{len(_saidas)} raise SystemExit no main()")
chk("manda UM alerta nomeando a variável", "_alerta_com_retry(" in _srcw2)
chk("e fica acordado sem reivindicar — pedidos ficam PENDENTE",
    "Sem consumir a" in _srcw2)
chk("a API recusa a requisição com código nomeado",
    "chave_malformada" in open(os.path.join(_RAIZ, "app.py"),
                               encoding="utf-8").read())

# LISTAGEM (11/08). Operar a fila as cegas nao da: perdi o id de um
# trabalho quando o comando que o criou foi interrompido, e nao havia como
# reencontra-lo — `buscar` exige o id e `contagem_por_estado` so conta.
print()
print("--- listagem da fila ---")
_f5 = F.Fila(":memory:")
_f5.criar_tabelas()
chk("fila vazia lista nada", _f5.recentes() == [])
_ids = [_f5.enfileirar({"i": i}, nome=f"Cliente {i}") for i in range(3)]
_r5 = _f5.recentes()
chk("lista os 3", len(_r5) == 3, str(len(_r5)))
chk("do mais NOVO para o mais velho",
    [x["id"] for x in _r5] == list(reversed(_ids)))
chk("respeita o limite", len(_f5.recentes(limite=2)) == 2)
chk("traz nome e estado", _r5[0]["nome"] == "Cliente 2"
    and _r5[0]["estado"] == F.PENDENTE)
# markdown NAO vem junto: a listagem e para operar, e trazer o texto de
# todo mundo seria caro e desnecessario.
chk("NAO devolve o markdown, so se existe",
    "markdown" not in _r5[0] and "tem_markdown" in _r5[0])
_f5.reivindicar("w")
_f5.concluir(_ids[0], markdown="# texto")
_c5 = [x for x in _f5.recentes() if x["id"] == _ids[0]][0]
chk("marca tem_markdown quando ha texto", _c5["tem_markdown"] is True)
chk("e calcula a duracao", _c5["duracao_s"] is not None)

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
