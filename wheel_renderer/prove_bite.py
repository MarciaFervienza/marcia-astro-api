"""Prova que cada property test MORDE.

Para cada propriedade: corrompe o SVG exatamente com o defeito que ela deve
pegar, verifica que ela grita, e verifica que as OUTRAS não gritam junto
(senão a propriedade não é específica — acusa tudo e não localiza nada).

Uma propriedade que nunca falha não é um teste; é decoração. Foi o que
aconteceu a noite inteira: 34/34 com os glifos sumidos.
"""
import warnings; warnings.filterwarnings("ignore")
import re, tempfile
from kerykeion import AstrologicalSubjectFactory
from kerykeion.chart_data_factory import ChartDataFactory
from kerykeion.charts.chart_drawer import ChartDrawer
from props import (ACTIVE_POINTS, PROPS, CUSP_PROPS, read_model, read_svg,
                   check_all)


def stock_svg(subject):
    cd = ChartDataFactory.create_natal_chart_data(subject, active_points=ACTIVE_POINTS)
    ch = ChartDrawer(chart_data=cd)
    out = tempfile.mkdtemp()
    ch.save_wheel_only_svg_file(output_path=out, filename="w", style="modern",
                                remove_css_variables=True)
    return open(f"{out}/w.svg").read()


# ---- mapa SEM cluster: baseline onde tudo deve passar -------------------
def clean_subject():
    """Procura um mapa real sem nenhum corpo deslocado — o Kerykeion só
    empurra quando há colisão, então num mapa esparso ele acerta tudo."""
    import random
    from datetime import date
    random.seed(11)
    CITIES = [("SP",-23.55,-46.63,"America/Sao_Paulo"),("NY",40.71,-74.01,"America/New_York"),
              ("LDN",51.51,-0.13,"Europe/London"),("TYO",35.68,139.65,"Asia/Tokyo")]
    for i in range(400):
        d = date.fromordinal(random.randint(date(1940,1,1).toordinal(),
                                            date(2020,12,31).toordinal()))
        m = random.randint(0, 1439); c = random.choice(CITIES)
        try:
            s = AstrologicalSubjectFactory.from_birth_data(
                f"C{i}", d.year, d.month, d.day, m//60, m%60,
                lat=c[1], lng=c[2], tz_str=c[3], online=False,
                active_points=ACTIVE_POINTS)
        except Exception:
            continue
        svg = stock_svg(s)
        res = check_all(s, svg)
        if all(not e for _, e in res):
            return s, svg, f"{d.day:02d}/{m//60:02d}/{d.year} {c[0]}"
    return None, None, None


# ---- corrupções: cada uma injeta UM defeito específico ------------------
def corrupt_drop_body(svg):
    """Apaga o ChartPoint de Vesta — a que sumiu de verdade ontem."""
    return re.sub(r"<g[^>]*kr:node='ChartPoint'[^>]*kr:slug='Vesta'[^>]*>.*?</g>",
                  "", svg, flags=re.DOTALL, count=1)

def corrupt_abs_pos(svg):
    """Mente no kr:absoluteposition do Sol."""
    return re.sub(r"(kr:node='ChartPoint'[^>]*kr:absoluteposition=')[\d.]+('[^>]*kr:slug='Sun')",
                  r"\g<1>123.456\g<2>", svg, count=1)

def corrupt_display_angle(svg):
    """Empurra o Sol 40° no desenho — mantém abs_pos correto.
    É exatamente o bug do espalhamento de 8°, amplificado."""
    def f(m):
        tag = m.group(0)
        rot = re.search(r"transform='rotate\((-?[\d.]+)", tag)
        if not rot: return tag
        new = float(rot.group(1)) - 40.0
        return tag.replace(f"rotate({rot.group(1)}", f"rotate({new:.6f}")
    return re.sub(r"<g[^>]*kr:node='ChartPoint'[^>]*kr:slug='Sun'[^>]*>", f, svg, count=1)

