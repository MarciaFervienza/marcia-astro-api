"""Prova de mordida — rodada 17/07 (21 achados da leitura da Márcia)."""
import warnings; warnings.filterwarnings("ignore")
import os
import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import text_verifier as tv, report_generator as rg
ok = True
def chk(tag, hits, esperado=True):
    global ok
    bom = bool(hits) == esperado
    ok = ok and bom
    marca = ("MORDE " if hits else "FALHOU") if esperado else ("FALSO+" if hits else "LIMPO ")
    if not bom: marca = ">>> " + marca
    print(f"{marca}  {tag}" + (f"  {hits[:1]}" if hits and esperado else ""))

def lex(t):
    return [{"kind": c} for p, c, *r in tv._FORBIDDEN_LEXICON
            for m in __import__("re").finditer(p, t, 2)]

print("--- item 7: Netuno-Plutão geracional ---")
chk("sextil Netuno-Plutão mencionado",
    tv._detect_netuno_plutao_mention("O sextil entre Netuno e Plutão marca uma coorte inteira."))
chk("Netuno e Plutão em frases separadas (legítimo)",
    tv._detect_netuno_plutao_mention("Netuno na casa 2 dissolve. Plutão na 12 transforma."), False)

print("\n--- item 8: signo como agente geracional ---")
chk("signo carrega para a geração",
    tv._detect_sign_as_generational_agent(
        "O signo de Escorpião carrega, para toda a sua geração, um peso de transformação."))
chk("planeta transpessoal em signo (correto)",
    tv._detect_sign_as_generational_agent(
        "O que Plutão em Escorpião carrega para toda a sua geração é o tema da transformação."), False)

print("\n--- item 9: falsa ausência de aspecto ---")
chart = {"aspects": [{"planet_a": "lilith", "planet_b": "venus", "type": "sextile"}]}
chk("diz que Lilith não tem aspectos (mas tem)",
    tv._detect_false_no_aspect_claims("Lilith não faz nenhum aspecto neste mapa.", chart))
chk("diz que Vesta não tem aspectos (e não tem)",
    tv._detect_false_no_aspect_claims("Vesta não faz nenhum aspecto neste mapa.", chart), False)

print("\n--- item 10: clítico de 3ª em texto de 2ª ---")
chk("'o que a move'", tv._detect_clitic_third_person("Entender o que a move é o começo.", {"person": "segunda"}))
chk("'o que te move' (correto)", tv._detect_clitic_third_person("Entender o que te move é o começo.", {"person": "segunda"}), False)
chk("clítico em modo TERCEIRA (legítimo)", tv._detect_clitic_third_person("Entender o que a move é o começo.", {"person": "terceira"}), False)

print("\n--- item 11: IC sozinho ---")
chk("'o IC dela'", tv._detect_bare_ic("O IC no mapa aponta para a origem."))
chk("'IC' traduzido (correto)", tv._detect_bare_ic("O fundo do céu, cúspide da casa 4, o IC, aponta para a origem."), False)

print("\n--- itens 12,14,15: léxico ---")
for tag, frase in (("pede descida", "Plutão pede descida antes da subida."),
                   ("mutable", "Um signo mutable adapta-se."),
                   ("saturina", "A postura saturina domina.")):
    chk(tag, lex(frase))
chk("'pede aprofundamento' (correto)", lex("Plutão pede aprofundamento."), False)
chk("'saturnina' (correto)", lex("A postura saturnina domina."), False)

print("\n--- item 16: glossário de signo ---")
chk("virgiliana", tv._detect_sign_adjectives("A energia virgiliana organiza."))
chk("virginiana (correto)", tv._detect_sign_adjectives("A energia virginiana organiza."), False)

print("\n--- item 13: lint de muleta ---")
doc = "# R\n\n## Seção X\n\n" + "Há uma dificuldade real e um ganho real, com valor real, de forma real e efeito real. " * 1
chk("'real' 5x numa seção (limite 4)", rg.detect_crutch_words(doc) if hasattr(rg,'detect_crutch_words') else tv.detect_crutch_words(doc))
chk("'real' 2x (dentro do limite)", tv.detect_crutch_words("# R\n\n## S\n\nUm ganho real e um risco real."), False)

print("\n--- spell_lint x glossário: divisão de trabalho ---")
sl = [x["word"] for x in tv.spell_lint("# R\n\n## S\n\nO signo mutable e o orgullo saturina, e a leitura virgiliana.", {}) if "word" in x]
print(f"       spell_lint  → {sl}")
gl = [x["match"] for x in tv._detect_sign_adjectives("A leitura virgiliana e a virginiana.")]
print(f"       glossário   → {gl} (com correção: virginiana)")

print("\n--- diagnóstico 2: voz em segunda pessoa chega ao Fio ---")
b = rg.voice_rules_block({"_voice": {"person": "segunda", "name": "Helena Penteado"}})
chk("bloco de 2ª pessoa não-vazio", [b] if "SEGUNDA PESSOA" in b else [])
chk("proíbe 3ª pessoa explicitamente", [b] if "terceira pessoa" in b else [])
chk("cobre a síntese final", [b] if "síntese final" in b else [])

print()
print("RODADA 17/07: TUDO PROVADO" if ok else ">>> ALGO FALHOU")
