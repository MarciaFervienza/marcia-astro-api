"""O PDF gera de ponta a ponta, com mandala, tabelas e painel?"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fixture import build_chart, HELENA
import app, pdf_generator as pg
from pypdf import PdfReader

chart = build_chart(HELENA)
body = {"name": "Helena Penteado", "datetime": "1992-09-18T09:50:00",
        "latitude": -19.9227318, "longitude": -43.9450948,
        "timezone": "America/Sao_Paulo", "birth_city": "Belo Horizonte, MG"}
svg, err = app._generate_chart_svg(body)
assert svg, f"SVG falhou: {err}"
asp = [{"planet_a_pt": "Sol", "planet_b_pt": "Júpiter",
        "type_pt": "conjunção", "orb": a["orb"]} for a in chart["aspects"]]
data = pg.generate_pdf(
    report_text="## Abertura\n\nTexto.\n\n## Fio Condutor\n\nFecho.\n",
    client_name="Helena Penteado", birth_date="18/09/1992", birth_time="09:50",
    birth_place="Belo Horizonte, MG", latitude=-19.9227318, longitude=-43.9450948,
    chart_image_url=svg, aspects=asp, points=chart["points"])
p = tempfile.mktemp(suffix=".pdf")
open(p, "wb").write(data)
n = len(PdfReader(p).pages)
os.unlink(p)
assert n >= 3, f"PDF com apenas {n} páginas"
print(f"PDF OK — {n} páginas, {len(data)//1024} KB")
