"""Verificador determinístico do texto do relatório (pós-síntese, pré-PDF).

Rodadas de detecção:

  2a. Léxico proibido — grafias erradas ("pertença", "herida", "desiluso"),
      muletas retóricas ("os dados não deixam dúvida", "fica claro que",
      "é evidente que"), termos rejeitados ("fachada solar").

  2b. Padrões de negação-substituição — todas as variantes sintáticas de
      "não é X, é Y" que sobreviverem ao prompt.

  2c. Nomenclatura de aspectos — locuções que combinam dois nomes de
      aspecto ("quadratura em sextil", "trígono de oposição"), erro
      diagnóstico comum.

  2d. Contagem anunciada vs enumeração — se o texto anuncia
      "três conjunções — A e B" o número tem que bater.

  2e. Léxico português — spellcheck com wordlist pt-BR (pyspellchecker),
      excluindo whitelist astrológica + nomes próprios do payload.
      Rede pra pegar futuros "herida" antes que sejam catalogados.

Ação sobre violações: para cada FRASE flagrada, aciona segunda passada de
reescrita direcionada. Máximo 2 tentativas por frase. Se persistir, loga
VERIFIER_FAIL e mantém a frase original (nunca bloqueia o relatório).

Retorna (texto_corrigido, lista_de_violações_com_status).
"""
from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger("natal-api")


# ============================================================
# 2a — LÉXICO PROIBIDO
# ============================================================
# Cada entrada: (pattern, categoria, sugestão, [validator opcional]).
# O validator recebe (full_text, match_obj) e retorna True se o match é
# uma violação REAL. Se ausente, todo match é violação.

def _pertenca_is_substantive(text, m):
    """`pertença` é AMBÍGUO em pt-BR:
       - substantivo (grafia errada de 'pertencimento') → violação
       - verbo 'pertencer' no subjuntivo presente 3sg ('que pertença',
         'embora pertença', 'para que pertença') → uso correto
    Retorna True apenas se o match é o substantivo. Heurística: se as
    últimas ~4 palavras antes do match contêm um marcador de subjuntivo
    ('que', 'embora', 'caso', 'talvez', 'quando', 'para que', 'de modo
    que'), é verbo — ignorar."""
    start = m.start()
    window = text[max(0, start - 50):start].lower()
    # Marcadores comuns de subjuntivo em posição próxima
    if re.search(r"\b(que|embora|caso|talvez|quando|conquanto|desde\s+que|para\s+que|de\s+modo\s+que|de\s+forma\s+que|sem\s+que|antes\s+que)\s+\S{0,20}$", window):
        return False
    return True


_FORBIDDEN_LEXICON = [
    # (pattern, categoria, sugestão, [validator])
    (r"\bpertença\b", "erro_grafia_pertenca", "pertencimento", _pertenca_is_substantive),
    (r"\bherida\b",    "erro_grafia_herida",   "ferida"),
    (r"\bdesiluso\b",  "erro_grafia_desiluso", "desilusão"),
    # Família espanhola/ibérica — mesma classe de herida/pertença: o modelo
    # escorrega para o castelhano em palavras emocionais. Vistos em produção:
    # orgullo, cerrada (16/07). Os demais são os vizinhos mais prováveis da
    # mesma família semântica (emoção/fechamento/vínculo).
    # "aquilombada" (16/07): rastreada — NÃO existe nos transcripts nem nos
    # chunks (fonte limpa); veio da síntese. Vocabulário fora da voz da Marcia.
    (r"\baquilombad[ao]s?\b", "termo_rejeitado",
     "vocabulário fora da voz — reescrever com termo direto (ex.: 'entrincheirada', 'fechada em si')"),
    # Achados do inventário 17/07:
    (r"\breencuadrar\b",  "erro_espanhol", "reenquadrar"),
    (r"\btencionam?\b",   "erro_grafia_tenciona", "tensiona (criar tensão); 'tencionar' é ter intenção"),
    (r"\bconjunção\s+cerrada\b", "termo_rejeitado",
     "não é termo da Marcia — usar 'conjunção apertada' ou 'conjunção justa'"),
    # Gramática pontual (casos reais de 17/07 — nunca mais verbatim):
    (r"\bo\s+que\s+te\s+exilaram\b", "gramatica",
     "sem regência válida — reescrever (ex.: 'o que exilaram em você foi…')"),
    (r"\bde\s+confiança\s+profissional\b", "gramatica",
     "sintagma invertido — 'um profissional de confiança'"),
    (r"\bno\s+quarto\s+sozinho\b", "gramatica_ambiguidade",
     "'sozinho' gruda em 'quarto' — reescrever ('sozinho no quarto')"),
    # --- PORTUGUÊS EUROPEU (17/07, 2ª rodada) -------------------------
    # São palavras portuguesas CORRETAS — em PT-PT. Nenhum corretor as
    # acusa; só uma lista explícita as pega. O relatório é pt-BR.
    # Ampliação de 19/07, ao desligar o spell_lint: ele era a única camada
    # que tocava (mal) nestas formas. Medido antes de escrever — o léxico
    # cobria 28 de 48 grafias pt-PT testadas. Estas são as que faltavam e
    # que NÃO são também palavra válida em pt-BR (por isso "rapaz" fica de
    # fora: em pt-BR é palavra corrente, não marca de Portugal).
    (r"\bactiv[oa](s)?\b",  "pt_europeu", "ativo(a)(s)"),
    (r"\bafect(?:ar|a|ou|am|ando|ado|ada)\b", "pt_europeu", "afetar/afeta/afetou"),
    (r"\bafecto(s)?\b",     "pt_europeu", "afeto(s)"),
    (r"\breacção|\breacções\b", "pt_europeu", "reação/reações"),
    (r"\bgénero(s)?\b",     "pt_europeu", "gênero(s)"),
    (r"\bautónom[ao](s)?\b", "pt_europeu", "autônomo(a)(s)"),
    (r"\bsinónimo(s)?\b",   "pt_europeu", "sinônimo(s)"),
    (r"\btelefónic[ao](s)?\b", "pt_europeu", "telefônico(a)"),
    (r"\bcónjuge(s)?\b",    "pt_europeu", "cônjuge(s)"),
    (r"\bsubti(?:l|s)\b",   "pt_europeu", "sutil/sutis"),
    (r"\bregisto(s)?\b",    "pt_europeu", "registro(s)"),
    (r"\bpercepcionar\b",   "pt_europeu", "perceber"),
    (r"\butente(s)?\b",     "pt_europeu", "usuário(s)"),
    (r"\becrã(s)?\b",       "pt_europeu", "tela(s)"),
    (r"\bcontacto(s)?\b",   "pt_europeu", "contato(s)"),
    (r"\bactiv(?:ar|a|ou|ando|ado|ada)\b", "pt_europeu", "ativar/ativa/ativou/ativando"),
    (r"\bcontact(ar|a|ou|ando)\b", "pt_europeu", "contatar/contata/contatou/contatando"),
    (r"\baspeto(s)?\b",     "pt_europeu", "aspecto(s)"),
    (r"\bharmónic[ao](s)?\b", "pt_europeu", "harmônico(a)"),
    (r"\bfenómeno(s)?\b",   "pt_europeu", "fenômeno(s)"),
    (r"\bténue(s)?\b",      "pt_europeu", "tênue(s)"),
    (r"\bgénese\b",         "pt_europeu", "gênese"),
    (r"\bgénio(s)?\b",      "pt_europeu", "gênio(s)"),
    (r"\banónim[ao](s)?\b", "pt_europeu", "anônimo(a)"),
    (r"\bcómod[ao](s)?\b",  "pt_europeu", "cômodo(a)"),
    (r"\becconómic|económic[ao](s)?\b", "pt_europeu", "econômico(a)"),
    (r"\bcrónic[ao](s)?\b", "pt_europeu", "crônico(a)"),
    (r"\btónic[ao](s)?\b",  "pt_europeu", "tônico(a)"),
    (r"\bprémio(s)?\b",     "pt_europeu", "prêmio(s)"),
    (r"\bbónus\b",          "pt_europeu", "bônus"),
    (r"\bfacto(s)?\b",      "pt_europeu", "fato(s)"),
    (r"\bactual(mente)?\b", "pt_europeu", "atual/atualmente"),
    (r"\bacç(ão|ões)\b",    "pt_europeu", "ação/ações"),
    (r"\bafectiv[ao](s)?\b","pt_europeu", "afetivo(a)"),
    (r"\bobjectiv[ao](s)?\b","pt_europeu", "objetivo(a)"),
    (r"\bdirecç(ão|ões)\b", "pt_europeu", "direção/direções"),
    (r"\bsector(es)?\b",    "pt_europeu", "setor(es)"),
    (r"\bperspetiva(s)?\b", "pt_europeu", "perspectiva(s)"),
    (r"\breceção\b",        "pt_europeu", "recepção"),
    (r"\badopt(ar|a|ou|ando)\b", "pt_europeu", "adotar/adota/adotou/adotando"),
    (r"\bóptim[ao](s)?\b",  "pt_europeu", "ótimo(a)"),
    (r"\beléctric[ao](s)?\b","pt_europeu", "elétrico(a)"),
    (r"\bexact[ao](s)?\b",  "pt_europeu", "exato(a)"),
    (r"\bprojecto(s)?\b",   "pt_europeu", "projeto(s)"),
    (r"\bcorrect[ao](s)?\b","pt_europeu", "correto(a)"),
    (r"\bdirect[ao](s)?\b", "pt_europeu", "direto(a)"),
    (r"\bselecç(ão|ões)\b", "pt_europeu", "seleção/seleções"),
    (r"\breflectir\b",      "pt_europeu", "refletir"),
    (r"\brespetiv[ao](s)?\b","pt_europeu", "respectivo(a)"),
    (r"\bconnosco\b",       "pt_europeu", "conosco"),
    (r"\bregist[oa]\b",     "pt_europeu", "registro"),
    (r"\bfactor(es)?\b",    "pt_europeu", "fator(es)"),
    # ITEM 2 — "<corpo> em <corpo>": erro astrológico grosseiro. "em" liga
    # corpo a SIGNO ou a CASA, nunca a outro corpo. "Vênus em Lilith" (17/07).
    (r"\b(?:Sol|Lua|Mercúrio|Vênus|Marte|Júpiter|Saturno|Urano|Netuno|"
     r"Plutão|Quíron|Lilith|Ceres|Palas|Juno|Vesta|Ascendente|Meio-do-Céu)"
     r"\s+em\s+"
     r"(?:Sol|Lua|Mercúrio|Vênus|Marte|Júpiter|Saturno|Urano|Netuno|"
     r"Plutão|Quíron|Lilith|Ceres|Palas|Juno|Vesta|Ascendente|Meio-do-Céu)\b",
     "erro_astrologico_em_corpo",
     "'em' liga corpo a SIGNO ou CASA, nunca a outro corpo — se é aspecto, "
     "nomeá-lo ('Vênus em sextil com Lilith'); se é conjunção, usar 'com'"),
    # --- achados da leitura de cliente, 18/07 ---
    # A METÁFORA É DELA e fica — rastreada nos transcripts (Valquiria
    # Zampirolli, Mapa Natal): "autoestima e amor próprio costuma ser
    # sentimentos que não fazem muito barulho… é uma autoestima que ela é
    # BARULHENTA". O RAG recuperou certo; o texto colou torto: usou
    # "barulhosa" (não é a palavra dela) e "circunda a casa 5" (não se
    # circunda uma casa). Corrige a FORMA, preserva a imagem.
    (r"\bbarulhos[ao]s?\b", "forma_da_metafora",
     "a palavra dela é 'barulhenta' — manter a imagem, corrigir a forma"),
    (r"\bcircund[ao]\s+a\s+casa\b|\bcircund[ao]\s+o\s+signo\b",
     "erro_conceitual",
     "não se circunda uma casa nem um signo — 'na casa 5', 'que atravessa a casa 5'"),
    (r"\bariando\b", "termo_inventado",
     "palavra inventada — reescrever ('em Áries', 'com qualidade ariana')"),
    (r"\babrí-l[oa]s?\b", "erro_acento", "abri-lo / abri-la (sem acento)"),
    (r"\bperforma(?:r|ndo|tiv[ao]s?|tic[ao]s?)\b", "termo_ingles",
     "anglicismo — 'atuar', 'desempenhar', 'representar'"),
    (r"\bperformances?\b", "termo_ingles",
     "anglicismo — 'desempenho', 'atuação'"),
    (r"\binsights?\b", "termo_ingles",
     "anglicismo — 'percepção', 'compreensão', 'estalo'"),
    (r"\besse\s+endereço\s+fala\b|\bo\s+endereço\s+fala\b",
     "termo_rejeitado", "'endereço' não é termo astrológico — usar 'posicionamento'"),
    # Gramática pontual (frases REAIS, 18/07 — nunca mais verbatim):
    (r"\bum[ao]\s+calor\s+humano\b", "concordancia",
     "'calor' é masculino — 'um calor humano'"),
    (r"\bvocê\s+tem\s+para\s+o\s+outro\b", "sintaxe",
     "falta o objeto — 'o que você tem para oferecer ao outro'"),
    (r"\braramente\s+deixava\s+de\s+sentir\s+como\b", "sintaxe",
     "dupla negação embaralha o sentido — reescrever afirmativamente"),
    (r"\ba\s+gente\s+precisa\s+brilhar\b", "registro_pessoa",
     "'a gente' quebra a 2ª pessoa — reescrever com 'você'"),
    (r"\bque\s+individualize\b", "modo_verbal",
     "subjuntivo indevido — 'o que individualiza'"),
    (r"\bmapa\s+emocional\s+mais\s+antigo\s+que\s+você\s+carrega\b",
     "frase_vazia", "não diz nada concreto — nomear o que é, de fato"),
    # "num-a-num" não existe em português (17/07):
    (r"\bnum[- ]a[- ]num\b", "termo_inexistente",
     "não existe em português — reescrever ('um a um', 'individualmente')"),
    # "posição interior do mapa" — não existe; provável confusão com o
    # hemisfério INFERIOR (17/07):
    (r"\bposiç(?:ão|ões)\s+(?:mais\s+)?interior(?:es)?\s+do\s+mapa\b",
     "erro_conceitual", "não existe 'posição interior' — se é hemisfério, é INFERIOR"),
    # Advérbio encaixado entre "não" e o verbo — sintaxe torta (17/07):
    (r"\bnão\s+(?:completamente|totalmente|inteiramente|plenamente|"
     r"exatamente|realmente|necessariamente|inteiramente)\s+"
     r"(?:antecipou|previu|percebeu|entendeu|compreendeu|reconheceu|"
     r"assimilou|integrou|elaborou|processou)\b", "sintaxe_adverbio_encaixado",
     "advérbio encaixado entre 'não' e o verbo — mover ('não antecipou completamente')"),
    # Metáfora abstrata sem referente concreto (17/07): "tecido da época"
    (r"\b(?:tecido|trama|malha|tessitura)\s+d[aoe]s?\s+"
     r"(?:época|tempo|mundo|vida|existência|real|realidade|história)\b",
     "metafora_sem_referente",
     "metáfora abstrata sem referente concreto — dizer o que é, concretamente"),
    # Vocabulário rebuscado / grafia banidos pela Márcia (triagem 17/07):
    (r"\basteróide?s?\b", "grafia_pre_reforma",
     "asteroide / asteroides — sem acento desde o Acordo de 1990"),
    (r"\bequilibrio\b", "erro_acento", "equilíbrio"),
    (r"\bcluster(s)?\b", "termo_ingles",
     "traduzir ('aglomerado', 'agrupamento') ou reescrever"),
    (r"\bmainstream\b", "termo_ingles",
     "traduzir ('convencional', 'estabelecido') ou remover"),
    (r"\blilitian[ao]s?\b", "termo_rejeitado",
     "neologismo que a Márcia não usa — dizer 'a energia de Lilith'"),
    (r"\bsubvalorad[ao]s?\b", "termo_rejeitado",
     "não é palavra corrente — usar 'subestimado' ou 'desvalorizado'"),
    # Vocabulário rebuscado banido pela Márcia (ver rare_word_lint):
    (r"\bguarec(?:er|e|em|ia|eu|endo|ido|ida)\b", "vocabulario_rebuscado",
     "palavra que ninguém usaria falando — trocar por 'abrigar', 'proteger', 'acolher'"),
    # --- rodada 17/07 (leitura completa da Márcia) ---
    (r"\bpede\s+(?:a\s+)?descida\b", "termo_rejeitado",
     "usar 'pede aprofundamento' — 'descida' não é termo dela"),
    (r"\bsaturina[s]?\b", "erro_grafia_adjetivo", "saturnina"),
    (r"\bsaturino[s]?\b", "erro_grafia_adjetivo", "saturnino"),
    # Termos astrológicos em INGLÊS (o modelo escorrega do jargão técnico):
    (r"\bmutable\b",  "termo_ingles", "mutável"),
    (r"\bcardinal\s+sign\b", "termo_ingles", "signo cardinal"),
    (r"\bfixed\s+sign\b",    "termo_ingles", "signo fixo"),
    (r"\bwaxing\b",   "termo_ingles", "crescente"),
    (r"\bwaning\b",   "termo_ingles", "minguante"),
    (r"\brising\s+sign\b", "termo_ingles", "Ascendente"),
    (r"\bhouse\s+cusp\b",  "termo_ingles", "cúspide da casa"),
    (r"\btrine\b",    "termo_ingles", "trígono"),
    (r"\bsquare\b",   "termo_ingles", "quadratura"),
    (r"\bsextile\b",  "termo_ingles", "sextil"),
    (r"\bopposition\b", "termo_ingles", "oposição"),
    (r"\bconjunction\b", "termo_ingles", "conjunção"),
    (r"\bretrograde\b", "termo_ingles", "retrógrado"),
    (r"\borgullo\b",    "erro_espanhol", "orgulho"),
    (r"\bcerrad[ao]s?\b", "erro_espanhol", "fechada/fechado"),
    (r"\borgullos[ao]s?\b", "erro_espanhol", "orgulhosa/orgulhoso"),
    (r"\bapoy[oa]\b",   "erro_espanhol", "apoio"),
    (r"\bconsuelo\b",   "erro_espanhol", "consolo"),
    (r"\bmiedo\b",      "erro_espanhol", "medo"),
    (r"\bvergüenza\b",  "erro_espanhol", "vergonha"),
    (r"\bcariño\b",     "erro_espanhol", "carinho"),
    (r"\bsoledad\b",    "erro_espanhol", "solidão"),
    (r"\bternura\s+y\b", "erro_espanhol", "'y' castelhano — usar 'e'"),
    (r"\bfuerza\b",     "erro_espanhol", "força"),
    (r"\bcorazón\b",    "erro_espanhol", "coração"),
    (r"\bentrega\s+total\s+y\b", "erro_espanhol", "'y' castelhano — usar 'e'"),
    # Concordância comparativa quebrada: "boas demais quanto às" (Helena,
    # 16/07) — 'demais quanto a' não existe; ou é 'tanto quanto', ou reescreve.
    (r"\b\w+\s+demais\s+quanto\s+(?:a|à|às|ao|aos)\b", "concordancia_comparativa",
     "estrutura comparativa quebrada — usar 'tanto … quanto' ou reescrever a comparação"),
    (r"os dados não deixam dúvida", "muleta_retorica",
     "substituir por uma afirmação direta sem invocar 'os dados'"),
    (r"\bnão deixa dúvida\b",       "muleta_retorica",
     "reescrever a asserção sem apelar para 'não deixa dúvida'"),
    (r"\bfica claro que\b",         "muleta_retorica",
     "afirmar diretamente, sem 'fica claro que'"),
    (r"\bé evidente que\b",         "muleta_retorica",
     "afirmar diretamente, sem 'é evidente que'"),
    (r"\bfachada solar\b",          "termo_rejeitado",
     "reformular sem 'fachada solar' — expressão indesejada"),
]



