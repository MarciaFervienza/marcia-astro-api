"""Censo: 500 mapas sintéticos x 2 seeds, as 7 propriedades, fabrica vs packing.

Criterio da Marcia, verbatim: "zero corpo em casa ou signo errado nos 500 mapas,
duas seeds. Nao 63.5%, nao 5%. Zero."

A 7a propriedade (compressao) entra agora com o mesmo criterio: zero.
Os mapas sao sinteticos DE PROPOSITO — 1000 datas/lugares aleatorios cobrem
configuracoes que nenhuma lista de clientes cobriria.
"""
import warnings; warnings.filterwarnings("ignore")
import sys, random, io, contextlib
from datetime import date
from kerykeion import AstrologicalSubjectFactory
from props import ACTIVE_POINTS, PROPS, check_all
from prove_bite import stock_svg
import packing

CITIES = [
    ("Sao Paulo",   -23.55, -46.63, "America/Sao_Paulo"),
    ("Lisboa",       38.71,  -9.14, "Europe/Lisbon"),
    ("Rio",         -22.91, -43.21, "America/Sao_Paulo"),
    ("Porto Alegre",-30.03, -51.23, "America/Sao_Paulo"),
    ("Belem",        -1.46, -48.50, "America/Belem"),
    ("Nova York",    40.71, -74.01, "America/New_York"),
    ("Londres",      51.51,  -0.13, "Europe/London"),
    ("Moscou",       55.75,  37.62, "Europe/Moscow"),
    ("Reykjavik",    64.15, -21.94, "Atlantic/Reykjavik"),
    ("Toquio",       35.68, 139.65, "Asia/Tokyo"),
]

def subjects(seed, n):
    r = random.Random(seed)
    lo, hi = date(1940,1,1).toordinal(), date(2020,12,31).toordinal()
    made = 0
    while made < n:
        d = date.fromordinal(r.randint(lo, hi))
        mi = r.randint(0, 1439)
        c = CITIES[r.randrange(len(CITIES))]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                s = AstrologicalSubjectFactory.from_birth_data(
                    f"S{made}", d.year, d.month, d.day, mi//60, mi%60,
                    lat=c[1], lng=c[2], tz_str=c[3], online=False,
                    active_points=ACTIVE_POINTS)
        except Exception:
            continue
        made += 1
        yield s, f"{d.day:02d}/{d.month:02d}/{d.year} {mi//60:02d}:{mi%60:02d} {c[0]}"

def run(mode, seed, n):
    """mode: 'fabrica' | 'packing'. Retorna (violacoes_por_prop, mapas_ruins, corpos)."""
    per = {name: 0 for name, _ in PROPS}
    bad_maps = 0; bodies = 0; worst = []
    for s, desc in subjects(seed, n):
        if mode == "packing": packing.install()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                svg = stock_svg(s)
        finally:
            packing.uninstall()
        res = check_all(s, svg)
        bodies += len(s.__dict__.get("_active", [])) or 18
        tot = 0
        for name, errs in res:
            per[name] += len(errs); tot += len(errs)
        if tot:
            bad_maps += 1
            if len(worst) < 5: worst.append((desc, res))
    return per, bad_maps, bodies, worst

if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    SEEDS = [7, 23]
    for seed in SEEDS:
        print(f"\n{'='*92}\nSEED {seed} — {N} mapas sinteticos\n{'='*92}")
        for mode in ("fabrica", "packing"):
            per, bad, bodies, worst = run(mode, seed, N)
            tot = sum(per.values())
            print(f"\n  {mode.upper():8} mapas com defeito: {bad}/{N}   "
                  f"violacoes totais: {tot}   corpos avaliados: {bodies}")
            for name, _ in PROPS:
                flag = "" if per[name] == 0 else "  <<<"
                print(f"      {name:<32} {per[name]:>6}{flag}")
            if mode == "packing" and worst:
                print("\n      exemplos de falha (packing):")
                for desc, res in worst[:3]:
                    for nm, errs in res:
                        for e in errs[:2]:
                            print(f"        {desc}: {e}")
