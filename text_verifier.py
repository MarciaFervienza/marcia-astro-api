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
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                if validator is not None and not validator(text, m):
                    continue
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

    # 4b/4c — afirmações sobre cúspides validadas contra a tabela real
    try:
        for cd in _validate_cusp_claims(text, chart):
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