# ============================================================
# GLOSSÁRIO DE ADJETIVOS DE SIGNO (item 16, 17/07)
# ============================================================
# Lista FECHADA da Márcia. Qualquer adjetivo derivado de nome de signo que
# NÃO esteja aqui é violação — "virgiliana" (por virginiana) é palavra real
# do português (de Virgílio), então o spellchecker JAMAIS a pegaria: é este
# glossário que a pega. Divisão de trabalho documentada no ESTADO.
_SIGN_ADJECTIVES_OK = {
    "ariana", "ariano", "arianas", "arianos",
    "taurina", "taurino", "taurinas", "taurinos",
    "geminiana", "geminiano", "geminianas", "geminianos",
    "canceriana", "canceriano", "cancerianas", "cancerianos",
    "leonina", "leonino", "leoninas", "leoninos",
    "virginiana", "virginiano", "virginianas", "virginianos",
    "libriana", "libriano", "librianas", "librianos",
    "escorpiana", "escorpiano", "escorpianas", "escorpianos",
    "sagitariana", "sagitariano", "sagitarianas", "sagitarianos",
    "capricorniana", "capricorniano", "capricornianas", "capricornianos",
    "aquariana", "aquariano", "aquarianas", "aquarianos",
    "pisciana", "pisciano", "piscianas", "piscianos",
}
# Variantes ERRADAS observadas ou previsíveis → correção. O detector é uma
# lista explícita (não um regex genérico) para nunca acusar palavra comum.
_SIGN_ADJECTIVE_ERRORS = {
    "virgiliana": "virginiana", "virgiliano": "virginiano",
    "virgemiana": "virginiana", "virgiana": "virginiana",
    "virginiense": "virginiana",
    "escorpiônica": "escorpiana", "escorpionica": "escorpiana",
    "escorpiniana": "escorpiana", "scorpiana": "escorpiana",
    "cancersiana": "canceriana", "cancriana": "canceriana",
    "capricorniense": "capricorniana", "capricorniana2": "capricorniana",
    "sagitariense": "sagitariana", "sagitarina": "sagitariana",
    "aquariense": "aquariana", "aquarina": "aquariana",
    "gemeniana": "geminiana", "gemininana": "geminiana",
    "libriense": "libriana", "librana": "libriana",
    "taurense": "taurina", "taureana": "taurina",
    "leoniana": "leonina", "leonesa": "leonina",
    "pisciense": "pisciana", "peixiana": "pisciana",
    "arieana": "ariana", "ariense": "ariana",
}


def _detect_sign_adjectives(text):
    """Adjetivo de signo fora do glossário fechado da Márcia."""
    out = []
    for wrong, right in _SIGN_ADJECTIVE_ERRORS.items():
        for m in re.finditer(rf"\b{wrong}\b", text, flags=re.IGNORECASE):
            out.append({"kind": "glossario_signo", "match": m.group(0),
                        "offset": m.start(), "suggestion": right})
    return out



# ============================================================
# GLOSSÁRIO DE ADJETIVOS PLANETÁRIOS (17/07, 2ª rodada)
# ============================================================
# Faltava: só existiam `saturina/saturino` soltos no léxico, e "saturniano"
# escapava de tudo (a Márcia pegou no relatório). Agora espelha exatamente
# o glossário de signos: lista fechada + variantes erradas com a correção.
#
# Formas canônicas confirmadas pela Márcia: saturnina / saturnino.
# As demais seguem o uso corrente em pt-BR. Lista EXTENSÍVEL — quando
# aparecer variante nova, entra aqui em vez de virar regex genérico, que
# acusaria palavra comum.
_PLANET_ADJECTIVE_ERRORS = {
    "saturniano": "saturnino", "saturniana": "saturnina",
    "saturnianos": "saturninos", "saturnianas": "saturninas",
    "saturnal": "saturnino", "saturnino2": "saturnino",
    "venusino": "venusiano", "venusina": "venusiana",
    "venerino": "venusiano", "venerina": "venusiana",
    "mercuriano2": "mercuriano", "mercurino": "mercurial",
    "mercurina": "mercurial",
    "jupiteriano2": "jupiteriano", "jupterino": "jupiteriano",
    "marciano2": "marcial", "martino": "marcial", "martina": "marcial",
    "uraniano2": "uraniano", "uranino": "uraniano",
    "netuniano2": "netuniano", "netunino": "netuniano",
    "plutonino": "plutoniano", "plutonina": "plutoniana",
    "quironino": "quironiano", "quironina": "quironiana",
    "lunino": "lunar", "lunina": "lunar",
    "solino": "solar", "solina": "solar",
}


def _detect_planet_adjectives(text):
    """Adjetivo planetário fora do glossário fechado."""
    out = []
    for wrong, right in _PLANET_ADJECTIVE_ERRORS.items():
        if wrong.endswith("2"):
            continue                      # marcador interno, não é padrão
        for m in re.finditer(rf"\b{wrong}\b", text, flags=re.IGNORECASE):
            out.append({"kind": "glossario_planeta", "match": m.group(0),
                        "offset": m.start(), "suggestion": right})
    return out


# ============================================================
# DETECTORES SEMÂNTICOS — rodada 17/07 (doutrina da Márcia)
# ============================================================
_SIGN_NAMES_PT = ("Áries|Aries|Touro|Gêmeos|Gemeos|Câncer|Cancer|Leão|Leao|"
                  "Virgem|Libra|Escorpião|Escorpiao|Sagitário|Sagitario|"
                  "Capricórnio|Capricornio|Aquário|Aquario|Peixes")
_TRANSPESSOAIS = ("Urano", "Netuno", "Plutão", "Plutao")


# O ASPECTO PRECISA LIGAR OS DOIS (19/07).
#
# A primeira versão acendia com qualquer frase que tivesse os dois nomes a
# até 80 caracteres E uma palavra de aspecto em QUALQUER lugar da frase. Numa
# frase que enumera vários aspectos isso é uma coincidência, não uma
# afirmação. No Lucca (19/07) ela acusou:
#   "…em oposição a Plutão e em trígono a Netuno"
# onde quem opõe Plutão e trina Netuno é o SOL — os dois não se aspectam
# entre si. O reescritor tentou duas vezes remover um aspecto inexistente,
# não conseguiu, e só o failed_kept_original impediu a corrupção.
#
# Agora as formas são explícitas: par hifenizado, "entre A e B", "A em
# <aspecto> com B", "A e B em <aspecto>", "<aspecto> de A com B".
_NP_ASP = (r"sextil|trígono|trigono|quadratura|oposição|oposicao|"
           r"conjunção|conjuncao|aspecto")
_NP_A = r"(?:Netuno|Neptuno)"
_NP_B = r"(?:Plutão|Plutao)"


def _np_formas(a, b):
    return [
        rf"{a}\s*[-–—]\s*{b}",
        rf"\bentre\s+(?:[oa]s?\s+)?{a}\s+e\s+(?:[oa]s?\s+)?{b}",
        rf"{a}\s+(?:e|com)\s+(?:[oa]s?\s+)?{b}\s+em\s+(?:{_NP_ASP})",
        # A janela entre o primeiro corpo e o nome do aspecto tolera um
        # sintagma de signo ("Plutão EM ESCORPIÃO faz sextil a Netuno") mas
        # NUNCA um "e" ou uma vírgula: essas iniciam nova oração, e foi por
        # aí que " e em trígono a " ligou dois aspectos do Sol um ao outro.
        rf"{a}(?:(?!\se\s|,)[^.!?]){{0,30}}?\b(?:{_NP_ASP})\s+"
        rf"(?:com|ao?s?|às?)\s+(?:[oa]s?\s+)?{b}",
        rf"(?:{_NP_ASP})\s+(?:de\s+)?(?:[oa]s?\s+)?{a}\s+(?:com|ao?s?|às?)\s+"
        rf"(?:[oa]s?\s+)?{b}",
    ]


_NP_PAR = "|".join(_np_formas(_NP_A, _NP_B) + _np_formas(_NP_B, _NP_A))


def _detect_netuno_plutao_mention(text):
    """Item 7: o sextil geracional Netuno-Plutão NUNCA é mencionado.

    Todo mundo nascido em ~1940-2035 tem esse aspecto: ele não diferencia
    ninguém e ocupa espaço que pertence ao mapa da pessoa. Doutrina da
    Márcia, 17/07: menção = violação.
    """
    out = []
    for m in re.finditer(_NP_PAR, text, flags=re.IGNORECASE):
        ini = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        fim = min([p for p in (text.find(".", m.end()), text.find("\n", m.end()))
                   if p != -1] or [len(text)])
        out.append({"kind": "geracional:netuno_plutao",
                    "match": text[ini:fim].strip()[:70],
                    "offset": m.start(),
                    "suggestion": ("REMOVER a menção ao aspecto Netuno-Plutão: é "
                                   "geracional, comum a toda a geração, e não diz "
                                   "nada sobre esta pessoa. Reescrever a frase sem "
                                   "ele, preservando o resto do sentido.")})
    return out


# Faixas de velocidade — quanto tempo o corpo fica num signo decide QUE
# linguagem coletiva a frase pode usar (refinamento da Márcia, 18/07):
_TRANSPESSOAIS = ("urano", "netuno", "plutão", "plutao")   # 7 a 20 anos
_SOCIAIS = ("júpiter", "jupiter", "saturno")               # 1 e 2,5 anos
_PESSOAIS = ("sol", "lua", "mercúrio", "mercurio", "vênus", "venus", "marte")

# "geração" é forte: só transpessoal sustenta. "coorte", "quem nasceu por
# volta de", "faixa etária" servem também para social — Saturno em Escorpião
# É compartilhado com quem nasceu na mesma época, só não é uma geração.
_GER_FORTE = r"gera(?:ção|cao|cional|cionais)|toda\s+uma\s+gera|sua\s+gera"
# "coletivo" SAIU da lista (19/07, 2ª correção — medida, não estimada).
#
# De manhã eu restringi ao uso NOMINAL ("um coletivo", "o coletivo"),
# achando que ali ele carregaria ideia de grupo-de-nascimento. Errado por
# duas razões, e as duas apareceram nos relatórios da noite:
#
#  1. O padrão não tinha \b antes do determinante, então o "o" final de
#     "dimensãO coletiva" casava. A instrução saía dizendo que o termo a
#     substituir era 'o coletiva' — que não é termo nenhum. Foi assim que
#     o reescritor falhou três vezes seguidas (R8: instrução que não
#     descreve o texto real).
#  2. Mesmo com \b, a restrição nominal não bastava. MEDIDO nas 8
#     ocorrências de "coletiv*" dos dois relatórios: 6 são vocabulário de
#     CASA 11 (grupos, círculos, causas, redes) e as 2 que são coorte de
#     verdade nomeiam Netuno e Plutão — transpessoais, que a regra já
#     permite. ZERO casos em que "coletivo" sozinho denuncia coorte.
#
# Toda menção legítima de coorte no corpus usa "geração" ou "coorte"
# explicitamente, e as duas continuam na lista. "coletivo" fala de GRUPO,
# não de tempo de nascimento — que é o que os outros marcadores medem.
_GER_FRACO = (r"coorte|quem\s+nasceu|faixa\s+etária|mesma\s+época|"
              r"nascid[oa]s?\s+n[ao]")


def _detect_sign_as_generational_agent(text):
    """Linguagem coletiva sustentada pelo corpo certo?

    Regra da Márcia, por faixa de velocidade:
      · transpessoal (Urano/Netuno/Plutão) em signo → "geração", "coletivo",
        "coorte": tudo permitido;
      · social (Júpiter/Saturno) em signo → "coorte", "quem nasceu por volta
        de", "faixa etária"; NUNCA "geração" (Júpiter fica 1 ano num signo,
        Saturno 2,5);
      · pessoal (Sol a Marte) → nenhuma linguagem coletiva.

    JANELA = PARÁGRAFO, não frase (18/07). Uma frase pode continuar o
    parágrafo anterior sem renomear o planeta: "O que essa geração carrega
    coletivamente…" seguia um parágrafo sobre Plutão em Capricórnio — a
    janela de frase não via o planeta e acusava.
    """
    out = []
    pos = 0
    for par in re.split(r"\n\s*\n", text):
        base = text.index(par, pos) if par.strip() else pos
        pos = base + len(par)
        if not par.strip() or par.strip().startswith("##"):
            continue
        low = par.lower()
        forte = re.search(_GER_FORTE, low)
        fraco = re.search(_GER_FRACO, low)
        if not (forte or fraco):
            continue
        # que corpos aparecem NESTE parágrafo?
        tem_trans = any(re.search(rf"\b{p}\b", low) for p in _TRANSPESSOAIS)
        tem_social = any(re.search(rf"\b{p}\b", low) for p in _SOCIAIS)
        tem_pessoal = any(re.search(rf"\b{p}\b", low) for p in _PESSOAIS)
        if tem_trans:
            continue                     # transpessoal sustenta qualquer termo
        alvo, motivo = None, None
        if forte and tem_social:
            alvo, motivo = forte, (
                "planeta SOCIAL (Júpiter fica ~1 ano num signo, Saturno ~2,5) "
                "não define geração. Trocar 'geração' por 'coorte', 'quem "
                "nasceu por volta dessa época' ou 'faixa etária' — ou nomear "
                "o transpessoal (Urano/Netuno/Plutão) se for dele que se fala")
        elif (forte or fraco) and tem_pessoal and not tem_social:
            alvo, motivo = (forte or fraco), (
                "corpo PESSOAL não fala de coletivo: ele muda de signo em dias "
                "ou semanas. Remover a moldura coletiva desta frase")
        elif forte and not (tem_social or tem_pessoal):
            alvo, motivo = forte, (
                "nenhum corpo nomeado sustenta 'geração'. Nomear o planeta "
                "transpessoal em signo, ou remover a moldura coletiva")
        if alvo is None:
            continue
        # A INSTRUÇÃO PRECISA NOMEAR O TEXTO REAL (19/07). O motivo fixo
        # dizia "Trocar 'geração' por 'coorte'". Na Helena a palavra presente
        # era "geracional" (adjetivo) e a frase JÁ dizia "coorte" na oração
        # seguinte: "esse Saturno carrega algo GERACIONAL: toda uma COORTE
        # nasceu…". A instrução não descrevia o texto, o reescritor não achou
        # o que trocar, falhou duas vezes e a violação ficou. Terceira vez
        # que instrução genérica quebra a reescrita — agora ela cita o
        # trecho exato e trata o adjetivo à parte do substantivo.
        _pal = alvo.group(0)
        _adj = _pal.lower().startswith("gerac") and "cional" in _pal.lower()
        _como = (f"O termo a substituir é '{_pal}', nesta ocorrência exata. "
                 + ("Como é ADJETIVO, não basta trocar por 'coorte' (que é "
                    "substantivo): reescrever para 'compartilhado com quem "
                    "nasceu por volta dessa época' ou 'de coorte'. Se a frase "
                    "já usa 'coorte' adiante, basta remover o adjetivo. "
                    if _adj else
                    "Trocar por 'coorte', 'quem nasceu por volta dessa época' "
                    "ou 'faixa etária'. ")
                 + "NÃO reescrever o resto da frase.")
        out.append({"kind": "geracional:signo_como_agente",
                    "match": par.strip()[:70],
                    "offset": base + alvo.start(),
                    "suggestion": motivo + ". " + _como})
    return out


_PT_BODY_TO_KEY = {
    "sol": "sun", "lua": "moon", "mercúrio": "mercury", "mercurio": "mercury",
    "vênus": "venus", "venus": "venus", "marte": "mars", "júpiter": "jupiter",
    "jupiter": "jupiter", "saturno": "saturn", "urano": "uranus",
    "netuno": "neptune", "plutão": "pluto", "plutao": "pluto",
    "quíron": "chiron", "quiron": "chiron", "lilith": "lilith",
    "ceres": "ceres", "palas": "pallas", "pallas": "pallas",
    "juno": "juno", "vesta": "vesta",
}


def _detect_false_no_aspect_claims(text, chart):
    """Item 9: afirmação de AUSÊNCIA de aspecto conferida contra a tabela.

    Caso real (Helena, 17/07): o texto disse que Lilith não tem aspectos
    depois de o próprio relatório ter lido o aspecto dela com Vênus.
    """
    out = []
    if not chart:
        return out
    aspected = set()
    for a in (chart.get("aspects") or []):
        for k in ("planet_a", "planet_b", "p1", "p2", "point_a", "point_b"):
            v = a.get(k)
            if isinstance(v, str):
                aspected.add(v.strip().lower())
    if not aspected:
        return out
    # A construção de ausência tem MUITAS formas. A que passou em 17/07 foi
    # pergunta-e-resposta: "Plutão na casa 12 tem aspectos? Nesta seção, a
    # lista está vazia — o que significa que Plutão opera sem os canais de
    # troca…". Três frases de interpretação sobre ausência FALSA, com a
    # quadratura Plutão-Quíron impressa na mesma página.
    _AUSENCIA = (
        r"não\s+(?:faz|forma|recebe|tem|possui|estabelece)\s+(?:nenhum\s+)?aspecto|"
        r"sem\s+(?:nenhum\s+)?aspecto|nenhum\s+aspecto|"
        r"a\s+lista\s+(?:está|fica|aparece)\s+vazia|lista\s+vazia|"
        r"não\s+há\s+aspectos|nenhuma\s+conex(?:ão|ões)\s+por\s+aspecto|"
        r"ausência\s+de\s+aspectos|estão\s+ausentes|"
        r"não\s+(?:aparece|surge|consta)\s+(?:em\s+)?nenhum\s+aspecto|"
        r"opera\s+(?:aqui\s+)?sem\s+(?:os\s+)?canais"
    )
    _CORPOS = (r"Sol|Lua|Mercúrio|Mercurio|Vênus|Venus|Marte|Júpiter|Jupiter|"
               r"Saturno|Urano|Netuno|Plutão|Plutao|Quíron|Quiron|Lilith|Ceres|"
               r"Palas|Pallas|Juno|Vesta")
    # POR JANELA, não por ordem. A afirmação de ausência pode vir antes ou
    # depois do nome do corpo, e pode atravessar frases (pergunta retórica +
    # resposta). Exigir os TRÊS elementos na mesma janela — corpo, marca de
    # ausência e a palavra "aspecto" — evita acusar "seus pais estão
    # ausentes", que é conteúdo legítimo.
    JANELA = 260
    for m in re.finditer(_AUSENCIA, text, flags=re.IGNORECASE):
        ini_j = max(0, m.start() - JANELA)
        janela = text[ini_j:m.end() + 60]
        if not re.search(r"aspecto", janela, flags=re.IGNORECASE):
            continue
        for bm in re.finditer(rf"\b({_CORPOS})\b", janela, flags=re.IGNORECASE):
            body_pt = bm.group(1).lower()
            key = _PT_BODY_TO_KEY.get(body_pt)
            if key and key in aspected:
                trecho = janela[max(0, bm.start() - 10):].strip()
                out.append({"kind": "aspecto:falsa_ausencia",
                            "match": trecho[:90], "offset": ini_j + bm.start(),
                            "suggestion": (
                                f"o texto conclui que {bm.group(1)} não tem aspectos, mas a "
                                f"tabela deste mapa LISTA aspectos para ele — e o leitor vê "
                                f"essa tabela na mesma página. REMOVER a conclusão de ausência "
                                f"e toda a interpretação construída sobre ela. Se a seção não "
                                f"recebeu aspectos novos, é porque já foram lidos antes: "
                                f"referencie, não negue.")})
                break
    return out


