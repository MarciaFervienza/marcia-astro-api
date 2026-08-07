"""VARREDURA v2 — só DEFORMAÇÃO DERIVACIONAL, não flexão.

Deformação = mesmo radical, sufixo ADJETIVAL diferente do que ela usa
(barulh-ENTA -> barulh-OSA). Flexão (plural, gênero, particípio, advérbio
em -mente) NÃO conta: é gramática normal.

O instrumento é validado no caso conhecido antes de qualquer conclusão.
"""
import re, os, json, glob, collections, unicodedata
SP="/private/tmp/claude-502/-Users-marciaqfervienza-Documents-Consultas-DB-Transcripts/10042d2d-393c-4073-8b22-e2a255463e44/scratchpad"

def norm(w):
    return "".join(c for c in unicodedata.normalize("NFD",w.lower())
                   if unicodedata.category(c)!="Mn")

# sufixos que FORMAM ADJETIVO a partir de um radical. Trocar um pelo outro
# muda a palavra, não a flexiona.
ADJ = ["issimo","issima","oso","osa","osos","osas","ento","enta","entos","entas",
       "ivo","iva","ivos","ivas","ico","ica","icos","icas","al","ais",
       "eiro","eira","eiros","eiras","udo","uda","onho","onha","il","iz"]

def parte(w):
    """(radical, sufixo_adjetival) ou None se não for adjetivo derivado."""
    n=norm(w)
    for s in sorted(ADJ,key=len,reverse=True):
        if len(n)-len(s)>=4 and n.endswith(s):
            return n[:-len(s)], s
    return None

def _base(suf):
    """classe do sufixo, ignorando gênero/número — -oso/-osa/-osos = 'os'."""
    return re.sub(r"(os|as|o|a)$","",suf) or suf

print("carregando a fonte…", flush=True)
adj_fonte=collections.defaultdict(collections.Counter)   # radical -> Counter(sufixo)
formas=collections.Counter()
_RAIZ_PROJ = "/Users/marciaqfervienza/Documents/Consultas DB"
for p in glob.glob(f"{_RAIZ_PROJ}/Transcripts/**/*.txt", recursive=True):
    try: t=open(p,encoding="utf-8",errors="ignore").read().lower()
    except Exception: continue
    for w in re.findall(r"[a-zà-ÿ][a-zà-ÿ]{5,}", t):
        formas[norm(w)]+=1
        pr=parte(w)
        if pr: adj_fonte[pr[0]][pr[1]]+=1
try:
    t=json.dumps(json.load(open(f"{_RAIZ_PROJ}/authoring_tool/authored_chunks.json",encoding="utf-8")),
                 ensure_ascii=False).lower()
    for w in re.findall(r"[a-zà-ÿ][a-zà-ÿ]{5,}", t):
        formas[norm(w)]+=3
        pr=parte(w)
        if pr: adj_fonte[pr[0]][pr[1]]+=3
except Exception: pass
print(f"  {len(adj_fonte):,} radicais adjetivais na fonte", flush=True)

# ---- VALIDAÇÃO DO INSTRUMENTO no caso conhecido ----
print("\nVALIDAÇÃO — o caso 'barulhenta/barulhosa':")
pr_ok, pr_mau = parte("barulhenta"), parte("barulhosa")
print(f"  barulhenta → radical {pr_ok}   barulhosa → radical {pr_mau}")
mesmo_radical = pr_ok and pr_mau and pr_ok[0]==pr_mau[0]
suf_dif = pr_ok and pr_mau and _base(pr_ok[1])!=_base(pr_mau[1])
print(f"  mesmo radical: {mesmo_radical}   sufixo diferente: {suf_dif}")
na_fonte = dict(adj_fonte.get(pr_ok[0] if pr_ok else "", {}))
print(f"  formas na fonte para esse radical: {na_fonte}")
print(f"  >>> instrumento {'DETECTA' if (mesmo_radical and suf_dif and na_fonte) else 'NÃO DETECTA'} o caso conhecido")

# ---- varredura nos relatórios ----
print("\n" + "="*70)
achados=[]
for nome in ("helena","lucca"):
    p=f"{SP}/rep_{nome}.md"
    if not os.path.exists(p): continue
    rel=open(p,encoding="utf-8").read()
    for m in re.finditer(r"\b([a-zà-ÿ][a-zà-ÿ]{5,})\b", rel.lower()):
        w=m.group(1); pr=parte(w)
        if not pr: continue
        rad,suf=pr
        cnt=adj_fonte.get(rad)
        if not cnt: continue
        dela,n=cnt.most_common(1)[0]
        if _base(dela)==_base(suf): continue          # mesma derivação: ok
        if n<10: continue                              # ela mal usa: não é imagem dela
        if formas.get(norm(w),0)>n//10: continue       # ela também usa a do relatório
        i=m.start()
        achados.append((n,nome,w,dela,rad,dict(cnt.most_common(3)),
                        rel[max(0,i-65):i+65].replace("\n"," ")))
achados.sort(reverse=True)
print(f"DEFORMAÇÕES DERIVACIONAIS: {len(achados)}")
for n,nome,w,dela,rad,cnt,tr in achados[:15]:
    print(f"\n[{nome}] «{w}»  —  ela usa «{dela}» ({n}×)")
    print(f"     radical '{rad}' na fonte: {cnt}")
    print(f"     …{tr.strip()}…")
if not achados: print("  (nenhuma)")
