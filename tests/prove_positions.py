"""Property tests da tabela de posições e do painel de elementos.
Regra R2: confere contra o MODELO do Kerykeion, nunca contra premissa minha."""
import warnings; warnings.filterwarnings("ignore")
import os
import sys, random, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from kerykeion import AstrologicalSubjectFactory
import positions_table as pt

from app import ACTIVE_POINTS as ACTIVE
ok = True

def chk(tag, cond):
    global ok
    ok = ok and cond
    print(f"{'OK    ' if cond else '>>> FALHOU'}  {tag}")

# --- 1. elemento/modalidade corretos, corpo a corpo, contra tabela canônica
CANON_EL = {"Ari":"Fogo","Leo":"Fogo","Sag":"Fogo","Tau":"Terra","Vir":"Terra",
 "Cap":"Terra","Gem":"Ar","Lib":"Ar","Aqu":"Ar","Can":"Água","Sco":"Água","Pis":"Água"}
CANON_MO = {"Ari":"Cardinal","Can":"Cardinal","Lib":"Cardinal","Cap":"Cardinal",
 "Tau":"Fixo","Leo":"Fixo","Sco":"Fixo","Aqu":"Fixo",
 "Gem":"Mutável","Vir":"Mutável","Sag":"Mutável","Pis":"Mutável"}
bad = [(s, pt.element_of(s), CANON_EL[s]) for s in pt.SIGN_ORDER if pt.element_of(s)!=CANON_EL[s]]
chk(f"elemento de cada signo (12/12) {bad if bad else ''}", not bad)
bad = [(s, pt.modality_of(s), CANON_MO[s]) for s in pt.SIGN_ORDER if pt.modality_of(s)!=CANON_MO[s]]
chk(f"modalidade de cada signo (12/12) {bad if bad else ''}", not bad)

# --- 2. em mapas reais: soma 12 nos dois eixos, corpos certos
CITIES=[("SP",-23.55,-46.63,"America/Sao_Paulo"),("LIS",38.71,-9.14,"Europe/Lisbon"),
 ("NY",40.71,-74.01,"America/New_York"),("REK",64.15,-21.94,"Atlantic/Reykjavik")]
r=random.Random(7); lo,hi=date(1940,1,1).toordinal(),date(2020,12,31).toordinal()
somas_el, somas_mo, n_corpos, erros = set(), set(), set(), []
N=120
for i in range(N):
    d0=date.fromordinal(r.randint(lo,hi)); mi=r.randint(0,1439); c=CITIES[r.randrange(4)]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s=AstrologicalSubjectFactory.from_birth_data(f"S{i}",d0.year,d0.month,d0.day,
              mi//60,mi%60,lat=c[1],lng=c[2],tz_str=c[3],online=False,active_points=ACTIVE)
    except Exception: continue
    rows=pt.read_positions(s)
    n_corpos.add(len(rows))
    el,mo=pt.count_elements_modalities(rows)
    somas_el.add(sum(el.values())); somas_mo.add(sum(mo.values()))
    # cada corpo contado cai no elemento/modalidade do signo REAL dele
    for row in rows:
        if row["slug"] not in pt.COUNTED_BODIES: continue
        if CANON_EL[row["sign"]]!=row["element"] or CANON_MO[row["sign"]]!=row["modality"]:
            erros.append((row["nome"],row["sign"]))
    # a casa é a GEOMÉTRICA do Kerykeion
    for row in rows:
        p=getattr(s,row["slug"].lower())
        if pt.H_NUM.get(str(p.house))!=row["house"]:
            erros.append(("casa",row["nome"]))
chk(f"soma dos elementos == 12 em {N} mapas (valores vistos: {somas_el})", somas_el=={12})
chk(f"soma das modalidades == 12 em {N} mapas (valores vistos: {somas_mo})", somas_mo=={12})
chk(f"tabela mostra EXATAMENTE os corpos ativos de produção ({len(ACTIVE)}): {n_corpos}", n_corpos=={len(ACTIVE)})
chk(f"elemento/modalidade/casa corretos corpo a corpo {erros[:3] if erros else ''}", not erros)

# --- 3. mordida: se a contagem incluir asteroide, a soma estoura
salvo=list(pt.COUNTED_BODIES)
pt.COUNTED_BODIES.append("Ceres")
s=AstrologicalSubjectFactory.from_birth_data("X",1992,9,18,9,50,lat=-19.9,lng=-43.9,
  tz_str="America/Sao_Paulo",online=False,active_points=ACTIVE)
el,_=pt.count_elements_modalities(pt.read_positions(s))
chk(f"MORDIDA: incluir Ceres faz a soma virar {sum(el.values())} (≠12)", sum(el.values())==13)
pt.COUNTED_BODIES[:] = salvo

print()
print("TABELA DE POSIÇÕES + PAINEL: TUDO PROVADO" if ok else ">>> ALGO FALHOU")