def _detect_clitic_third_person(text, voice):
    """Item 10: registro de 2ª pessoa padroniza 'te', nunca o clítico 'a/o'.

    "o que a move" → "o que te move". Só roda em modo SEGUNDA pessoa; em
    terceira pessoa o clítico é correto. Estreito: verbo + clítico logo
    após, com lista fechada de verbos, para não acusar artigo.
    """
    out = []
    if (voice or {}).get("person") == "terceira":
        return out
    verbos = (r"move|moveu|movem|define|definiu|definem|protege|protegeu|"
              r"protegem|sustenta|sustentou|sustentam|guia|guiou|guiam|"
              r"habita|atravessa|atravessou|marca|marcou|marcam|"
              r"acompanha|acompanhou|acompanham|exila|exilou|exilaram|"
              r"empurra|empurrou|empurram|puxa|puxou|puxam")
    for m in re.finditer(rf"\b(?:o\s+que|aquilo\s+que|algo\s+que)\s+(a|o)\s+({verbos})\b",
                         text, flags=re.IGNORECASE):
        out.append({"kind": "registro:clitico_terceira", "match": m.group(0),
                    "offset": m.start(),
                    "suggestion": ("o relatório usa 'te' como pronome de 2ª pessoa — "
                                   f"trocar por 'te {m.group(2)}'.")})
    return out


def _detect_third_person_leak(text, voice):
    """Item 5 (18/07): "ser ela mesma" num texto de 2ª pessoa.

    Passou pelos dois detectores existentes — o de clítico só olha
    'o que a move', e o léxico não tinha entrada. Aqui: 'ela mesma' /
    'ele mesmo' / 'ela própria' com um 'você' por perto (60 caracteres)
    é vazamento. A janela evita acusar referência legítima a substantivo
    feminino ('a Lua… ela mesma'), que existe sem 'você' ao lado.
    """
    out = []
    if (voice or {}).get("person") == "terceira":
        return out
    for m in re.finditer(r"\b(?:ela|ele)\s+(?:mesm[ao]|própri[ao])\b",
                         text, flags=re.IGNORECASE):
        janela = text[max(0, m.start() - 60):m.start()]
        if re.search(r"\bvocê\b|\bseu\b|\bsua\b", janela, flags=re.IGNORECASE):
            out.append({"kind": "registro:vazamento_terceira", "match": m.group(0),
                        "offset": m.start(),
                        "suggestion": ("o relatório fala com 'você' — trocar por "
                                       "'você mesma' / 'você mesmo'")})
    return out


# INSTANCIAÇÃO DO SUJEITO EM TERCEIRA PESSOA (19/07)
#
# A Abertura da Helena saiu com "há uma pessoa aqui que pensa muito… uma
# régua que ela aplica" — relatório dirigido em SEGUNDA pessoa.
#
# Por que `_detect_third_person_leak` não via: ele procura pronome de 3ª, e
# num relatório de astrologia quase todo "ela/ele" se refere a um PLANETA ou
# a um ASPECTO ("a Lua… ela balança", "o trígono… ele conecta"). Medido:
# 31 ocorrências de ela/ele na Helena, UMA é o vazamento. Um detector de
# pronome cru daria 30 falsos positivos e seria desligado na primeira rodada.
#
# O que denuncia o vazamento não é o pronome — é a INSTANCIAÇÃO: o texto
# cria um referente de terceira pessoa para quem deveria ser "você". O
# pronome vem depois, arrastado.
_PESSOA_TERCEIRA = [
    (r"\bhá\s+uma\s+pessoa\b", "cria um referente em 3ª pessoa"),
    (r"\buma\s+pessoa\s+aqui\b", "cria um referente em 3ª pessoa"),
    (r"\ba\s+pessoa\s+que\b", "fala do sujeito em 3ª pessoa"),
    (r"\b[oa]\s+nativ[oa]\b", "jargão astrológico em 3ª pessoa"),
    (r"\b[oa]\s+consulente\b", "jargão astrológico em 3ª pessoa"),
    (r"\b[oa]\s+sujeito\s+d", "jargão astrológico em 3ª pessoa"),
]
# "você é a pessoa que analisa" é SEGUNDA pessoa e está correto — sem esta
# exceção, era falso positivo (medido, 19/07).
_PESSOA_OK_ANTES = re.compile(r"(?:você\s+é|é\s+você|seja|se\s+torna)\s*$",
                              re.IGNORECASE)


def _detect_person_instantiation(text, voice):
    """O texto inventa um 'ele/ela' para quem devia ser 'você'."""
    if (voice or {}).get("person") == "terceira":
        return []
    out, vistos = [], set()
    for pat, motivo in _PESSOA_TERCEIRA:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            if _PESSOA_OK_ANTES.search(text[max(0, m.start() - 18):m.start()]):
                continue
            # a mesma ocorrência casa em mais de um padrão ("há uma pessoa"
            # e "uma pessoa aqui"): reporta uma vez só
            if any(abs(m.start() - v) < 20 for v in vistos):
                continue
            vistos.add(m.start())
            out.append({
                "kind": "voz:sujeito_em_terceira_pessoa",
                "match": text[m.start():m.start() + 70].strip(),
                "offset": m.start(),
                "suggestion": (
                    f"'{m.group(0)}' {motivo}, mas este relatório é dirigido "
                    f"a ela em SEGUNDA pessoa. Reescrever falando com a "
                    f"pessoa ('você pensa muito', 'você cresceu nesse "
                    f"ambiente'), inclusive os pronomes que vêm depois e que "
                    f"foram arrastados para a 3ª ('uma régua que ela aplica' "
                    f"→ 'que você aplica')."),
            })
    return out


def _detect_bare_ic(text):
    """Item 11: IC nunca aparece sozinho — precisa de tradução."""
    out = []
    for m in re.finditer(r"\bIC\b", text):
        janela = text[max(0, m.start() - 90):m.end() + 90].lower()
        if "casa 4" in janela or "fundo do céu" in janela or "fundo do ceu" in janela:
            continue
        out.append({"kind": "jargao:ic_sozinho", "match": "IC", "offset": m.start(),
                    "suggestion": ("'IC' é sigla técnica — escrever 'cúspide da casa 4', "
                                   "'fundo do céu', ou ambos ('o fundo do céu, cúspide "
                                   "da casa 4').")})
    return out


# ---- ANDAIME DO PROMPT VAZANDO NO RELATÓRIO (19/07) --------------------
#
# O relatório do Lucca saiu com "A passagem 8 do seu mapa, que conecta
# Saturno em Escorpião a esse Plutão". "Passagem 8" é a NUMERAÇÃO INTERNA
# dos trechos do RAG: `format_chunks_for_prompt` monta cabeçalhos
# "--- Passagem 8 (relevância=0.83, tipo=…) ---", e o prompt ainda fala em
# "cada passagem acima". O modelo citou o andaime como se fosse conteúdo do
# mapa. O cliente lê e não tem como saber o que é — e isso foi para o PDF.
#
# CUIDADO: "passagem" é palavra legítima em astrologia — "a passagem de
# Saturno por Escorpião" é trânsito, não andaime. O detector pega só a
# referência ao aparato: passagem NUMERADA, passagem "acima"/"fornecida",
# e os restos crus do cabeçalho.
_SCAFFOLD_PATTERNS = [
    (r"\bpassage(?:m|ns)\s+\d+", "passagem numerada"),
    (r"\bpassage(?:m|ns)\s+(?:acima|abaixo|anterior(?:es)?|fornecid[ao]s?|"
     r"citad[ao]s?|listad[ao]s?)\b", "passagem do aparato"),
    (r"\btrechos?\s+(?:\d+|acima|fornecid[ao]s?)\b", "trecho do aparato"),
    (r"\brelevância\s*=", "cabeçalho cru do RAG"),
    (r"\b(?:d[oa]s?|n[oa]s?|conforme\s+[oa]s?)\s+"
     r"(?:dados|material|contexto|conteúdo)\s+"
     r"(?:fornecid[ao]s?|acima|listad[ao]s?)\b", "referência ao material do prompt"),
    (r"\bconforme\s+(?:listado|indicado|descrito)\s+acima\b", "referência ao prompt"),
    (r"\bDADOS\s+DO\s+MAPA\b", "cabeçalho do prompt"),
]


def _detect_prompt_scaffolding_leak(text):
    """O texto cita o andaime do prompt em vez do mapa da pessoa."""
    out = []
    for pat, rot in _SCAFFOLD_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            out.append({
                "kind": "andaime:vazamento_do_prompt",
                "match": text[max(0, m.start() - 20):m.end() + 20].strip()[:70],
                "offset": m.start(),
                "suggestion": (
                    f"'{m.group(0)}' é {rot} — vocabulário INTERNO do prompt, "
                    f"invisível para quem lê. O cliente não sabe o que é uma "
                    f"'passagem' numerada nem um 'material fornecido'. "
                    f"Reescrever nomeando o FATO ASTROLÓGICO de que a frase "
                    f"trata (o aspecto, o planeta, a casa), e apagar a "
                    f"referência ao aparato."),
            })
    return out


# ---- item 13: lint de MULETA (contagem, não regex fixo) ----------------
# "real/realmente" virou a muleta nova (abundante na rodada de 17/07). O
# limiar é por SEÇÃO para não punir um relatório longo. Reporta no meta —
# não reescreve — para que a PRÓXIMA muleta seja pega pelo mesmo mecanismo.
_CRUTCH_CANDIDATES = [
    ("real", r"\breal(?:mente|is)?\b"),
    ("genuíno", r"\bgenuín[ao]s?\b|\bgenuinamente\b"),
    ("profundo", r"\bprofund[ao]s?\b|\bprofundamente\b|\bprofundidade\b"),
    ("exatamente", r"\bexatamente\b"),
    ("específico", r"\bespecífic[ao]s?\b|\bespecificamente\b"),
    ("concreto", r"\bconcret[ao]s?\b|\bconcretamente\b"),
    ("silencioso", r"\bsilencios[ao]s?\b|\bsilenciosamente\b"),
    ("verdadeiro", r"\bverdadeir[ao]s?\b"),
]
CRUTCH_PER_SECTION_LIMIT = 4
# LIMIAR DE DOCUMENTO (17/07): a Márcia ouviu "real" ~30 vezes no relatório
# inteiro, mas ~3 por seção — abaixo do limiar de seção, invisível para o
# detector. Muleta dispersa é muleta igual. O documento tem ~16 seções, então
# 12 ocorrências já é uma a cada seção e meia: audível.
CRUTCH_PER_DOC_LIMIT = 12


def detect_crutch_words(report_text):
    """Palavra-muleta em DUAS escalas.

    Retorna {"por_secao": [...], "documento": [...], "totais": {...}}.

    · por_secao  — mesma palavra acima do limiar DENTRO de uma seção
                   (concentração: o leitor tropeça no parágrafo).
    · documento  — mesma palavra acima do limiar no relatório INTEIRO
                   (dispersão: o leitor não tropeça, mas o ouvido registra).
    · totais     — contagem de TODAS as candidatas no documento, mesmo
                   abaixo do limiar, para a Márcia calibrar sobre dado.
    """
    por_secao, totais = [], {}
    blocos = re.split(r"\n##\s+", report_text)
    for blk in blocos[1:]:
        titulo, _, corpo = blk.partition("\n")
        for nome, pat in _CRUTCH_CANDIDATES:
            n = len(re.findall(pat, corpo, flags=re.IGNORECASE))
            if not n:
                continue
            totais[nome] = totais.get(nome, 0) + n
            if n > CRUTCH_PER_SECTION_LIMIT:
                por_secao.append({"section": titulo.strip(), "word": nome,
                                  "count": n, "limit": CRUTCH_PER_SECTION_LIMIT})
    documento = [{"word": w, "count": c, "limit": CRUTCH_PER_DOC_LIMIT}
                 for w, c in sorted(totais.items(), key=lambda x: -x[1])
                 if c > CRUTCH_PER_DOC_LIMIT]
    return {"por_secao": por_secao, "documento": documento,
            "totais": dict(sorted(totais.items(), key=lambda x: -x[1]))}


# ============================================================
# 2b — PADRÕES DE NEGAÇÃO-SUBSTITUIÇÃO
# ============================================================
# Alguns padrões são frouxos por design — o "é" ou "mas" completa a estrutura.
# Todos foram calibrados pra evitar match em construções legítimas.
# ============================================================
# NEGAÇÃO-SUBSTITUIÇÃO — VARREDURA DE FORMAS (19/07)
# ============================================================
# A Márcia mediu: das 22 ocorrências genuínas do inventário do GPT, os
# padrões antigos pegavam ~35%. E o meu instrumento de MEDIÇÃO tinha o
# mesmo viés do detector — achei 5 das 22 e reportei "70% de cobertura",
# número produzido pelo próprio viés. Por isso a enumeração abaixo saiu das
# 22 FRASES REAIS, não do que os padrões conseguem enxergar.
#
# A cegueira NÃO era só o travessão. A forma dominante é a INVERTIDA —
# afirmação primeiro, negação depois ("como ferramenta, não como filtro") —
# 8 das 22, e não havia NENHUMA cobertura dela. Separador: 18 das 22 usam
# vírgula, 1 travessão, 2 ponto.
#
# Oito formas, cada uma com separador variável:
#   A  não X, mas Y                    (9 no corpus)
#   B  X, não Y   ← a invertida        (10)
#   C  não porque X, mas porque Y      (2)
#   D  não V X. V Y                    (1)
#   E  não V X, V Y                    (1)
#   F  não V X — V Y                   (3)
#   G  X em vez de Y                   (6)
#   H  não X, não Y. É Z               (1)
#
# Medição: 22/22 das reais acendem; 12/12 dos contrastes comuns ficam
# limpos ("X, mas Y", "tensão entre X e Y", "quando X e quando Y",
# comparativos). No corpus dos dois relatórios: 33 ocorrências.
_NS_SEP = r"[,;]|\s[—–]"
_NS_NEG = r"(?:não|nao|nunca|jamais)"
_NS_ADV = r"(?:mas|porém|porem|contudo|todavia|e\s+sim|senão|senao)"
_NS_INT = r"(?:apenas|só|so|somente|necessariamente|exatamente|propriamente)"

_NEGACAO_FORMAS = [
    (rf"\b{_NS_NEG}\s+porque\b[^.;:!?]{{2,90}}?(?:{_NS_SEP})\s*{_NS_ADV}\s+porque\b",
     "forma_C_nao_porque_mas_porque"),
    (rf"\b{_NS_NEG}\s+(?:{_NS_INT}\s+)?[^.;:!?]{{2,90}}?(?:{_NS_SEP})\s*{_NS_ADV}\b",
     "forma_A_nao_x_mas_y"),
    (rf"\b{_NS_NEG}\s+(?:era|é|e|foi|seria)\s+[^.;:!?]{{2,50}}?,\s*"
     rf"{_NS_NEG}\s+(?:era|é|e|foi)\b", "forma_H_dupla_negacao"),
    (rf"\b{_NS_NEG}\s+(\w{{3,}})\b[^.!?]{{2,90}}?\.\s*\1\b",
     "forma_D_verbo_repetido_ponto"),
    # F exige VERBO contrastante sem sujeito depois do travessão. Sem a
    # exclusão, pegava qualquer negação seguida de travessão ("não são
    # fixos — cada um…"): 13 ocorrências no corpus, quase todas continuação.
    (rf"\b{_NS_NEG}\s+\w{{3,}}\b[^.;:!?]{{2,90}}?\s[—–]\s*"
     rf"(?!e\b|mas\b|ou\b|que\b|porque\b|se\b|quando\b|como\b|onde\b|"
     rf"[oa]s?\b|um\b|uma\b|para\b|por\b|com\b|de\b|em\b|isso\b|"
     rf"ele\b|ela\b|você\b|é\b|há\b|no\b|na\b|ao\b|à\b|até\b|já\b|"
     rf"talvez\b|nem\b|sem\b|antes\b|depois\b|aquilo\b|esse\b|essa\b)"
     rf"\w+(?:a|e|am|em|ou|iu|ia|va|ram|rem)\b", "forma_F_verbo_travessao"),
    (rf"\b{_NS_NEG}\s+(\w{{3,}})\b[^.;:!?]{{2,90}}?,\s*\1\b",
     "forma_E_verbo_repetido_virgula"),
    (r"\b(?:em\s+vez\s+d[eoa]|ao\s+invés\s+d[eoa])\b", "forma_G_em_vez_de"),
    # B — a INVERTIDA. A negação tem de vir colada no separador, senão é
    # oração nova. Exclui tag de fala ("…, não sei", "…, não é?").
    (rf"(?:{_NS_SEP})\s*{_NS_NEG}\s+(?:{_NS_INT}\s+)?"
     rf"(?!sei\b|é\?|e\?|vou\b|posso\b|dá\b|há\b|tem\b)"
     rf"(?:[oa]s?|um|uma|uns|umas|n[oa]s?|d[oa]s?|ao?s?|como|para|por|"
     rf"pel[oa]s?|que|se|te|lhe|me|apenas|só|sempre|\w+ndo|\w+r)\b",
     "forma_B_invertida"),
]

