"""SUÍTE-CANÁRIO — todo detector tem uma frase que ELE tem de acender.

Por que existe (18/07): `_detect_rulership` estava MORTO. Uma edição minha
apagou a tabela `_REGENCIA`; o detector rodava sem ela e devolvia sempre
vazio. Nos testes isso é indistinguível de "está limpo". Foi encontrado por
acaso.

A prova de ontem não protege o código de hoje. Aqui cada classe tem uma
frase sabidamente defeituosa; se qualquer uma parar de acender, o gate
falha e o detector morto grita na hora, em vez de virar silêncio que parece
aprovação.

Regra: a frase-canário é INVENTADA, não a real que motivou o detector —
"mordeu no teste" ≠ "classe coberta".
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fixture import build_chart, HELENA, LUCCA
import text_verifier as tv
import report_generator as rg
import pdf_generator as pg

H = build_chart(HELENA)
L = build_chart(LUCCA)

# (nome da classe, chamada, frase-canário) — cada uma DEVE acender
CANARIOS = [
    # ---- detectores factuais (chart) ----
    ("casa errada / inconsistente",
     lambda t: tv._detect_house_inconsistency(t, L),
     "Quíron conversa com Plutão em Capricórnio na casa 9."),
    ("ângulo afirmado errado",
     lambda t: tv._detect_angle_claims(t, L),
     "Mercúrio circula enquanto Vênus se firma, na cúspide do meio do céu."),
    ("regência errada",
     lambda t: tv._detect_rulership(t, L),
     "Marte rege o Nodo Norte com firmeza."),
    ("falsa ausência de aspecto",
     lambda t: tv._detect_false_no_aspect_claims(t, L),
     "Saturno aqui não forma nenhum aspecto com os demais corpos."),
    ("aspecto AFIRMADO inexistente",
     lambda t: tv._detect_asserted_aspect(t, H),
     "# M\n\n## Vênus: como você ama\n\n"
     "A quadratura entre Vênus e Saturno organiza o desejo."),
    ("aspecto de TIPO errado",
     lambda t: tv._detect_asserted_aspect(t, H),
     "# M\n\n## Quíron\n\nO trígono entre Saturno e Quíron abre a ferida."),
    ("aspecto elíptico (assunto da seção)",
     lambda t: tv._detect_asserted_aspect(t, H),
     "# M\n\n## Saturno: onde você amadurece\n\n"
     "A conjunção com o Sol mostra que ele não opera sozinho."),
    ("ângulo por 'cúspide da casa N'",
     lambda t: tv._detect_angle_claims(t, L),
     "Sua Vênus está em Gêmeos, na cúspide da casa 10."),
    ("ângulo por nome (fundo do céu)",
     lambda t: tv._detect_angle_claims(t, L),
     "Vênus repousa no fundo do céu."),
    ("ângulo por sigla (sobre o MC)",
     lambda t: tv._detect_angle_claims(t, L),
     "Vênus paira sobre o MC."),
    ("cúspide afirmada errada",
     lambda t: tv._validate_cusp_claims(t, L),
     "A cúspide da casa 7 em Áries organiza os vínculos."),

    # ---- doutrina ----
    ("Netuno-Plutão geracional",
     lambda t: tv._detect_netuno_plutao_mention(t),
     "O sextil entre Netuno e Plutão marca a coorte inteira."),
    ("signo como agente geracional",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "O signo de Peixes carrega, para toda a sua geração, uma névoa."),

    # ---- registro e voz ----
    ("clítico de 3ª em texto de 2ª",
     lambda t: tv._detect_clitic_third_person(t, {"person": "segunda"}),
     "Reconhecer aquilo que a sustenta é o primeiro passo."),
    ("vazamento 'ela mesma'",
     lambda t: tv._detect_third_person_leak(t, {"person": "segunda"}),
     "Você floresce quando há espaço para ser ela própria."),
    ("IC sem tradução",
     lambda t: tv._detect_bare_ic(t),
     "O IC organiza a base da carta."),

    # ---- glossários ----
    ("glossário de signo",
     lambda t: tv._detect_sign_adjectives(t),
     "A postura escorpiônica pesa nesse ponto."),
    ("glossário planetário",
     lambda t: tv._detect_planet_adjectives(t),
     "O tom saturniano atravessa a leitura."),

    # ---- aspectos (forma) ----
    ("composição de aspecto inválida",
     lambda t: tv._detect_invalid_aspect_composition(t),
     "A quadratura em sextil entre os dois pesa."),
    ("par de aspecto incompleto",
     lambda t: tv._detect_broken_aspect_pair(t),
     "O trígono entre sua Vênus está em Touro acrescenta algo."),
    ("contagem anunciada x enumerada",
     lambda t: tv._detect_count_mismatch(t),
     "Há três conjunções centrais: Sol e Júpiter, Lua e Saturno."),

    # ---- artefato ----
    ("markdown vazado no PDF",
     lambda t: pg.lint_final_text([("Seção", [t])]),
     "O fecho chega aqui.## Fio Condutor"),
    ("frase colada",
     lambda t: pg.lint_final_text([("Seção", [t])]),
     "Isso se assenta com calma.A seguir vem o resto."),
    ("repetição entre seções",
     lambda t: rg.detect_cross_section_repetition(t),
     "# R\n\n## Uma\n\nA base emocional se organiza em torno do vínculo e da "
     "troca constante com quem está perto.\n\n## Outra\n\nA base emocional se "
     "organiza em torno do vínculo e da troca constante com quem está perto.\n"),
    ("muleta por documento",
     lambda t: tv.detect_crutch_words(t)["documento"],
     "# R\n" + "".join(f"\n## S{i}\n\nUm ganho real, um risco real, um valor real.\n"
                       for i in range(1, 11))),
]

# ---- léxico: uma frase por CATEGORIA, gerada do próprio padrão ----
_LEX_CANARIOS = {
    "erro_grafia_pertenca": "A pertença ao grupo pesa.",
    "erro_grafia_herida": "A herida antiga volta.",
    "erro_grafia_desiluso": "O desiluso chega depois.",
    "erro_espanhol": "Há um certo orgullo nisso.",
    "erro_grafia_tenciona": "A quadratura tenciona o quadro.",
    "erro_grafia_adjetivo": "A postura saturina domina.",
    "termo_ingles": "Um signo mutable adapta-se.",
    "termo_rejeitado": "A energia aquilombada resiste.",
    "termo_inventado": "O Sol ariando na casa 1.",
    "termo_inexistente": "Um vínculo num-a-num comprometido.",
    "erro_acento": "Preciso abrí-lo devagar.",
    "grafia_pre_reforma": "Juno é o asteróide do compromisso.",
    "pt_europeu": "O contacto com o outro muda tudo.",
    "concordancia": "Uma calor humano genuíno.",
    "concordancia_comparativa": "Pessoas boas demais quanto às difíceis.",
    "registro_pessoa": "A gente precisa brilhar.",
    "modo_verbal": "O que individualize essa energia.",
    "sintaxe": "Você tem para o outro com naturalidade.",
    "sintaxe_adverbio_encaixado": "De maneiras que você não completamente antecipou.",
    "gramatica": "O que te exilaram foi a recusa.",
    "gramatica_ambiguidade": "A voz não nasce no quarto sozinho.",
    "frase_vazia": "Esse é o mapa emocional mais antigo que você carrega.",
    "muleta_retorica": "Fica claro que isso pesa.",
    "vocabulario_rebuscado": "Ele busca guarecer o que sente.",
    "erro_conceitual": "A posição mais interior do mapa.",
    "metafora_sem_referente": "Forças vivas no tecido da época.",
    "forma_da_metafora": "Uma autoestima barulhosa.",
    "erro_astrologico_em_corpo": "A Vênus em Lilith organiza o desejo.",
}


def _lex_hits(texto):
    achados = []
    for entry in tv._FORBIDDEN_LEXICON:
        pat, cat = entry[0], entry[1]
        val = entry[3] if len(entry) > 3 else None
        for m in re.finditer(pat, texto, flags=re.IGNORECASE):
            if val is not None and not val(texto, m):
                continue
            achados.append(cat)
    return achados


def main():
    vivos, mortos = 0, []
    print("SUÍTE-CANÁRIO — cada detector tem de acender na sua frase\n")

    print("A) DETECTORES")
    for nome, fn, frase in CANARIOS:
        try:
            r = fn(frase)
            acendeu = bool(r)
        except Exception as e:
            acendeu = False
            r = f"ERRO {type(e).__name__}: {e}"
        if acendeu:
            vivos += 1
            print(f"  vivo    {nome}")
        else:
            mortos.append(nome)
            print(f"  MORTO   {nome}   → {r}")

    print("\nB) CATEGORIAS DE LÉXICO")
    for cat, frase in sorted(_LEX_CANARIOS.items()):
        hits = _lex_hits(frase)
        if cat in hits:
            vivos += 1
            print(f"  vivo    {cat}")
        else:
            mortos.append(f"lexico:{cat}")
            print(f"  MORTO   {cat}   (acendeu: {hits or 'nada'})")

    print("\nC) NEGAÇÃO-SUBSTITUIÇÃO (19 padrões)")
    canario_neg = [
        "não é fraqueza: é escolha.",
        "não são limites — são direções.",
        "não como fuga, mas como prática.",
        "os vínculos pedem troca — e não apenas estabilidade.",
        "O mapa não pede que você recue. Pede que você avance.",
        "nunca produziu calma. Produziu apenas ruído.",
        "A pergunta não é se você aguenta — é o quanto custa.",
        "não virá para confirmar, mas para expor.",
        "não porque falte cuidado — eles existem. Mas porque viraram adiamento.",
    ]
    acesos = set()
    for frase in canario_neg:
        for pat, cat in tv._NEGATION_SUBSTITUTION_PATTERNS:
            if re.search(pat, frase, flags=re.IGNORECASE):
                acesos.add(cat)
    total_pat = len(tv._NEGATION_SUBSTITUTION_PATTERNS)
    print(f"  {len(acesos)} de {total_pat} padrões acesos pelas 9 frases-canário")
    if len(acesos) < 6:
        mortos.append("neg_subst (menos de 6 padrões acesos)")
    else:
        vivos += 1

    print("\n" + "=" * 60)
    print(f"VIVOS: {vivos}    MORTOS: {len(mortos)}")
    for m in mortos:
        print(f"   MORTO: {m}")
    print("=" * 60)
    return 1 if mortos else 0


if __name__ == "__main__":
    sys.exit(main())
