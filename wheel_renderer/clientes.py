import warnings; warnings.filterwarnings("ignore")
import os, re, tempfile
from kerykeion import AstrologicalSubjectFactory
from svglib.svglib import svg2rlg
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.graphics import renderPDF
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from props import ACTIVE_POINTS, check_all
from prove_bite import stock_svg
import packing

DEST="/Users/marciaqfervienza/Desktop/mapa-natal-pdfs/CLIENTES"
os.makedirs(DEST, exist_ok=True)
SIGN="Ári Tou Gêm Cân Leã Vir Lib Esc Sag Cap Aqu Pei".split()

CASES=[
 ("carlos_ed","Carlos Ed","Rio de Janeiro",      1974,10,15, 7,58,-22.9110,-43.2094,"America/Sao_Paulo"),
 ("liza_g",   "Liza G",   "Porto Alegre",        1974,10,14,19, 5,-30.0325,-51.2304,"America/Sao_Paulo"),
 ("gisela_d", "Gisela D", "Rodeio, SC",          1981, 8,28, 9,30,-26.9233,-49.3685,"America/Sao_Paulo"),
 ("kyle_b",   "Kyle B",   "Sandy Springs, GA",   1984,11, 8, 6,30, 33.9243,-84.3785,"America/New_York"),
 ("monica_b", "Monica B", "Medellin, Colombia",  1955, 7,22, 7, 0,  6.2697,-75.6026,"America/Bogota"),
]

def como_producao(svg):
    """Aplica EXATAMENTE o pós-processamento do app.py (linhas 392-428).

    Sem isto o banco de testes mente: os PDFs saíam com o símbolo de aspecto
    no miolo (que produção apaga) e com as linhas-guia engrossadas (que
    produção apaga por inteiro). Aprovar um PDF que não é o produto é aprovar
    outra coisa — foi o que quase aconteceu em 16/07/2026.

    Se o app.py mudar este pós-processamento, mudar aqui junto.
    """
    # símbolos de aspecto (△ □ ☌ …) sobrepostos no meio de cada linha de aspecto
    svg = re.sub(r"<use\b[^>]*\bxlink:href=['\"]#orb\d+['\"][^>]*/>", "", svg)
    # mesma correção de glifo que produção aplica (packing.fix_lilith_glyph)
    from packing import fix_lilith_glyph
    svg, _ok = fix_lilith_glyph(svg)
    if not _ok:
        print("  AVISO: glifo de Lilith não corrigido — símbolo do Kerykeion mudou")
    # linhas-guia glifo → posição real (comportamento Astro Gold: sem tether)
    svg = re.sub(r"<g\s+kr:node=['\"]Indicator['\"][^>]*>.*?</g>\s*", "", svg,
                 flags=re.DOTALL)
    return svg

def pdf(svg,out,sub):
    t=tempfile.mkdtemp(); p=f"{t}/w.svg"; open(p,"w").write(svg)
    d=svg2rlg(p); r=18.0*cm/92.0
    d.scale(r,r); d.width*=r; d.height*=r
    c=canvas.Canvas(out,pagesize=A4)
    c.setFillColor(HexColor("#F8F5EF")); c.rect(0,0,A4[0],A4[1],stroke=0,fill=1)
    c.setFillColor(HexColor("#2F2F2F")); c.setFont("Helvetica-Bold",10)
    c.drawString(1.5*cm,A4[1]-1.3*cm,sub)
    renderPDF.draw(d,c,(A4[0]-d.width)/2,(A4[1]-d.height)/2-0.4*cm)
    c.save()

print(f"{'mapa':<11} {'nascimento':<26} {'UTC':>6} {'Asc':>11} {'Sol':>11} {'fab':>4} {'pack':>5}")
print("-"*82)
for slug,nome,cid,y,mo,d,h,mi,lat,lng,tz in CASES:
    s=AstrologicalSubjectFactory.from_birth_data(nome,y,mo,d,h,mi,lat=lat,lng=lng,tz_str=tz,
        online=False,active_points=ACTIVE_POINTS)
    packing.uninstall(); sf=stock_svg(s); ff=dict(check_all(s,sf))
    nf=len(ff["display dentro do SIGNO"])+len(ff["display dentro da CASA"])
    packing.install();   sp=stock_svg(s); fp=dict(check_all(s,sp))
    np_=len(fp["display dentro do SIGNO"])+len(fp["display dentro da CASA"])
    packing.uninstall()
    bs=f"{d:02d}/{mo:02d}/{y} {h:02d}:{mi:02d} {cid}"
    asc=f"{int(s.first_house.position)}° {SIGN[int(float(s.first_house.abs_pos)//30)]}"
    sol=f"{int(s.sun.position)}° {SIGN[int(float(s.sun.abs_pos)//30)]}"
    off=s.julian_day and ""
    import datetime, zoneinfo
    o=datetime.datetime(y,mo,d,h,mi,tzinfo=zoneinfo.ZoneInfo(tz)).utcoffset()
    offs=f"{int(o.total_seconds()//3600):+d}"
    pdf(como_producao(sf), f"{DEST}/{slug}__1_fabrica.pdf", f"FABRICA · {nome} · {bs} · {nf} corpos errados")
    pdf(como_producao(sp), f"{DEST}/{slug}__2_packing.pdf", f"PACKING · {nome} · {bs} · {np_} corpos errados")
    print(f"{slug:<11} {bs:<26} {offs:>6} {asc:>11} {sol:>11} {nf:>4} {np_:>5}")
print(f"\nPDFs em {DEST}")
