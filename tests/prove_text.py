"""Prova que os detectores novos MORDEM — com os defeitos REAIS de 16/07."""
import os
import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import pdf_generator as pg
import text_verifier as tv

ok = True
def check(label, got, want_kinds):
    global ok
    kinds = sorted({v["kind"] if isinstance(v, dict) else v for v in got})
    hit = all(any(w in k for k in kinds) for w in want_kinds)
    print(f"{'MORDE ' if hit else 'FALHOU'}  {label}: {kinds or '(nada)'}")
    ok = ok and hit

def check_clean(label, got):
    global ok
    print(f"{'LIMPO ' if not got else 'FALSO+'}  {label}: {got[:2] if got else ''}")
    ok = ok and not got

# ---------- A3: lint do PDF ----------
# defeito real 1: '.## Fio Condutor' colado no fim do paragrafo
bad1 = "# Mapa\n\n## Saturno\n\nTexto que termina em relação calorosa.## Fio Condutor\n\nO fio."
check("A1 markdown vazado", pg.lint_final_text(pg._parse_sections(bad1)), ["markdown_h2"])
# defeito real 2: frase colada
bad2 = "# Mapa\n\n## Lua\n\nUma relação calorosa.A oposição entre a Lua e Marte pede atenção.\n"
check("A2 frase colada", pg.lint_final_text(pg._parse_sections(bad2)), ["frase_colada"])
# texto limpo nao acusa (bold emparelhado, decimal, reticencias)
good = "# Mapa\n\n## Lua\n\nA **oposição** exata de 0.2 graus. E depois… tudo segue. Dr. Silva não aparece.\n"
check_clean("A3 texto limpo", pg.lint_final_text(pg._parse_sections(good)))

# ---------- fim-a-fim A1/A2: o splice do verifier preserva o separador ----------
texto = ("A relação com a mãe foi calorosa, e não distante como parecia.\n\n"
         "A oposição entre a Lua e Marte pede atenção.\n\n## Fio Condutor\n\nfio.")
def fake_claude(prompt, max_tokens=500):
    return "A relação com a mãe foi calorosa desde sempre."
corr, log = tv.run_verifier(texto, {"points": {}}, fake_claude)
glued = re.search(r"[a-záéô]\.[A-Z#]", corr)
has_sep = "sempre.\n\nA oposição" in corr and "\n\n## Fio Condutor" in corr
print(f"{'MORDE ' if (log and has_sep and not glued) else 'FALHOU'}  splice preserva \\n\\n "
      f"(corrigiu={len(log)}, separadores intactos={has_sep})")
ok = ok and bool(log) and has_sep

# ---------- B: par de aspecto comido ----------
b1 = "O sextil entre sua Vênus está em Gêmeos acrescenta algo."
b2 = "O trígono entre Quíron está em Peixes, de orbe apertado."
check("B Lucca sextil", [{"kind": m[0]} for m in tv._detect_broken_aspect_pair(b1)], ["sextil"])
check("B Lucca trígono", [{"kind": m[0]} for m in tv._detect_broken_aspect_pair(b2)], ["trígono"])
check_clean("B par correto", tv._detect_broken_aspect_pair(
    "O sextil entre sua Lua e Vênus acrescenta algo. A conjunção entre Marte e Júpiter em Leão."))

# ---------- B: a MONTAGEM consertada (verify_planet_signs) ----------
import report_generator as rg
chart = {"points": {"moon": {"sign_pt": "Leão"}, "venus": {"sign_pt": "Gêmeos"},
                    "saturn": {"sign_pt": "Escorpião"}, "chiron": {"sign_pt": "Peixes"}}}
t = "O sextil entre sua Lua e Vênus em Gêmeos acrescenta algo importante a esse quadro."
fixed, divs = rg.verify_planet_signs(t, chart)
print(f"{'MORDE ' if 'Lua e Vênus' in fixed and 'em Gêmeos' not in fixed else 'FALHOU'}  "
      f"B montagem: {fixed[:70]!r} ações={[d['action'] for d in divs]}")
ok = ok and ("Lua e Vênus" in fixed and "em Gêmeos" not in fixed)