_NEGATION_SUBSTITUTION_PATTERNS = [
    # "não é X, é Y" / "não é X. É Y"
    (r"\bnão\s+é\s+[^.,;:!?]{1,60}[,.]\s*[éÉ]\b", "nao_e_x_e_y"),
    # "não apenas X, mas Y" / "não somente X, mas Y"
    (r"\bnão\s+(?:apenas|somente|só)\s+[^.,;:!?]{1,60},?\s*mas\b", "nao_apenas_mas"),
    # "menos X e mais Y"
    (r"\bmenos\s+[^.,;:!?]{1,40}\s+e\s+mais\b", "menos_e_mais"),
    # "não se trata de X, e sim de Y" / "não se trata de X, mas de Y"
    (r"\bnão\s+se\s+trata\s+de\s+[^.,;:!?]{1,60},?\s+(?:e\s+sim|mas)\b", "nao_se_trata_e_sim"),
    # "não é tanto X quanto Y"
    (r"\bnão\s+é\s+tanto\s+[^.,;:!?]{1,40}\s+quanto\b", "nao_tanto_quanto"),
    # "aqui não há X, há Y"
    (r"\bnão\s+há\s+[^.,;:!?]{1,40},?\s*há\b", "nao_ha_ha"),
    # "isso não significa X, significa Y"
    (r"\bnão\s+significa\s+[^.,;:!?]{1,60},?\s*significa\b", "nao_significa_significa"),
    # "Y, e não X"
    # A oração ANTES do "e não" tem de ser AFIRMATIVA. Sem isso, "não pede
    # permissão, e não há nada de errado nisso" — negação COORDENADA, língua
    # normal — era acusada como negação-substituição (19/07).
    (r"\b(?![Nn]ão\b)\w(?:(?![Nn]ão\b)[^.,;:!?]){4,59},\s+e\s+não\s+"
     r"[a-záéíóúãõçâêôà]", "y_e_nao_x"),
    # ---- variantes do inventário 17/07 (balde 1: nega E substitui) ----
    # "não é X — é Y" / "não são X — são Y" / "não é X; é Y"
    (r"\bnão\s+(?:é|são)\s+[^.,;:!?]{1,60}[;—–]\s*(?:é|são)\b", "nao_e_x_e_y_pausa"),
    # "não como X, mas como Y"
    (r"\bnão\s+como\s+[^.,;:!?]{1,50},?\s*mas\s+como\b", "nao_como_x_mas_como_y"),
    # "Y — e não X" (a variante com travessão da y_e_nao_x)
    (r"\b(?![Nn]ão\b)\w(?:(?![Nn]ão\b)[^.;:!?]){4,59}\s+[—–]\s*e\s+não\s+"
     r"[a-záéíóúãõçâêôà]", "y_travessao_e_nao_x"),
    # "— e não apenas X"
    (r"\be\s+não\s+apenas\s+[a-záéíóúãõçâêôà]", "e_nao_apenas_x"),
    # "não X; ao contrário, Y"
    (r"\bnão\s+[^.;:!?]{1,60};\s*ao\s+contrário\b", "nao_x_ao_contrario"),
    # "nunca X. Apenas/Só Y" ("Silenciar... nunca produziu adequação. Produziu apenas...")
    (r"\bnunca\s+[^.!?]{1,70}\.\s*(?:Apenas|Só)\b", "nunca_x_apenas_y"),
    # "a pergunta não é X — é Y" (e "a pergunta que fica aqui não é...")
    (r"\ba\s+pergunta\s+(?:\w+\s+){0,4}não\s+é\b", "pergunta_nao_e_x"),
    # "não para X, mas para Y" (com até 2 palavras entre 'não' e 'para':
    # "não virá para confirmar, mas para te expor")
    (r"\bnão\s+(?:\w+\s+){0,2}para\s+[^.,;:!?]{1,50},\s*mas\s+para\b", "nao_para_x_mas_para_y"),
    # "não o que X, mas o que Y"
    (r"\bnão\s+o\s+que\s+[^.,;:!?]{1,60},\s*mas\s+o\s+que\b", "nao_oque_mas_oque"),
    # "não porque X. Mas porque Y"
    (r"\bnão\s+porque\s+[^.;!?]{1,80}[.;]\s*[Mm]as\s+porque\b", "nao_porque_mas_porque"),
    # VERBO REPETIDO: "não pede X. Pede Y" / "não aponta X — aponta Y" /
    # "nunca produziu X. Produziu Y" — a forma mais geral da família.
    (r"\b(?:não|nunca)\s+(\w{4,})\s+[^.;—–!?]{1,60}[.;—–]\s*\1\b", "nao_verbo_x_verbo_y"),
]

# Texto FIXO de template aprovado pela Márcia (nota de rodapé da tabela de
# aspectos e afins): mascarado de TODOS os scans do verifier. O inventário
# do GPT flagrou "em vez de cobrir cada aspecto individualmente" — é texto
# de template, não de síntese; decisão de 17/07: whitelist.
_FIXED_TEMPLATE_WHITELIST = [
    "em vez de cobrir cada aspecto individualmente",
    "pode ou não mencionar aspectos específicos de forma explícita",
]


def _mask_fixed_templates(text):
    """Substitui os trechos whitelistados por espaços (mesmo comprimento —
    offsets preservados) antes de qualquer detecção."""
    for frag in _FIXED_TEMPLATE_WHITELIST:
        i = 0
        low, fl = text.lower(), frag.lower()
        while True:
            i = low.find(fl, i)
            if i < 0:
                break
            text = text[:i] + " " * len(frag) + text[i + len(frag):]
            low = text.lower()
            i += len(frag)
    return text


# ============================================================
# 2c — NOMENCLATURA DE ASPECTOS
# ============================================================
_ASPECT_NAMES = (
    "conjunção", "conjuncao",
    "oposição", "oposicao",
    "quadratura",
    "trígono", "trigono",
    "sextil", "sextilis",
    "quincúncio", "quincuncio",
    "semisextil", "semi-sextil",
    "semiquadratura", "semi-quadratura",
    "sesquiquadratura", "sesqui-quadratura",
)


def _detect_invalid_aspect_composition(text):
    """Flagra 'quadratura em sextil', 'trígono de oposição', etc.
    Composição inválida = dois nomes de aspecto ligados por preposição
    curta (em/de/com/na/no/à/ao) dentro de 25 caracteres."""
    alt = "|".join(sorted(set(_ASPECT_NAMES), key=len, reverse=True))
    pat = re.compile(
        rf"\b({alt})\s+(?:em|de|com|na|no|à|ao)\s+({alt})\b",
        flags=re.IGNORECASE,
    )
    return [(m.group(0), m.start()) for m in pat.finditer(text)]


def _detect_broken_aspect_pair(text):
    """Flagra aspecto com o segundo corpo comido: 'o sextil entre sua Vênus
    está em Gêmeos', 'o trígono entre Quíron está em Peixes' (Lucca, 16/07).

    Um aspecto é uma relação entre DOIS corpos: depois de '<aspecto> entre
    <corpo>' tem que vir 'e <corpo>'. Se em vez disso vem um verbo ('está',
    'fica', 'encontra-se', 'acrescenta', 'traz'…), o par foi mutilado — a
    regressão veio do corretor de signos removendo um planeta do grupo, mas
    o detector casa a SUPERFÍCIE, seja qual for a origem. O detector de
    julho (_detect_invalid_aspect_composition) cobre outra classe (dois
    nomes de aspecto compostos) e não pega esta.
    """
    alt = "|".join(sorted(set(_ASPECT_NAMES), key=len, reverse=True))
    pat = re.compile(
        rf"\b(?:o\s+|a\s+|um\s+|uma\s+)?({alt})\s+entre\s+"
        rf"(?:sua?\s+|o\s+|a\s+)?"
        rf"[A-ZÁÉÍÓÚÂÊÔ][\wáéíóúâêôãõç-]*\s+"          # um corpo só...
        rf"(?!e\s|com\s)"                               # ...sem 'e <corpo>'
        rf"(?:está|estão|fica|ficam|encontra-se|forma|acrescenta|traz|cria|aponta)\b",
        flags=re.IGNORECASE,
    )
    return [(m.group(0), m.start()) for m in pat.finditer(text)]


# ============================================================
# 2f — VOZ (os dois interruptores, decisão da Márcia 17/07)
# ============================================================
_ASTRO_POSSESSIVE = (
    r"\b(?:seu|sua|seus|suas)\s+(?:Sol|Lua|Mercúrio|Vênus|Marte|Júpiter|"
    r"Saturno|Urano|Netuno|Plutão|Quíron|Lilith|Nodo[s]?|Ascendente|"
    r"Meio-do-Céu|mapa|casa\s+\d+)\b"
)
_VERB_NELE = (
    r"\b(?:cria|criam|desperta|despertam|gera|geram|produz|produzem|"
    r"instala|instalam|acende|acendem|provoca|provocam|planta|plantam|"
    r"deixa|deixam|constrói|constroem)\s+nel[ea]\b"
)


def _detect_voice_violations(text, chart):
    """O detector de pessoa INVERTE conforme a voz do relatório.

    Modo SEGUNDA pessoa (padrão): o defeito é falar do sujeito em 3ª —
    verbo de efeito + nele/nela ("cria nele uma disponibilidade", Lucca).

    Modo TERCEIRA pessoa (relatório sobre outra pessoa, lido pelo
    responsável): o defeito é o "você" dirigido ao SUJEITO. Superfície
    detectável sem semântica: possessivo de 2ª + termo astrológico ("sua
    Lua", "seu mapa", "sua casa 4") — isso só pode estar se dirigindo ao
    dono do mapa. O "você" dirigido ao LEITOR (orientação ao responsável)
    não casa esse padrão e continua permitido.

    Também no modo terceira: artigo com gênero errado antes do primeiro
    nome do sujeito ("a Lucca" para sujeito masculino) — cobre os pronomes
    do sujeito na superfície mais frequente.
    """
    out = []
    v = (chart or {}).get("_voice") or {}
    person = v.get("person", "segunda")
    if person == "terceira":
        for m in re.finditer(_ASTRO_POSSESSIVE, text):
            out.append(("voz:voce_dirigido_ao_sujeito", m.group(0), m.start(),
                        "relatório em TERCEIRA pessoa — o mapa é de "
                        f"{v.get('name') or 'outra pessoa'}; reescrever como "
                        "'a Lua de <nome>', 'o mapa de <nome>'"))
        first = (v.get("name") or "").split()[0] if v.get("name") else ""
        g = (chart or {}).get("gender")
        if first and g in ("masculino", "feminino"):
            wrong_art = "a" if g == "masculino" else "o"
            for m in re.finditer(rf"\b{wrong_art}\s+{re.escape(first)}\b",
                                 text, flags=re.IGNORECASE):
                out.append(("voz:artigo_genero_sujeito", m.group(0), m.start(),
                            f"artigo não concorda com o gênero do sujeito "
                            f"({g}) — usar '{'o' if g=='masculino' else 'a'} {first}'"))
    else:
        for m in re.finditer(_VERB_NELE, text, flags=re.IGNORECASE):
            out.append(("voz:pessoa_terceira_em_segunda", m.group(0), m.start(),
                        "o relatório fala com 'você' — reescrever em 2ª pessoa "
                        "('cria em você…')"))
        # REFLEXIVO de 3ª pessoa — o vazamento de 17/07 ("há espaço para ser
        # ela mesma" num texto em 2ª pessoa). O padrão acima só cobria
        # verbo + nele/nela e por isso não pegou. Formas estreitas: só as
        # que não têm leitura legítima falando com "você".
        for pat, sug in (
            (r"\bser\s+el[ea]\s+mesm[ao]\b",
             "usar 'ser você mesma' / 'ser quem você é'"),
            (r"\b(?:para|por|com)\s+el[ea]\s+mesm[ao]\b",
             "usar 'para você mesma' ou 'para si mesma'"),
            (r"\bel[ea]\s+mesm[ao]\s+(?:precisa|sente|busca|quer|pode|vive)\b",
             "reescrever com 'você'"),
        ):
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                out.append(("voz:pessoa_terceira_em_segunda", m.group(0), m.start(),
                            f"texto em 2ª pessoa — {sug}"))
    return out


# ============================================================
# 2d — CONTAGEM ANUNCIADA vs ENUMERAÇÃO
# ============================================================
_NUMBER_WORDS = {
    "duas": 2, "dois": 2,
    "três": 3, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10,
}

# Substantivos plurais aspectuais/estruturais que costumam ser anunciados+listados
_COUNTABLES = (
    "conjunções", "oposições", "quadraturas", "trígonos", "sextis",
    "aspectos", "planetas", "casas", "signos",
)


def _detect_count_mismatch(text):
    """Anuncia N substantivo — plural: enumeração ...
    Match:  "três conjunções — A, B e C"
            "quatro planetas: X, Y, Z e W"
    Compara N anunciado com número de itens (contagem de vírgulas + 1 pra 'e').
    """
    hits = []
    countables_alt = "|".join(_COUNTABLES)
    numbers_alt = "|".join(_NUMBER_WORDS.keys())
    pat = re.compile(
        # Até 3 palavras de modificador entre o contável e o separador
        # ("três conjunções CENTRAIS:", "quatro planetas MAIS APERTADOS —").
        # Sem isso o detector só via a forma colada e passava batido em
        # qualquer frase com adjetivo — buraco revelado pela suíte-canário
        # (18/07), não por um relatório.
        rf"\b({numbers_alt})\s+({countables_alt})"
        rf"(?:\s+\w+){{0,3}}\s*[—:–-]\s*([^.!?\n]+)",
        flags=re.IGNORECASE,
    )
    for m in pat.finditer(text):
        n_word = m.group(1).lower()
        expected = _NUMBER_WORDS.get(n_word)
        enum_text = m.group(3).strip()
        # Conta separadores: vírgulas + " e " conta como itens
        # "A, B e C" → 2 vírgulas → 2 seps, 3 itens (mas às vezes tem só " e "):
        # "A e B" → 1 " e " → 2 itens
        items = re.split(r",\s*|\s+e\s+", enum_text)
        items = [it.strip() for it in items if it.strip()]
        # Descarta itens que são obviamente continuação de frase (mais de 6 palavras)
        # — melhor falso negativo que falso positivo agressivo
        if any(len(it.split()) > 8 for it in items):
            continue
        actual = len(items)
        if expected is not None and actual != expected:
            hits.append((m.group(0), m.start(), expected, actual))
    return hits


# ============================================================
# 2e — LÉXICO PORTUGUÊS (SPELLCHECK)
# ============================================================
_ASTRO_WHITELIST = {
    # Signos + variantes
    "áries", "aries", "touro", "gêmeos", "gemeos", "câncer", "cancer",
    "leão", "leao", "virgem", "libra", "escorpião", "escorpiao",
    "sagitário", "sagitario", "capricórnio", "capricornio",
    "aquário", "aquario", "peixes",
    # Planetas + variantes
    "sol", "lua", "mercúrio", "mercurio", "vênus", "venus", "marte",
    "júpiter", "jupiter", "saturno", "urano", "netuno", "plutão", "plutao",
    "quíron", "quiron", "chiron", "lilith", "ceres", "vesta", "juno", "palas", "pallas",
    # Nodos, ângulos
    "ascendente", "meio-do-céu", "meio-do-ceu", "descendente", "ic",
    "asc", "mc", "nodo", "nodos", "nodal",
    # Aspectos e vocabulário técnico
    "sextil", "sextis", "trígono", "trigono", "conjunção", "conjuncao",
    "oposição", "oposicao", "quadratura", "quincúncio", "quincuncio",
    "semisextil", "sesquiquadratura", "semi-sextil", "semi-quadratura",
    "retrógrado", "retrogrado", "retrogradação", "retrogradacao",
    "domicílio", "domicilio", "exaltação", "exaltacao", "regência", "regencia",
    "cúspide", "cuspide", "casa", "casas", "eixo",
    "geracional", "coorte", "arquétipo", "arquetipo", "arquetípico", "arquetipico",
    # Termos frequentes do relatório
    "kerykeion", "efemérides", "efemerides",
    # Português coloquial que dicionários às vezes deixam de fora
    "autoconhecimento", "empoderar", "empoderamento",
}


def _normalize_word(w):
    return w.lower().strip()


def _payload_names(chart):
    """Nomes/localidades vindos do payload que devem entrar na whitelist
    (nome do cliente pode ter grafias regionais ou nomes estrangeiros)."""
    out = set()
    name = (chart or {}).get("_client_name") or (chart or {}).get("name") or ""
    for tok in re.split(r"\s+", name):
        tok = _normalize_word(tok)
        if tok:
            out.add(tok)
    city = (chart or {}).get("birth_city") or ""
    for tok in re.split(r"[\s,]+", city):
        tok = _normalize_word(tok)
        if tok:
            out.add(tok)
    return out


def _detect_unknown_words(text, chart):
    """Roda pyspellchecker pt-BR sobre palavras >=4 caracteres, ignorando
    whitelist astrológica + payload. Retorna lista de (palavra, offset,
    sugestão_de_correção|None). Nunca lança — se pyspellchecker falha em
    carregar o dicionário PT, retorna [] com aviso no log."""
    try:
        from spellchecker import SpellChecker
    except Exception as e:
        logger.warning("verifier: pyspellchecker unavailable (%s)", e)
        return []
    try:
        spell = SpellChecker(language="pt")
    except Exception as e:
        logger.warning("verifier: pt dictionary unavailable (%s)", e)
        return []
    whitelist = set(_ASTRO_WHITELIST) | _payload_names(chart)
    out = []
    for m in re.finditer(r"\b([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]{3,})\b", text):
        w = m.group(1)
        wn = _normalize_word(w)
        if wn in whitelist:
            continue
        # Ignora palavras com maiúscula interna (nomes próprios compostos)
        if any(c.isupper() for c in w[1:]):
            continue
        # Ignora palavras que começam com maiúscula seguidas de minúsculas
        # (nomes próprios comuns) — nossa rede é pra grafia errada em
        # palavras minúsculas.
        if w[0].isupper():
            continue
        if wn in spell:
            continue
        # É palavra desconhecida — flagra
        sug = spell.correction(wn)
        # Se a sugestão é a mesma palavra (ou None), não temos sinal de erro
        if sug and sug != wn:
            out.append((w, m.start(), sug))
    return out


# ============================================================
# 4b/4c — VALIDAÇÃO DE AFIRMAÇÕES SOBRE CÚSPIDES
# ============================================================
# Detecta frases que afirmam algo sobre a cúspide de uma casa e valida
# contra a tabela de cúspides real. Cobre 3 padrões:
#   (i)   "<signo> na cúspide da casa N" / "cúspide da casa N em <signo>"
#         / "casa N tem cúspide em <signo>"
#   (ii)  "casa N em <signo>" quando o sujeito é a CASA (não um planeta)
#   (iii) "<signo> na casa N" quando o sujeito é a CASA
# Ação: qualquer discrepância é enviada à reescrita com instrução
# EXPLÍCITA de REMOVER a menção (não corrigir), preservando o resto do
# sentido — cúspide "corrigida" pelo modelo é risco maior que a ausência.

_SIGN_NAMES_PT = [
    "Áries","Aries","Touro","Gêmeos","Gemeos","Câncer","Cancer",
    "Leão","Leao","Virgem","Libra","Escorpião","Escorpiao",
    "Sagitário","Sagitario","Capricórnio","Capricornio",
    "Aquário","Aquario","Peixes",
]
_SIGN_CANON = {
    "Áries":"Áries","Aries":"Áries","Touro":"Touro",
    "Gêmeos":"Gêmeos","Gemeos":"Gêmeos","Câncer":"Câncer","Cancer":"Câncer",
    "Leão":"Leão","Leao":"Leão","Virgem":"Virgem","Libra":"Libra",
    "Escorpião":"Escorpião","Escorpiao":"Escorpião",
    "Sagitário":"Sagitário","Sagitario":"Sagitário",
    "Capricórnio":"Capricórnio","Capricornio":"Capricórnio",
    "Aquário":"Aquário","Aquario":"Aquário","Peixes":"Peixes",
}

_HOUSE_WORDS = {
    "1":"1","um":"1","primeira":"1","i":"1",
    "2":"2","dois":"2","duas":"2","segunda":"2","ii":"2",
    "3":"3","três":"3","tres":"3","terceira":"3","iii":"3",
    "4":"4","quatro":"4","quarta":"4","iv":"4",
    "5":"5","cinco":"5","quinta":"5","v":"5",
    "6":"6","seis":"6","sexta":"6","vi":"6",
    "7":"7","sete":"7","sétima":"7","setima":"7","vii":"7",
    "8":"8","oito":"8","oitava":"8","viii":"8",
    "9":"9","nove":"9","nona":"9","ix":"9",
    "10":"10","dez":"10","décima":"10","decima":"10","x":"10",
    "11":"11","onze":"11","décima primeira":"11","decima primeira":"11","xi":"11",
    "12":"12","doze":"12","décima segunda":"12","decima segunda":"12","xii":"12",
}