def corrupt_tick(svg):
    """Move todos os indicadores 15° — o tick deixa de marcar a longitude real."""
    def f(m):
        tag = m.group(0)
        rot = re.search(r"rotate\((-?[\d.]+)", tag)
        new = float(rot.group(1)) - 15.0
        return tag.replace(f"rotate({rot.group(1)}", f"rotate({new:.6f}")
    return re.sub(r"<g[^>]*kr:node='Indicator'[^>]*>", f, svg)

def corrupt_cusps(svg):
    """Substitui as cúspides por asc + i*30 — o bug que eu mesmo cometi."""
    caps = re.findall(r"<g[^>]*kr:node='Cusp'[^>]*kr:absoluteposition='([\d.]+)'", svg)
    if not caps: return svg
    base = float(caps[0])
    i = [0]
    def f(m):
        tag = m.group(0)
        fake = (base + i[0] * 30.0) % 360
        i[0] += 1
        return re.sub(r"(kr:absoluteposition=')[\d.]+'", rf"\g<1>{fake:.6f}'", tag)
    return re.sub(r"<g[^>]*kr:node='Cusp'[^>]*>", f, svg)


def make_corrupt_compress(subject):
    """Aproxima dois corpos que estão longe — o defeito da Monica.

    Escolhe um par vizinho separado por 4°–20° e arrasta o segundo por cima do
    primeiro, ficando a 0.2°. Sem sair do signo: o alvo é a posição do vizinho,
    e o par é escolhido entre corpos do MESMO signo, então o deslocamento não
    cruza fronteira nenhuma. Assim só a propriedade de compressão pode gritar —
    se signo ou casa gritarem junto, a corrupção não é específica.
    """
    from props import read_model
    m = read_model(subject)
    bodies = sorted((b["abs_pos"], s) for s, b in m["bodies"].items())
    n = len(bodies)
    for i in range(n):
        pa, a = bodies[i]
        pb, b = bodies[(i + 1) % n]
        gap = (pb - pa) % 360
        if not (4.0 < gap < 20.0):
            continue
        if m["bodies"][a]["sign"] != m["bodies"][b]["sign"]:
            continue          # cruzaria fronteira de signo — não serve
        if m["bodies"][a]["house"] != m["bodies"][b]["house"]:
            continue          # cruzaria fronteira de casa
        delta = gap - 0.2     # b recua até 0.2° de a

        def fn(svg, slug=b, delta=delta):
            def f(mm):
                tag = mm.group(0)
                rot = re.search(r"transform='rotate\((-?[\d.]+)", tag)
                if not rot:
                    return tag
                new = float(rot.group(1)) + delta   # wheel angle é o negativo
                return tag.replace(f"rotate({rot.group(1)}", f"rotate({new:.6f}")
            return re.sub(rf"<g[^>]*kr:node='ChartPoint'[^>]*kr:slug='{slug}'[^>]*>",
                          f, svg, count=1)
        return fn, f"puxa {b} por cima de {a} ({gap:.1f}° -> 0.2°)"
    return None, None


CORRUPTIONS = [
    ("apaga o corpo Vesta",            corrupt_drop_body,     "todos os corpos desenhados"),
    ("mente no abs_pos do Sol",        corrupt_abs_pos,       "abs_pos do SVG == modelo"),
    ("empurra o Sol 40° no desenho",   corrupt_display_angle, "display dentro do SIGNO"),
    ("move os ticks 15°",              corrupt_tick,          "tick na longitude real"),
    ("cúspides viram asc + i*30",      corrupt_cusps,         "cúspides == modelo"),
]


