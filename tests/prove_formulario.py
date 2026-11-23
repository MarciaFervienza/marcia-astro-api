"""O FORMULÁRIO PRÓPRIO (23/11).

O que tem de ser verdade:
  - erro de preenchimento aparece na hora, com o campo, e NÃO gasta
    código nem manda e-mail;
  - o pedido que chega ao gerador tem EXATAMENTE os nomes de campo de
    sempre (name, email, gender, birth_date, birth_time,
    unknown_birth_time, birth_city, report_for);
  - a porta é o código: sem código válido nada é gerado, e a página não
    carrega a chave da API;
  - a recusa do código volta para a página, e não por e-mail;
  - fila fora do ar não custa o código da cliente.
"""
import warnings; warnings.filterwarnings("ignore")
import datetime
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.path.join(_tmp, "prova.db")
os.environ.setdefault("API_SECRET_KEY", "chave-de-teste-" + "x" * 20)
import app as _app
import vouchers as V

falhas = 0


def chk(rot, cond, det=""):
    global falhas
    if cond:
        print(f"  OK      {rot}")
    else:
        falhas += 1
        print(f"  ERRADO  {rot}" + (f"  → {det}" if det else ""))


RS = "Santa Rosa, Rio Grande do Sul, Brasil"
_v = V.abrir()
_v.guardar_cidade(RS, -27.8707, -54.4796, "America/Sao_Paulo")

_app.FILA_ASSINCRONA = False
_app.VOUCHER_OBRIGATORIO = True
_app._missing_required_keys = lambda: []
_app.chaves_malformadas = lambda: []
_app._send_failure_alert = lambda *a, **k: None
_enviados = []
_app._enviar_email_bruto = (
    lambda assunto, texto, destino=None, detalhe_out=None, html=None:
    (_enviados.append((assunto, destino)) or True))
_gerados = []
_resposta_geracao = [({"status": "ok"}, 200)]


def _geracao_falsa(body, ctx=None):
    _gerados.append(dict(body))
    corpo, http = _resposta_geracao[0]
    if http != 200:
        _app._devolver_voucher(body.get("_voucher"), "prova")
    return corpo, http


_app.executar_geracao = _geracao_falsa
# A geração do formulário roda numa thread. Para as provas de A a F ela
# roda na hora, no mesmo lugar, para dar para olhar o que chegou ao
# gerador. A thread de verdade é provada na seção G.
_segundo_plano_real = _app._gerar_em_segundo_plano
_app._gerar_em_segundo_plano = (
    lambda body, ctx: _app.executar_geracao(body, ctx))
_cli = _app.app.test_client()


def _bom(**troca):
    d = {"nome": "Pessoa Exemplo", "email": "cliente@exemplo.com",
         "genero": "feminino", "data": "1990-05-10", "hora": "08:30",
         "sem_hora": False, "cidade": RS, "para_quem": "meu",
         "codigo": _v.emitir(V.ALUNA)}
    d.update(troca)
    return d


def _envia(d, ip="198.51.100.7"):
    _app._rate_ip_hits.clear()
    _app._rate_email_hits.clear()
    return _cli.post("/formulario/enviar", json=d,
                     headers={"X-Forwarded-For": ip})


print("=" * 68)
print("A) A PÁGINA")
print("=" * 68)
_p = _cli.get("/formulario")
_h = _p.get_data(as_text=True)
chk("abre e é HTML", _p.status_code == 200
    and _p.headers["Content-Type"].startswith("text/html"))
chk("pode ser embutida no site (nenhuma trava de moldura)",
    "X-Frame-Options" not in _p.headers
    and "Content-Security-Policy" not in _p.headers)
chk("NÃO carrega a chave da API",
    os.environ["API_SECRET_KEY"] not in _h and "api_key" not in _h
    and "X-API-Key" not in _h)
chk("sem travessão no que a cliente lê", "—" not in _h and "–" not in _h)
chk("os textos da cliente não usam innerHTML", "innerHTML" not in _h)
chk("tem todos os campos do formulário antigo",
    all(t in _h for t in (
        "Nome completo", "E-mail", "Em qual gênero você prefere receber",
        "Data de nascimento", "Hora de nascimento (HH:MM)",
        "Não sei minha hora de nascimento", "Cidade de nascimento",
        "Para quem é esse relatório?", "Este mapa é meu, para mim",
        "É sobre outra pessoa, e eu vou ler", "Código de acesso",
        "Gerar meu Mapa Natal")))
chk("promete 24 horas", "em até 24 horas" in _h)

