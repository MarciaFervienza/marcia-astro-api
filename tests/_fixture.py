"""Chart de teste montado a partir de um subject Kerykeion REAL.

Regra R1 do ESTADO: nenhuma fixture inventa dado que o Kerykeion pode
fornecer. A primeira versão deste helper tinha points sem `sign_pt` e as 16
seções falharam com KeyError — o erro era da fixture, não do código.
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
from kerykeion import AstrologicalSubjectFactory, NatalAspects

_SIGN = {"Ari":"Áries","Tau":"Touro","Gem":"Gêmeos","Can":"Câncer","Leo":"Leão",
         "Vir":"Virgem","Lib":"Libra","Sco":"Escorpião","Sag":"Sagitário",
         "Cap":"Capricórnio","Aqu":"Aquário","Pis":"Peixes"}
_EN = {"Ari":"aries","Tau":"taurus","Gem":"gemini","Can":"cancer","Leo":"leo",
       "Vir":"virgo","Lib":"libra","Sco":"scorpio","Sag":"sagittarius",
       "Cap":"capricorn","Aqu":"aquarius","Pis":"pisces"}
_H = {"First_House":1,"Second_House":2,"Third_House":3,"Fourth_House":4,
      "Fifth_House":5,"Sixth_House":6,"Seventh_House":7,"Eighth_House":8,
      "Ninth_House":9,"Tenth_House":10,"Eleventh_House":11,"Twelfth_House":12}
_K = {"Sun":"sun","Moon":"moon","Mercury":"mercury","Venus":"venus","Mars":"mars",
      "Jupiter":"jupiter","Saturn":"saturn","Uranus":"uranus","Neptune":"neptune",
      "Pluto":"pluto","Chiron":"chiron","Mean_Lilith":"lilith",
      "Mean_North_Lunar_Node":"north_node","Mean_South_Lunar_Node":"south_node",
      "Ceres":"ceres","Pallas":"pallas","Juno":"juno","Vesta":"vesta"}
_PA = {"conjunction":"conjunção","opposition":"oposição","trine":"trígono",
       "square":"quadratura","sextile":"sextil"}

HELENA = ("Helena Penteado", 1992, 9, 18, 9, 50, -19.9227318, -43.9450948,
          "America/Sao_Paulo", "feminino")
LUCCA = ("Lucca Quitete Fervienza", 2014, 6, 29, 9, 50, 33.7544657, -84.3898151,
         "America/New_York", "masculino")


def build_chart(caso=HELENA):
    nome, y, mo, d, hh, mi, lat, lng, tz, genero = caso
    s = AstrologicalSubjectFactory.from_birth_data(
        nome, y, mo, d, hh, mi, lat=lat, lng=lng, tz_str=tz,
        online=False, active_points=app.ACTIVE_POINTS)
    pts = {}
    for slug, key in _K.items():
        p = getattr(s, slug.lower(), None)
        if p is None:
            continue
        pts[key] = {"sign": _EN[str(p.sign)], "sign_pt": _SIGN[str(p.sign)],
                    "degrees": round(float(p.position), 1),
                    "house": _H.get(str(p.house))}
    asp = [{"planet_a": _K[a.p1_name], "planet_b": _K[a.p2_name],
            "type": a.aspect, "type_pt": _PA[a.aspect],
            "orb": round(float(a.orbit), 2)}
           for a in NatalAspects(s).relevant_aspects
           if a.aspect in _PA and a.p1_name in _K and a.p2_name in _K]
    # Kerykeion NÃO gera aspectos de asteróides nem de Nodos — produção os
    # computa à parte. Sem esta linha a fixture entregava um chart onde Juno
    # não aspectava nada, e uma varredura local acusou de "inventada" a
    # quadratura Mercúrio-Juno da Helena, que é real (orbe 0,4°). Chamamos a
    # rotina DE PRODUÇÃO — não uma segunda implementação (R3).
    _tem = {(frozenset((a["planet_a"], a["planet_b"])), a["type"]) for a in asp}
    for a in app._compute_missing_aspects(pts):
        if (frozenset((a["planet_a"], a["planet_b"])), a["type"]) not in _tem:
            asp.append(a)
    hs = [s.first_house, s.second_house, s.third_house, s.fourth_house,
          s.fifth_house, s.sixth_house, s.seventh_house, s.eighth_house,
          s.ninth_house, s.tenth_house, s.eleventh_house, s.twelfth_house]
    _ch = {
        "points": pts, "aspects": asp, "gender": genero, "name": nome,
        "ascendant": {"sign": _EN[str(s.first_house.sign)],
                      "sign_pt": _SIGN[str(s.first_house.sign)],
                      "degrees": round(float(s.first_house.position), 1)},
        "midheaven": {"sign": _EN[str(s.tenth_house.sign)],
                      "sign_pt": _SIGN[str(s.tenth_house.sign)],
                      "degrees": round(float(s.tenth_house.position), 1)},
        "cusps": {str(i + 1): {"sign": _EN[str(h.sign)],
                               "sign_pt": _SIGN[str(h.sign)],
                               "degrees": round(float(h.position), 1)}
                  for i, h in enumerate(hs)},
    }
    # REGRA DOS 5°: sem isto a fixture entregava um chart onde nenhum corpo
    # estava na fronteira, e toda varredura local acusava a frase que o
    # prompt EXIGE ("na fronteira entre a casa 7 e a 8"). Produção reportava
    # zero e eu reportava dois. Chamamos a rotina DE PRODUÇÃO (R3), que
    # grava house_geometric e devolve os movimentos.
    _ch["_house_moves"] = app.apply_five_degree_rule(_ch)
    return _ch