if __name__ == "__main__":
    print("Procurando um mapa real sem deslocamento (baseline limpo)...")
    s, svg, desc = clean_subject()
    if s is None:
        print("  NENHUM mapa limpo encontrado — o stock desloca em todos.")
        print("  Isso ja e o resultado: a propriedade morde na fabrica inteira.")
        raise SystemExit(1)
    print(f"  baseline: {desc} — todas as {len(PROPS)} propriedades passam\n")

    print(f"{'corrupção injetada':<32} {'propriedade alvo':<28} {'mordeu?':<9} efeito colateral")
    print("-" * 100)
    all_ok = True
    for label, fn, target in CORRUPTIONS:
        bad = fn(svg)
        if bad == svg:
            print(f"{label:<32} {target:<28} {'N/A':<9} corrupção não aplicou")
            all_ok = False
            continue
        res = dict(check_all(s, bad))
        bit = len(res[target]) > 0
        others = [n for n, e in res.items() if e and n != target]
        mark = "SIM" if bit else "NAO ✗"
        if not bit: all_ok = False
        print(f"{label:<32} {target:<28} {mark:<9} {', '.join(others) if others else '(nenhum)'}")
        if bit:
            print(f"{'':32} └─ {res[target][0][:78]}")

    # a corrupção de compressão depende do mapa (precisa de um par no mesmo
    # signo e casa, separado por 4°-20°), então é construída a partir dele
    fn, label = make_corrupt_compress(s)
    if fn is None:
        print("\nsem par elegível no baseline para a corrupção de compressão")
        all_ok = False
    else:
        bad = fn(svg)
        target = "desenho não comprime"
        res = dict(check_all(s, bad))
        bit = len(res[target]) > 0
        others = [n for n, e in res.items() if e and n != target]
        print(f"{label:<32} {target:<28} {'SIM' if bit else 'NAO ✗':<9} "
              f"{', '.join(others) if others else '(nenhum)'}")
        if bit:
            print(f"{'':32} └─ {res[target][0][:78]}")
        else:
            all_ok = False

    print()
    if all_ok:
        print("Todas as propriedades mordem, e cada uma localiza o seu defeito.")
    else:
        print("ALGUMA propriedade não mordeu — não confiar nela.")

    # ---- regressão: o defeito REAL que existiu, no mapa REAL da Márcia ------
    # Sem injeção e sem síntese. Reinstala o packing POR GRUPO — a versão que
    # rodou até 15/07/2026 e produziu o PDF que a Márcia reprovou — e mostra
    # que a propriedade nova o condena. É a prova que impede o auto-engano de
    # afrouxar o teste até o código passar: ela tem que reprovar o passado.
    print("\n" + "=" * 100)
    print("REGRESSÃO: mapa da Monica, packing POR GRUPO (o defeito) vs CADEIA GLOBAL (o conserto)")
    print("=" * 100)
    import packing

    mon = AstrologicalSubjectFactory.from_birth_data(
        "Monica B", 1955, 7, 22, 7, 0, lat=6.2697, lng=-75.6026,
        tz_str="America/Bogota", online=False, active_points=ACTIVE_POINTS)

    def grouped_resolve(planets_with_angles, min_separation=8.0):
        """O resolver antigo, verbatim na sua lógica: agrupa por (casa, signo)
        e empacota cada grupo dentro da sua caixa, sem enxergar os vizinhos."""
        from props import H_NUM, SIGN_NUM
        import kerykeion.charts.draw_modern as dm
        cusps = packing._CTX["cusps"]; seventh = packing._CTX["seventh"]
        packing._CTX["scale_by_slug"] = {}
        groups = {}
        for it in planets_with_angles:
            p = it["point"]
            groups.setdefault((H_NUM[str(p.house)], SIGN_NUM[str(p.sign)]), []).append(it)
        for _key, items in groups.items():
            iv = packing.allowed_interval(items[0]["point"], cusps)
            if iv is None:
                for it in items:
                    it["display_angle"] = it["angle"]
                continue
            z_lo, span = iv
            items.sort(key=lambda it: (float(it["point"].abs_pos) - z_lo) % 360)
            trues = [(float(it["point"].abs_pos) - z_lo) % 360 for it in items]
            n = len(trues)
            m = packing.EDGE_MARGIN
            usable = max(0.0, span - 2 * m)
            sep = min(8.0, usable / (n - 1)) if n > 1 else 8.0
            u = [trues[i] - i * sep for i in range(n)]
            y = packing._pava(u)
            d = [y[i] + i * sep for i in range(n)]
            if d[0] < m:
                d = [v + (m - d[0]) for v in d]
            if d[-1] > span - m:
                d = [v - (d[-1] - (span - m)) for v in d]
            for it, dz in zip(items, d):
                it["display_angle"] = (dm._zodiac_to_wheel_angle((z_lo + dz) % 360,
                                                                seventh)) % 360
        return planets_with_angles

    for label, resolver in (("POR GRUPO (antigo)", grouped_resolve),
                            ("CADEIA GLOBAL (atual)", None)):
        packing.install()
        if resolver is not None:
            import kerykeion.charts.draw_modern as dm2
            dm2._resolve_planet_collisions = resolver
        try:
            msvg = stock_svg(mon)
        finally:
            packing.uninstall()
        res = check_all(mon, msvg)
        bad = sum(len(e) for _, e in res)
        print(f"\n  {label}: {bad} violação(ões) em 7 propriedades")
        for name, errs in res:
            if errs:
                print(f"    {name}")
                for e in errs:
                    print(f"      {e}")
        if not bad:
            print("    (todas as 7 passam)")

    # ---- prova que a propriedade de cúspide morde ---------------------------
    print("\n" + "=" * 100)
    print("PROPRIEDADE DE CÚSPIDE (contrato 16/07: todas inteiras, corpos sentam sobre a linha)")
    print("=" * 100)

    def check_cusp(subject, svg_text):
        model = read_model(subject); drawn = read_svg(svg_text)
        name, fn = CUSP_PROPS[0]
        return fn(model, drawn)

    packing.install()
    try:
        msvg = stock_svg(mon)
    finally:
        packing.uninstall()

    # (a) LINHA SUMIDA — o defeito real que a Márcia pegou na impressão de
    #     16/07 (a interrupção apagava o eixo ASC/MC). Apaga a linha da casa 1.
    import re as _re
    m0 = _re.search(r"<line x1='50.0' y1='6.5'[^>]*stroke-width='0.6'[^>]*/>\n", msvg)
    bad_missing = msvg.replace(m0.group(0), "", 1)
    e = check_cusp(mon, bad_missing)
    hit = any("cúspide-sumida" in x for x in e)
    print(f"\n(a) apaga a linha da casa angular: {len(e)} violação(ões)"
          f"   → {'MORDE' if hit else 'NAO MORDEU ✗'}")
    if e: print(f"      └─ {e[0][:88]}")

    # (b) LINHA TORTA — 0.5° fora do ângulo da cúspide: cúspide falsa.
    rot = _re.search(r"rotate\((-?[\d.]+)", m0.group(0)).group(1)
    twisted = m0.group(0).replace(f"rotate({rot}", f"rotate({float(rot)-0.5:.6f}")
    bad_twist = msvg.replace(m0.group(0), twisted, 1)
    e = check_cusp(mon, bad_twist)
    hit = any("cúspide-falsa" in x for x in e)
    print(f"(b) torce a linha 0.5°: {len(e)} violação(ões)"
          f"   → {'MORDE' if hit else 'NAO MORDEU ✗'}")
    if e: print(f"      └─ {e[0][:88]}")

    # (c) LINHA CORTADA — regressão da interrupção descartada: parte a linha
    #     em dois tocos colineares. O contrato novo exige INTEIRA.
    top = m0.group(0).replace("y2='28.0'", "y2='9.0'")
    bot = m0.group(0).replace("y1='6.5'", "y1='27.0'")
    bad_cut = msvg.replace(m0.group(0), top + bot, 1)
    e = check_cusp(mon, bad_cut)
    hit = any("cúspide-cortada" in x for x in e)
    print(f"(c) corta a linha em dois tocos: {len(e)} violação(ões)"
          f"   → {'MORDE' if hit else 'NAO MORDEU ✗'}")
    if e: print(f"      └─ {e[0][:88]}")

    # (d) estado ATUAL zera.
    e = check_cusp(mon, msvg)
    print(f"(d) estado atual (todas inteiras): {len(e)} violação(ões)"
          f"   → {'ZEROU' if not e else 'NAO zerou ✗'}")
