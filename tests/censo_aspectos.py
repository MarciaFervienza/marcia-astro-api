"""Distribuição da contagem de aspectos em 500 mapas.
Mede LIMITES, não estimativa: piso = NatalAspects cru; teto = cru + todos os
pares manuais candidatos (asteroides×planetas, nodos×planetas, nodos×asteroides)
dentro do orbe. Produção fica entre os dois — verificado na Helena e no Lucca."""
import warnings; warnings.filterwarnings("ignore")
import os
import sys, random, io, contextlib, statistics
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from kerykeion import AstrologicalSubjectFactory, NatalAspects
import app

PT_ASP={"conjunction":"conjunção","opposition":"oposição","trine":"trígono",
        "square":"quadratura","sextile":"sextil"}
PRINCIPAIS=["sun","moon","mercury","venus","mars","jupiter","saturn","uranus","neptune","pluto"]
ASTER=["ceres","pallas","juno","vesta"]; NODOS=["mean_north_lunar_node"]
ANG={"conjunção":0,"sextil":60,"quadratura":90,"trígono":120,"oposição":180}
ORBE_MAX=8.0

# ÂNGULOS FORA: produção descarta todo aspecto cujo corpo não esteja em
# _KER_TO_KEY, e Ascendente/Meio-do-Céu NÃO estão lá. A Márcia não usa
# aspectos a ângulos. O censo anterior os incluía — inflado, descartado.
_KER_KEYS={"Sun","Moon","Mercury","Venus","Mars","Jupiter","Saturn","Uranus",
 "Neptune","Pluto","Chiron","Mean_Lilith","Mean_North_Lunar_Node",
 "Mean_South_Lunar_Node","Ceres","Vesta","Juno","Pallas"}

def conta(s):
    cru=sum(1 for a in NatalAspects(s).relevant_aspects
            if a.aspect in PT_ASP and a.p1_name in _KER_KEYS and a.p2_name in _KER_KEYS)
    pos={}
    for n in PRINCIPAIS+ASTER+NODOS:
        p=getattr(s,n,None)
        if p is not None: pos[n]=float(p.abs_pos)
    manuais=0
    pares=[(a,b) for a in ASTER for b in PRINCIPAIS]+ \
          [(a,b) for a in NODOS for b in PRINCIPAIS]+ \
          [(a,b) for a in NODOS for b in ASTER]
    for x,y in pares:
        if x not in pos or y not in pos: continue
        d=abs(((pos[x]-pos[y]+180)%360)-180)
        if any(abs(d-ang)<=ORBE_MAX for ang in ANG.values()): manuais+=1
    return cru, cru+manuais

CITIES=[("SP",-23.55,-46.63,"America/Sao_Paulo"),("LIS",38.71,-9.14,"Europe/Lisbon"),
 ("RIO",-22.91,-43.21,"America/Sao_Paulo"),("POA",-30.03,-51.23,"America/Sao_Paulo"),
 ("BEL",-1.46,-48.50,"America/Belem"),("NY",40.71,-74.01,"America/New_York"),
 ("LDN",51.51,-0.13,"Europe/London"),("MOS",55.75,37.62,"Europe/Moscow"),
 ("REK",64.15,-21.94,"Atlantic/Reykjavik"),("TYO",35.68,139.65,"Asia/Tokyo")]
r=random.Random(7); lo,hi=date(1940,1,1).toordinal(),date(2020,12,31).toordinal()
cru_l,teto_l=[],[]
for i in range(500):
    d0=date.fromordinal(r.randint(lo,hi)); mi=r.randint(0,1439); c=CITIES[r.randrange(10)]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s=AstrologicalSubjectFactory.from_birth_data(f"S{i}",d0.year,d0.month,d0.day,
              mi//60,mi%60,lat=c[1],lng=c[2],tz_str=c[3],online=False,
              active_points=app.ACTIVE_POINTS)
    except Exception: continue
    a,b=conta(s); cru_l.append(a); teto_l.append(b)

def pct(v,p): 
    v=sorted(v); k=(len(v)-1)*p/100
    import math
    f,c=math.floor(k),math.ceil(k)
    return v[int(k)] if f==c else v[f]+(v[c]-v[f])*(k-f)

print(f"{len(cru_l)} mapas\n")
print(f"{'':>8} {'mediana':>8} {'p90':>6} {'p99':>6} {'máx':>6}   {'>34':>6}")
for lab,v in (("PISO",cru_l),("TETO",teto_l)):
    acima=100*sum(1 for x in v if x>34)/len(v)
    print(f"{lab:>8} {statistics.median(v):>8.0f} {pct(v,90):>6.0f} {pct(v,99):>6.0f} "
          f"{max(v):>6} {acima:>5.1f}%")
print("\nreferência (produção conhecida): Helena 27, Lucca 28")
h=AstrologicalSubjectFactory.from_birth_data("H",1992,9,18,9,50,lat=-19.9227318,
  lng=-43.9450948,tz_str="America/Sao_Paulo",online=False,active_points=app.ACTIVE_POINTS)
print(f"  Helena — piso {conta(h)[0]}, teto {conta(h)[1]}, produção 27")
for lim in (34,):
    print(f"\n%% dos mapas acima de {lim} aspectos:")
    print(f"   pelo PISO: {100*sum(1 for x in cru_l if x>lim)/len(cru_l):.1f}%")
    print(f"   pelo TETO: {100*sum(1 for x in teto_l if x>lim)/len(teto_l):.1f}%")
