"""Bateria CP1 v2 — cobertura exigida pela auditoria."""
import warnings; warnings.filterwarnings("ignore")
import sys, os
sys.path.insert(0, os.environ.get("SP", "."))
from renderer import fixture, render, assert_render, build_pdf, MAX_LANES

DEST = "/Users/marciaqfervienza/Desktop/mapa-natal-pdfs/CP1_v2"
os.makedirs(DEST, exist_ok=True)

# Corpos elegíveis a retro usados nas fixtures (nunca Sun/Moon)
E = ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]

def n6_at(center):
    """Cluster N=6, span 20°, 2 retrógrados em corpos elegíveis."""
    span = 20.0
    step = span / 5
    bodies = []
    names = ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Neptune"]
    for i, nm in enumerate(names):
        pos = (center - span/2 + i * step) % 360
        retro = nm in ("Jupiter", "Saturn")
        bodies.append((nm, pos, retro))
    return fixture(bodies)

CASES = {}

# 1. N=6 nos 12 centros
for c in range(0, 360, 30):
    CASES[f"N6_c{c:03d}"] = n6_at(float(c))

# 2. Cluster cruzando 0° Áries
CASES["cross_aries"] = fixture([
    ("Mercury", 354.0, False), ("Venus", 357.5, True),
    ("Mars", 1.0, False), ("Jupiter", 4.5, False), ("Saturn", 6.0, True),
])

# 3. Cruzando fronteira de signo (Touro 60°)
CASES["cross_sign"] = fixture([
    ("Venus", 57.0, False), ("Mars", 59.2, False),
    ("Jupiter", 61.0, True), ("Saturn", 63.0, False),
])

# 4. Corpo colado em cúspide (asc=30 → cusp7=210; corpo a 209.95 e 210.05)
CASES["near_cusp"] = fixture([
    ("Mercury", 209.95, False), ("Venus", 210.05, False), ("Mars", 213.0, True),
])

# 5. Dois corpos na MESMA longitude
CASES["same_longitude"] = fixture([
    ("Mercury", 100.000, False), ("Venus", 100.000, True), ("Mars", 104.0, False),
])

# 6. Dois clusters próximos
CASES["two_clusters"] = fixture([
    ("Mercury", 95.0, False), ("Venus", 99.0, False), ("Mars", 103.0, True),
    ("Jupiter", 125.0, False), ("Saturn", 129.0, True), ("Neptune", 133.0, False),
])

# 7. Planeta isolado adjacente a cluster (gap 9° > 8°)
CASES["isolated_adjacent"] = fixture([
    ("Mercury", 96.0, False), ("Venus", 100.0, False), ("Mars", 104.0, False),
    ("Jupiter", 113.0, True),
])

# 8. Graus de 1 e 2 dígitos explícitos
CASES["digit_widths"] = fixture([
    ("Mercury", 65.0, False),      # 5° Gêmeos (1 dígito)
    ("Venus", 145.5, True),        # 25°30' Leão (2 dígitos)
    ("Mars", 200.25, False),       # 20°15' Libra
])

# 9. Retrógrados reais da Helena (Saturno/Urano/Netuno RX) — mapeamento flag→corpo
CASES["helena_retros"] = None  # preenchido em runtime com Kerykeion

def load_helena():
    from geopy.geocoders import Nominatim
    from timezonefinder import TimezoneFinder
    from kerykeion import AstrologicalSubjectFactory
    loc = Nominatim(user_agent="cp1/1.0", timeout=15).geocode("Belo Horizonte, MG", language="pt")
    tz = TimezoneFinder().timezone_at(lat=loc.latitude, lng=loc.longitude)
    s = AstrologicalSubjectFactory.from_birth_data("Helena", 1992,9,18,9,50,
        lat=loc.latitude, lng=loc.longitude, tz_str=tz, online=False)
    NAME = {"sun":"Sun","moon":"Moon","venus":"Venus","mars":"Mars",
            "saturn":"Saturn","uranus":"Uranus","neptune":"Neptune"}
    bodies = []
    for pn in ("sun","moon","venus","mars","saturn","uranus","neptune"):
        p = getattr(s, pn)
        bodies.append((NAME[pn], float(p.abs_pos), bool(getattr(p, "retrograde", False))))
    asc = float(s.first_house.abs_pos)
    return fixture(bodies), asc, [b for b in bodies if b[2]]

if __name__ == "__main__":
    helena_fix, helena_asc, helena_retros = load_helena()
    CASES["helena_retros"] = helena_fix

    print(f"Helena retrógrados reais (ground truth Kerykeion): {[(n, round(a,2)) for n,a,r in helena_retros]}")
    print(f"\n{'caso':<22} {'corpos':>6} {'asserções':>10}  resultado")
    print("-" * 75)

    passed, failed = 0, 0
    for name, planets in CASES.items():
        asc = helena_asc if name == "helena_retros" else 30.0
        try:
            svg, records, defs_audit = render(planets, asc_deg=asc)
            errors = assert_render(svg, planets, asc_deg=asc)
            if errors:
                failed += 1
                print(f"{name:<22} {len(planets):>6} {'':>10}  ❌ {len(errors)} erro(s)")
                for e in errors[:4]:
                    print(f"    · {e}")
            else:
                passed += 1
                n_ticks = svg.count("kr-tick")
                n_labels = svg.count("kr-label")
                n_leaders = svg.count("kr-leader")
                print(f"{name:<22} {len(planets):>6} {'t'+str(n_ticks)+'/l'+str(n_labels)+'/ld'+str(n_leaders):>10}  ✅")
                build_pdf(svg, f"{DEST}/{name}.pdf", f"CP1v2 · {name} · {len(planets)} corpos")
        except RuntimeError as e:
            failed += 1
            print(f"{name:<22} {len(planets):>6} {'':>10}  ⛔ FAIL-CLOSED: {e}")

    # 10. N=8 deve fail-closed com log
    print(f"\nN=8 (fail-closed esperado):")
    try:
        big = fixture([(n, 200.0 + i*2.0, False) for i, n in enumerate(
            ["Mercury","Venus","Mars","Jupiter","Saturn","Uranus","Neptune","Pluto"])])
        svg, _, _ = render(big)
        print("  ❌ N=8 NÃO falhou — política quebrada")
    except RuntimeError as e:
        print(f"  ⛔ {e}")

    # defs audit
    from renderer import build_defs
    _, audit = build_defs()
    print(f"\nDefs audit: originais presentes byte a byte: {audit['originals_present']}, "
          f"clones __mono adicionados: {audit['n_clones']}")
    print(f"\n{passed} passaram · {failed} falharam · PDFs em {DEST}")