def _extract_house_number(house_word):
    """Retorna string '1'-'12' ou None se não reconhecido."""
    if not house_word:
        return None
    w = house_word.strip().lower()
    return _HOUSE_WORDS.get(w)


def _get_cusps(chart):
    """Retorna dict {int_num: sign_canon} pra as 12 casas do mapa, ou None
    se cúspides não estiverem disponíveis."""
    cusps = (chart or {}).get("cusps") or {}
    if not cusps:
        return None
    out = {}
    for k, v in cusps.items():
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        s = (v or {}).get("sign_pt")
        if s:
            out[n] = _SIGN_CANON.get(s, s)
    return out or None


# Padrões pra afirmações sobre cúspide (case-insensitive). Cada padrão
# tem grupos (signo, casa) OU (casa, signo) — verificar sinônimos.
def _detect_cusp_claims(text):
    """Retorna lista de dicts {match, offset, sign_claimed, house_num,
    pattern_name}."""
    signs_alt = "|".join(sorted(set(_SIGN_NAMES_PT), key=len, reverse=True))
    house_alt = "|".join(sorted(set(_HOUSE_WORDS.keys()), key=len, reverse=True))
    # "casa X" ou "X casa" — ambas variações. Chamamos de HOUSE_LOC
    # (localização da casa). Grupo 'hn' captura o número/palavra.
    house_loc = (
        rf"(?:casa\s+(?P<hn1>{house_alt})|(?P<hn2>{house_alt})\s+casa)"
    )
    def _extract_hn_from_match(m):
        hn = m.groupdict().get("hn1") or m.groupdict().get("hn2")
        return _extract_house_number(hn) if hn else None

    hits = []

    # (i-a) "<signo> na cúspide da <house_loc>"
    pat1 = re.compile(
        rf"({signs_alt})\s+(?:na|em)\s+cúspide\s+da\s+{house_loc}\b",
        flags=re.IGNORECASE,
    )
    for m in pat1.finditer(text):
        hn = _extract_hn_from_match(m)
        if hn:
            hits.append({"match": m.group(0), "offset": m.start(),
                         "sign_claimed": _SIGN_CANON.get(m.group(1).capitalize(), m.group(1)),
                         "house_num": int(hn), "pattern": "signo_na_cuspide_casa_N"})

    # (i-b) "cúspide da <house_loc> em <signo>"
    pat2 = re.compile(
        rf"cúspide\s+da\s+{house_loc}\s+(?:em|está\s+em|é\s+em)\s+({signs_alt})\b",
        flags=re.IGNORECASE,
    )
    for m in pat2.finditer(text):
        hn = _extract_hn_from_match(m)
        if hn:
            # último grupo nomeado é o signo
            sign_grp = m.groups()[-1]
            hits.append({"match": m.group(0), "offset": m.start(),
                         "sign_claimed": _SIGN_CANON.get(sign_grp.capitalize(), sign_grp),
                         "house_num": int(hn), "pattern": "cuspide_casa_N_em_signo"})

    # (i-c) "<house_loc> tem cúspide em <signo>"
    pat3 = re.compile(
        rf"{house_loc}\s+tem\s+cúspide\s+em\s+({signs_alt})\b",
        flags=re.IGNORECASE,
    )
    for m in pat3.finditer(text):
        hn = _extract_hn_from_match(m)
        if hn:
            sign_grp = m.groups()[-1]
            hits.append({"match": m.group(0), "offset": m.start(),
                         "sign_claimed": _SIGN_CANON.get(sign_grp.capitalize(), sign_grp),
                         "house_num": int(hn), "pattern": "casa_N_tem_cuspide"})

    # (ii) "<house_loc> em <signo>" (subject = house)
    pat4 = re.compile(
        rf"\b{house_loc}\s+em\s+({signs_alt})\b",
        flags=re.IGNORECASE,
    )
    for m in pat4.finditer(text):
        hn = _extract_hn_from_match(m)
        if hn:
            sign_grp = m.groups()[-1]
            hits.append({"match": m.group(0), "offset": m.start(),
                         "sign_claimed": _SIGN_CANON.get(sign_grp.capitalize(), sign_grp),
                         "house_num": int(hn), "pattern": "casa_N_em_signo"})

    # (iii) "<signo> na casa N" — sujeito CASA (não planeta). Mas essa
    # sintaxe é comum pra planeta: "Vênus em Câncer na casa 8" → deve
    # ser IGNORADA. Só flaga se NÃO houver planeta próximo (janela de
    # 40 chars antes) que pudesse ser o sujeito. Padrão comum de sujeito-
    # CASA: "com Gêmeos na casa 8", "há Gêmeos na casa 8".
    pat5 = re.compile(
        rf"({signs_alt})\s+na\s+casa\s+({house_alt})\b",
        flags=re.IGNORECASE,
    )
    _planet_names = r"\b(Sol|Lua|Mercúrio|Mercurio|Vênus|Venus|Marte|Júpiter|Jupiter|Saturno|Urano|Netuno|Plutão|Plutao|Quíron|Quiron|Lilith|Ceres|Vesta|Juno|Palas|Pallas|Nodo)\b"
    for m in pat5.finditer(text):
        # Janela de 40 chars antes da match — se tem nome de planeta ali
        # o sujeito PODE ser o planeta (planeta-em-signo-em-casa). Skip.
        before = text[max(0, m.start()-40):m.start()]
        if re.search(_planet_names, before, flags=re.IGNORECASE):
            continue
        hn = _extract_house_number(m.group(2))
        if hn:
            hits.append({"match": m.group(0), "offset": m.start(),
                         "sign_claimed": _SIGN_CANON.get(m.group(1).capitalize(), m.group(1)),
                         "house_num": int(hn), "pattern": "signo_na_casa_N"})

    return hits


def _validate_cusp_claims(text, chart):
    """Retorna lista de afirmações sobre cúspide que DIVERGEM da tabela real.
    Cada item: dict com match, offset, sign_claimed, house_num, sign_real,
    pattern. Se cúspides não estão disponíveis (chart sem 'cusps'), não flaga
    nada — melhor não flagar do que flagar sem base."""
    cusps_by_num = _get_cusps(chart)
    if not cusps_by_num:
        return []
    out = []
    for claim in _detect_cusp_claims(text):
        hn = claim["house_num"]
        real = cusps_by_num.get(hn)
        if not real:
            continue
        if claim["sign_claimed"] != real:
            out.append({**claim, "sign_real": real})
    return out


# ============================================================
# SPLIT EM FRASES E LOCALIZAÇÃO DE MATCH → FRASE
# ============================================================
def _split_sentences(text):
    """Split simples em frases. Retorna lista de (start, end, sentence_text).
    Marca de frase: . ! ? seguido de espaço/quebra e letra maiúscula, ou
    fim de linha dupla. Preserva offsets originais."""
    if not text:
        return []
    boundaries = [0]
    # Marca finais de frase
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÃÕÂÊÔÀÇ])", text):
        boundaries.append(m.end())
    # Quebras duplas (parágrafo) também
    for m in re.finditer(r"\n\s*\n", text):
        boundaries.append(m.end())
    boundaries = sorted(set(boundaries))
    boundaries.append(len(text))
    sents = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        seg = text[s:e]
        if seg.strip():
            sents.append((s, e, seg))
    return sents


def _sentence_for_offset(sentences, offset):
    """Retorna (idx, s, e, text) da frase que contém o offset dado, ou None."""
    for i, (s, e, txt) in enumerate(sentences):
        if s <= offset < e:
            return i, s, e, txt
    return None


# ============================================================
# REESCRITA DIRECIONADA DE FRASE
# ============================================================
def _rewrite_sentence(sentence, violations_here, call_claude_fn):
    """Reescreve UMA frase, listando as violações que precisa eliminar.
    call_claude_fn é passada em vez de importada pra evitar ciclo de
    import com report_generator."""
    if not violations_here:
        return sentence
    listing = "\n".join(f"- {v}" for v in violations_here)
    prompt = (
        "Você é Marcia Fervienza. A FRASE abaixo, extraída de um relatório "
        "de mapa natal que você acabou de escrever, contém uma ou mais "
        "violações de regras editoriais. Sua tarefa é reescrever APENAS "
        "essa frase, eliminando as violações listadas, preservando o "
        "sentido e o tom.\n\n"
        f"VIOLAÇÕES A ELIMINAR:\n{listing}\n\n"
        "REGRAS PARA A REESCRITA:\n"
        "- Preserve o sentido, o tema e a extensão aproximada da frase.\n"
        "- NUNCA use construção de negação-substituição: nada de "
        "'não é X, é Y' em qualquer forma (não apenas/mas, menos/e mais, "
        "não se trata/e sim, y e não x, aqui não há/há, etc.). Afirme "
        "diretamente.\n"
        "- Se a violação for uma palavra específica (grafia errada, termo "
        "rejeitado), substitua por uma alternativa correta que preserve "
        "o sentido.\n"
        "- Voz íntima, direta, precisa. Sem metáforas dramáticas, sem "
        "'funda' (use 'profunda'), sem 'presença' como substantivo vago, "
        "sem palavras em inglês.\n"
        "- Se a violação for uma contagem incorreta (ex.: 'três X — A e B'), "
        "corrija a contagem ou a enumeração para bater.\n"
        "- Se a violação for uma CÚSPIDE INCORRETA ('casa X em <signo>' ou "
        "'<signo> na cúspide da casa X' que não bate com a real): você DEVE "
        "REMOVER completamente a menção à cúspide/casa nessa frase — NÃO "
        "substitua o signo errado pelo signo certo. Preserve o resto do "
        "sentido psicológico. Uma frase sem menção à cúspide é sempre "
        "preferível a uma frase com cúspide corrigida pelo modelo.\n\n"
        f"FRASE A REESCREVER:\n\"\"\"\n{sentence.strip()}\n\"\"\"\n\n"
        "Retorne APENAS a frase reescrita, sem aspas, sem introdução, sem "
        "explicação. Uma única frase (ou 2 frases curtas se o sentido exigir)."
    )
    saida = call_claude_fn(prompt, max_tokens=500).strip()
    _mot = _motivo_reescrita_invalida(sentence, saida)
    if _mot:
        raise _ReescritaInvalida(_mot)
    return saida


class _ReescritaInvalida(Exception):
    """A saída do reescritor não é uma frase — é comentário sobre a frase."""


# META-COMENTÁRIO DO REESCRITOR VAZANDO NO RELATÓRIO (19/07)
#
# O relatório do Lucca saiu com QUATRO injeções do raciocínio do próprio
# reescritor, emendadas no texto, no PDF e no e-mail:
#     "--- Aguarda — vou corrigir corretamente."
#     "--- Ainda há \"ela\" — corrijo completamente:"
#     "A violação de voz dizia respeito a … Revisando:"
# seguidas de duas ou três versões alternativas da mesma frase.
#
# O prompt JÁ mandava "Retorne APENAS a frase reescrita, sem introdução,
# sem explicação". Não bastou — instrução não é garantia. A saída passava
# direto para o splice sem ninguém perguntar se aquilo era uma frase.
#
# Esta é a QUARTA falha da camada de reescrita, e a única que injeta texto
# NOVO no produto (as três anteriores: offset na frase anterior, instrução
# ambígua, descarte mudo). A verificação pós-aplicação PEGOU — mas só
# depois de o PDF ter sido gerado e enviado. Detectar não basta: tem de
# recusar ANTES do splice.
_META_REESCRITA = re.compile(
    r"(?:\b(?:corrijo|reescrevendo|conforme\s+solicitado|"
    r"vers(?:ão|ao)\s+corrigida|viola(?:ção|cao)\s+de\s+voz|"
    r"a\s+frase\s+reescrita|segue\s+a\s+frase|"
    r"aqui\s+est[áa]\s+a\s+frase)\b)"
    # "Aguarda —" e "Revisando:" terminam em PONTUAÇÃO: o \b final do
    # grupo caía depois do travessão e a alternativa nunca casava.
    r"|(?:\baguarda\b\s*[—–-])"
    r"|(?:\brevisando\b\s*:)"
    r"|(?:\beliminando\s+a\s+viola)",
    re.IGNORECASE)


def _motivo_reescrita_invalida(original, saida):
    """Devolve o motivo se a saída NÃO for uma frase reescrita, ou None."""
    if not saida:
        return "saída vazia"
    # 1. Bloco separador solto: o modelo abriu uma seção nova.
    if re.search(r"(?m)^\s*---\s*$", saida):
        return "contém separador '---' — saída em blocos, não uma frase"
    # 2. Uma frase reescrita não tem parágrafos. Este critério sozinho pega
    #    as quatro injeções reais do Lucca.
    blocos = [b for b in re.split(r"\n\s*\n", saida) if b.strip()]
    if len(blocos) > 1:
        return f"saída em {len(blocos)} blocos — reescrita de frase é um bloco só"
    # 3. Comentário sobre a tarefa em vez da tarefa.
    m = _META_REESCRITA.search(saida)
    if m:
        return f"meta-comentário do reescritor: {m.group(0)!r}"
    # 4. Inchaço: 2,5× o original mais folga é reescrita virando ensaio.
    if len(saida) > 2.5 * len(original) + 80:
        return (f"saída {len(saida)} chars contra {len(original)} do original "
                f"— não é reescrita, é acréscimo")
    return None


# ============================================================
# ORQUESTRADOR
# ============================================================


def _detectar_tudo(text, chart):
    """TODAS as detecções, numa rotina só.

    Extraída de run_verifier em 18/07 para que o scan inicial E a
    verificação pós-aplicação usem exatamente o mesmo código. Duas rotinas
    de detecção seriam a segunda lista que já nos mordeu — e aqui o risco é
    pior: a checagem final poderia dizer "limpo" por não procurar o que o
    scan procura.

    Retorna a lista de violações (sem status; quem aplica é run_verifier).
    """
    scan_text = _mask_fixed_templates(text)
    violations_all = []  # cada item: {"kind","match","offset","suggestion","sentence_idx"}

    def _add(kind, match_text, offset, suggestion=""):
        # NORMALIZAÇÃO DO OFFSET (19/07). Vários padrões abrem com uma classe
        # negada tipo `[^.;:!?]{5,60}` — que, logo depois de um ponto final,
        # começa a casar NO ESPAÇO separador. O offset então caía na frase
        # ANTERIOR, e `_sentence_for_offset` mandava o reescritor consertar a
        # frase errada: na Helena (19/07) ele reescreveu "Outras pessoas
        # percebem em você algo expansivo…" — correta — enquanto
        # "…a camada de chegada — e não necessariamente…" ficava intacta.
        # Pior: o re-verify olhava a frase reescrita, não achava nada e
        # registrava "corrected". Defeito vivo + log dizendo que foi curado.
        _lead = len(match_text) - len(match_text.lstrip())
        violations_all.append({
            "kind": kind, "match": match_text[:120],
            "offset": offset + _lead, "suggestion": suggestion,
        })

    # 2a — léxico proibido (com validator opcional por entrada)
    try:
        for entry in _FORBIDDEN_LEXICON:
            pat, cat, sugg = entry[0], entry[1], entry[2]
            validator = entry[3] if len(entry) > 3 else None
            for m in re.finditer(pat, scan_text, flags=re.IGNORECASE):
                if validator is not None and not validator(text, m):
                    continue
                _add(f"lexico:{cat}", m.group(0), m.start(), sugg)
    except Exception as e:
        logger.warning("verifier 2a failed: %s", e)

    # 2b — negação-substituição
    try:
        # As formas se SOBREPÕEM por construção (a mesma frase casa em A e
        # em B). Sem dedupe o reescritor recebia a mesma instrução duas
        # vezes para o mesmo trecho.
        _neg_pos = []
        for pat, cat in (_NEGACAO_FORMAS + _NEGATION_SUBSTITUTION_PATTERNS):
            for m in re.finditer(pat, scan_text, flags=re.IGNORECASE):
                # "X, e não Y" só é SUBSTITUIÇÃO se X for afirmativo. Em
                # "não pede permissão, e não há nada de errado" a oração já
                # é negativa: negação COORDENADA, língua normal. O regex
                # sozinho não resolve — ele desliza e começa a casar DEPOIS
                # do "não", e lookbehind em Python é de largura fixa. Aqui a
                # oração inteira é conferida.
                if cat in ("y_e_nao_x", "y_travessao_e_nao_x"):
                    _ini = max(scan_text.rfind(c, 0, m.start())
                               for c in ".;:!?\n") + 1
                    if re.search(r"\bn[ãa]o\b", scan_text[_ini:m.start()],
                                 flags=re.IGNORECASE):
                        continue
                if any(abs(m.start() - p) < 40 for p in _neg_pos):
                    continue
                _neg_pos.append(m.start())
                _add(f"neg_subst:{cat}", m.group(0), m.start(),
                     "reescrever a frase afirmando diretamente, sem passar por 'não X'")
    except Exception as e:
        logger.warning("verifier 2b failed: %s", e)

    # 2c — nomenclatura de aspectos
    try:
        for match_text, offset in _detect_invalid_aspect_composition(scan_text):
            _add("aspecto:composicao_invalida", match_text, offset,
                 "usar UM único nome de aspecto — a locução combina dois nomes indevidamente")
    except Exception as e:
        logger.warning("verifier 2c failed: %s", e)

    # 2c' — aspecto com o segundo corpo comido ("entre sua Vênus está em")
    try:
        for match_text, offset in _detect_broken_aspect_pair(scan_text):
            _add("aspecto:par_incompleto", match_text, offset,
                 "um aspecto liga DOIS corpos — reescrever nomeando os dois "
                 "('o sextil entre sua Lua e Vênus…') ou remover a menção ao aspecto")
    except Exception as e:
        logger.warning("verifier 2c' failed: %s", e)

    # 2d — contagem vs enumeração
    try:
        for match_text, offset, expected, actual in _detect_count_mismatch(scan_text):
            _add("contagem:desbatida", match_text, offset,
                 f"o texto anuncia {expected} itens mas enumera {actual} — corrigir a contagem ou a enumeração")
    except Exception as e:
        logger.warning("verifier 2d failed: %s", e)

    # 2e — spellcheck
    # 2e — SPELLCHECK: DESLIGADO (17/07, 2ª rodada).
    #
    # O dicionário "pt" do pyspellchecker é português EUROPEU: ele não
    # conhece "contato", "bônus", "perspectiva", "harmônico" (formas
    # brasileiras) e SUGERE "contacto", "bónus", "perspetiva", "harmónico".
    # O detector existia dormente — devolvia [] porque a lib não estava
    # instalada. Ao entrar no requirements.txt (c93a9ae) ele ACORDOU e
    # passou a reescrever pt-BR em pt-PT: 49 "correções" no relatório da
    # Helena, e a contaminação europeia que a Márcia reportou.
    #
    # Não basta ignorar a sugestão: a palavra correta seria flagrada como
    # violação e mandada para reescrita mesmo assim. Fica DESLIGADO até
    # existir dicionário pt-BR de verdade. Quem faz o trabalho de léxico
    # são as listas explícitas (família espanhola, família PT-PT, termos em
    # inglês) e os glossários fechados de signo e de planeta — todos com a
    # forma correta na sugestão, que é o que o reescritor precisa.
    #
    # `spell_lint` (flag-only, no meta) segue existindo, mas com a mesma
    # limitação registrada: com este dicionário ele acusa palavra brasileira
    # correta. Ver ESTADO.

    # 2f — voz (consciente dos dois interruptores)
    try:
        for kind, match_text, offset, sugg in _detect_voice_violations(scan_text, chart):
            _add(kind, match_text, offset, sugg)
    except Exception as e:
        logger.warning("verifier 2f failed: %s", e)

    # 2g — SLOT DE GÊNERO (inventário 17/07): termo generificado que não
    # corresponde ao gênero do sujeito. Caso real: "se um dia você tiver
    # filhos... a transformação não passe pela MATERNIDADE" num relatório
    # masculino (Lucca). "materna/paterna" (a figura) são legítimos em
    # qualquer gênero — só o substantivo do papel do PRÓPRIO sujeito é
    # flagrado.
    try:
        _gender = (chart or {}).get("gender")
        _gpat = None
        if _gender == "masculino":
            _gpat = (r"\bmaternidade\b", "para sujeito masculino: 'paternidade' (ou reescrever)")
        elif _gender == "feminino":
            _gpat = (r"\bpaternidade\b", "para sujeito feminino: 'maternidade' (ou reescrever)")
        if _gpat:
            for m in re.finditer(_gpat[0], scan_text, flags=re.IGNORECASE):
                _add("genero:slot_papel", m.group(0), m.start(), _gpat[1])
    except Exception as e:
        logger.warning("verifier 2g failed: %s", e)

    # --- rodada 17/07: doutrina da Márcia (detectores semânticos) ---
    for fn, tag in ((_detect_netuno_plutao_mention, "netuno_plutao"),
                    (_detect_sign_as_generational_agent, "signo_geracional"),
                    (_detect_sign_adjectives, "glossario_signo"),
                    (_detect_planet_adjectives, "glossario_planeta"),
                    (_detect_bare_ic, "ic_sozinho")):
        try:
            for v in fn(scan_text):
                _add(v["kind"], v["match"], v["offset"], v["suggestion"])
        except Exception as e:
            logger.warning("verifier %s failed: %s", tag, e)
    try:
        for v in _detect_false_no_aspect_claims(scan_text, chart):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
        for fn in (_detect_house_inconsistency, _detect_angle_claims,
                   _detect_rulership, _detect_asserted_aspect):
            for v in fn(scan_text, chart):
                _add(v["kind"], v["match"], v["offset"], v["suggestion"])
    except Exception as e:
        logger.warning("verifier falsa_ausencia failed: %s", e)
    try:
        for v in _detect_clitic_third_person(scan_text, (chart or {}).get("_voice")):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
        for v in _detect_third_person_leak(scan_text, (chart or {}).get("_voice")):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
    except Exception as e:
        logger.warning("verifier clitico failed: %s", e)

    try:
        for v in _detect_person_instantiation(scan_text, (chart or {}).get("_voice")):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
        for v in _detect_prompt_scaffolding_leak(scan_text):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
    except Exception as e:
        logger.warning("verifier andaime failed: %s", e)

    # LINT DE PALAVRA (19/07) — a camada que faltava. O GPT achou sete
    # corrupções que todos os lints deixaram passar; nenhuma vinha do
    # splice (todas no meio da frase), eram erros de palavra do modelo.
    # Entra aqui, e não como lint de meta, para passar pela MESMA máquina
    # de reescrita e pós-verificação: se a correção não pegar, vira
    # PERSISTIU em vez de sumir num campo que ninguém lê.
    try:
        from word_lint import word_lint as _wl
        for v in _wl(scan_text):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
            # R3 (palavra:corrompida) é FLAG-ONLY (19/07). As outras quatro
            # regras mediram ZERO falso positivo e reescrevem. Esta acusou
            # "aterrissa" e "internalizado" — pt-BR correto, flexões de
            # palavras que ESTÃO no léxico de domínio mas cujas formas
            # flexionadas não estão. Mandar o reescritor "consertar" palavra
            # certa é o defeito que matou o spell_lint. Sinaliza no log, não
            # toca no texto.
            if v["kind"] == "palavra:corrompida":
                violations_all[-1]["no_rewrite"] = True
    except Exception as e:
        logger.warning("verifier word_lint failed: %s", e)

    # 4b/4c — afirmações sobre cúspides validadas contra a tabela real
    try:
        for cd in _validate_cusp_claims(scan_text, chart):
            _add(
                f"cuspide:divergencia_{cd['pattern']}",
                cd["match"],
                cd["offset"],
                (f"o texto afirma '{cd['sign_claimed']}' para a cúspide da casa "
                 f"{cd['house_num']}, mas a cúspide real neste mapa é "
                 f"'{cd['sign_real']}'. AÇÃO: REMOVER a menção à cúspide/casa nesta "
                 f"frase preservando o resto do sentido. NUNCA substituir "
                 f"'{cd['sign_claimed']}' por '{cd['sign_real']}' — a correção do "
                 f"signo por conta própria é risco maior que a ausência da "
                 f"menção. Reformule a frase eliminando a afirmação sobre a "
                 f"cúspide (ou sobre 'casa N em <signo>') e mantenha o tema/"
                 f"conteúdo psicológico do que vinha em torno."),
            )
    except Exception as e:
        logger.warning("verifier 4b/4c failed: %s", e)

    return violations_all


