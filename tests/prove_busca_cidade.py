"""BUSCA DE CIDADE NA PÁGINA DO FORMULÁRIO (23/11).

O que tem de ser verdade:
  - a busca pública mostra rótulos e GUARDA as coordenadas de cada um;
  - o rótulo que a cliente escolheu, de volta no campo de cidade, vira
    exatamente aquelas coordenadas, sem geocodificar de novo;
  - texto digitado à mão (que não é rótulo da lista) NÃO vira escolha;
  - a busca pública tem teto por IP e não entrega coordenada nem chave;
  - sem banco, a busca ainda mostra a lista e nada levanta.
"""
import warnings; warnings.filterwarnings("ignore")
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
CA = "Santa Rosa, Califórnia, Estados Unidos"
_OPS = [
    {"id": "x1", "rotulo": CA, "lat": 38.4405, "lng": -122.7144,
     "tz": "America/Los_Angeles"},
    {"id": "x2", "rotulo": RS, "lat": -27.8707, "lng": -54.4796,
     "tz": "America/Sao_Paulo"},
    {"id": "x3", "rotulo": RS, "lat": -27.8700, "lng": -54.4800,
     "tz": "America/Sao_Paulo"},
]
_chamadas = []


def _busca_falsa(q, limit=6):
    _chamadas.append(q)
    if q == "quebra":
        return [], "provedor fora do ar"
    if q == "nenhuma":
        return [], None
    return list(_OPS), None


_app.buscar_cidades = _busca_falsa
_cli = _app.app.test_client()

print("=" * 68)
print("A) A BUSCA PÚBLICA")
print("=" * 68)
_r = _cli.get("/cidades?q=santa+rosa")
_j = _r.get_json()
chk("responde 200 sem chave nenhuma", _r.status_code == 200,
    str(_r.status_code))
chk("devolve os rótulos, sem repetir o mesmo lugar",
    _j["opcoes"] == [CA, RS], str(_j.get("opcoes")))
_bruto = _r.get_data(as_text=True)
chk("NÃO entrega coordenada, fuso, id nem a chave da API",
    "38.44" not in _bruto and "America/" not in _bruto
    and "x1" not in _bruto and os.environ["API_SECRET_KEY"] not in _bruto)
chk("menos de 3 letras nem consulta o provedor",
    _cli.get("/cidades?q=sa").get_json()["opcoes"] == []
    and "sa" not in _chamadas)
chk("cidade que não existe devolve lista vazia, não erro",
    _cli.get("/cidades?q=nenhuma").get_json() == {"status": "ok",
                                                  "opcoes": []})
_rq = _cli.get("/cidades?q=quebra")
chk("provedor fora do ar vira 502 sem vazar o erro interno",
    _rq.status_code == 502 and "provedor" not in _rq.get_data(as_text=True))

print()
print("=" * 68)
print("B) O QUE A CLIENTE ESCOLHEU É O QUE O RELATÓRIO USA")
print("=" * 68)
_e = _app._cidade_escolhida(RS)
chk("o rótulo escolhido volta com as coordenadas que a busca mostrou",
    _e is not None and abs(_e[0] - (-27.8707)) < 1e-6
    and abs(_e[1] - (-54.4796)) < 1e-6 and _e[2] == "America/Sao_Paulo"
    and _e[3] == RS, str(_e))
chk("a homônima da Califórnia é OUTRA escolha, com outro fuso",
    _app._cidade_escolhida(CA)[2] == "America/Los_Angeles")
chk("espaço em volta não atrapalha",
    _app._cidade_escolhida("  " + RS + " ") is not None)
chk("texto digitado à mão NÃO vira escolha",
    _app._cidade_escolhida("Santa Rosa") is None
    and _app._cidade_escolhida("santa rosa, rio grande do sul, brasil")
    is None and _app._cidade_escolhida("") is None
    and _app._cidade_escolhida(None) is None)
_v = V.abrir()
_v.guardar_cidade(RS, 0.0, 0.0, "Etc/UTC")
chk("a primeira gravação vale: uma busca posterior não troca a escolha",
    _app._cidade_escolhida(RS)[2] == "America/Sao_Paulo")

import inspect as _insp
_nuc = _insp.getsource(_app._nucleo_da_geracao)
_i_esc = _nuc.index("_cidade_escolhida(birth_city)")
_i_geo = _nuc.index("_geocode_birth_city(birth_city)")
chk("a geração consulta a escolha ANTES de geocodificar",
    _i_esc < _i_geo)
chk("e a checagem de ambiguidade só roda quando NÃO houve escolha",
    "if not _escolhida:\n        _ops, _err_busca = buscar_cidades" in _nuc)

print()
print("=" * 68)
print("C) TETO E DEGRADAÇÃO")
print("=" * 68)
_app._CIDADES_HITS.clear()
_cab = {"X-Forwarded-For": "203.0.113.9"}
_cods = [_cli.get("/cidades?q=santa+rosa", headers=_cab).status_code
         for _ in range(_app._CIDADES_MAX_POR_HORA + 1)]
chk("o teto por IP morde na busca seguinte ao limite",
    _cods[:-1] == [200] * _app._CIDADES_MAX_POR_HORA and _cods[-1] == 429,
    str(_cods[-3:]))