print()
print("=" * 68)
print("B) ERRO DE PREENCHIMENTO — na hora, com o campo, sem gastar nada")
print("=" * 68)
_casos = [
    ("nome vazio", {"nome": ""}, "nome"),
    ("e-mail torto", {"email": "cliente@exemplo"}, "email"),
    ("gênero ausente", {"genero": ""}, "genero"),
    ("30 de fevereiro", {"data": "1990-02-30"}, "data"),
    ("data fora do formato", {"data": "10/05/1990"}, "data"),
    ("data no futuro", {"data": "2999-01-01"}, "data"),
    ("hora 25:00", {"hora": "25:00"}, "hora"),
    ("hora 9:05 (um dígito)", {"hora": "9:05"}, "hora"),
    ("cidade digitada à mão", {"cidade": "Santa Rosa"}, "cidade"),
    ("cidade vazia", {"cidade": ""}, "cidade"),
    ("para quem ausente", {"para_quem": ""}, "para"),
    ("para quem inventado", {"para_quem": "todo mundo"}, "para"),
    ("código vazio", {"codigo": ""}, "codigo"),
]
for _rot, _troca, _campo in _casos:
    _d = _bom(**_troca)
    _cod = _d["codigo"] if "codigo" not in _troca else None
    _n_e, _n_g = len(_enviados), len(_gerados)
    _j = _envia(_d).get_json()
    _intacto = (_cod is None) or _v.consultar(_cod)["usado_em"] is None
    chk(f"{_rot}: aponta o campo '{_campo}', não gera, não gasta, "
        "não manda e-mail",
        _j.get("ok") is False and _j.get("campo") == _campo
        and bool(_j.get("mensagem")) and len(_gerados) == _n_g
        and len(_enviados) == _n_e and _intacto, str(_j))

_hoje = datetime.date.today()
_dezessete = _hoje.replace(year=_hoje.year - 17).isoformat()
_d = _bom(data=_dezessete)
_j = _envia(_d).get_json()
chk("menor de 18: recusa na página, com o texto da Márcia, sem gastar",
    _j.get("campo") == "data" and "18 anos ou mais" in _j.get("mensagem", "")
    and "agendar uma consulta" in _j["mensagem"]
    and _v.consultar(_d["codigo"])["usado_em"] is None, str(_j))
_dezoito = _hoje.replace(year=_hoje.year - 18).isoformat()
chk("quem faz 18 HOJE passa",
    _envia(_bom(data=_dezoito)).get_json().get("ok") is True)
chk("corpo que não é um objeto não derruba o servidor",
    _cli.post("/formulario/enviar", data="x",
              content_type="application/json").get_json().get("ok") is False)

print()
print("=" * 68)
print("C) O PEDIDO CHEGA AO GERADOR COM OS NOMES DE SEMPRE")
print("=" * 68)
_gerados.clear()
_d = _bom(email="  Cliente@Exemplo.com ", para_quem="presente",
          genero="masculino")
_j = _envia(_d).get_json()
chk("pedido bom é aceito", _j == {"ok": True}, str(_j))
_g = _gerados[-1] if _gerados else {}
chk("nomes e valores exatos do contrato do gerador",
    _g.get("name") == "Pessoa Exemplo"
    and _g.get("email") == "cliente@exemplo.com"
    and _g.get("gender") == "masculino"
    and _g.get("birth_date") == "1990-05-10"
    and _g.get("birth_time") == "08:30"
    and _g.get("unknown_birth_time") is False
    and _g.get("birth_city") == RS
    and _g.get("report_for") == "presente", str(_g))
chk("nenhum campo com nome novo vaza para o gerador",
    set(_g) <= {"name", "email", "gender", "birth_date", "birth_time",
                "unknown_birth_time", "birth_city", "report_for",
                "_voucher"}, str(sorted(_g)))
chk("o código viaja reservado, fora dos dados do mapa",
    _g.get("_voucher") == _d["codigo"] and "codigo" not in _g
    and _v.consultar(_d["codigo"])["usado_em"] is not None)
chk("os três 'para quem' são os que o gerador reconhece sem adivinhar",
    [_app._resolver_report_for(k) for k in _app._FORM_PARA_QUEM]
    == [("a", False), ("b", False), ("c", False)])
_d = _bom(sem_hora=True, hora="")
_envia(_d)
_g = _gerados[-1]
chk("sem hora: unknown_birth_time verdadeiro e nenhum birth_time",
    _g.get("unknown_birth_time") is True and "birth_time" not in _g)
_pb = _app._parse_birth_inputs(_g.get("birth_date"), _g.get("birth_time"),
                               _g.get("unknown_birth_time"))
chk("e o gerador aceita esse par como hora desconhecida",
    "error" not in _pb and _pb.get("time_estimated") is True, str(_pb))

