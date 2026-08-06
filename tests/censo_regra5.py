"""Regra dos 5° — QA nos 5 mapas + Helena/Lucca, e censo em 500 sintéticos.
Replica EXATAMENTE o caminho de produção: sign+degrees arredondados a 0.1."""
import warnings; warnings.filterwarnings("ignore")
import os
import random, contextlib, io
from datetime import date
from kerykeion import AstrologicalSubjectFactory
_ACTIVE=["Sun","Moon","Mercury","Venus","Mars","Jupiter","Saturn","Uranus","Neptune",
    "Pluto","Chiron","Mean_Lilith","Mean_North_Lunar_Node","Mean_South_Lunar_Node",
    "Ceres","Pallas","Juno","Vesta"]
_SIGN_EN={"Ari":"aries","Tau":"taurus","Gem":"gemini","Can":"cancer","Leo":"leo","Vir":"virgo","Lib":"libra","Sco":"scorpio","Sag":"sagittarius","Cap":"capricorn","Aqu":"aquarius","Pis":"pisces"}
_HN={"First_House":1,"Second_House":2,"Third_House":3,"Fourth_House":4,"Fifth_House":5,"Sixth_House":6,"Seventh_House":7,"Eighth_House":8,"Ninth_House":9,"Tenth_House":10,"Eleventh_House":11,"Twelfth_House":12}
ORDER=list(_SIGN_EN.values())
KEY={"Sun":"sun","Moon":"moon","Mercury":"mercury","Venus":"venus","Mars":"mars","Jupiter":"jupiter","Saturn":"saturn","Uranus":"uranus","Neptune":"neptune","Pluto":"pluto","Chiron":"chiron","Mean_Lilith":"lilith","Mean_North_Lunar_Node":"north_node","Mean_South_Lunar_Node":"south_node","Ceres":"ceres","Pallas":"pallas","Juno":"juno","Vesta":"vesta"}
ATTR={"Sun":"sun","Moon":"moon","Mercury":"mercury","Venus":"venus","Mars":"mars","Jupiter":"jupiter","Saturn":"saturn","Uranus":"uranus","Neptune":"neptune","Pluto":"pluto","Chiron":"chiron","Mean_Lilith":"mean_lilith","Mean_North_Lunar_Node":"mean_north_lunar_node","Mean_South_Lunar_Node":"mean_south_lunar_node","Ceres":"ceres","Pallas":"pallas","Juno":"juno","Vesta":"vesta"}

BARRED=[]
def moves_for(s):
    H=[s.first_house,s.second_house,s.third_house,s.fourth_house,s.fifth_house,s.sixth_house,
       s.seventh_house,s.eighth_house,s.ninth_house,s.tenth_house,s.eleventh_house,s.twelfth_house]
    cusp={i+1: ORDER.index(_SIGN_EN[h.sign])*30.0+round(float(h.position),1) for i,h in enumerate(H)}
    out=[]
    for slug in _ACTIVE:
        p=getattr(s,ATTR[slug])
        pos=ORDER.index(_SIGN_EN[p.sign])*30.0+round(float(p.position),1)
        h=_HN.get(p.house,0)
        if not h: continue
        nxt=(h%12)+1
        gap=(cusp[nxt]-pos)%360.0
        same=(int(pos//30)==int(cusp[nxt]//30))
        if 0.0<gap<5.0 and same:
            out.append((KEY[slug],h,nxt,round(gap,2),ORDER[int(pos//30)]))
        elif 0.0<gap<5.0:
            BARRED.append((KEY[slug],h,nxt,round(gap,2),
                           ORDER[int(pos//30)],ORDER[int(cusp[nxt]//30)]))
    return out

QA=[("carlos_ed",1974,10,15,7,58,-22.9110,-43.2094,"America/Sao_Paulo"),
 ("liza_g",1974,10,14,19,5,-30.0325,-51.2304,"America/Sao_Paulo"),
 ("gisela_d",1981,8,28,9,30,-26.9233,-49.3685,"America/Sao_Paulo"),
 ("kyle_b",1984,11,8,6,30,33.9243,-84.3785,"America/New_York"),
 ("monica_b",1955,7,22,7,0,6.2697,-75.6026,"America/Bogota"),
 ("helena",1992,9,18,9,50,-19.9227,-43.9451,"America/Sao_Paulo"),
 ("lucca",2014,6,29,9,50,33.7544657,-84.3898151,"America/New_York")]
print("=== 5 MAPAS DE QA (+ Helena e Lucca) — corpos movidos pela regra ===")
for nome,y,mo,d,h,mi,lat,lng,tz in QA:
    s=AstrologicalSubjectFactory.from_birth_data(nome,y,mo,d,h,mi,lat=lat,lng=lng,
        tz_str=tz,online=False,active_points=_ACTIVE)
    mv=moves_for(s)
    print(f"\n{nome} — {len(mv)} movido(s):")
    for pk,a,b,g,sg in mv:
        print(f"   {pk:<12} casa {a:>2} → {b:>2}   (a {g}° da cúspide, ambos em {sg})")
    for pk,a,b,g,s1,s2 in BARRED:
        print(f"   [BARRADO por signo] {pk:<10} ficaria {a}→{b} (a {g}°) mas "
              f"corpo em {s1} e cúspide em {s2} — FICA NA {a}")
    BARRED.clear()

print("\n=== CENSO 500 sintéticos ===")
r=random.Random(7); lo,hi=date(1940,1,1).toordinal(),date(2020,12,31).toordinal()
CITIES=[("SP",-23.55,-46.63,"America/Sao_Paulo"),("LIS",38.71,-9.14,"Europe/Lisbon"),
 ("NY",40.71,-74.01,"America/New_York"),("MOS",55.75,37.62,"Europe/Moscow"),
 ("REK",64.15,-21.94,"Atlantic/Reykjavik"),("TYO",35.68,139.65,"Asia/Tokyo")]
tot=0; per=[]; mx=0
for i in range(500):
    d0=date.fromordinal(r.randint(lo,hi)); mi0=r.randint(0,1439); c=CITIES[r.randrange(6)]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s=AstrologicalSubjectFactory.from_birth_data(f"S{i}",d0.year,d0.month,d0.day,
                mi0//60,mi0%60,lat=c[1],lng=c[2],tz_str=c[3],online=False,active_points=_ACTIVE)
    except Exception: continue
    n=len(moves_for(s)); per.append(n); tot+=n; mx=max(mx,n)
import statistics
print(f"mapas: {len(per)}  corpos movidos no total: {tot}")
print(f"média por mapa: {tot/len(per):.2f}   mediana: {statistics.median(per)}   máximo: {mx}")
print(f"mapas com 0 movidos: {sum(1 for n in per if n==0)}  com 1-2: {sum(1 for n in per if 1<=n<=2)}  com 3+: {sum(1 for n in per if n>=3)}")