def run_verifier(text, chart, call_claude_fn):
    """Roda todas as detecções, agrupa por frase, reescreve as frases
    afetadas (até 2 tentativas), retorna (texto_corrigido, log_de_violações).
    Nunca levanta — falha em qualquer detector é logada e o resto segue."""
    if not text:
        return text, []

    # Texto fixo de template aprovado não entra em nenhum scan (offsets
    # preservados — a máscara troca por espaços do mesmo comprimento).
    scan_text = _mask_fixed_templates(text)

    violations_all = _detectar_tudo(text, chart)

    if not violations_all:
        return text, []

    # Agrupa violações por frase
    sentences = _split_sentences(text)
    per_sent = {}
    log_out = []            # nasce aqui: o laço abaixo já registra descartes
    for v in violations_all:
        # Violação SINALIZADA: entra no log, não vai para reescrita. É a
        # saída honesta quando não se sabe qual lado é o certo — melhor que
        # deixar o reescritor adivinhar (foi assim que o casa_inconsistente
        # corrompeu texto correto, 18/07).
        if v.get("no_rewrite"):
            log_out.append({**v, "status": "sinalizada_sem_reescrita"})
            continue
        info = _sentence_for_offset(sentences, v["offset"])
        if info is None:
            # DROP SILENCIOSO (19/07). Isto dava `continue` mudo: na Helena,
            # 19 violações detectadas e 17 no log — duas evaporaram sem
            # deixar rastro. Uma violação que ninguém consegue localizar numa
            # frase é justamente a que precisa ser vista.
            logger.warning("verifier: violação sem frase correspondente: %s %r @%d",
                           v["kind"], v["match"][:60], v["offset"])
            log_out.append({**v, "status": "SEM_FRASE_NAO_CORRIGIDA"})
            continue
        idx, s, e, txt = info
        v["sentence_idx"] = idx
        per_sent.setdefault(idx, []).append(v)

    # Reescreve cada frase afetada (até 2 tentativas)
    # Aplicação de trás para frente pra preservar offsets
    corrected = text
    # As reescritas são INDEPENDENTES entre si: cada uma só lê a própria frase
    # e as próprias violações. Rodavam EM SÉRIE — uma chamada ao Claude por
    # tentativa, por frase — e a geração passou dos 300s do proxy do Railway
    # (o gunicorn aguenta 900s; o proxy corta antes e devolve 502).
    # Aqui elas rodam em paralelo. A APLICAÇÃO no documento continua
    # sequencial e de trás para frente, para preservar os offsets — o
    # resultado é idêntico, só o relógio muda.
    def _rewrite_one(idx):
        vs_i = per_sent[idx]
        _s, _e, orig_i = sentences[idx]
        current = orig_i
        succeeded = False
        last_violations = vs_i
        attempt = 0
        for attempt in range(1, 3):
            listing = [f"{v['kind']} — {v['match']!r} — {v['suggestion']}"
                       for v in last_violations]
            try:
                rewritten = _rewrite_sentence(current, listing, call_claude_fn)
            except _ReescritaInvalida as exc:
                # Saída recusada ANTES do splice. Conta como tentativa
                # gasta: se a segunda também vier assim, fica o original.
                logger.warning("verifier: reescrita RECUSADA (tentativa %d): %s",
                               attempt, exc)
                continue
            except Exception as exc:
                logger.warning("verifier: rewrite call failed (attempt %d): %s",
                               attempt, exc)
                break
            # Re-verifica a frase reescrita quanto às MESMAS categorias que
            # foram flagradas — se sumiram, sucesso.
            new_hits = _reverify_sentence(rewritten, last_violations, chart)
            if not new_hits:
                current = rewritten
                succeeded = True
                break
            # Se ainda tem violações, tenta de novo com a nova frase
            current = rewritten
            last_violations = new_hits
        return idx, current, succeeded, last_violations, attempt

    _idxs = sorted(per_sent.keys(), reverse=True)
    _res = {}
    if _idxs:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, len(_idxs))) as _ex:
            for _r in _ex.map(_rewrite_one, _idxs):
                _res[_r[0]] = _r

    for idx in _idxs:
        vs = per_sent[idx]
        s, e, orig_sent = sentences[idx]
        _, current, succeeded, last_violations, attempt = _res[idx]
        if succeeded:
            # O separador de cauda (espaço, \n, \n\n) pertence ao DOCUMENTO,
            # não à frase: o segmento de _split_sentences termina onde a
            # próxima frase começa, então ele carrega o separador — e a
            # reescrita volta strip()ada. Sem repor a cauda, cada correção
            # colava a frase na seguinte ("…calorosa.A oposição…") e, quando
            # a frase era a última antes de um cabeçalho, comia o \n\n e o
            # markdown vazava literal no PDF (".## Fio Condutor", 16/07 —
            # 5 reescritas nos dois relatórios, 5 frases coladas).
            trail = orig_sent[len(orig_sent.rstrip()):]
            corrected = corrected[:s] + current + trail + corrected[e:]
            for v in vs:
                log_out.append({**v, "status": "corrected", "attempts": attempt})
        else:
            logger.warning(
                "VERIFIER_FAIL sentence_idx=%d violations=%d after 2 attempts; "
                "keeping original. Sentence: %r",
                idx, len(vs), orig_sent[:200],
            )
            for v in vs:
                log_out.append({**v, "status": "failed_kept_original", "attempts": 2})


    # ================================================================
    # VERIFICAÇÃO PÓS-APLICAÇÃO (18/07) — o verifier verificando a si mesmo.
    #
    # Ele nunca teve isto. O log dizia "corrected" e o texto final podia
    # continuar errado — ou ficar PIOR: no Lucca, a instrução de
    # inconsistência de casa não dizia qual era a certa, o reescritor
    # uniformizou para a errada, e o log registrou sucesso. Corrupção
    # silenciosa é pior que defeito não detectado.
    #
    # Aqui as MESMAS detecções rodam sobre o texto CORRIGIDO. Toda violação
    # marcada como "corrected" que reaparecer vira `persistiu`; qualquer
    # violação NOVA que a reescrita tenha introduzido vira `introduzida`.
    # As duas sobem no log com status próprio — alto, não em silêncio.
    try:
        _post = _detectar_tudo(corrected, chart)
        _antes_keys = {(v["kind"], v["match"]) for v in violations_all}
        _corrigidas = {(v["kind"], v["match"]) for v in log_out
                       if v.get("status") == "corrected"}
        for _v in _post:
            _k = (_v["kind"], _v["match"])
            if _k in _corrigidas:
                log_out.append({**_v, "status": "PERSISTIU_APOS_CORRECAO"})
                logger.warning("verifier: violação persistiu após correção: %s %r",
                               _v["kind"], _v["match"][:60])
            elif _k not in _antes_keys:
                log_out.append({**_v, "status": "INTRODUZIDA_PELA_REESCRITA"})
                logger.warning("verifier: reescrita INTRODUZIU violação: %s %r",
                               _v["kind"], _v["match"][:60])
    except Exception as _e:
        logger.warning("verificação pós-aplicação falhou: %s", _e)

    return corrected, log_out


def _reverify_sentence(sentence, prior_violations, chart):
    """Re-roda os detectores das MESMAS categorias que estavam presentes
    antes, sobre a frase reescrita. Se persistirem, retorna as novas
    ocorrências (com offsets relativos à frase); senão retorna []."""
    kinds = {v["kind"].split(":")[0] for v in prior_violations}
    out = []
    if "lexico" in kinds:
        for entry in _FORBIDDEN_LEXICON:
            pat, cat, sugg = entry[0], entry[1], entry[2]
            validator = entry[3] if len(entry) > 3 else None
            for m in re.finditer(pat, sentence, flags=re.IGNORECASE):
                if validator is not None and not validator(sentence, m):
                    continue
                out.append({"kind": f"lexico:{cat}", "match": m.group(0),
                            "offset": m.start(), "suggestion": sugg})
    if "neg_subst" in kinds:
        for pat, cat in _NEGATION_SUBSTITUTION_PATTERNS:
            for m in re.finditer(pat, sentence, flags=re.IGNORECASE):
                out.append({"kind": f"neg_subst:{cat}", "match": m.group(0),
                            "offset": m.start(),
                            "suggestion": "afirmar diretamente sem 'não X primeiro'"})
    if "aspecto" in kinds:
        for match_text, offset in _detect_invalid_aspect_composition(sentence):
            out.append({"kind": "aspecto:composicao_invalida", "match": match_text,
                        "offset": offset,
                        "suggestion": "usar um único nome de aspecto"})
        for match_text, offset in _detect_broken_aspect_pair(sentence):
            _c = re.search(rf"\b({_CORPO_RE})\b", match_text, flags=re.IGNORECASE)
            out.append({"kind": "aspecto:par_incompleto", "match": match_text,
                        "offset": offset,
                        "suggestion": _sugestao_par_aspecto(
                            chart, _c.group(1) if _c else None)})
    if "contagem" in kinds:
        for match_text, offset, exp, act in _detect_count_mismatch(sentence):
            out.append({"kind": "contagem:desbatida", "match": match_text,
                        "offset": offset,
                        "suggestion": (f"o texto anuncia {exp} e enumera {act}. NÃO É POSSÍVEL "
                            f"saber qual lado é o correto a partir do texto — pode "
                            f"faltar um item ou sobrar no anúncio. SINALIZADO, sem "
                            f"reescrita automática."),
             "no_rewrite": True})
    # spellcheck desligado na re-verificação também (ver 2e acima)
    if "voz" in kinds:
        for kind, match_text, offset, sugg in _detect_voice_violations(sentence, chart):
            out.append({"kind": kind, "match": match_text, "offset": offset,
                        "suggestion": sugg})
    for _pref in ("geracional", "glossario_signo", "glossario_planeta", "jargao", "fato", "aspecto:falsa",
                  "registro:clitico"):
        if any(k.startswith(_pref) for k in kinds):
            for v in prior_violations:
                if v["kind"].startswith(_pref) and v["match"].strip():
                    m2 = re.search(re.escape(v["match"][:40]), sentence, flags=re.IGNORECASE)
                    if m2:
                        out.append({"kind": v["kind"], "match": m2.group(0),
                                    "offset": m2.start(), "suggestion": v["suggestion"]})
    if "genero" in kinds:
        for v in prior_violations:
            if v["kind"].startswith("genero"):
                m2 = re.search(r"\b" + re.escape(v["match"]) + r"\b", sentence, flags=re.IGNORECASE)
                if m2:
                    out.append({"kind": v["kind"], "match": m2.group(0),
                                "offset": m2.start(), "suggestion": v["suggestion"]})
    if "cuspide" in kinds:
        for cd in _validate_cusp_claims(sentence, chart):
            out.append({
                "kind": f"cuspide:divergencia_{cd['pattern']}",
                "match": cd["match"], "offset": cd["offset"],
                "suggestion": (f"ainda diverge — afirma '{cd['sign_claimed']}' "
                               f"mas real é '{cd['sign_real']}'. REMOVER "
                               f"a menção à cúspide/casa por completo."),
            })
    return out

# ============================================================
# DETECTORES FACTUAIS — prioridade 3 (leitura de cliente, 18/07)
# ============================================================
# Mesma família da alucinação de signo: o texto afirma DADO, e o dado está
# errado. É a classe mais grave depois da ausência falsa de aspecto, porque
# o cliente tem a tabela na mesma página.

_CORPO_RE = (r"Sol|Lua|Mercúrio|Mercurio|Vênus|Venus|Marte|Júpiter|Jupiter|"
             r"Saturno|Urano|Netuno|Plutão|Plutao|Quíron|Quiron|Lilith|"
             r"Ceres|Palas|Pallas|Juno|Vesta|Nodo\s+Norte|Nodo\s+Sul")
_PT2KEY = {
    "sol":"sun","lua":"moon","mercúrio":"mercury","mercurio":"mercury",
    "vênus":"venus","venus":"venus","marte":"mars","júpiter":"jupiter",
    "jupiter":"jupiter","saturno":"saturn","urano":"uranus","netuno":"neptune",
    "plutão":"pluto","plutao":"pluto","quíron":"chiron","quiron":"chiron",
    "lilith":"lilith","ceres":"ceres","palas":"pallas","pallas":"pallas",
    "juno":"juno","vesta":"vesta","nodo norte":"north_node","nodo sul":"south_node",
}



def _corpo_mais_proximo_antes(texto, pos, limite=70):
    """O corpo nomeado imediatamente ANTES de `pos`, sem cruzar ponto final.

    Ancorar no ATRIBUTO e buscar o corpo para trás é o oposto do que os
    detectores faziam: eles casavam `(corpo) … atributo` e pegavam o
    PRIMEIRO nome do trecho. Em "Saturno acompanha Plutão em Capricórnio na
    casa 4" isso atribuía a casa a Saturno — e como Saturno está mesmo na 4,
    nada era acusado (18/07). Em português o atributo pertence ao corpo mais
    próximo à esquerda.
    """
    janela = texto[max(0, pos - limite):pos]
    corpos = list(re.finditer(rf"\b({_CORPO_RE})\b", janela, flags=re.IGNORECASE))
    if not corpos:
        return None, None
    ult = corpos[-1]
    if re.search(r"[.!?]", janela[ult.end():]):
        return None, None          # ponto final entre eles: outra frase
    return re.sub(r"\s+", " ", ult.group(1).strip().lower()), janela[ult.start():]



