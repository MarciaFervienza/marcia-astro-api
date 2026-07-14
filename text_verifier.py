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
_FORBIDDEN_LEXICON = [
    # (pattern, categoria, sugestão para o prompt de reescrita)
    (r"\bpertença\b", "erro_grafia_pertenca", "pertencimento"),
    (r"\bherida\b",    "erro_grafia_herida",   "ferida"),
    (r"\bdesiluso\b",  "erro_grafia_desiluso", "desilusão"),
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
]


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
        "corrija a contagem ou a enumeração para bater.\n\n"
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

    violations_all = []  # cada item: {"kind","match","offset","suggestion","sentence_idx"}

    def _add(kind, match_text, offset, suggestion=""):
        violations_all.append({
            "kind": kind, "match": match_text[:120],
            "offset": offset, "suggestion": suggestion,
        })

    # 2a — léxico proibido
    try:
        for pat, cat, sugg in _FORBIDDEN_LEXICON:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                _add(f"lexico:{cat}", m.group(0), m.start(), sugg)
    except Exception as e:
        logger.warning("verifier 2a failed: %s", e)

    # 2b — negação-substituição
    try:
        for pat, cat in _NEGATION_SUBSTITUTION_PATTERNS:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                _add(f"neg_subst:{cat}", m.group(0), m.start(),
                     "reescrever a frase afirmando diretamente, sem passar por 'não X'")
    except Exception as e:
        logger.warning("verifier 2b failed: %s", e)

    # 2c — nomenclatura de aspectos
    try:
        for match_text, offset in _detect_invalid_aspect_composition(text):
            _add("aspecto:composicao_invalida", match_text, offset,
                 "usar UM único nome de aspecto — a locução combina dois nomes indevidamente")
    except Exception as e:
        logger.warning("verifier 2c failed: %s", e)

    # 2d — contagem vs enumeração
    try:
        for match_text, offset, expected, actual in _detect_count_mismatch(text):
            _add("contagem:desbatida", match_text, offset,
                 f"o texto anuncia {expected} itens mas enumera {actual} — corrigir a contagem ou a enumeração")
    except Exception as e:
        logger.warning("verifier 2d failed: %s", e)

    # 2e — spellcheck
    try:
        for w, offset, sug in _detect_unknown_words(text, chart):
            _add("spell:palavra_desconhecida", w, offset,
                 f"palavra fora do dicionário pt-BR; sugestão do corretor: '{sug}'")
    except Exception as e:
        logger.warning("verifier 2e failed: %s", e)

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
            corrected = corrected[:s] + current + corrected[e:]
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
        for pat, cat, sugg in _FORBIDDEN_LEXICON:
            for m in re.finditer(pat, sentence, flags=re.IGNORECASE):
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
    return out