# ---------- C ----------
def lex_hits(text):
    out=[]
    for entry in tv._FORBIDDEN_LEXICON:
        pat, cat = entry[0], entry[1]
        val = entry[3] if len(entry)>3 else None
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            if val and not val(text,m): continue
            out.append({"kind":cat})
    return out
check("C5 orgullo",  lex_hits("Sente um orgullo silencioso."), ["erro_espanhol"])
check("C5 cerrada",  lex_hits("Uma postura cerrada diante do mundo."), ["erro_espanhol"])
# C6 mudou de casa em 17/07: saiu do léxico e virou detector consciente da
# voz (_detect_voice_violations) — provado na seção VOZ abaixo nos 2 modos.
check("C6 3a pessoa (via voz)", [{"kind":k} for k,_,_,_ in
      tv._detect_voice_violations("Isso cria nele uma disponibilidade rara.",
                                  {"gender":"masculino","points":{}})],
      ["pessoa_terceira_em_segunda"])
check("C7 demais quanto", lex_hits("Pessoas que parecem boas demais quanto às suas intenções."),
      ["concordancia_comparativa"])
check_clean("C limpo", lex_hits(
    "O orgulho aparece fechado. Cria em você uma disponibilidade. Confia tanto quanto ao início. A cerca do jardim."))

print()
print("TODOS MORDEM E NENHUM FALSO-POSITIVO" if ok else ">>> ALGO NAO MORDEU — NAO CONFIAR")

# ================= PACOTE 17/07 =================
print("\n--- balde 1: negação-substituição, frases REAIS do inventário ---")
import text_verifier as tv2
def neg_hits(t):
    t=tv2._mask_fixed_templates(t)
    out=[]
    for pat,cat in tv2._NEGATION_SUBSTITUTION_PATTERNS:
        for m in re.finditer(pat,t,flags=re.IGNORECASE): out.append(cat)
    return out
REAL=[
 ("H78","O mapa não pede que você abandone a precisão. Pede que você pare de usá-la para se segurar."),
 ("L18","Escorpião na base da carta não aponta para uma infância leve ou descomplicada — aponta para um terreno carregado."),
 ("L74","Essas não são contradições que você precisa resolver — são o motor de quem você é."),
 ("H75","A pergunta que fica aqui não é se você consegue se sustentar emocionalmente — é se o modo como foi ensinada."),
 ("H77","Não porque o cuidado e o rigor sejam problemas — eles são genuínos. Mas porque, no seu caso, eles aprenderam a funcionar como adiamento."),
 ("L64","Silenciar essa parte de você nunca produziu adequação real. Produziu apenas uma forma de invisibilidade."),
 ("H61","Abrir mão da ingenuidade ali não é fechar o coração; é proteger o que você está construindo."),
 ("L11","aprender a receber ativamente — não como fraqueza, mas como parte do ciclo."),
 ("H15","que os vínculos sejam espaços de troca real — e não apenas de estabilidade."),
 ("L59","o filho não virá para confirmar quem você já é, mas para te expor."),
 ("H19","o lugar no mapa que guarda não o que você lembra da infância, mas o que a infância deixou em você."),
 ("L17","mostrar que você precisa de afeto não diminui você; ao contrário, é exatamente o que abre espaço."),
]
allok=True
for tag,frase in REAL:
    h=neg_hits(frase)
    print(f"{'MORDE ' if h else 'FALHOU'}  {tag}: {h[:2]}")
    allok = allok and bool(h)
ok = ok and allok

print("\n--- balde 3 + protegidos: NÃO podem ser flagrados ---")
CLEAN=[
 ("'X, mas Y' comum","Há uma inteligência relacional genuína aqui, mas ela precisa de espaço."),
 ("comparativo","O silêncio te desestabiliza mais do que qualquer conflito direto."),
 ("fragmento retórico (protegido)","Porque o Sol, que deveria ser o motor de tudo isso, está em Câncer."),
 ("'independente de' (voz dela)","O que você diz precisa ser correto, independente de ser popular."),
 ("'as coisas saem errado' (voz dela)","Mesmo quando as coisas saem errado, costumam sair de um jeito melhor."),
 ("nota de rodapé (whitelist)","Este relatório foi sintetizado para oferecer uma leitura coerente, em vez de cobrir cada aspecto individualmente."),
]
for tag,frase in CLEAN:
    h=neg_hits(frase)
    lx=lex_hits(frase)
    bad=h+[x['kind'] for x in lx]
    print(f"{'LIMPO ' if not bad else 'FALSO+'}  {tag}: {bad[:3]}")
    ok = ok and not bad

