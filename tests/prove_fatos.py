"""PRIORIDADE 3 — os três detectores factuais mordem nos casos REAIS?"""
import warnings; warnings.filterwarnings("ignore")
import sys; import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import text_verifier as tv
ok=True
def chk(tag,hits,esperado=True):
    global ok
    bom=bool(hits)==esperado; ok=ok and bom
    marca=("MORDE " if hits else ">>> FALHOU") if esperado else ("FALSO+" if hits else "LIMPO ")
    print(f"  {marca}  {tag}")
    if hits and esperado: print(f"      → {hits[0]['suggestion'][:92]}")

# chart do LUCCA (dados reais)
LUCCA={"points":{
 "pluto":{"sign":"capricorn","sign_pt":"Capricórnio","house":5},
 "venus":{"sign":"gemini","sign_pt":"Gêmeos","house":11},
 "north_node":{"sign":"libra","sign_pt":"Libra","house":3},
 "south_node":{"sign":"aries","sign_pt":"Áries","house":9},
 "mars":{"sign":"libra","sign_pt":"Libra","house":3},
 "saturn":{"sign":"scorpio","sign_pt":"Escorpião","house":4}},
 "midheaven":{"sign":"taurus","sign_pt":"Touro"},
 "ascendant":{"sign":"leo","sign_pt":"Leão"}}

print("1) CASA INCONSISTENTE ENTRE SEÇÕES\n")
chk("Plutão dito na casa 6 (real: 5)",
    tv._detect_house_inconsistency("Plutão na casa 6 opera de forma discreta.", LUCCA))
chk("mesmo corpo com DUAS casas no relatório",
    tv._detect_house_inconsistency(
      "Plutão na casa 6 aparece cedo. Mais adiante, Plutão na casa 5 conduz a leitura.", LUCCA))
chk("casa correta (real: 5)",
    tv._detect_house_inconsistency("Plutão na casa 5 conduz a criatividade.", LUCCA), False)

print("\n2) MENÇÃO A ÂNGULO VALIDADA CONTRA OS DADOS\n")
chk("Vênus 'na cúspide do meio do céu' (Vênus Gêmeos, MC Touro)",
    tv._detect_angle_claims("Vênus na cúspide do meio do céu marca a vocação.", LUCCA))
chk("Vênus no ascendente (Vênus Gêmeos, Asc Leão)",
    tv._detect_angle_claims("Vênus no ascendente colore a chegada.", LUCCA))
chk("menção sem afirmar conjunção",
    tv._detect_angle_claims("O meio do céu em Touro pede construção lenta.", LUCCA), False)

print("\n3) REGÊNCIA CONTRA A TABELA\n")
chk("Marte rege o Nodo Norte (Libra → Vênus)",
    tv._detect_rulership("Marte rege o Nodo Norte e dá o tom da direção.", LUCCA))
chk("Marte rege o Nodo Sul (Áries → correto)",
    tv._detect_rulership("Marte rege o Nodo Sul, e isso reforça o padrão antigo.", LUCCA), False)
chk("Vênus rege o Nodo Norte (correto)",
    tv._detect_rulership("Vênus rege o Nodo Norte em Libra.", LUCCA), False)
chk("Plutão rege Saturno em Escorpião (moderno — aceito)",
    tv._detect_rulership("Plutão rege Saturno, que está em Escorpião.", LUCCA), False)
chk("Marte rege Saturno em Escorpião (tradicional — aceito)",
    tv._detect_rulership("Marte rege Saturno, que está em Escorpião.", LUCCA), False)
print()
print("PRIORIDADE 3: TUDO PROVADO" if ok else ">>> ALGO FALHOU")
sys.exit(0 if ok else 1)
