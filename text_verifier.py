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
# DETECTORES SEMÂNTICOS — rodada 17/07 (doutrina da Márcia)
# ============================================================
_SIGN_NAMES_PT = ("Áries|Aries|Touro|Gêmeos|Gemeos|Câncer|Cancer|Leão|Leao|"
                  "Virgem|Libra|Escorpião|Escorpiao|Sagitário|Sagitario|"
                  "Capricórnio|Capricornio|Aquário|Aquario|Peixes")
_TRANSPESSOAIS = ("Urano", "Netuno", "Plutão", "Plutao")


def _detect_netuno_plutao_mention(text):
    """Item 7: o sextil geracional Netuno-Plutão NUNCA é mencionado.

    Todo mundo nascido em ~1940-2035 tem esse aspecto: ele não diferencia
    ninguém e ocupa espaço que pertence ao mapa da pessoa. Doutrina da
    Márcia, 17/07: menção = violação.
    """
    out = []
    pat = (r"\b(?:Netuno|Neptuno)\b[^.!?]{0,80}?\b(?:Plutão|Plutao)\b"
           r"|\b(?:Plutão|Plutao)\b[^.!?]{0,80}?\b(?:Netuno|Neptuno)\b")
    for m in re.finditer(pat, text):
        # A palavra do aspecto costuma vir ANTES dos dois nomes ("O sextil
        # entre Netuno e Plutão") — olhar só o trecho entre eles não a
        # encontra. Avalia a FRASE inteira que contém o par.
        ini = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        fim = min([p for p in (text.find(".", m.end()), text.find("\n", m.end()))
                   if p != -1] or [len(text)])
        seg = text[ini:fim].strip()
        if re.search(r"sextil|trígono|trigono|quadratura|oposição|oposicao|"
                     r"conjunção|conjuncao|aspecto", seg, flags=re.IGNORECASE):
            out.append({"kind": "geracional:netuno_plutao", "match": seg[:70],
                        "offset": m.start(),
                        "suggestion": ("REMOVER a menção ao aspecto Netuno-Plutão: é "
                                       "geracional, comum a toda a geração, e não diz "
                                       "nada sobre esta pessoa. Reescrever a frase sem "
                                       "ele, preservando o resto do sentido.")})
    return out


def _detect_sign_as_generational_agent(text):
    """Item 8: SIGNO não carrega nada para uma geração — planeta transpessoal
    em signo, sim.

    "O signo de Escorpião carrega, para toda a sua geração, …" está errado:
    todo mundo tem Escorpião em algum lugar do mapa. O que marca geração é
    Plutão/Netuno/Urano EM Escorpião. Flagra 'signo + linguagem geracional'
    quando não há planeta transpessoal na mesma frase.
    """
    out = []
    ger = (r"gera(?:ção|cao|cional|cionais)|coorte|toda\s+uma\s+gera|"
           r"sua\s+gera|pessoas\s+nascidas")
    for m in re.finditer(rf"[^.!?]*\b(?:{_SIGN_NAMES_PT})\b[^.!?]*", text):
        seg = m.group(0)
        if not re.search(ger, seg, flags=re.IGNORECASE):
            continue
        if any(p.lower() in seg.lower() for p in _TRANSPESSOAIS):
            continue                      # tem o planeta: construção correta
        if not re.search(r"\bo\s+signo\b|\bsigno\s+de\b|\b(?:carrega|marca|"
                         r"define|descreve|traz)\b", seg, flags=re.IGNORECASE):
            continue
        out.append({"kind": "geracional:signo_como_agente", "match": seg.strip()[:70],
                    "offset": m.start(),
                    "suggestion": ("signo não carrega nada para uma geração — quem "
                                   "marca geração é o PLANETA TRANSPESSOAL no signo. "
                                   "Reescrever como 'o que Plutão/Netuno/Urano em "
                                   "<signo> carrega' ou remover a moldura geracional.")})
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
    pat = (r"\b(Sol|Lua|Mercúrio|Mercurio|Vênus|Venus|Marte|Júpiter|Jupiter|"
           r"Saturno|Urano|Netuno|Plutão|Plutao|Quíron|Quiron|Lilith|Ceres|"
           r"Palas|Pallas|Juno|Vesta)\b[^.!?]{0,90}?"
           r"(não\s+(?:faz|forma|recebe|tem|possui)\s+(?:nenhum\s+)?aspecto|"
           r"sem\s+(?:nenhum\s+)?aspecto|nenhum\s+aspecto)")
    for m in re.finditer(pat, text, flags=re.IGNORECASE):
        body_pt = m.group(1).lower()
        key = _PT_BODY_TO_KEY.get(body_pt)
        if key and key in aspected:
            out.append({"kind": "aspecto:falsa_ausencia", "match": m.group(0)[:70],
                        "offset": m.start(),
                        "suggestion": (f"o texto afirma que {m.group(1)} não tem aspectos, "
                                       f"mas a tabela deste mapa LISTA aspectos para ele. "
                                       f"REMOVER a afirmação de ausência (ou reescrever "
                                       f"para 'poucos aspectos', se for o caso).")})
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


def detect_crutch_words(report_text):
    """Palavra-muleta: mesma palavra repetida acima do limiar numa seção."""
    out = []
    blocos = re.split(r"\n##\s+", report_text)
    for blk in blocos[1:]:
        titulo, _, corpo = blk.partition("\n")
        for nome, pat in _CRUTCH_CANDIDATES:
            n = len(re.findall(pat, corpo, flags=re.IGNORECASE))
            if n > CRUTCH_PER_SECTION_LIMIT:
                out.append({"section": titulo.strip(), "word": nome, "count": n,
                            "limit": CRUTCH_PER_SECTION_LIMIT})
    return out