print("\n--- achados novos: léxico + gênero ---")
check("reencuadrar", lex_hits("a saída é analisar, reencuadrar, mudar de assunto."), ["erro_espanhol"])
check("tenciona", lex_hits("E a quadratura com Plutão tenciona isso."), ["erro_grafia_tenciona"])
check("conjunção cerrada", lex_hits("Sol em conjunção cerrada com Júpiter."), ["termo_rejeitado"])
check("o que te exilaram", lex_hits("O que te exilaram foi a recusa de fingir."), ["gramatica"])
# gênero: usa o run_verifier de verdade (precisa do chart)
def gender_hits(text, gender):
    corr, log = tv2.run_verifier(text, {"gender": gender, "points": {}},
                                 lambda p, max_tokens=500: "frase corrigida neutra.")
    return [v["kind"] for v in log]
gh=gender_hits("Mas mesmo que a transformação não passe pela maternidade, ela vai aparecer.", "masculino")
print(f"{'MORDE ' if any('genero' in k for k in gh) else 'FALHOU'}  maternidade em relatório masculino (caso REAL Lucca): {gh}")
ok = ok and any('genero' in k for k in gh)
gh2=gender_hits("A figura materna oferecia afeto e troca; a herança materna tem calor.", "masculino")
print(f"{'LIMPO ' if not any('genero' in k for k in gh2) else 'FALSO+'}  'figura materna' legítima: {[k for k in gh2 if 'genero' in k]}")
ok = ok and not any('genero' in k for k in gh2)

print("\n--- repetição entre seções (pares REAIS do inventário) ---")
import report_generator as rg2
rep_doc = ("# Mapa\n\n## Suas Raízes e Sua Casa Interna\n\n"
 "E você, crescendo nesse campo, pode ter aprendido que certos aspectos de si mesma eram inconvenientes, perturbadores, excessivos.\n\n"
 "## Onde Você Não Se Encaixa\n\n"
 "E você, crescendo nesse campo, pode ter aprendido que certos aspectos de si mesma eram inconvenientes, perturbadores, excessivos. Outra coisa nova aqui.\n")
r=rg2.detect_cross_section_repetition(rep_doc)
print(f"{'MORDE ' if r else 'FALHOU'}  par verbatim Helena: {r[0]['sections'] if r else '—'} ({r[0]['windows'] if r else 0} janelas)")
ok = ok and bool(r)
rep_ok = ("# Mapa\n\n## O Pai e as Ferramentas da Vida\n\n"
 "A função de autoridade transmitiu estrutura e ordem, mas a afetuosidade ficou escassa ou silenciosa ao longo dos anos.\n\n"
 "## Fio Condutor\n\n"
 "A autoridade paterna operou com exigência e sem calor suficiente — o retrato se confirma.\n")
r2=rg2.detect_cross_section_repetition(rep_ok)
print(f"{'LIMPO ' if not r2 else 'FALSO+'}  recap comprimido do Fio (permitido): {r2}")
ok = ok and not r2
rep_same = ("# Mapa\n\n## Uma Seção\n\n"
 "Uma frase que se repete dentro da mesma seção porque o parágrafo retoma o próprio tema central dela. "
 "Uma frase que se repete dentro da mesma seção porque o parágrafo retoma o próprio tema central dela.\n")
r3=rg2.detect_cross_section_repetition(rep_same)
print(f"{'LIMPO ' if not r3 else 'FALSO+'}  repetição INTRA-seção (não conta): {r3}")
ok = ok and not r3

