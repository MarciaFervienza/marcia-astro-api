"""RETRY DE CHAMADA EXTERNA — ele realmente segura?

Em 19/07 a geolocalização devolveu 429 em três rodadas e o endpoint
respondeu HTTP 400. O retry existia no meu script de teste e não no
produto: mesma classe de "o instrumento não é o produto" que a fixture
repetiu quatro vezes no mesmo dia.

Este arquivo prova as quatro propriedades que importam:
  1. erro transitório é repetido e a chamada seguinte tem sucesso
  2. erro DEFINITIVO não é repetido (não queima o tempo do cliente)
  3. esgotadas as tentativas, o erro volta — não é engolido
  4. o backoff cresce (1s, 2s, 4s), não martela o serviço

`_sleep` é injetado para o teste não esperar 7 segundos de verdade; as
esperas são registradas e conferidas.
"""
import warnings; warnings.filterwarnings("ignore")
# O retry LOGA "Error code: 429" ao repetir. O gate procura /Error/ na
# saída dos testes para pegar traceback — o log do próprio teste passando
# fazia o gate reprovar. Silencia o logger: o que este arquivo afere são
# os OK/ERRADO impressos, não o log.
import logging; logging.getLogger("natal-api").setLevel(logging.CRITICAL)
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

falhas = 0


def chk(rot, cond):
    global falhas
    if cond:
        print(f"  OK      {rot}")
    else:
        falhas += 1
        print(f"  ERRADO  {rot}")


class _Servico:
    """Falha as `n_falhas` primeiras chamadas com `exc`, depois devolve ok."""

    def __init__(self, n_falhas, exc):
        self.n_falhas = n_falhas
        self.exc = exc
        self.chamadas = 0

    def __call__(self):
        self.chamadas += 1
        if self.chamadas <= self.n_falhas:
            raise self.exc
        return "coordenadas"


print("=" * 62)
print("RETRY COM BACKOFF")
print("=" * 62)

# --- 1. transitório: repete e chega ao sucesso -------------------------
esperas = []
svc = _Servico(2, Exception("Non-successful status code 429"))
res, err = app._com_retry(svc, rotulo="teste", _sleep=esperas.append)
chk("429 duas vezes → sucesso na terceira", res == "coordenadas" and err is None)
chk(f"chamou o serviço 3 vezes (chamou {svc.chamadas})", svc.chamadas == 3)
chk(f"esperou entre as tentativas ({len(esperas)} esperas)", len(esperas) == 2)

# --- 4. o backoff CRESCE ----------------------------------------------
chk(f"backoff crescente e ≥ 1s, 2s: {[round(e,1) for e in esperas]}",
    len(esperas) == 2 and 1.0 <= esperas[0] < esperas[1] and esperas[1] >= 2.0)

esperas4 = []
svc4 = _Servico(3, Exception("503 Service Unavailable"))
app._com_retry(svc4, rotulo="teste", _sleep=esperas4.append)
chk(f"terceira espera ≥ 4s (dobra a cada vez): {[round(e,1) for e in esperas4]}",
    len(esperas4) == 3 and esperas4[2] >= 4.0)

# --- 2. definitivo: NÃO repete -----------------------------------------
esperas2 = []
svc2 = _Servico(9, ValueError("Cidade de nascimento não encontrada"))
res2, err2 = app._com_retry(svc2, rotulo="teste", _sleep=esperas2.append)
chk("erro definitivo não é repetido (1 chamada só)", svc2.chamadas == 1)
chk("erro definitivo volta para quem chamou", res2 is None and err2 is not None)
chk("erro definitivo não dorme", esperas2 == [])

# --- 3. esgotou: o erro volta, não some --------------------------------
esperas3 = []
svc3 = _Servico(99, Exception("Non-successful status code 429"))
res3, err3 = app._com_retry(svc3, tentativas=4, rotulo="teste", _sleep=esperas3.append)
chk("esgotadas as tentativas, devolve o erro", res3 is None and "429" in str(err3))
chk(f"tentou 4 vezes e parou (tentou {svc3.chamadas})", svc3.chamadas == 4)

# --- classificação dos sinais ------------------------------------------
print()
print("Classificação transitório × definitivo:")
for msg, quer in [
    ("Non-successful status code 429", True),
    ("HTTPError 502 Bad Gateway", True),
    ("503 Service Unavailable", True),
    ("Read timed out", True),
    ("GeocoderUnavailable", True),
    ("Too Many Requests", True),
    ("Cidade de nascimento não encontrada: Xpto", False),
    ("Campo 'birth_city' obrigatório", False),
    ("geopy não instalado", False),
    ("Non-successful status code 404", False),
]:
    got = app._erro_transitorio(Exception(msg))
    chk(f"{'repete ' if quer else 'não rep'} {msg[:44]!r}", got == quer)

# --- o geocode REAL usa o retry ----------------------------------------
print()
import inspect
src = inspect.getsource(app._geocode_birth_city)
chk("_geocode_birth_city passa pelo _com_retry", "_com_retry" in src)
chk("não sobrou chamada direta a geolocator.geocode fora do retry",
    src.count("geolocator.geocode") == 1 and "lambda" in src)

print()
if falhas:
    print(f">>> {falhas} FALHOU — o retry não segura")
    raise SystemExit(1)
print("RETRY 19/07: TUDO PROVADO")


# ============================================================
# call_claude também repete? (19/07)
# Um relatório faz 16+ chamadas; sem retry, um 429 derruba tudo.
# ============================================================
print()
print("=" * 62)
print("call_claude PASSA PELO RETRY")
print("=" * 62)
import report_generator as rg

src_cc = inspect.getsource(rg.call_claude)
chk("call_claude usa _com_retry", "_com_retry" in src_cc)
chk("call_claude propaga o erro quando esgota", "raise exc" in src_cc)

# prova de comportamento, não só de forma: 429 duas vezes e sucesso
_chamadas = {"n": 0}


class _FakeMsgs:
    def create(self, **kw):
        _chamadas["n"] += 1
        if _chamadas["n"] <= 2:
            raise Exception("Error code: 429 - rate_limit_error")

        class _R:
            content = [type("B", (), {"text": "  texto da seção  "})()]
        return _R()


_orig_anth, _orig_init = rg._anth, rg.init_clients
rg._anth = type("A", (), {"messages": _FakeMsgs()})()
rg.init_clients = lambda: None
# o retry dorme via retry_util._time.sleep — patchar rg.time.sleep
# não teria efeito nenhum e o teste dormiria 3s de verdade
import retry_util as _ru
_orig_sleep = _ru._time.sleep
_ru._time.sleep = lambda s: None
try:
    out = rg.call_claude("prompt qualquer")
    chk(f"429 duas vezes → texto na terceira (chamou {_chamadas['n']})",
        out == "texto da seção" and _chamadas["n"] == 3)
finally:
    _ru._time.sleep = _orig_sleep
    rg._anth, rg.init_clients = _orig_anth, _orig_init

print()
if falhas:
    print(f">>> {falhas} FALHOU — o retry não segura")
    raise SystemExit(1)
print("RETRY call_claude 19/07: TUDO PROVADO")