print()
print("=" * 68)
print("D) A PORTA É O CÓDIGO")
print("=" * 68)
_n_g, _n_e = len(_gerados), len(_enviados)
_j = _envia(_bom(codigo="MF-ZZZZ-ZZZZ")).get_json()
chk("código que não existe: recusa na página, no campo do código",
    _j.get("ok") is False and _j.get("campo") == "codigo"
    and "Não encontramos esse código" in _j.get("mensagem", ""), str(_j))
chk("e não gera nada", len(_gerados) == _n_g)
chk("e NÃO manda e-mail: a página já disse", len(_enviados) == _n_e)
_d = _bom()
_envia(_d)
_j = _envia(_d).get_json()
chk("código já usado: diz que já foi usado",
    _j.get("campo") == "codigo" and "já foi usado" in _j.get("mensagem", ""),
    str(_j))
_app.CODIGO_ADMIN = "EQUIPE-TESTE-2026-ABC"
chk("a senha da equipe também abre, como no formulário antigo",
    _envia(_bom(codigo="EQUIPE-TESTE-2026-ABC")).get_json().get("ok") is True
    and "_voucher" not in _gerados[-1])
_app.CODIGO_ADMIN = ""

print()
print("=" * 68)
print("E) QUANDO O SERVIDOR NÃO CONSEGUE")
print("=" * 68)
_resposta_geracao[0] = ({"status": "error", "message": "x"}, 500)
_d = _bom()
_j = _envia(_d).get_json()
chk("a geração falha DEPOIS de aceito: a página já disse 'recebemos', "
    "e o código volta",
    _j == {"ok": True}
    and _v.consultar(_d["codigo"])["usado_em"] is None, str(_j))
_resposta_geracao[0] = ({"status": "ok"}, 200)

_rp_orig = _app._receber_pedido


def _rp_quebra(*a, **k):
    raise RuntimeError("quebrou na porta")


_app._receber_pedido = _rp_quebra
# As duas quebras provocadas abaixo (aqui e na seção G) fazem o servidor
# registrar a exceção com o rastro completo, que é o comportamento certo.
# O gate lê "Traceback" na saída como prova quebrada, então o registro
# fica calado só durante a provocação.
import logging
_log_app = logging.getLogger("natal-api")
_log_app.disabled = True
_j = _envia(_bom()).get_json()
_log_app.disabled = False
chk("exceção ao receber: mensagem geral, sem campo, com o contato",
    _j.get("ok") is False and _j.get("campo") == ""
    and "executivo@marciafervienza.com" in _j.get("mensagem", ""), str(_j))
_app._receber_pedido = _rp_orig

_app.FILA_ASSINCRONA = True
_fila_orig = _app._fila_ou_erro


def _fila_fora():
    return None, (_app.jsonify({"status": "error",
                                "code": "fila_indisponivel"}), 503)


_app._fila_ou_erro = _fila_fora
_d = _bom()
_j = _envia(_d).get_json()
chk("fila fora do ar: mensagem geral e o código da cliente VOLTA",
    _j.get("ok") is False and _j.get("campo") == ""
    and _v.consultar(_d["codigo"])["usado_em"] is None, str(_j))
_app._fila_ou_erro = _fila_orig
_app.FILA_ASSINCRONA = False

print()
print("=" * 68)
print("F) O CAMINHO ANTIGO CONTINUA DE PÉ")
print("=" * 68)
_CH = {"X-API-Key": os.environ["API_SECRET_KEY"]}
_app._rate_ip_hits.clear()
_app._rate_email_hits.clear()
_cod = _v.emitir(V.ALUNA)
_r = _cli.post("/generate-report", headers=_CH, json={"data": {
    "name": "Pessoa Exemplo", "email": "cliente@exemplo.com",
    "gender": "feminino", "birth_date": "1990-05-10", "birth_time": "08:30",
    "birth_city": RS, "report_for": "Este mapa é meu, para mim",
    "codigo": _cod}})
chk("a API com chave (automação do Wix) segue aceitando o pedido",
    _r.status_code == 200 and _gerados[-1].get("_voucher") == _cod,
    str(_r.status_code))
chk("e sem chave continua fechada",
    _cli.post("/generate-report", json={"name": "x"}).status_code == 401)
chk("o formulário próprio NÃO aceita pedido sem passar pela validação",
    _cli.post("/formulario/enviar", json={
        "name": "x", "email": "a@b.co", "birth_city": RS,
        "codigo": _v.emitir(V.ALUNA)}).get_json().get("ok") is False)

