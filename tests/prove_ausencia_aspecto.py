"""PRIORIDADE 2 — o detector de ausência de aspecto morde nas SEIS frases
REAIS que a Márcia pegou nos relatórios de 18/07?

Regra R4: a propriedade tem que reprovar o passado. Estas são as frases
verbatim que saíram em produção, com os aspectos que a tabela lista."""
import warnings; warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import text_verifier as tv

# chart mínimo com os aspectos que EXISTEM em cada mapa (só o que o detector usa)
HELENA = {"aspects": [
    {"planet_a":"pluto","planet_b":"chiron"}, {"planet_a":"jupiter","planet_b":"pluto"},
    {"planet_a":"neptune","planet_b":"pluto"}, {"planet_a":"sun","planet_b":"pluto"},
    {"planet_a":"venus","planet_b":"lilith"}]}
LUCCA = {"aspects": [
    {"planet_a":"saturn","planet_b":"chiron"}, {"planet_a":"saturn","planet_b":"lilith"},
    {"planet_a":"saturn","planet_b":"pluto"}, {"planet_a":"uranus","planet_b":"lilith"},
    {"planet_a":"mars","planet_b":"lilith"}, {"planet_a":"venus","planet_b":"lilith"},
    {"planet_a":"pallas","planet_b":"neptune"}]}

SEIS = [
 ("Helena/Plutão", HELENA,
  "Plutão na casa 12 tem aspectos? Nesta seção, a lista está vazia — o que "
  "significa que Plutão opera aqui sem os canais de troca que os aspectos criariam."),
 ("Helena/Lilith", HELENA,
  "Lilith na casa 4 não tem nenhum aspecto ativo listado neste mapa."),
 ("Lucca/Saturno", LUCCA,
  "Saturno aparece sem aspectos a outros planetas, o que o deixa operando sozinho."),
 ("Lucca/Lilith", LUCCA,
  "Para Lilith, não há aspectos que a conectem ao resto do mapa."),
 ("Lucca/Palas", LUCCA,
  "Palas não forma aspecto com nenhum outro corpo, e por isso opera de forma isolada."),
 ("Lucca/Saturno-2", LUCCA,
  "Saturno tem aspectos? Nesta seção nenhum aspecto está listado."),
]
# frases que NÃO podem ser flagradas (ausência VERDADEIRA ou texto normal)
LIMPAS = [
 ("ausência verdadeira (Ceres não tem aspecto)", HELENA,
  "Ceres não faz nenhum aspecto neste mapa."),
 ("texto normal sobre aspectos", HELENA,
  "Plutão forma uma quadratura tensa com Quíron, e isso pede atenção."),
 ("menção a lista sem concluir ausência", HELENA,
  "A lista de aspectos de Plutão aparece na tabela ao lado."),
]

ok = True
print("SEIS FRASES REAIS — o detector morde?\n")
for tag, chart, frase in SEIS:
    hits = tv._detect_false_no_aspect_claims(frase, chart)
    bom = bool(hits)
    ok = ok and bom
    print(f"  {'MORDE ' if bom else '>>> FALHOU'}  {tag}")
    print(f"      {frase[:88]}…")
    if hits:
        print(f"      → {hits[0]['suggestion'][:88]}…")
print("\nFRASES QUE NÃO PODEM SER FLAGRADAS:\n")
for tag, chart, frase in LIMPAS:
    hits = tv._detect_false_no_aspect_claims(frase, chart)
    bom = not hits
    ok = ok and bom
    print(f"  {'LIMPO ' if bom else '>>> FALSO POSITIVO'}  {tag}")
print()
print("PRIORIDADE 2: TUDO PROVADO" if ok else ">>> ALGO FALHOU")
sys.exit(0 if ok else 1)
