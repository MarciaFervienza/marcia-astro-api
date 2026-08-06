"""Mede o tamanho de fonte EFETIVO no PDF final: percorre o content stream
rastreando q/Q/cm (matriz) e Tf (corpo nominal). tamanho_real = nominal x escala."""
import warnings; warnings.filterwarnings("ignore")
import os
import re, sys, math, collections
from pypdf import PdfReader

TOK = re.compile(r"(q|Q|cm|Tf|BT|ET|Tm)|(/[A-Za-z0-9+\-]+)|(-?\d*\.?\d+)")

def efetivos(pdf_path, pagina=1):
    rd = PdfReader(pdf_path)
    page = rd.pages[pagina]
    raw = page.get_contents().get_data().decode("latin-1", "ignore")
    fontmap = {}
    fonts = page.get("/Resources", {}).get("/Font", {})
    for k, v in (fonts.items() if fonts else []):
        try: fontmap[k] = str(v.get_object().get("/BaseFont", k))
        except Exception: fontmap[k] = k
    pilha, escala = [], 1.0
    nums, nome = [], None
    out = collections.Counter()
    for m in TOK.finditer(raw):
        op, ref, num = m.group(1), m.group(2), m.group(3)
        if num is not None:
            try: nums.append(float(num))
            except ValueError: pass
            continue
        if ref is not None:
            nome = ref; continue
        if op == "q":
            pilha.append(escala)
        elif op == "Q":
            escala = pilha.pop() if pilha else 1.0
        elif op == "cm" and len(nums) >= 6:
            a, b, c, d = nums[-6], nums[-5], nums[-4], nums[-3]
            escala *= math.sqrt(abs(a * d - b * c)) or 1.0
        elif op == "Tf" and nums and nome:
            out[(fontmap.get(nome, nome).split("+")[-1], round(nums[-1] * escala, 2))] += 1
        if op in ("cm", "Tf", "Tm"):
            nums = []
    return out

for p in sys.argv[1:]:
    print(f"\n=== {p.split('/')[-1]} ===")
    t = efetivos(p)
    for (f, s), n in sorted(t.items(), key=lambda x: x[0][1]):
        print(f"   {f:<24} {s:>6.2f} pt   ({n}x)")
