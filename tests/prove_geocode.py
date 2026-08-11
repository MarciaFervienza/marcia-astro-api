"""PROVA DA CADEIA DE GEOCODIFICAÇÃO (19/07).

O Nominatim bloqueia o IP do Railway por política de datacenter — provado
comparando produção (429 em tudo) com a máquina da Márcia (200 imediato,
mesmas coordenadas). A queda para o Photon existe por causa disso.

As asserções de LÓGICA usam duplos determinísticos: um teste de cadeia de
provedores que depende de rede é um teste que vai falhar por motivo
errado e ser silenciado. A medição contra os provedores reais fica em
`--rede`, fora do gate.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import geocode_util as gu  # noqa: E402

falhas = []


def checa(nome, cond, detalhe=""):
    print(f"{'OK   ' if cond else 'FALHA'} {nome}" + (f"   → {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


_BH = [{"lat": -19.9227, "lng": -43.9451,
        "rotulo": "Belo Horizonte, Minas Gerais, Brasil"}]


def _com_cadeia(nominatim, photon):
    """Troca as implementações e devolve buscar_bruto('x')."""
    orig = dict(gu._IMPL)
    try:
        gu._IMPL["nominatim"] = nominatim
        gu._IMPL["photon"] = photon
        return gu.buscar_bruto("Belo Horizonte, MG, Brasil")
    finally:
        gu._IMPL.clear()
        gu._IMPL.update(orig)


def _ok(res):
    return lambda q, limit: res


def _explode(msg):
    def _f(q, limit):
        raise RuntimeError(msg)
    return _f


# ------------------------------------------------------------------ 1
# Caminho normal: o Nominatim responde e o Photon NEM É CHAMADO. É o que
# preserva os 7 mapas que a Márcia já validou.
_tocou = {"photon": False}


def _photon_espiao(q, limit):
    _tocou["photon"] = True
    return [{"lat": 0.0, "lng": 0.0, "rotulo": "Errado, Errado, Errado"}]


_res, _prov, _err = _com_cadeia(_ok(_BH), _photon_espiao)
checa("1. Nominatim respondendo: resolve por ele", _prov == "nominatim" and not _err,
      f"provedor={_prov!r} erro={_err!r}")
checa("1b. Nominatim respondendo: Photon NÃO é consultado",
      _tocou["photon"] is False,
      "consultar os dois dobraria a carga e poderia divergir")

# ------------------------------------------------------------------ 2
# Nominatim com 429 (o caso real): cai para o Photon e resolve.
_res, _prov, _err = _com_cadeia(_explode("Non-successful status code 429"),
                                _ok(_BH))
checa("2. Nominatim em 429: cai para o Photon",
      _prov == "photon" and not _err and _res == _BH,
      f"provedor={_prov!r} erro={str(_err)[:120]!r}")

# ------------------------------------------------------------------ 3
# Nominatim respondendo VAZIO não é erro — é "essa cidade não existe".
# Cair para o Photon aqui trocaria um erro claro por uma coincidência:
# um provedor pode inventar uma correspondência que o outro recusou, e
# a cliente receberia um mapa de outro lugar sem nunca ver um aviso.
_res, _prov, _err = _com_cadeia(_ok([]), _photon_espiao)
checa("3. resposta VAZIA é definitiva, não cai para o próximo",
      _prov == "nominatim" and _res == [] and not _err,
      f"provedor={_prov!r} n={len(_res)}")

# ------------------------------------------------------------------ 4
# Os dois fora: erro, nunca coordenada inventada.
_res, _prov, _err = _com_cadeia(_explode("429"), _explode("503"))
checa("4. cadeia inteira fora: devolve ERRO, não coordenada",
      _res == [] and _prov is None and bool(_err), f"erro={str(_err)[:90]!r}")
checa("4b. o erro nomeia os DOIS provedores",
      "nominatim" in (_err or "") and "photon" in (_err or ""),
      "sem os nomes ninguém sabe o que caiu")

# ------------------------------------------------------------------ 5
# FORMATO DO RÓTULO. `_ambiguidade_real` parte por vírgula e lê a ÚLTIMA
# parte como país. Um rótulo do Photon com outro formato faria a detecção
# de ambiguidade CALAR — e é ela que impede o "Santa Rosa → Califórnia",
# que já produziu um mapa errado com aparência perfeita.
_rot = gu._rotulo_photon({"name": "Santa Rosa", "state": "Rio Grande do Sul",
                          "country": "Brasil"})
checa("5. rótulo do Photon termina no país",
      _rot.split(",")[-1].strip() == "Brasil", f"rotulo={_rot!r}")

import app  # noqa: E402

_amb = app._ambiguidade_real([
    {"rotulo": gu._rotulo_photon({"name": "Santa Rosa",
                                  "state": "Rio Grande do Sul",
                                  "country": "Brasil"}),
     "tz": "America/Sao_Paulo"},
    {"rotulo": gu._rotulo_photon({"name": "Santa Rosa", "state": "California",
                                  "country": "United States"}),
     "tz": "America/Los_Angeles"},
])
checa("5b. ambiguidade AINDA é detectada em rótulo do Photon", bool(_amb),
      f"amb={_amb}")

# ------------------------------------------------------------------ 6
# FONTE ÚNICA: busca (autocomplete) e geocodificação (geração) passam
# pela MESMA rotina. Se divergissem, o city_id que a cliente escolheu
# poderia não ser onde o mapa é feito — e ninguém veria.
import inspect  # noqa: E402

_sb = inspect.getsource(app.buscar_cidades)
_sg = inspect.getsource(app._geocode_birth_city)
checa("6. busca e geocodificação usam a MESMA rotina",
      "geocode_util.buscar_bruto" in _sb and "geocode_util.buscar_bruto" in _sg)
checa("6b. nenhuma das duas chama Nominatim direto",
      "Nominatim(" not in _sb and "Nominatim(" not in _sg,
      "chamada direta escaparia da queda")

# ------------------------------------------------------------------ 7
# REINJEÇÃO: sem a queda, o 429 volta a ser erro fatal. Prova que as
# asserções 2 e 4 mordem, em vez de passarem por vacuidade.
_orig_prov = gu.PROVEDORES
try:
    gu.PROVEDORES = ("nominatim",)
    _res, _prov, _err = _com_cadeia(_explode("429"), _ok(_BH))
    checa("7. reinjeção: sem a queda, o 429 volta a ser fatal",
          bool(_err) and _prov is None, f"provedor={_prov!r}")
finally:
    gu.PROVEDORES = _orig_prov

# ------------------------------------------------------------------ 8
# O FILTRO DE LUGAR, com a resposta REAL que o Photon devolveu para
# "Atlanta, Georgia, USA" em 19/07. Sem filtro, a PRIMEIRA resposta é uma
# oficina mecânica — coordenada plausível, aparência perfeita, mapa de
# outra pessoa. Gravada aqui porque a peça mais perigosa do módulo não
# pode ter prova que dependa de o Photon estar no ar.
_ATLANTA_CRU = {"features": [
    {"geometry": {"coordinates": [-84.30, 33.85]},
     "properties": {"osm_key": "shop", "osm_value": "car_repair",
                    "type": "house", "name": "Simba Motors USA LLC",
                    "state": "Georgia", "country": "United States"}},
    {"geometry": {"coordinates": [-84.35, 33.79]},
     "properties": {"osm_key": "amenity", "osm_value": "clinic",
                    "type": "house", "name": "Caduceus USA",
                    "state": "Georgia", "country": "United States"}},
    {"geometry": {"coordinates": [-84.32, 33.81]},
     "properties": {"osm_key": "landuse", "osm_value": "industrial",
                    "type": "locality", "name": "Argos USA",
                    "state": "GA", "country": "United States"}},
]}
checa("8. filtro rejeita POI: nenhuma loja vira cidade",
      gu.filtra_lugares(_ATLANTA_CRU) == [],
      f"deixou passar {gu.filtra_lugares(_ATLANTA_CRU)}")

_BH_CRU = {"features": [
    {"geometry": {"coordinates": [-43.9451, -19.9227]},
     "properties": {"osm_key": "place", "osm_value": "municipality",
                    "type": "city", "name": "Belo Horizonte",
                    "state": "Minas Gerais", "country": "Brasil"}},
    {"geometry": {"coordinates": [-43.95, -19.85]},
     "properties": {"osm_key": "aeroway", "osm_value": "aerodrome",
                    "type": "house", "name": "Aeroporto da Pampulha",
                    "state": "Minas Gerais", "country": "Brasil"}},
]}
_f = gu.filtra_lugares(_BH_CRU)
checa("8b. filtro aceita a cidade e descarta o aeroporto",
      len(_f) == 1 and _f[0]["rotulo"].startswith("Belo Horizonte"),
      f"{[x['rotulo'] for x in _f]}")

# ------------------------------------------------------------------ 9
# SOLTAR SEGMENTO. "USA" envenena o Photon (casa com nome de comércio) e
# nenhum lugar sobra; sem soltar o segmento, Atlanta — o mapa do Lucca —
# ficaria sem geocodificação na queda.
_pedidos = []


def _cru_falso(q, limit):
    _pedidos.append(q)
    return [] if "USA" in q else [{"lat": 33.7545, "lng": -84.3898,
                                   "rotulo": "Atlanta, Georgia, United States"}]


_orig_cru = gu._photon_cru
try:
    gu._photon_cru = _cru_falso
    _r = gu._via_photon("Atlanta, Georgia, USA", 5)
finally:
    gu._photon_cru = _orig_cru
checa("9. solta o último segmento quando nada é achado",
      len(_r) == 1 and _r[0]["rotulo"].startswith("Atlanta"),
      f"consultou {_pedidos}")
checa("9b. tenta a consulta INTEIRA primeiro",
      _pedidos and _pedidos[0] == "Atlanta, Georgia, USA",
      f"primeira consulta foi {_pedidos[0]!r}" if _pedidos else "não consultou")

# ------------------------------------------------------------------ rede
if "--rede" in sys.argv:
    import math
    print("\n--- medição contra os provedores REAIS ---")
    VERIF = [("Belo Horizonte, MG, Brasil", -19.9227, -43.9451),
             ("Atlanta, Georgia, USA", 33.7545, -84.3898),
             ("Rio de Janeiro, RJ, Brasil", -22.9068, -43.1729),
             ("São Paulo, SP, Brasil", -23.5505, -46.6333),
             ("Porto Alegre, RS, Brasil", -30.0346, -51.2177),
             ("Curitiba, PR, Brasil", -25.4284, -49.2733),
             ("Santa Rosa, RS, Brasil", -27.8702, -54.4797)]
    for q, lat, lng in VERIF:
        try:
            r = gu._via_photon(q, 3)
            d = math.hypot((lat - r[0]["lat"]) * 111.0,
                           (lng - r[0]["lng"]) * 111.0 * math.cos(math.radians(lat)))
            # 25 km cobre diferença de centroide de município; acima disso
            # já não é a mesma cidade.
            checa(f"rede: Photon acerta {q.split(',')[0]}", d < 25.0,
                  f"{d:.1f} km — {r[0]['rotulo'][:50]}")
        except Exception as exc:                        # noqa: BLE001
            checa(f"rede: Photon acerta {q.split(',')[0]}", False, repr(exc))

print()
if falhas:
    print(f">>> {len(falhas)} FALHOU: {falhas}")
    sys.exit(1)
print(">>> CADEIA DE GEOCODIFICAÇÃO: TUDO PROVADO")
