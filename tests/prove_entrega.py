"""RELATÓRIO ESCRITO E NÃO ENTREGUE NÃO É SUCESSO (23/11).

O caso real: às 18h42 de 23/11 um mapa foi escrito, o SendGrid respondeu
503 no envio, e o servidor anotou um aviso no log e devolveu "success".
Ninguém recebeu nada, ninguém foi avisado, e o código ficou gasto.

O que tem de ser verdade agora:
  - falha passageira do envio é tentada de novo, com espera;
  - falha definitiva (endereço inválido) não é repetida à toa;
  - se o e-mail não sai, a geração devolve FALHA (a casca devolve o
    código) e a equipe é avisada com os dados para refazer.
"""
import warnings; warnings.filterwarnings("ignore")
import inspect
import logging
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = os.path.join(tempfile.mkdtemp(), "prova.db")
os.environ.setdefault("API_SECRET_KEY", "chave-de-teste-" + "x" * 20)
import app as _app

falhas = 0


def chk(rot, cond, det=""):
    global falhas
    if cond:
        print(f"  OK      {rot}")
    else:
        falhas += 1
        print(f"  ERRADO  {rot}" + (f"  → {det}" if det else ""))


_dormiu = []


class _Relogio:
    @staticmethod
    def sleep(s):
        _dormiu.append(s)

    def __getattr__(self, k):
        import time
        return getattr(time, k)


_tempo_orig = _app._time
_app._time = _Relogio()
_respostas = []
_chamadas = []


def _envio_falso(**kw):
    _chamadas.append(kw)
    r = _respostas.pop(0) if _respostas else True
    if isinstance(r, Exception):
        raise r
    return r


_app.send_report_email = _envio_falso


def _tenta(*respostas):
    _respostas[:] = list(respostas)
    _chamadas.clear()
    _dormiu.clear()
    return _app._enviar_relatorio_insistindo(
        "cliente@exemplo.com", "Pessoa Exemplo", b"%PDF", "10/05/1990",
        "Santa Rosa")


print("=" * 68)
print("A) O ENVIO INSISTE NO QUE É PASSAGEIRO")
print("=" * 68)
chk("envio que dá certo de primeira: uma chamada, nenhuma espera",
    _tenta(True) is True and len(_chamadas) == 1 and _dormiu == [])
_503 = "SendGrid HTTP 503: Service Unavailable"
chk("o 503 de 23/11: tenta de novo e ENTREGA na segunda",
    _tenta(_503, True) is True and len(_chamadas) == 2 and _dormiu == [5],
    f"{len(_chamadas)} {_dormiu}")
chk("leva os mesmos dados em toda tentativa",
    all(c["to_email"] == "cliente@exemplo.com" and c["pdf_bytes"] == b"%PDF"
        for c in _chamadas))
_r = _tenta(_503, _503, _503, _503, _503, _503)
chk("SendGrid fora do ar o tempo todo: 5 tentativas, depois desiste "
    "com o erro na mão",
    _r == _503 and len(_chamadas) == 5
    and _dormiu == list(_app._EMAIL_RELATORIO_ESPERAS), f"{_r} {_dormiu}")
chk("as esperas somam minutos, não segundos nem horas",
    120 <= sum(_app._EMAIL_RELATORIO_ESPERAS) <= 600)
chk("limite de taxa (429) e erro de rede também valem nova tentativa",
    _tenta("SendGrid HTTP 429: Too Many Requests", True) is True
    and _tenta("SendGrid request failed: timeout", True) is True)
_400 = "SendGrid HTTP 400: Does not contain a valid address."
chk("endereço inválido (4xx) NÃO é repetido: uma chamada e o erro volta",
    _tenta(_400, True) == _400 and len(_chamadas) == 1 and _dormiu == [])
_log = logging.getLogger("natal-api")
_log.disabled = True
_r = _tenta(RuntimeError("quebrou"), True)
_log.disabled = False
chk("exceção no envio não derruba: conta como falha e tenta de novo",
    _r is True and len(_chamadas) == 2)

print()
print("=" * 68)
print("B) NÃO ENTREGUE É FALHA — o código volta e a equipe é avisada")
print("=" * 68)
_nuc = inspect.getsource(_app._nucleo_da_geracao)
_i_envio = _nuc.index("_enviar_relatorio_insistindo(")
_i_falha = _nuc.index('"code": "email_nao_enviado"')
_i_ok = _nuc.rindex('"status": "success"')
chk("a geração usa o envio que insiste", _i_envio > 0)
chk("e-mail não enviado devolve 502 ANTES de chegar ao 'success'",
    _i_envio < _i_falha < _i_ok
    and "}), 502" in _nuc[_i_falha:_i_falha + 400])
_trecho = _nuc[_nuc.index("if recipient and not email_sent:"):_i_falha]
chk("avisa a equipe pelos dois canais, com os dados para refazer",
    "_alerta_com_retry(" in _trecho and "body.get('name')" in _trecho
    and "recipient" in _trecho and "birth_city" in _trecho)
chk("diz à equipe que o código foi devolvido e o que fazer",
    "foi devolvido" in _trecho and "preencher o formulário de novo" in _trecho)
chk("o painel registra o desfecho como e-mail que falhou",
    'desfecho="email_falhou"' in _trecho)

# A casca devolve o código em qualquer retorno que não seja 200.
import vouchers as V
_v = V.abrir()
_cod = _v.emitir(V.ALUNA)
_v.consumir(_cod, email="cliente@exemplo.com")
_app._nucleo_da_geracao = lambda body, ctx=None: (
    {"status": "error", "code": "email_nao_enviado"}, 502)
_app.executar_geracao({"_voucher": _cod})
chk("com o 502, o código da cliente volta a ficar disponível",
    _v.consultar(_cod)["usado_em"] is None)
_app._time = _tempo_orig

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("ENTREGA 23/11: TUDO PROVADO")
