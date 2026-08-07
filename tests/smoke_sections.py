"""As 16 seções de produção EXECUTAM? (pega NameError que ast.parse não pega)"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fixture import build_chart, HELENA, LUCCA
import report_generator as rg

falhas = []
total = 0
for caso, nome in ((HELENA, "Helena"), (LUCCA, "Lucca")):
    chart = build_chart(caso)
    rg.described_aspect_themes.clear()
    rg._section_aspect_audit.clear()
    rg._section_suppressed.clear()
    secs = [x["name"] for x in rg.build_sections(chart)]
    for sec in secs:
        total += 1
        try:
            rg.section_chart_context(sec, chart)
        except Exception as e:
            falhas.append(f"{nome}/{sec}: {type(e).__name__}: {e}")
    # seção dona nunca pode ficar sem os aspectos do próprio corpo
    rg.described_aspect_themes.clear()
    rg._section_aspect_audit.clear()
    for sec in secs:
        f = rg.aspects_for_section_filtered(sec, chart)
        for a in f:
            rg.described_aspect_themes.add(rg._aspect_dedup_key(a))
        if not f and sec in rg.OWNER_SECTIONS:
            keys = rg._planets_for_section(sec, chart)
            tem = any(any(k in (a["planet_a"], a["planet_b"]) for k in keys)
                      for a in chart["aspects"])
            if tem:
                falhas.append(f"{nome}/{sec}: seção DONA sem os aspectos do corpo")

for f in falhas:
    print("FALHA:", f)
print(f"{total} execuções de seção, {len(falhas)} falhas")
sys.exit(1 if falhas else 0)
