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

# Chart com um corpo na FRONTEIRA (regra dos 5°): Netuno geométrico na 7,
# lido na 8. `fmt_position` MANDA o texto nomear as duas casas — os
# detectores de casa precisam aceitar ambas, e não colapsar para uma.
import copy as _copy
FRONT = _copy.deepcopy(H)
FRONT["points"]["neptune"] = {"sign": "pisces", "sign_pt": "Peixes",
                              "degrees": 1.0, "house": 8, "house_geometric": 7}
FRONT["_house_moves"] = [{"planet": "neptune", "from_house": 7,
                          "to_house": 8, "gap_to_cusp": 1.2}]

CANARIOS = [
    # ---- detectores factuais (chart) ----
    ("casa errada / inconsistente",
     lambda t: tv._detect_house_inconsistency(t, L),
     "Quíron conversa com Plutão em Capricórnio na casa 9."),
    ("fronteira não é salvo-conduto: casa fora do par ainda é erro",
     lambda t: tv._detect_house_inconsistency(t, FRONT),
     "Netuno em Peixes está na casa 5, onde encontra o brilho."),
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
    # --- 19/07: as 4 formas do levantamento das 73 ocorrências reais.
    ("forma: par hifenizado DEPOIS ('a oposição Sol-Vênus')",
     lambda t: tv._detect_asserted_aspect(t, H),
     "A oposição Sol-Vênus acrescenta uma tensão ao quadro."),
    ("forma: contração 'do/da' ('o trígono da Lua com Marte')",
     lambda t: tv._detect_asserted_aspect(t, H),
     "o trígono da Lua com Marte pesa aqui."),
    ("forma: 'de A com B' ('a conjunção de Mercúrio com Saturno')",
     lambda t: tv._detect_asserted_aspect(t, H),
     "A conjunção de Mercúrio com Saturno em Virgem amplifica tudo isso."),
    ("forma: oração relativa ('o trígono que Saturno forma em Marte')",
     lambda t: tv._detect_asserted_aspect(t, L),
     "O trígono que Saturno forma em Marte tem orbe fechado."),
    ("forma: par ANTES ligado por 'com' ('O Sol com Netuno em sextil')",
     lambda t: tv._detect_asserted_aspect(t, L),
     "O Sol com Netuno em sextil acrescenta uma camada de poder."),
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
    ("Netuno-Plutão pela forma 'A em <aspecto> com B'",
     lambda t: tv._detect_netuno_plutao_mention(t),
     "Netuno em sextil com Plutão organiza o pano de fundo."),
    ("geracional: social + 'geração' (proibido)",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Júpiter em Touro marca uma geração inteira voltada ao valor material."),
    ("geracional: pessoal + coletivo (proibido)",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Vênus em Libra descreve o gosto coletivo de toda uma geração."),
    ("geracional: 'quem nasceu por volta de' com corpo pessoal",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Marte em Áries é compartilhado com quem nasceu por volta dessa época."),
    ("geracional: sem planeta (proibido)",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "O signo de Peixes carrega, para toda a sua geração, uma névoa."),

    # ---- registro e voz ----
    ("clítico de 3ª em texto de 2ª",
     lambda t: tv._detect_clitic_third_person(t, {"person": "segunda"}),
     "Reconhecer aquilo que a sustenta é o primeiro passo."),
    ("vazamento 'ela mesma'",
     lambda t: tv._detect_third_person_leak(t, {"person": "segunda"}),
     "Você floresce quando há espaço para ser ela própria."),
    ("andaime do prompt vazado",
     lambda t: tv._detect_prompt_scaffolding_leak(t),
     "Conforme as passagens acima, o padrão se confirma."),
    ("sujeito instanciado em 3ª pessoa (Abertura da Helena)",
     lambda t: tv._detect_person_instantiation(t, {"person": "segunda"}),
     "A primeira coisa que sinto é que há uma pessoa aqui que pensa muito."),
    ("regência errada na forma 'tem X como regente'",
     lambda t: tv._detect_rulership(t, L),
     "O Nodo Norte em Libra tem Marte como seu regente."),
    ("regência errada na voz passiva",
     lambda t: tv._detect_rulership(t, L),
     "O Nodo Norte em Libra é regido por Marte."),
    ("palavra colada",
     lambda t: __import__("word_lint").word_lint(t),
     "o idealismo pode se voltarcontra você"),
    ("contração que não aconteceu",
     lambda t: __import__("word_lint").word_lint(t),
     "a clareza de o que você pode dar"),
    ("palavra corrompida (uma letra a mais)",
     lambda t: __import__("word_lint").word_lint(t),
     "a rigidez saturninan gravada na casa 4"),
    ("acento faltando depois de infinitivo",
     lambda t: __import__("word_lint").word_lint(t),
     "você quer fincar ancoras nesse terreno"),
    # --- 19/07: as 8 formas de negação-substituição, das 22 frases REAIS.
    ("neg A: não X, mas Y",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "não por falta de clareza, mas por um impulso de refinar"),
    ("neg B: X, não Y (a invertida — 8 das 22, zero cobertura antes)",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "recebeu isso como ferramenta, não como filtro"),
    ("neg C: não porque X, mas porque Y",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "Não porque você seja sociável, mas porque o modo de operar pede troca."),
    ("neg D: não V X. V Y",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "não pede que você escolha entre pensar e sentir. Pede que se conheça"),
    ("neg E: não V X, V Y",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "não vem de fora para dentro, vem de dentro para fora"),
    ("neg F: não V X — V Y",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "Esses comentários não ficam do lado de fora — entram"),
    ("neg G: X em vez de Y",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "O pertencimento alimenta em vez de drenar"),
    ("neg H: não X, não Y. É Z",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "não era leve, não era superficial. Era carregado"),
    # --- 19/07 (última auditoria antes dos testers): classes NOVAS
    ("regência de ÂNGULO (Asc Sagitário → Júpiter, não o Sol)",
     lambda t: tv._detect_angle_rulership(t, H),
     "o seu Meio-do-Céu em Leão, com o Sol em Virgem como seu regente "
     "de ascendente trabalhando nos bastidores."),
    ("regência de ângulo pela forma 'o regente do Asc é X'",
     lambda t: tv._detect_angle_rulership(t, H),
     "O regente do Ascendente é Marte, e isso organiza a chegada."),
    ("elemento compartilhado falso (Sol Câncer água × Lua Leão fogo)",
     lambda t: tv._detect_shared_element(t, L),
     "quando o Sol e a Lua estão no mesmo elemento, como acontece aqui"),
    ("modalidade compartilhada falsa",
     lambda t: tv._detect_shared_element(t, L),
     "o Sol e a Lua na mesma modalidade sustentam esse ritmo"),
    ("gerundial portuguesa ('a alimentar' por 'alimentando')",
     lambda t: tv._detect_gerundial_portuguesa(t),
     "um Sol canceriano a alimentar e proteger, uma Lua leonina a brilhar"),
    ("'ainda' + pretérito sem negação (sentido invertido)",
     lambda t: tv._detect_ainda_sem_negacao(t),
     "a diplomacia serve para proteger alguém que ainda aprendeu a "
     "ocupar lugar nos relacionamentos"),
    ("primeira pessoa predicativa ('o que me faz eu')",
     lambda t: tv._detect_primeira_pessoa_predicativa(t),
     "onde o que me faz eu, e ninguém mais, precisa ser reconhecido"),
    ("graus sem preposição ('poucos graus de Ceres')",
     lambda t: tv._detect_graus_sem_preposicao(t),
     "Vesta também está em Libra, poucos graus de Ceres, na casa 3"),
    ("gênero do determinante ('um inquietação')",
     lambda t: __import__("word_lint").word_lint(t),
     "quando o outro se torna previsível, acende um inquietação"),
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
    ("meta-comentário do corretor no artefato (Lucca, 19/07)",
     lambda t: [v for v in pg.lint_final_text([("S", [t])])
                if v["kind"] == "meta_comentario_do_corretor"],
     "Aguarda — vou corrigir corretamente."),
    ("meta-comentário recusado na ORIGEM, antes do splice",
     lambda t: ([1] if tv._motivo_reescrita_invalida("Frase original.", t) else []),
     'Ainda há "ela" — corrijo completamente:'),
    ("saída em blocos recusada (uma frase não tem parágrafos)",
     lambda t: ([1] if tv._motivo_reescrita_invalida("Frase original.", t) else []),
     "Primeira versão da frase.\n\n---\n\nSegunda versão da frase."),
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


# Frases que NÃO podem acender — falso positivo é tão grave quanto detector
# morto: gasta reescrita em texto correto e pode corrompê-lo.
NEGATIVOS = [
    ("Netuno e Plutão na frase sem se aspectarem (Sol é o sujeito)",
     lambda t: tv._detect_netuno_plutao_mention(t),
     "O Sol em Câncer em oposição a Plutão e em trígono a Netuno aponta "
     "para uma autoridade difícil."),
    ("par sem hífen encadeado (Mercúrio e Marte / Quíron e Plutão)",
     lambda t: tv._detect_asserted_aspect(t, L),
     "Mercúrio e Marte em trígono, Quíron e Plutão em sextil: esses são "
     "aspectos de um sistema que aprende fazendo."),
    ("qualificador entre o aspecto e a preposição",
     lambda t: tv._detect_asserted_aspect(t, H),
     "O Sol em quadratura quase exata aos Nodos, e Saturno retrógrado em "
     "oposição a Quíron, compõem o retrato."),
    # 19/07 (2ª correção): "coletivo" é vocabulário de CASA 11, não marcador
    # de coorte. Medido: 6 das 8 ocorrências no corpus são grupos/círculos,
    # e as 2 de coorte nomeiam transpessoal. Derrotou o reescritor 3×.
    ("casa 11: 'dimensão coletiva' não é reivindicação de coorte",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Com Vênus na casa 11, o território do afeto tem uma dimensão "
     "coletiva e social: seus vínculos se constroem em grupos."),
    ("casa 11: 'voltado para o coletivo' não é coorte",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "o Sol em Virgem na casa 11, voltado para o coletivo, para os grupos"),
    ("transpessoal + 'coletivamente' segue liberado",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Plutão em Escorpião carrega algo geracional — a sua coorte inteira "
     "nasceu com ele ali, marcada coletivamente por uma relação com o poder."),
    # --- 19/07: a passada de revisão de língua NÃO pode tocar nestes.
    # Os fragmentos retóricos dela são orações incompletas DE PROPÓSITO, e
    # uma revisão vai querer completá-los. (Decisão da Márcia de 19/07:
    # "independente de" e "as coisas saem errado" SAÍRAM dos protegidos —
    # agora que existe ferramenta de língua, o padrão é português correto.)
    ("fragmento retórico isolado é estilo, não frase quebrada",
     lambda t: ([1] if __import__("revisao_lingua").divergencia_de_invariante(t, t)
                else []),
     "Porque o Sol, que deveria ser o motor de tudo isso, está em Câncer."),
    ("revisão que troca o SIGNO é rejeitada",
     lambda t: ([] if __import__("revisao_lingua").divergencia_de_invariante(
         "Saturno em Aquário na casa 3", t) else [1]),
     "Saturno em Peixes na casa 3"),
    ("revisão que troca a CASA é rejeitada",
     lambda t: ([] if __import__("revisao_lingua").divergencia_de_invariante(
         "Saturno em Aquário na casa 3", t) else [1]),
     "Saturno em Aquário na casa 4"),
    ("revisão que INVENTA palavra é recusada ('carma')",
     lambda t: ([] if __import__("revisao_lingua").motivo_recusa(
         "Já a quadratura aos Nodos do carregada de significado traz tensão.", t)
         else [1]),
     "Já a quadratura aos Nodos do carma é carregada de significado traz tensão."),
    ("revisão que APAGA frase é recusada",
     lambda t: ([] if __import__("revisao_lingua").motivo_recusa(
         "Isso provavelmente tem uma história. A mãe nutria de um jeito "
         "recolhido e pouco visível para quem estava de fora do círculo.", t)
         else [1]),
     "A mãe nutria de um jeito recolhido e pouco visível para quem estava "
     "de fora do círculo."),
    ("meta-comentário do revisor é recusado antes do splice",
     lambda t: ([] if __import__("revisao_lingua").motivo_recusa(
         "A hesitação já é uma forma de se calar, e isso pesa no cotidiano.", t)
         else [1]),
     "Aqui está o parágrafo revisado: a hesitação já é uma forma."),
    ("'você é a pessoa que…' é 2ª pessoa e não pode acender",
     lambda t: tv._detect_person_instantiation(t, {"person": "segunda"}),
     "Você é a pessoa que analisa e organiza o que o grupo tenta entender."),
    ("regência CERTA na forma nova não pode acender",
     lambda t: tv._detect_rulership(t, L),
     "O Nodo Norte em Libra tem Vênus como seu regente."),
    ("'revisando' astrológico legítimo não é meta-comentário",
     lambda t: [v for v in pg.lint_final_text([("S", [t])])
                if v["kind"] == "meta_comentario_do_corretor"],
     "pode te manter parada revisando o que já estava bom"),
    ("reescrita legítima de uma frase é ACEITA",
     lambda t: ([1] if tv._motivo_reescrita_invalida(
         "O perigo concreto é entrar num acordo enxergando potencial.", t) else []),
     "O perigo é fechar um acordo olhando o que a outra pessoa pode vir a ser."),
    ("pt-BR fora do dicionário europeu não é corrupção",
     lambda t: __import__("word_lint").word_lint(t),
     "você precisa planejar e registrar as seções do contato harmônico"),
    ("prefixo produtivo não é palavra colada",
     lambda t: __import__("word_lint").word_lint(t),
     "autoafirmação e autoexigência num raciocínio contraintuitivo"),
    # --- contraste COMUM não pode acender (lista da Márcia, 19/07)
    ("contraste 'X, mas Y' é língua, não negação-substituição",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "você quer profundidade, mas aceita a superfície quando precisa"),
    ("'tensão entre X e Y' não é negação-substituição",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "há uma tensão entre pensar e sentir que atravessa o mapa"),
    ("'quando X e quando Y' não é negação-substituição",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "quando você age por competência e quando age por medo"),
    ("comparativo não é negação-substituição",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "é mais fácil falar do que escutar quando o assunto pesa"),
    ("negação simples não é negação-substituição",
     lambda t: [x for x in tv._detectar_tudo(t, None) if x["kind"].startswith("neg_subst")],
     "ele não pede permissão para acontecer, e não há nada de errado nisso"),
    ("enumeração de aspectos não inventa par (Helena, 19/07)",
     lambda t: tv._detect_asserted_aspect(t, H),
     "Os aspectos já descritos — a conjunção com Júpiter, o trígono da Lua "
     "com Saturno, a conexão com Plutão — confirmam sustentação."),
    # negativos das classes novas
    ("regência de ângulo CERTA não pode acender",
     lambda t: tv._detect_angle_rulership(t, H),
     "Júpiter, regente do Ascendente em Sagitário, organiza a chegada."),
    ("elemento compartilhado VERDADEIRO não pode acender",
     lambda t: tv._detect_shared_element(t, L),
     "Vênus e Mercúrio no mesmo elemento sustentam a mesma leitura"),
    ("'a partir de' não é gerundial portuguesa",
     lambda t: tv._detect_gerundial_portuguesa(t),
     "a ferida a partir da qual você construiu tudo isso"),
    ("'tem a dizer' não é gerundial portuguesa",
     lambda t: tv._detect_gerundial_portuguesa(t),
     "o que ele tem a dizer importa mais do que parece"),
    ("'começar a operar' é licenciado em pt-BR",
     lambda t: tv._detect_gerundial_portuguesa(t),
     "Começar a operar a partir dessa equivalência muda o jogo."),
    ("'ainda NÃO aprendeu' está correto",
     lambda t: tv._detect_ainda_sem_negacao(t),
     "você ainda não aprendeu a dizer não, e isso pesa"),
    ("'a poucos graus de' está correto",
     lambda t: tv._detect_graus_sem_preposicao(t),
     "Vesta está a poucos graus de Ceres, na mesma casa"),
    ("'o coração' não é erro de gênero",
     lambda t: __import__("word_lint").word_lint(t),
     "o coração dela sabe disso antes da cabeça"),
    ("1ª pessoa da Márcia é por design, não vazamento",
     lambda t: tv._detect_primeira_pessoa_predicativa(t),
     "O que me interessa neste mapa é que ele não é simples."),
    ("anafórica sem par não pode acender ('esse sextil')",
     lambda t: tv._detect_asserted_aspect(t, H),
     "Mas o que se constrói com esse sextil é conhecimento próprio."),
    ("forma nova não inventa par em frase certa (oposição Sol-Plutão)",
     lambda t: tv._detect_asserted_aspect(t, L),
     "A oposição Sol-Plutão acrescenta uma tensão entre quem você é."),
    ("'passagem' astrológica legítima é trânsito, não andaime",
     lambda t: tv._detect_prompt_scaffolding_leak(t),
     "A passagem de Saturno por Escorpião marcou esses anos."),
    # --- 19/07: a fronteira que o próprio prompt exige não pode ser acusada.
    ("fronteira: as duas casas nomeadas, como o prompt manda",
     lambda t: tv._detect_house_inconsistency(t, FRONT),
     "Netuno em Peixes está na fronteira entre a casa 7 e a casa 8, "
     "com mais força na 8."),
    ("fronteira: só a casa de leitura",
     lambda t: tv._detect_house_inconsistency(t, FRONT),
     "Netuno em Peixes está na casa 8."),
    ("'na casa 7' não é conjunção ao Descendente",
     lambda t: tv._detect_angle_claims(t, FRONT),
     "Netuno em Peixes está na casa 7."),
    ("'na casa 10' não é conjunção ao meio do céu",
     lambda t: tv._detect_angle_claims(t, H),
     "Vênus está na casa 10 e organiza a cena."),
    # --- 19/07: as três frases CORRETAS da Helena que os detectores acusavam.
    ("par hifenizado encadeado (Vênus-Quíron / Saturno-Quíron)",
     lambda t: tv._detect_asserted_aspect(t, H),
     "O Vênus-Quíron em sextil e o Saturno-Quíron em oposição completam "
     "o panorama."),
    ("'projetos coletivos' é vocabulário de casa 11, não de coorte",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Esse Mercúrio opera na sua casa 11 — o território dos grupos, dos "
     "círculos de afinidade, dos projetos coletivos e da vida social."),
    ("'vínculos em projetos coletivos' com Vênus",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Com Vênus na casa 11, seus vínculos afetivos se organizam em torno "
     "de projetos coletivos."),
    ("transpessoal pode dizer geração",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Netuno em Peixes dá a essa geração uma permeabilidade ao invisível."),
    ("social pode dizer coorte",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Júpiter em Touro marca a coorte que nasceu por volta dessa época."),
    ("parágrafo anterior nomeia o transpessoal",
     lambda t: tv._detect_sign_as_generational_agent(t),
     "Plutão em Capricórnio desmonta o que foi construído sobre bases "
     "frágeis. O que essa geração carrega coletivamente é uma relação com "
     "estrutura e colapso."),
    ("enumeração de aspectos com 'aos'",
     lambda t: tv._detect_asserted_aspect(t, H),
     "# M\n\n## Sol\n\nA quadratura aos Nodos já foi lida antes."),
    ("seção com dois sujeitos",
     lambda t: tv._detect_asserted_aspect(t, H),
     "# M\n\n## Sol e Saturno: seu ideal\n\n"
     "Quando Saturno está em oposição a Quíron, há uma ferida."),
    ("corpo realmente no signo do ângulo",
     lambda t: tv._detect_angle_claims(t, L),
     "Lilith aparece junto ao Ascendente."),
]


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

    print("\nA2) NEGATIVOS — não podem acender")
    for nome, fn, frase in NEGATIVOS:
        try:
            r = fn(frase)
        except Exception as e:
            r = [f"ERRO {e}"]
        if r:
            mortos.append(f"FALSO POSITIVO: {nome}")
            print(f"  FALSO+  {nome}   → {r[:1]}")
        else:
            vivos += 1
            print(f"  limpo   {nome}")

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