def _sugestao_par_aspecto(chart, corpo_pt):
    """Instrução para template de aspecto quebrado — COM a resposta.

    A versão anterior dizia só "aspecto precisa de dois corpos nomeados",
    sem dizer QUAL é o segundo. Isso é a mesma forma que fez o
    casa_inconsistente corromper: o reescritor resolve como pode, e aqui
    "como pode" significa INVENTAR um corpo. Agora a sugestão lista os
    aspectos reais daquele corpo neste mapa.
    """
    if not corpo_pt:
        return ("o nome de um aspecto precisa de DOIS corpos. Reescrever "
                "nomeando os dois, ou remover a menção ao aspecto.")
    key = _PT2KEY.get(re.sub(r"\s+", " ", corpo_pt.strip().lower()))
    reais = []
    for a in ((chart or {}).get("aspects") or []):
        pa, pb = a.get("planet_a"), a.get("planet_b")
        if key in (pa, pb):
            outro = pb if pa == key else pa
            nome_outro = next((k for k, v in _PT2KEY.items() if v == outro), outro)
            reais.append(f"{a.get('type_pt', a.get('type'))} com {nome_outro}")
    if not reais:
        return (f"o nome de um aspecto precisa de DOIS corpos, e {corpo_pt} NÃO tem "
                f"aspectos neste mapa. REMOVER a menção ao aspecto — não inventar "
                f"o segundo corpo.")
    return (f"o nome de um aspecto precisa de DOIS corpos. Os aspectos REAIS de "
            f"{corpo_pt} neste mapa são: {'; '.join(reais)}. Nomear o par correto "
            f"a partir desta lista, ou remover a menção. NUNCA inventar o segundo corpo.")


def _detect_house_inconsistency(text, chart):
    """Um corpo com DUAS casas diferentes ao longo do relatório.

    Caso real (Lucca, 18/07): Plutão na casa 6 numa seção e na 5 na seção
    própria. Contradição interna — o leitor não tem como saber qual vale.
    Confere cada afirmação contra `points`, e reporta a divergência.
    """
    out = []
    pontos = (chart or {}).get("points") or {}
    if not pontos:
        return out
    vistos = {}
    # CORPO NA FRONTEIRA (regra dos 5°, reconciliado 19/07).
    #
    # `fmt_position` MANDA o texto nomear as duas casas: "na fronteira entre
    # a casa 7 e a casa 8, com mais força na 8" — porque a mandala desenha na
    # geométrica e a leitura é na seguinte. Este detector tomava só
    # `house_geometric` como verdade, então acusava a frase que o próprio
    # prompt exigiu: casa_errada pela 8, casa_inconsistente por citar as duas.
    # A sugestão mandava colapsar para a 7, o reescritor obedecia, e a
    # fronteira — que existe justamente para o leitor não ver contradição
    # entre texto e mandala — era destruída. Para esses corpos, AS DUAS
    # casas são verdadeiras.
    _moves = {m.get("planet"): m
              for m in ((chart or {}).get("_house_moves") or [])}

    def _casas_ok(key, real):
        _s = {real}
        _mv = _moves.get(key)
        if _mv:
            _s |= {_mv.get("from_house"), _mv.get("to_house")}
        _s.discard(None)
        return _s

    # LIGAÇÃO PELO CORPO MAIS PRÓXIMO (corrigido 18/07).
    # A primeira versão casava `(corpo) … casa N` e pegava o PRIMEIRO nome do
    # trecho: em "Saturno acompanha Plutão em Capricórnio na casa 4" ela
    # atribuía a casa a SATURNO. Agora ancora em "casa N" e procura o corpo
    # imediatamente ANTES — que é a quem a casa pertence em português.
    pat = rf"\bcasa\s+(\d{{1,2}})\b"
    for m in re.finditer(pat, text, flags=re.IGNORECASE):
        janela = text[max(0, m.start() - 60):m.start()]
        if re.search(r"[.!?]", janela[::-1][:0] or ""):
            pass
        corpos = list(re.finditer(rf"\b({_CORPO_RE})\b", janela, flags=re.IGNORECASE))
        if not corpos:
            continue
        # o mais próximo = o último da janela; e não pode haver ponto final
        # entre ele e "casa N" (aí são frases diferentes)
        ult = corpos[-1]
        if re.search(r"[.!?]", janela[ult.end():]):
            continue
        nome = re.sub(r"\s+", " ", ult.group(1).strip().lower())
        casa = int(m.group(1))
        key = _PT2KEY.get(nome)
        if not key or key not in pontos:
            continue
        real = pontos[key].get("house_geometric") or pontos[key].get("house")
        _trecho = janela[ult.start():] + m.group(0)
        vistos.setdefault(key, []).append((casa, m.start(), _trecho))
        if real and casa not in _casas_ok(key, real):
            out.append({"kind": "fato:casa_errada", "match": _trecho[:60],
                        "offset": m.start(),
                        "suggestion": (f"o texto diz casa {casa} para {nome}, "
                                       f"mas a tabela deste mapa diz casa {real}. "
                                       f"Corrigir para {real} ou remover a menção.")})
    for key, itens in vistos.items():
        casas = {c for c, _, _ in itens}
        if len(casas) > 1:
            real = pontos[key].get("house_geometric") or pontos[key].get("house")
            if casas <= _casas_ok(key, real) and key in _moves:
                continue          # é a fronteira que o prompt pediu, não contradição
            nome_pt = itens[0][2]
            # A SUGESTÃO PRECISA DIZER QUAL É A CERTA (18/07).
            # A primeira versão dizia só "atribui mais de uma casa ([4, 5]) —
            # contradição interna", sem a resposta. O reescritor uniformizou
            # para a ERRADA: o texto dizia casa 5 (correta) e saiu casa 4.
            # Detector correto + instrução ambígua = corruptor. Pior que não
            # detectar, porque estraga texto que estava certo.
            out.append({"kind": "fato:casa_inconsistente",
                        "match": nome_pt[:60], "offset": itens[0][1],
                        "suggestion": (f"o relatório atribui MAIS DE UMA casa ao mesmo "
                                       f"corpo ({sorted(casas)}) — contradição interna. "
                                       f"A CORRETA, pela tabela deste mapa, é a casa "
                                       f"{real}. Uniformizar para {real}; NUNCA para "
                                       f"outra das listadas.")})
    return out


_ANGULOS = {
    "meio do céu": "midheaven", "meio-do-céu": "midheaven", "mc": "midheaven",
    "ascendente": "ascendant", "asc": "ascendant",
}



# ---- CLASSE NOVA (18/07): aspecto AFIRMADO que não existe --------------
# O detector irmão (_detect_false_no_aspect_claims) cobre o inverso: negar
# aspecto que existe. Ninguém cobria AFIRMAR aspecto que não existe — foi
# "a conjunção com o Sol" na seção de Saturno da Helena (Saturno em Aquário,
# Sol em Virgem: não há conjunção). Três frases de leitura sobre um aspecto
# inventado.
#
# Duas formas de erro, ambas cobertas:
#   · o par não se aspecta de jeito nenhum;
#   · o par se aspecta, mas com OUTRO aspecto (diz trígono onde é quadratura).
_ASPECTO_PT = {
    "conjunção": "conjunction", "conjuncao": "conjunction",
    "oposição": "opposition", "oposicao": "opposition",
    "trígono": "trine", "trigono": "trine",
    "quadratura": "square", "sextil": "sextile",
}


def _aspectos_reais_de(chart, key):
    """[(outro_key, tipo)] dos aspectos REAIS do corpo neste mapa."""
    out = []
    for a in ((chart or {}).get("aspects") or []):
        pa, pb = a.get("planet_a"), a.get("planet_b")
        if key == pa:
            out.append((pb, a.get("type")))
        elif key == pb:
            out.append((pa, a.get("type")))
    return out



def _corpo_da_secao(texto, pos):
    """O corpo que é ASSUNTO da seção onde `pos` cai.

    Necessário para a forma ELÍPTICA do aspecto: "o trígono com a Lua e a
    conjunção com o Sol" — na seção de Saturno, o primeiro corpo é Saturno
    e não aparece na frase. Sem isto o detector emparelhava "Lua × Sol",
    que não é o que o texto afirma (18/07).
    """
    cab = None
    for m in re.finditer(r"(?m)^##\s+(.+)$", texto[:pos]):
        cab = m.group(1)
    if not cab:
        return None
    prefixo = cab.split(":")[0]
    # TODOS os corpos do título, não só o primeiro. Seções como
    # "Sol e Saturno" têm dois sujeitos possíveis, e a frase pode ser sobre
    # qualquer um deles: "Quando Saturno está em oposição a Quíron" numa
    # seção "Sol e Saturno" foi emparelhada com SOL e virou falso positivo
    # (18/07). Com sujeito ambíguo, só se acusa se NENHUM candidato servir.
    achados = re.findall(rf"\b({_CORPO_RE})\b", prefixo, flags=re.IGNORECASE)
    if not achados:
        return None
    return [re.sub(r"\s+", " ", a.strip().lower()) for a in achados]


def _par_antes(antes):
    """Par de corpos IMEDIATAMENTE antes do nome do aspecto: "Vênus-Quíron
    em sextil", "Mercúrio e Marte em trígono", "O Sol com Plutão em sextil".
    Uma função só — as duas cópias que isto substitui saíram de sincronia na
    primeira edição e o detector quebrou com AttributeError."""
    return re.search(rf"\b({_CORPO_RE})\s*(?:[-–—]|\se\s|\scom\s)\s*"
                     rf"({_CORPO_RE})\b\s+em\s+$", antes, flags=re.IGNORECASE)


# LEVANTAMENTO DE FORMAS (19/07, aprovado pela Márcia: cobrir a CLASSE em
# vez do exemplo). Varri as 73 ocorrências de nome de aspecto nos dois
# relatórios e classifiquei por qual ramo do detector as trata. Nenhuma caía
# mais na regra genérica — mas 7 de 15 abstenções eram par AFIRMADO que o
# detector ignorava em silêncio. Falso negativo não custa reescrita, e por
# isso não aparece em nenhum alarme: passa direto para o PDF.
#
# As quatro formas que faltavam, todas colhidas do texto real:
#   · "a oposição Sol-Plutão"                      → par hifenizado DEPOIS
#   · "a conjunção de Mercúrio com Júpiter"        → "de A com B"
#   · "o trígono que Saturno forma em Quíron"      → oração relativa
#   · "O Sol com Plutão em sextil"                 → par antes ligado por "com"
_ART = r"(?:[oa]s?\s+|seus?\s+|suas?\s+)?"


def _par_depois(depois):
    """Par de corpos DEPOIS do nome do aspecto, nas formas que não usam
    'entre' nem a elíptica 'com B'."""
    for pat in (
        # "a oposição Sol-Plutão", "o trígono Lua-Saturno"
        rf"^\s+{_ART}({_CORPO_RE})\s*[-–—]\s*({_CORPO_RE})\b",
        # "a conjunção de Mercúrio com Júpiter", "o trígono DA Lua com Saturno".
        # A contração ("da/do/das/dos") faltava: a frase real da Helena caía
        # na regra genérica e emparelhava o Júpiter da cláusula anterior com
        # a Lua — "Júpiter trígono Lua", que a frase não diz (19/07).
        rf"^\s+d[eao]s?\s+{_ART}({_CORPO_RE})\s+(?:com|ao?s?|às?)\s+"
        rf"{_ART}({_CORPO_RE})\b",
        # "o trígono que Saturno forma com/em Quíron"
        rf"^\s+que\s+{_ART}({_CORPO_RE})\s+(?:forma|faz|estabelece|mantém)\s+"
        rf"(?:com|em|ao?s?|às?)\s+{_ART}({_CORPO_RE})\b",
    ):
        m = re.match(pat, depois, flags=re.IGNORECASE)
        if m:
            return m
    return None


def _detect_asserted_aspect(text, chart):
    """Aspecto AFIRMADO entre dois corpos, conferido contra a tabela.

    Ancora no NOME DO ASPECTO e busca os corpos em volta (R: ancoragem por
    proximidade, não pelo primeiro nome do trecho).
    """
    out = []
    pontos = (chart or {}).get("points") or {}
    aspectos = (chart or {}).get("aspects") or []
    if not pontos or not aspectos:
        return out
    nomes = "|".join(_ASPECTO_PT.keys())
    for m in re.finditer(rf"\b({nomes})\b", text, flags=re.IGNORECASE):
        tipo = _ASPECTO_PT[re.sub(r"\s+", " ", m.group(1).strip().lower())]
        antes = text[max(0, m.start() - 70):m.start()]
        depois = text[m.end():m.end() + 70]
        # corte em ponto final: aspecto e corpos têm de estar na mesma frase
        if "." in antes:
            antes = antes[antes.rindex(".") + 1:]
        if "." in depois:
            depois = depois[:depois.index(".")]
        c_antes = list(re.finditer(rf"\b({_CORPO_RE})\b", antes, flags=re.IGNORECASE))
        c_depois = list(re.finditer(rf"\b({_CORPO_RE})\b", depois, flags=re.IGNORECASE))
        # par: o mais próximo de cada lado; ou os dois primeiros depois
        # A FORMA decide quantos corpos a frase nomeia:
        #   "<aspecto> COM X"        → elíptica: só X; o outro é o assunto
        #                              da seção (a forma do defeito real).
        #   "<aspecto> ENTRE A E B"  → par explícito.
        #   "A <aspecto> B"          → par explícito.
        cand = []
        # Preposições da forma elíptica. Só "com" estava previsto; o texto
        # real usa também "a/ao/aos/à/às" ("a quadratura AOS Nodos"). Sem
        # reconhecer, caía na regra genérica e emparelhava dois corpos que a
        # frase não relaciona — falso positivo na Helena, 18/07:
        # "a conjunção com Júpiter e Mercúrio, a quadratura aos Nodos, o
        #  sextil com Plutão" virou "Mercúrio quadratura Plutão".
        # QUALIFICADOR ENTRE O ASPECTO E A PREPOSIÇÃO (19/07). O texto real
        # escreve "em quadratura QUASE EXATA aos Nodos", "em oposição
        # APERTADA a Quíron". Sem prever isso, a preposição não era
        # reconhecida, a forma caía na regra genérica e emparelhava dois
        # corpos que a frase não relaciona: na Helena, "o Sol em quadratura
        # quase exata aos Nodos, e Saturno retrógrado em oposição a Quíron"
        # — frase inteiramente correta — virou "Sol quadratura Saturno".
        _QUAL = (r"(?:\s+(?:muito|quase|bastante|perfeitamente|razoavelmente|"
                 r"extremamente|relativamente)?\s*"
                 r"(?:exat[ao]|apertad[ao]|fechad[ao]|ampl[ao]|larg[ao]|"
                 r"just[ao]|precis[ao]|separativ[ao]|aplicativ[ao]))*")
        m_com = re.match(rf"{_QUAL}\s+(?:com|ao?s?|às?)\s+(?:a|o|as|os|sua|seu)?\s*"
                         rf"({_CORPO_RE})\b", depois, flags=re.IGNORECASE)
        m_entre = re.match(rf"{_QUAL}\s+entre\s+(?:a|o|as|os|sua|seu)?\s*"
                           rf"({_CORPO_RE})\b[^.]{{0,20}}?\se\s+(?:a|o|sua|seu)?\s*"
                           rf"({_CORPO_RE})\b", depois, flags=re.IGNORECASE)
        _pd = _par_depois(depois)
        if m_entre:
            cand = [m_entre.group(1), m_entre.group(2)]
        elif _pd:
            cand = [_pd.group(1), _pd.group(2)]
        elif m_com:
            _sujs = _corpo_da_secao(text, m.start()) or []
            _alvo = m_com.group(1)
            _ka = _PT2KEY.get(re.sub(r"\s+", " ", _alvo.strip().lower()))
            # sujeito AMBÍGUO (seção com dois corpos): se QUALQUER um deles
            # tiver esse aspecto com o alvo, a frase é plausível — não acusa.
            _plausivel = False
            for _s in _sujs:
                _ks = _PT2KEY.get(_s)
                if not _ks or not _ka:
                    continue
                if any(o == _ka and t == tipo for o, t in _aspectos_reais_de(chart, _ks)):
                    _plausivel = True
                    break
            if _plausivel:
                continue
            if len(_sujs) == 1:
                cand = [_sujs[0], _alvo]
            else:
                continue        # ambíguo e nenhum serve: sinaliza pelo par explícito
        elif re.match(rf"{_QUAL}\s+(?:com|ao?s?|às?|entre)\s", depois):
            # A frase TEM preposição de aspecto, mas o que vem depois não é
            # um corpo reconhecido ("a quadratura aos Nodos"). Cair na regra
            # genérica aqui INVENTA um par: foi assim que "a conjunção com
            # Júpiter e Mercúrio, a quadratura aos Nodos, o sextil com
            # Plutão" virou "Mercúrio quadratura Plutão" (Helena, 18/07).
            # Sem par confiável, não acusa.
            continue
        elif _par_antes(antes):
            # PAR ANTES DO ASPECTO "Vênus-Quíron em sextil" (19/07). É a notação
            # mais explícita que existe: os DOIS corpos vêm antes do aspecto.
            # A regra genérica quebrava na frase encadeada
            #   "O Vênus-Quíron em sextil e o Saturno-Quíron em oposição"
            # — pegava o Quíron do primeiro par e o Saturno do segundo, e
            # acusava "Quíron sextil Saturno" numa frase inteiramente certa
            # (Helena, 19/07; o par real é Saturno-Quíron em OPOSIÇÃO, e a
            # frase diz exatamente isso).
            # A forma SEM hífen é igualmente explícita e apareceu no Lucca
            # (19/07): "Mercúrio e Marte em trígono, Quíron e Plutão em
            # sextil" — frase certa — virava "Marte trígono Quíron", com o
            # Marte do primeiro par e o Quíron do segundo.
            _h = _par_antes(antes)
            cand = [_h.group(1), _h.group(2)]
        elif c_antes and c_depois:
            cand = [c_antes[-1].group(1), c_depois[0].group(1)]
        if len(cand) != 2:
            continue
        k1 = _PT2KEY.get(re.sub(r"\s+", " ", cand[0].strip().lower()))
        k2 = _PT2KEY.get(re.sub(r"\s+", " ", cand[1].strip().lower()))
        if not k1 or not k2 or k1 == k2:
            continue
        reais = {(o, t) for o, t in _aspectos_reais_de(chart, k1)}
        se_aspectam = [t for o, t in reais if o == k2]
        if not se_aspectam:
            # nenhum aspecto entre os dois
            lista = _lista_aspectos_pt(chart, k1) or "nenhum"
            out.append({"kind": "fato:aspecto_inexistente",
                        "match": f"{cand[0]} {m.group(1)} {cand[1]}"[:70],
                        "offset": m.start(),
                        "suggestion": (f"{cand[0]} e {cand[1]} NÃO formam aspecto "
                                       f"neste mapa. Os aspectos reais de {cand[0]} "
                                       f"são: {lista}. Reescrever a partir desta "
                                       f"lista ou remover a afirmação — nunca "
                                       f"inventar o aspecto.")})
        elif tipo not in se_aspectam:
            certo = ", ".join(sorted({_pt_do_tipo(t) for t in se_aspectam}))
            out.append({"kind": "fato:aspecto_tipo_errado",
                        "match": f"{cand[0]} {m.group(1)} {cand[1]}"[:70],
                        "offset": m.start(),
                        "suggestion": (f"entre {cand[0]} e {cand[1]} a tabela deste "
                                       f"mapa mostra {certo}, não {m.group(1)}. "
                                       f"Corrigir para {certo}.")})
    return out


def _pt_do_tipo(tipo_en):
    for pt, en in _ASPECTO_PT.items():
        if en == tipo_en:
            return pt
    return tipo_en


def _lista_aspectos_pt(chart, key):
    partes = []
    for outro, tipo in _aspectos_reais_de(chart, key):
        nome = next((k for k, v in _PT2KEY.items() if v == outro), outro)
        partes.append(f"{_pt_do_tipo(tipo)} com {nome}")
    return "; ".join(partes)