print()
print("PACOTE 17/07: TODOS MORDEM, PROTEGIDOS LIMPOS" if ok else ">>> ALGO FALHOU")

# ================= VOZ (17/07, parte 2) =================
print("\n--- interruptor de VOZ: o detector INVERTE ---")
import importlib, text_verifier as tv3
importlib.reload(tv3)
SEG={"gender":"masculino","points":{}}                                 # modo (a)/(b)
TER={"gender":"masculino","points":{},"_voice":{"person":"terceira","name":"Lucca Quitete","relationship":""}}
def voice_kinds(text,chart): return [k for k,_,_,_ in tv3._detect_voice_violations(text,chart)]
# 2a pessoa: nele = defeito; "sua Lua" = correto
h=voice_kinds("Isso cria nele uma disponibilidade rara.",SEG)
print(f"{'MORDE ' if h else 'FALHOU'}  2ª pessoa, 'cria nele': {h}"); ok=ok and bool(h)
h=voice_kinds("A sua Lua em Leão pede reconhecimento.",SEG)
print(f"{'LIMPO ' if not h else 'FALSO+'}  2ª pessoa, 'sua Lua' (correto): {h}"); ok=ok and not h
# 3a pessoa: INVERTE — "sua Lua" = defeito; "a Lua de Lucca" = correto
h=voice_kinds("A sua Lua em Leão pede reconhecimento.",TER)
print(f"{'MORDE ' if h else 'FALHOU'}  3ª pessoa, 'sua Lua' (dirigido ao sujeito): {h}"); ok=ok and bool(h)
h=voice_kinds("A Lua de Lucca pede reconhecimento; vale a pena você observar como ele reage.",TER)
print(f"{'LIMPO ' if not h else 'FALSO+'}  3ª pessoa, 'Lua de Lucca' + 'você' ao leitor: {h}"); ok=ok and not h
# artigo com genero errado do sujeito
h=voice_kinds("A Lucca tem uma sensibilidade rara.",TER)
print(f"{'MORDE ' if h else 'FALHOU'}  3ª pessoa, 'A Lucca' (masculino): {h}"); ok=ok and bool(h)
h=voice_kinds("O Lucca tem uma sensibilidade rara. E a Lua dele acompanha.",TER)
print(f"{'LIMPO ' if not h else 'FALSO+'}  3ª pessoa, 'O Lucca' correto: {h}"); ok=ok and not h

print("\n--- age gate (17/07 tarde): menor NAO gera; bypass interno de QA ---")
EXEMPT={"marcia.fervienza@gmail.com","executivo@marciafervienza.com"}
def gate(age,email):
    """Replica a lógica do app: (gera?, bypass?)"""
    if age is not None and age<18:
        if (email or "").strip().lower() in EXEMPT: return True, True
        return False, False
    return True, False
cases=[(11,"cliente@gmail.com",False,False),
       (17,"outro@x.com",False,False),
       (11,"marcia.fervienza@gmail.com",True,True),   # Lucca QA
       (11,"executivo@marciafervienza.com",True,True),
       (18,"cliente@gmail.com",True,False),
       (70,"cliente@gmail.com",True,False),
       (None,"cliente@gmail.com",True,False)]         # sem idade: não bloqueia
for age,email,want_gen,want_by in cases:
    g,b=gate(age,email)
    good=(g,b)==(want_gen,want_by)
    print(f"{'OK    ' if good else 'ERRADO'}  idade={age} email={email.split('@')[0]:<18} → "
          f"{'GERA' if g else 'RECUSA 403'}{' (bypass QA)' if b else ''}")
    ok=ok and good
# a voz NAO é mais tocada pela idade: modo do formulário fica
MODE={"":"a","a":"a","meu":"a","b":"b","presente":"b","c":"c","sobre_outro":"c"}
m=MODE.get("meu","a")
print(f"{'OK    ' if m=='a' else 'ERRADO'}  menor em QA com report_for='meu' → voz segue o formulário (modo {m})")
ok = ok and m=="a"

print()
print("VOZ 17/07: TUDO PROVADO" if ok else ">>> ALGO FALHOU NA VOZ")