chk("outro IP não é punido",
    _cli.get("/cidades?q=santa+rosa",
             headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200)
_app._CIDADES_HITS.clear()

_orig = _app._vouchers_ou_erro
_app._vouchers_ou_erro = lambda: (None, ("sem banco", 503))
_rs = _cli.get("/cidades?q=santa+rosa")
chk("sem banco, a busca ainda mostra a lista",
    _rs.status_code == 200 and _rs.get_json()["opcoes"] == [CA, RS])
chk("e a consulta da escolha devolve None em vez de levantar",
    _app._cidade_escolhida(RS) is None)
_app._vouchers_ou_erro = _orig

print()
print("=" * 68)
print("D) A CAIXINHA DA PÁGINA")
print("=" * 68)
_p = _cli.get("/busca-cidade")
_h = _p.get_data(as_text=True)
chk("a página abre e é HTML", _p.status_code == 200
    and _p.headers["Content-Type"].startswith("text/html"))
chk("pode ser embutida no site (nenhuma trava de moldura)",
    "X-Frame-Options" not in _p.headers
    and "Content-Security-Policy" not in _p.headers)
chk("avisa a página de fora da escolha, pelo nome combinado",
    "cidade-escolhida" in _h and "postMessage" in _h)
chk("escreve os nomes como TEXTO, nunca como marcação",
    "innerHTML" not in _h and "textContent" in _h)
chk("não carrega a chave da API",
    os.environ["API_SECRET_KEY"] not in _h and "api_key" not in _h)
chk("sem travessão no que a cliente lê", "—" not in _h and "–" not in _h)

print()
print("=" * 68)
print("E) A TRANCA — cidade que não saiu da lista NÃO GERA")
print("=" * 68)
_enviados = []
_app._enviar_email_bruto = (
    lambda assunto, texto, destino=None, detalhe_out=None, html=None:
    (_enviados.append((assunto, texto, destino, html)) or True))


def _pedido(cidade, email="cliente@exemplo.com"):
    return {"name": "Pessoa Exemplo", "email": email,
            "birth_date": "1990-05-10", "birth_time": "08:30",
            "birth_city": cidade, "gender": "feminino",
            "_voucher": None}


chk("a tranca nasce DESLIGADA", _app.CIDADE_SO_DA_LISTA is False)

_geo_chamado = []
_geo_orig = _app._geocode_birth_city


def _geo_espiao(c):
    _geo_chamado.append(c)
    return None, None, None, "parou aqui de propósito"


_app._geocode_birth_city = _geo_espiao
_app._send_failure_alert = lambda *a, **k: None
_c0, _h0 = _app._nucleo_da_geracao(_pedido("Santa Rosa"))
chk("desligada, texto livre segue o caminho antigo (geocodifica)",
    _geo_chamado == ["Santa Rosa"] and _h0 == 400, f"{_geo_chamado} {_h0}")

_app.CIDADE_SO_DA_LISTA = True
_geo_chamado.clear()
_n0 = len(_enviados)
_c1, _h1 = _app._nucleo_da_geracao(_pedido("Santa Rosa"))
chk("ligada, texto livre é recusado com 422 (a casca devolve o código)",
    _h1 == 422 and _c1["code"] == "cidade_fora_da_lista", f"{_h1} {_c1}")
chk("e NÃO tenta adivinhar: nem chega a geocodificar", _geo_chamado == [])
chk("quem preencheu recebe UM e-mail",
    len(_enviados) == _n0 + 1 and _enviados[-1][2] == "cliente@exemplo.com")
_tc = _enviados[-1][1]
chk("o e-mail diz o que fazer e que o código continua disponível",
    "Buscar" in _tc and "continua disponível" in _tc
    and "executivo@marciafervienza.com" in _tc)
chk("assinado pela equipe, sem travessão, em HTML também",
    _tc.rstrip().endswith(_app._BV_ASSINATURA) and "—" not in _tc
    and "–" not in _tc and bool(_enviados[-1][3]))

# A casca: 422 devolve o código reservado.
_vv = V.abrir()
_cod = _vv.emitir(V.ALUNA)
_vv.consumir(_cod, email="cliente@exemplo.com")
_b = _pedido("Santa Rosa"); _b["_voucher"] = _cod
_app.executar_geracao(_b)
chk("o código reservado volta a ficar disponível depois da recusa",
    _vv.consultar(_cod)["usado_em"] is None)

# Cidade escolhida passa da tranca. Provado na função da decisão, e não
# rodando o núcleo: com a cidade aceita ele seguiria até a geração de
# verdade, que custa dinheiro e não cabe numa prova.
chk("ligada, cidade ESCOLHIDA na lista não é recusada pela tranca",
    _app._tranca_da_cidade_recusa(_app._cidade_escolhida(RS),
                                  "cliente@exemplo.com") is False
    and _app._tranca_da_cidade_recusa(None, "cliente@exemplo.com") is True)
chk("o núcleo decide por essa função, depois de consultar a escolha",
    _nuc.index("_cidade_escolhida(birth_city)")
    < _nuc.index("_tranca_da_cidade_recusa(_escolhida")
    < _nuc.index("_geocode_birth_city(birth_city)"))

_interno = sorted(_app._RATE_EXEMPT_EMAILS)[0]
_geo_chamado.clear()
_c3, _h3 = _app._nucleo_da_geracao(_pedido("Santa Rosa", email=_interno))
chk("e-mail interno de QA passa da tranca, como na trava de idade",
    _geo_chamado == ["Santa Rosa"] and _c3.get("code") != "cidade_fora_da_lista")
_app.CIDADE_SO_DA_LISTA = False
_app._geocode_birth_city = _geo_orig

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("BUSCA DE CIDADE 23/11: TUDO PROVADO")