def _detect_angle_claims(text, chart):
    """CORPO SOBRE ÂNGULO — dito de qualquer forma.

    Generalizado a partir do CONCEITO, não do exemplo (18/07). A versão
    anterior só conhecia nomes de ângulo ("meio do céu", "Ascendente") e
    passou batido em "na cúspide da casa 10", que era exatamente o defeito
    real. O conceito é: o texto afirma que um corpo está SOBRE um ângulo.

    Os quatro ângulos e todas as formas de nomeá-los:
      casa 1  = Ascendente, Asc, AC
      casa 4  = fundo do céu, Imum Coeli, IC
      casa 7  = Descendente, Desc, DC
      casa 10 = meio do céu, Medium Coeli, MC
    E as formas de dizer "sobre": na/no, sobre, junto a, colado a, conjunto
    a, em conjunção com, na cúspide de.
    """
    out = []
    ch = chart or {}
    # nome do ângulo -> (chave no chart, número da casa)
    ANG = {
        r"ascendente|\basc\b|\bac\b": ("ascendant", 1),
        r"fundo\s+do\s+céu|imum\s+coeli|\bic\b": (None, 4),
        r"descendente|\bdesc\b|\bdc\b": (None, 7),
        r"meio[\s-]do[\s-]céu|medium\s+coeli|\bmc\b": ("midheaven", 10),
    }
    SOBRE = (r"(?:n[ao]s?|sobre|junto\s+[àa]o?|colad[ao]\s+[àa]o?|"
             r"conjunt[ao]\s+[àa]o?|em\s+conjunção\s+com|encostad[ao]\s+[àa]o?)"
             # artigo opcional: "sobre O MC", "junto AO fundo do céu"
             r"\s+(?:[oa]s?\s+)?(?:cúspide\s+d[oa]\s+)?")
    for regex, (chave, casa_num) in ANG.items():
        # forma 1: nome do ângulo
        pat1 = rf"{SOBRE}(?:{regex})"
        # forma 2: "cúspide da casa N" com o N do ângulo.
        #
        # "na casa 7" NÃO é conjunção ao Descendente (19/07) — é a maneira
        # ordinária de dizer que o corpo está DENTRO da casa 7. Enquanto
        # `n[ao]s?` entrava aqui, "Netuno em Peixes está na casa 7" era lido
        # como "Netuno sobre a cúspide da casa 7" e acusado por divergência
        # de signo. Para a forma "casa N" a conjunção precisa ser explícita:
        # ou a palavra "cúspide", ou uma preposição que só significa
        # conjunção ("sobre", "junto à", "colado à"…) — nunca "na".
        _CONJ = (r"sobre|junto\s+[àa]o?|colad[ao]\s+[àa]o?|conjunt[ao]\s+[àa]o?|"
                 r"em\s+conjunção\s+com|encostad[ao]\s+[àa]o?")
        pat2 = (rf"(?:(?:n[ao]s?|{_CONJ})\s+(?:[oa]s?\s+)?cúspide\s+d[oa]\s+"
                rf"|(?:{_CONJ})\s+(?:[oa]s?\s+)?)casa\s+{casa_num}\b")
        for pat in (pat1, pat2):
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                nome, trecho = _corpo_mais_proximo_antes(text, m.start())
                if not nome:
                    continue
                key = _PT2KEY.get(nome)
                if not key:
                    continue
                p = (ch.get("points") or {}).get(key)
                if not p:
                    continue
                # signo do ângulo: do chart quando existe, senão da cúspide
                alvo = ch.get(chave) if chave else None
                if not alvo:
                    alvo = (ch.get("cusps") or {}).get(str(casa_num))
                if not alvo:
                    continue
                if str(p.get("sign", "")).lower() == str(alvo.get("sign", "")).lower():
                    continue          # mesmo signo: plausível, não acusa
                rot = {1: "Ascendente", 4: "fundo do céu", 7: "Descendente",
                       10: "meio do céu"}[casa_num]
                out.append({"kind": "fato:angulo_errado",
                            "match": ((trecho or "") + m.group(0))[:70],
                            "offset": m.start(),
                            "suggestion": (f"{nome} está em {p.get('sign_pt')} e o "
                                           f"{rot} (cúspide da casa {casa_num}) em "
                                           f"{alvo.get('sign_pt')} — não estão juntos. "
                                           f"REMOVER a afirmação de conjunção ao "
                                           f"ângulo; não trocar por outro ângulo.")})
    return out



# REGÊNCIA: moderna como principal, tradicional como corregente — é como a
# Márcia lê (confirmado 17/07). Plutão rege Escorpião com Marte corregente;
# Urano rege Aquário com Saturno; Netuno rege Peixes com Júpiter. Só acusa
# quando a afirmação está errada nos DOIS sistemas.
_REGENCIA = {
    "aries": {"mars"}, "áries": {"mars"},
    "taurus": {"venus"}, "touro": {"venus"},
    "gemini": {"mercury"}, "gêmeos": {"mercury"},
    "cancer": {"moon"}, "câncer": {"moon"},
    "leo": {"sun"}, "leão": {"sun"},
    "virgo": {"mercury"}, "virgem": {"mercury"},
    "libra": {"venus"},
    "scorpio": {"pluto", "mars"}, "escorpião": {"pluto", "mars"},
    "sagittarius": {"jupiter"}, "sagitário": {"jupiter"},
    "capricorn": {"saturn"}, "capricórnio": {"saturn"},
    "aquarius": {"uranus", "saturn"}, "aquário": {"uranus", "saturn"},
    "pisces": {"neptune", "jupiter"}, "peixes": {"neptune", "jupiter"},
}


def _pares_regencia(text):
    """(regente, alvo, offset) para cada AFIRMAÇÃO de regência no texto.

    LEVANTAMENTO DE FORMAS (19/07). A versão anterior ancorava no verbo
    "rege" e assumia sempre regente-antes / alvo-depois. Cobria uma forma
    só. A frase real do Lucca — "O Nodo Norte em Libra tem Marte como seu
    regente" — não acendia, e a Márcia perguntou se era o Nodo sendo
    tratado diferente. Não era: é a FORMA. Nesta construção os papéis se
    invertem, o alvo vem antes e o regente depois, e a palavra é "regente",
    que o regex do verbo nem alcançava.

    Terceira vez hoje que um detector cobre o exemplo e não a classe (as
    outras: formas de aspecto, adjetivos de signo/planeta).
    """
    C = _CORPO_RE
    A = r"(?:[oa]s?\s+|seus?\s+|suas?\s+)?"
    S = r"(?:\s+em\s+[A-Za-zÀ-ÿ]+)?"          # "em Libra" opcional
    FORMAS = [
        # (padrão, grupo do REGENTE, grupo do ALVO)
        (rf"\b({C})\b\s+reg(?:e|em)\s+{A}({C})\b", 1, 2),
        (rf"\b({C})\b{S}\s+(?:é|são|está)\s+regid[oa]s?\s+por\s+{A}({C})\b", 2, 1),
        (rf"\b({C})\b{S}\s+tem\s+{A}({C})\b\s+como\s+{A}regente", 2, 1),
        (rf"\bregente\s+d[eo]\s+{A}({C})\b{S}\s+(?:é|:)\s*{A}({C})\b", 2, 1),
        (rf"\bregência\s+d[eo]\s+{A}({C})\b{S}\s+(?:é|cabe\s+a|pertence\s+a)\s*"
         rf"{A}({C})\b", 2, 1),
        (rf"\b({C})\b,\s+regente\s+d[eo]\s+{A}({C})\b", 1, 2),
        (rf"\bregente\s+d[eo]\s+{A}({C})\b,\s+{A}({C})\b", 2, 1),
    ]
    vistos = set()
    for pat, gr, ga in FORMAS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            chave = (m.start(), m.end())
            if chave in vistos:
                continue
            vistos.add(chave)
            yield m.group(gr), m.group(ga), m.start(), m.group(0)


def _detect_rulership(text, chart):
    """Regência AFIRMADA, conferida contra o signo em que o alvo está.

    Caso real (Lucca): "Marte rege o Nodo Norte" — o Nodo Norte está em
    Libra, regido por Vênus; Marte rege o Nodo Sul, que está em Áries.
    """
    out = []
    pontos = (chart or {}).get("points") or {}
    if not pontos:
        return out
    for reg_nome, alvo_nome, pos, trecho in _pares_regencia(text):
        reg = _PT2KEY.get(re.sub(r"\s+", " ", (reg_nome or "").strip().lower()))
        alvo = _PT2KEY.get(re.sub(r"\s+", " ", (alvo_nome or "").strip().lower()))
        if not reg or not alvo or reg == alvo or alvo not in pontos:
            continue
        signo = str(pontos[alvo].get("sign", "")).lower()
        validos = _REGENCIA.get(signo)
        if validos and reg not in validos:
            certo = ", ".join(sorted(validos))
            out.append({"kind": "fato:regencia_errada",
                        "match": trecho[:70],
                        "offset": pos,
                        "suggestion": (f"{alvo_nome} está em "
                                       f"{pontos[alvo].get('sign_pt')}, regido por "
                                       f"{certo} — não por {reg_nome}. Corrigir para "
                                       f"{certo}.")})
    return out


# ============================================================
# spell_lint — CAMADA DE ORTOGRAFIA (ideia da Márcia, 17/07)
# ============================================================
# Mesmo padrão de pdf_lint / repetition_lint: asserção sobre o ARTEFATO,
# reportada no meta. MODO FLAG-ONLY: não reescreve nada. A whitelist
# (domain_lexicon.txt) cresce rodando sobre relatórios já limpos; só depois
# disso spell_lint: [] entra como gate ao lado dos outros dois.
#
# DIVISÃO DE TRABALHO (documentada no ESTADO):
#   · spell_lint pega o que NÃO É PALAVRA: mutable, orgullo, saturina,
#     reencuadrar.
#   · O GLOSSÁRIO DE SIGNO pega o que É palavra mas está errado no domínio:
#     "virgiliana" existe em português (de Virgílio) — nenhum corretor a
#     acusaria. Só a lista fechada da Márcia a pega.
# Uma camada não substitui a outra.

_DOMAIN_WORDS = None


def _load_domain_lexicon():
    global _DOMAIN_WORDS
    if _DOMAIN_WORDS is not None:
        return _DOMAIN_WORDS
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "domain_lexicon.txt")
    words = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    words.add(line)
    except Exception as e:
        logger.warning("domain_lexicon.txt não carregou: %s", e)
    _DOMAIN_WORDS = words
    return words


def spell_lint(report_text, chart=None, max_report=60):
    """Palavras fora do dicionário E fora do dicionário de domínio.

    LIMITAÇÃO CONHECIDA (17/07): o dicionário "pt" do pyspellchecker é
    português EUROPEU. Ele desconhece formas brasileiras corretas
    ("contato", "bônus", "perspectiva", "harmônico") e enclíticas comuns
    ("conhecê-lo", "sustentá-la"). Por isso este lint é FLAG-ONLY e nunca
    alimenta reescrita — o detector que fazia isso (2e) foi desligado
    depois de reescrever pt-BR em pt-PT no relatório da Helena.
    Quem faz o trabalho de verdade são as listas explícitas e os glossários
    fechados. Este lint serve como rede para palavra inventada; a whitelist
    (domain_lexicon.txt) precisa crescer bastante antes de virar gate.

    Retorna lista de {word, count, sample} — flag-only. Nomes próprios do
    payload (nome do cliente, cidade) são excluídos.
    """
    out = []
    try:
        from spellchecker import SpellChecker
    except ImportError:
        return [{"error": "pyspellchecker não instalado — spell_lint inativo"}]
    try:
        sp = SpellChecker(language="pt")
    except Exception as e:
        return [{"error": f"dicionário pt indisponível: {e}"}]

    domain = _load_domain_lexicon()
    proper = set()
    for key in ("name", "birth_city"):
        val = (chart or {}).get(key) or ""
        for tok in re.findall(r"[\wÀ-ÿ]+", str(val)):
            proper.add(tok.lower())
    # Palavras iniciadas por maiúscula no meio da frase = provável nome
    # próprio; contadas à parte para não poluir a primeira rodada.
    text = _mask_fixed_templates(report_text)
    tokens = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-']{2,}", text)
    counts, samples = {}, {}
    for tok in tokens:
        low = tok.lower().strip("-'")
        if not low or low in domain or low in proper:
            continue
        if tok[0].isupper():
            continue                       # nome próprio / início de frase
        if not sp.unknown([low]):
            continue
        counts[low] = counts.get(low, 0) + 1
        if low not in samples:
            i = text.lower().find(low)
            samples[low] = text[max(0, i - 40):i + len(low) + 40].replace("\n", " ")
    for w in sorted(counts, key=lambda w: -counts[w])[:max_report]:
        out.append({"word": w, "count": counts[w], "sample": samples[w]})
    return out



# ============================================================
# rare_word_lint — VOCABULÁRIO REBUSCADO (pedido da Márcia, 17/07)
# ============================================================
# A leitora (Marcelle) precisou parar e buscar palavras mais de uma vez.
# Régua da Márcia: proibido o que ninguém usaria FALANDO; mantido o que soa
# elevado mas é corrente. Caso confirmado: "guarecer" fora, "arrefecer"
# dentro.
#
# MODO FLAG-ONLY, como o spell_lint: reporta no meta, não reescreve. A
# Márcia tria; o que for banido vai para _RARE_BANNED (que é reescrito),
# o que for aprovado vai para domain_lexicon.txt.
#
# LIMITAÇÃO MEDIDA (a mesma do spell_lint): a lista de frequência do
# pyspellchecker é de português EUROPEU e tem buracos. Ela acerta o caso
# da Márcia — guarecer 0.00/milhão contra arrefecer 4.23 — mas dá ZERO a
# palavras correntes como "pertencimento", "autoconhecimento", "sutil" e
# "epifania". Por isso: (a) o domain_lexicon é aplicado antes, (b) o lint
# reporta a frequência ao lado de cada palavra, para a triagem ser sobre
# dado e não sobre a minha opinião, (c) nunca vira gate sozinho.
RARE_PER_MILLION = 0.5      # abaixo disto = candidata a rebuscada
# Sufixos de flexão que o corpus europeu não cobre: derivados regulares de
# palavras comuns. Verificados contra a RAIZ — se a raiz é conhecida, a
# flexão não é rebuscada. Também estrutural, não lista.
_RARE_SUFIXOS = ("mente", "ção", "ções", "dade", "dades", "ismo", "ista",
                 "istas", "vel", "veis", "oso", "osa", "osos", "osas")
RARE_MIN_LEN = 6            # palavras curtas raramente são rebuscadas

# Banidas explicitamente pela Márcia — estas SÃO reescritas.
_RARE_BANNED = {
    "guarecer": "abrigar / proteger",
    "lilitiana": "a energia de Lilith",
    "subvalorado": "subestimado / desvalorizado",
}

_RARE_FREQ = None


def _rare_freq():
    global _RARE_FREQ
    if _RARE_FREQ is not None:
        return _RARE_FREQ
    try:
        from spellchecker import SpellChecker
        wf = SpellChecker(language="pt").word_frequency
        _RARE_FREQ = (wf.dictionary, wf.total_words)
    except Exception as e:
        logger.warning("rare_word_lint indisponível: %s", e)
        _RARE_FREQ = ({}, 0)
    return _RARE_FREQ


RARE_LINT_ENABLED = False   # DESLIGADO em 17/07 — ver docstring


def rare_word_lint(report_text, chart=None, max_report=40):
    """Palavras de baixa frequência — candidatas a rebuscadas.

    DESLIGADO (17/07). Duas tentativas de torná-lo útil falharam:
      1. whitelist do vocabulário corrente → a lista seguinte veio MAIOR
         (69 → 78), porque o texto muda a cada geração;
      2. ignorar enclíticos e compostos por REGRA → limpou a morfologia
         gerativa, mas sobraram 272 palavras com 3% de sinal.
    A causa é o corpus: o dicionário "pt" do pyspellchecker é europeu e não
    conhece vocabulário brasileiro comum (afetivo, receptiva, decepção,
    acadêmica, ingênuo, transitar, resiliência). Não é lacuna preenchível.

    O que FUNCIONA é o léxico explícito, com a forma correta na sugestão:
    guarecer, asteróide, equilibrio, mainstream, cluster, activar, lilitiana,
    subvalorado — todos pegos por lista, nenhum por frequência.

    A função fica para quem quiser reativar com um dicionário pt-BR de
    verdade (hunspell tem pt_BR). `RARE_LINT_ENABLED = True` a religa.

    Retorna [{word, per_million, count, sample}] ordenado da mais rara para
    a menos rara. Flag-only.
    """
    if not RARE_LINT_ENABLED:
        return []
    freq, total = _rare_freq()
    if not total:
        return [{"error": "lista de frequência indisponível"}]
    domain = _load_domain_lexicon()
    proper = set()
    for key in ("name", "birth_city"):
        for tok in re.findall(r"[\wÀ-ÿ]+", str((chart or {}).get(key) or "")):
            proper.add(tok.lower())
    text = _mask_fixed_templates(report_text)
    vistos, amostra = {}, {}
    for m in re.finditer(rf"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-']{{{RARE_MIN_LEN - 1},}}", text):
        w = m.group(0)
        low = w.lower().strip("-'")
        if low in domain or low in proper or w[0].isupper():
            continue
        # RUÍDO ESTRUTURAL, ignorado por REGRA e não por lista (17/07).
        # O corpus é europeu e não tem formas GERATIVAS do português:
        #   · enclíticos e mesóclitos — qualquer verbo + pronome forma uma
        #     palavra nova (habitá-la, dizê-lo, articulá-los, recebê-las).
        #     Nenhuma whitelist alcança: a primeira rodada whitelistou 64
        #     palavras e a lista seguinte veio MAIOR (69 → 78).
        #   · compostos com hífen (bem-estar, boa-fé, matéria-prima).
        # Estas formas nunca são "vocabulário rebuscado" — são morfologia.
        if "-" in low:
            continue
        pm = 1e6 * freq.get(low, 0) / total
        if pm >= RARE_PER_MILLION:
            continue
        # Derivado regular de palavra comum? Se a RAIZ é frequente, o
        # derivado não é rebuscado — é morfologia que o corpus não listou
        # ("cronicamente" de "crônico", "assertividade" de "assertivo").
        _raiz = None
        for suf in _RARE_SUFIXOS:
            if low.endswith(suf) and len(low) - len(suf) >= 4:
                _raiz = low[:-len(suf)]
                break
        if _raiz:
            _cands = (_raiz, _raiz + "o", _raiz + "a", _raiz + "e",
                      _raiz + "co", _raiz + "ca", _raiz + "vo", _raiz + "va")
            if any(1e6 * freq.get(c, 0) / total >= RARE_PER_MILLION for c in _cands):
                continue
        vistos[low] = vistos.get(low, 0) + 1
        if low not in amostra:
            i = m.start()
            amostra[low] = text[max(0, i - 45):i + len(w) + 45].replace("\n", " ")
    out = []
    for w in sorted(vistos, key=lambda w: (1e6 * freq.get(w, 0) / total, -vistos[w])):
        out.append({"word": w, "count": vistos[w],
                    "per_million": round(1e6 * freq.get(w, 0) / total, 2),
                    "banned": w in _RARE_BANNED,
                    "sample": amostra[w]})
    return out[:max_report]