print()
print("=" * 68)
print("F2) O TETO POR CONEXÃO CONTA RECUSA, NÃO PEDIDO ACEITO")
print("=" * 68)
# 23/11: a Marcelle, refazendo mapas com código válido, bateu no teto de
# 4 por IP, que contava pedido aceito e nunca era devolvido.
_app._rate_ip_hits.clear()
_app._rate_email_hits.clear()
_H = {"X-Forwarded-For": "203.0.113.50"}
_oks = [_cli.post("/formulario/enviar", json=_bom(email=f"c{i}@exemplo.com"),
                  headers=_H).get_json().get("ok")
        for i in range(_app._RATE_MAX_PER_IP + 6)]
chk("muitos pedidos ACEITOS da mesma conexão passam todos",
    all(o is True for o in _oks), str(_oks))
_mesmo = [_cli.post("/formulario/enviar", json=_bom(), headers=_H)
          .get_json().get("ok") for _ in range(5)]
chk("e o mesmo e-mail pode pedir vários mapas, cada um com o seu código",
    all(o is True for o in _mesmo), str(_mesmo))
_app.CODIGO_ADMIN = "EQUIPE-TESTE-2026-ABC"
_eq = [_cli.post("/formulario/enviar",
                 json=_bom(codigo="EQUIPE-TESTE-2026-ABC"), headers=_H)
       .get_json().get("ok") for _ in range(_app._RATE_MAX_PER_IP + 3)]
chk("a senha da equipe não bate em teto nenhum", all(o is True for o in _eq))
_app.CODIGO_ADMIN = ""
_H2 = {"X-Forwarded-For": "203.0.113.51"}
_ruins = [_cli.post("/formulario/enviar",
                    json=_bom(codigo="MF-ZZZZ-ZZZ%d" % (i % 8 + 2)),
                    headers=_H2).get_json()
          for i in range(_app._RATE_MAX_PER_IP + 1)]
chk("código errado conta: depois do teto, a conexão é bloqueada",
    all(j.get("campo") == "codigo" for j in _ruins[:-1])
    and _ruins[-1].get("campo") == ""
    and "bloqueados por 24 horas" in _ruins[-1].get("mensagem", ""),
    str(_ruins[-1]))
chk("bloqueada, nem código válido passa (é o que segura a adivinhação)",
    _cli.post("/formulario/enviar", json=_bom(), headers=_H2)
    .get_json().get("ok") is False)
chk("e a outra conexão segue livre",
    _cli.post("/formulario/enviar", json=_bom(), headers=_H)
    .get_json().get("ok") is True)
_app._rate_ip_hits.clear()
_app._rate_email_hits.clear()

print()
print("=" * 68)
print("G) A PÁGINA NÃO ESPERA A GERAÇÃO")
print("=" * 68)
# Em produção a fila está desligada e a geração leva de 2 a 4 minutos e
# meio. A automação do Wix escondia essa espera; o formulário não.
import threading
import time
_app._gerar_em_segundo_plano = _segundo_plano_real
_solta = threading.Event()
_fez = []


def _geracao_lenta(body, ctx=None):
    _solta.wait(10)
    _fez.append(dict(body))
    return {"status": "ok"}, 200


_app.executar_geracao = _geracao_lenta
_t0 = time.time()
_d = _bom()
_j = _envia(_d).get_json()
_dt = time.time() - _t0
chk("responde 'recebemos' SEM esperar a geração terminar",
    _j == {"ok": True} and _dt < 3 and _fez == [], f"{_dt:.1f}s {_j}")
chk("o código já está reservado quando a página responde",
    _v.consultar(_d["codigo"])["usado_em"] is not None)
_solta.set()
for _ in range(50):
    if _fez:
        break
    time.sleep(0.1)
chk("e a geração acontece em seguida, com os mesmos dados",
    len(_fez) == 1 and _fez[0].get("name") == "Pessoa Exemplo"
    and _fez[0].get("_voucher") == _d["codigo"], str(_fez))

_alertas = []
_app._send_failure_alert = lambda *a, **k: _alertas.append(a[0])


def _geracao_explode(body, ctx=None):
    raise RuntimeError("explodiu no meio")


_app.executar_geracao = _geracao_explode
_log_app.disabled = True
_th = _segundo_plano_real({"name": "Pessoa Exemplo",
                           "email": "cliente@exemplo.com"}, {"ip": "x"})
_th.join(5)
_log_app.disabled = False
chk("exceção na thread não derruba o servidor e AVISA a equipe",
    not _th.is_alive() and _alertas == ["geracao_segundo_plano"],
    str(_alertas))
chk("a thread não é daemon: num reinício educado ela termina o trabalho",
    _th.daemon is False)
_app.executar_geracao = _geracao_falsa

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("FORMULÁRIO PRÓPRIO 23/11: TUDO PROVADO")