# ============================================================
# 2b — PADRÕES DE NEGAÇÃO-SUBSTITUIÇÃO
# ============================================================
# Alguns padrões são frouxos por design — o "é" ou "mas" completa a estrutura.
# Todos foram calibrados pra evitar match em construções legítimas.
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
    (r"[^.,;:!?]{5,60},\s+e\s+não\s+[a-záéíóúãõçâêôà]", "y_e_nao_x"),
    # ---- variantes do inventário 17/07 (balde 1: nega E substitui) ----
    # "não é X — é Y" / "não são X — são Y" / "não é X; é Y"
    (r"\bnão\s+(?:é|são)\s+[^.,;:!?]{1,60}[;—–]\s*(?:é|são)\b", "nao_e_x_e_y_pausa"),
    # "não como X, mas como Y"
    (r"\bnão\s+como\s+[^.,;:!?]{1,50},?\s*mas\s+como\b", "nao_como_x_mas_como_y"),
    # "Y — e não X" (a variante com travessão da y_e_nao_x)
    (r"[^.;:!?]{5,60}\s+[—–]\s*e\s+não\s+[a-záéíóúãõçâêôà]", "y_travessao_e_nao_x"),
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
        rf"\b({numbers_alt})\s+({countables_alt})\s*[—:–-]\s*([^.!?\n]+)",
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
    return call_claude_fn(prompt, max_tokens=500).strip()


# ============================================================
# ORQUESTRADOR
# ============================================================
def run_verifier(text, chart, call_claude_fn):
    """Roda todas as detecções, agrupa por frase, reescreve as frases
    afetadas (até 2 tentativas), retorna (texto_corrigido, log_de_violações).
    Nunca levanta — falha em qualquer detector é logada e o resto segue."""
    if not text:
        return text, []

    # Texto fixo de template aprovado não entra em nenhum scan (offsets
    # preservados — a máscara troca por espaços do mesmo comprimento).
    scan_text = _mask_fixed_templates(text)

    violations_all = []  # cada item: {"kind","match","offset","suggestion","sentence_idx"}

    def _add(kind, match_text, offset, suggestion=""):
        violations_all.append({
            "kind": kind, "match": match_text[:120],
            "offset": offset, "suggestion": suggestion,
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
        for pat, cat in _NEGATION_SUBSTITUTION_PATTERNS:
            for m in re.finditer(pat, scan_text, flags=re.IGNORECASE):
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
    try:
        for w, offset, sug in _detect_unknown_words(scan_text, chart):
            _add("spell:palavra_desconhecida", w, offset,
                 f"palavra fora do dicionário pt-BR; sugestão do corretor: '{sug}'")
    except Exception as e:
        logger.warning("verifier 2e failed: %s", e)

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
                    (_detect_bare_ic, "ic_sozinho")):
        try:
            for v in fn(scan_text):
                _add(v["kind"], v["match"], v["offset"], v["suggestion"])
        except Exception as e:
            logger.warning("verifier %s failed: %s", tag, e)
    try:
        for v in _detect_false_no_aspect_claims(scan_text, chart):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
    except Exception as e:
        logger.warning("verifier falsa_ausencia failed: %s", e)
    try:
        for v in _detect_clitic_third_person(scan_text, (chart or {}).get("_voice")):
            _add(v["kind"], v["match"], v["offset"], v["suggestion"])
    except Exception as e:
        logger.warning("verifier clitico failed: %s", e)

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

    if not violations_all:
        return text, []

    # Agrupa violações por frase
    sentences = _split_sentences(text)
    per_sent = {}
    for v in violations_all:
        info = _sentence_for_offset(sentences, v["offset"])
        if info is None:
            continue
        idx, s, e, txt = info
        v["sentence_idx"] = idx
        per_sent.setdefault(idx, []).append(v)

    # Reescreve cada frase afetada (até 2 tentativas)
    # Aplicação de trás para frente pra preservar offsets
    corrected = text
    log_out = []
    for idx in sorted(per_sent.keys(), reverse=True):
        vs = per_sent[idx]
        s, e, orig_sent = sentences[idx]
        current = orig_sent
        succeeded = False
        last_violations = vs
        for attempt in range(1, 3):
            listing = [f"{v['kind']} — {v['match']!r} — {v['suggestion']}" for v in last_violations]
            try:
                rewritten = _rewrite_sentence(current, listing, call_claude_fn)
            except Exception as e:
                logger.warning("verifier: rewrite call failed (attempt %d): %s", attempt, e)
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
            out.append({"kind": "aspecto:par_incompleto", "match": match_text,
                        "offset": offset,
                        "suggestion": "aspecto precisa de dois corpos nomeados"})
    if "contagem" in kinds:
        for match_text, offset, exp, act in _detect_count_mismatch(sentence):
            out.append({"kind": "contagem:desbatida", "match": match_text,
                        "offset": offset,
                        "suggestion": f"anuncia {exp} mas enumera {act}"})
    if "spell" in kinds:
        for w, offset, sug in _detect_unknown_words(sentence, chart):
            out.append({"kind": "spell:palavra_desconhecida", "match": w,
                        "offset": offset,
                        "suggestion": f"desconhecida; sugestão '{sug}'"})
    if "voz" in kinds:
        for kind, match_text, offset, sugg in _detect_voice_violations(sentence, chart):
            out.append({"kind": kind, "match": match_text, "offset": offset,
                        "suggestion": sugg})
    for _pref in ("geracional", "glossario_signo", "jargao", "aspecto:falsa",
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
    """Palavras fora do dicionário pt-BR E fora do dicionário de domínio.

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

