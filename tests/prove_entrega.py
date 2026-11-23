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
print("=" * 68)
print("C) O ALERTA VAI PARA AS DUAS, COM O QUE FAZER")
print("=" * 68)
_alerta_orig = _app._ALERT_RECIPIENT
_app._ALERT_RECIPIENT = "dona@exemplo.com"
chk("os destinos são a Márcia e a Marcelle, sem repetir",
    _app._alerta_destinos() == ["dona@exemplo.com",
                                "executivo@marciafervienza.com"]
    and _app._alerta_destinos(["DONA@exemplo.com", "x@exemplo.com"])
    == ["dona@exemplo.com", "executivo@marciafervienza.com",
        "x@exemplo.com"])
_posts = []


class _Resp:
    status_code = 202
    text = ""


_app.requests.post = lambda url, **kw: (_posts.append((url, kw)) or _Resp())
_app._telegram_enviar = lambda *a, **k: False
_app.SENDGRID_API_KEY, _app.EMAIL_FROM_ADDRESS = "sg-falso", "de@exemplo.com"
_app._alert_last_sent.clear()
try:
    raise RuntimeError("frase quebrada")
except RuntimeError as _e:
    _app._send_failure_alert("lingua_falha_fechada", _e, {
        "name": "Pessoa Exemplo", "email": "cliente@exemplo.com",
        "birth_date": "1990-05-10", "birth_city": "Santa Rosa"})
_pl = (_posts[-1][1].get("json") or {}) if _posts else {}
_para = [p["to"][0]["email"] for p in _pl.get("personalizations", [])]
chk("o alerta de falha sai para as duas, cada uma no seu envio",
    _para == ["dona@exemplo.com", "executivo@marciafervienza.com"],
    str(_para))
_corpo_alerta = (_pl.get("content") or [{}])[0].get("value", "")
chk("abre dizendo O QUE FAZER, em português de gente",
    _corpo_alerta.startswith("O QUE FAZER:")
    and "senha da equipe" in _corpo_alerta
    and "avise a Márcia" in _corpo_alerta)
chk("e traz os dados para refazer o pedido",
    "Pessoa Exemplo" in _corpo_alerta and "cliente@exemplo.com" in _corpo_alerta
    and "1990-05-10" in _corpo_alerta and "Santa Rosa" in _corpo_alerta)

_brutos = []
_falha_para = {"dona@exemplo.com": 1}


def _bruto_falso(assunto, texto, destino=None, **k):
    if _falha_para.get(destino, 0) > 0:
        _falha_para[destino] -= 1
        return False
    _brutos.append(destino)
    return True


_app._enviar_email_bruto = _bruto_falso
_app._time = _Relogio()
_app._alerta_com_retry("assunto", "texto")
chk("alerta com retry: as duas recebem, e quem já recebeu NÃO recebe "
    "de novo quando a outra precisa de nova tentativa",
    sorted(_brutos) == ["dona@exemplo.com",
                        "executivo@marciafervienza.com"], str(_brutos))
_app._time = _tempo_orig
_app._ALERT_RECIPIENT = _alerta_orig

print()
print("=" * 68)
print("D) O PAINEL SOBREVIVE AO REINÍCIO")
print("=" * 68)
_app._registra_geracao(nome="Pessoa Exemplo", desfecho="ok",
                       email_enviado=True)
_app._registra_geracao(nome="Outra Pessoa", desfecho="email_falhou",
                       email_enviado=False)
# Um reinício apaga a memória do processo. O banco fica.
with _app._geracoes_lock:
    _app._ULTIMAS_GERACOES.clear()
_gs, _fonte = _app._geracoes_para_o_painel()
chk("com a memória apagada, o painel ainda tem as duas gerações",
    _fonte == "banco"
    and [g.get("nome") for g in _gs][-2:] == ["Pessoa Exemplo",
                                              "Outra Pessoa"], str(_gs)[-200:])
chk("cada linha traz o dia, além da hora",
    all(g.get("dia") and g.get("quando") for g in _gs[-2:]))
chk("a ordem é da mais antiga para a mais nova",
    _gs[-1].get("desfecho") == "email_falhou")
_cli = _app.app.test_client()
_rp = _cli.get("/ultimas-geracoes",
               headers={"X-API-Key": os.environ["API_SECRET_KEY"]})
chk("o endereço do painel entrega do banco, e continua exigindo a chave",
    _rp.status_code == 200 and _rp.get_json().get("fonte") == "banco"
    and len(_rp.get_json()["geracoes"]) >= 2
    and _cli.get("/ultimas-geracoes").status_code == 401)
_orig_v = _app._vouchers_ou_erro
_app._vouchers_ou_erro = lambda: (None, ("sem banco", 503))
_app._registra_geracao(nome="Sem Banco", desfecho="ok", email_enviado=True)
_gs2, _fonte2 = _app._geracoes_para_o_painel()
chk("sem banco, registrar não levanta e o painel cai para a memória",
    _fonte2 == "memoria" and _gs2[-1].get("nome") == "Sem Banco")
_app._vouchers_ou_erro = _orig_v
_painel = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "painel.py"), encoding="utf-8").read()
chk("o script do painel conta 'email_falhou' entre os que não saíram",
    'if "falh" in desf:' in _painel and "g.get('dia')" in _painel)

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("ENTREGA 23/11: TUDO PROVADO")
