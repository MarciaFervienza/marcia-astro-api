"""LINT DE PALAVRA — a camada que faltava (19/07).

O GPT achou sete corrupções que TODOS os lints existentes deixaram passar:

    Helena: "se voltarcontra você", "como você mente pensa"
    Lucca:  "fincar ancoras", "a rigidez saturninan gravada",
            "a retrogradiação de Saturno", "zona de conforto arianas",
            "a clareza de o que você pode dar"

DIAGNÓSTICO (19/07). A hipótese era corrupção de splice — a camada de
reescrita emendando com fronteira errada, quarta falha da mesma camada.
**Não se confirma.** Seis das sete estão no MEIO da frase, e o splice só
emenda em limite de frase (`corrected[:s] + current + trail +
corrected[e:]`, com s/e vindos de `_split_sentences`). A sétima coincide
com um início de frase, mas é palavra malformada, não duas coladas. E
nenhuma das sete aparece na saída registrada de cleanup, correction_rewrite
ou sign_divergence.

São erros de palavra gerados pelo modelo. Passaram porque não existia lint
de palavra: `pdf_lint` confere frase colada (`[a-z]\\.[A-Z]`), o léxico
confere termos enumerados, e nenhum dos dois olha a palavra em si.

POR QUE NÃO É O spell_lint DE VOLTA. O dicionário "pt" do pyspellchecker é
europeu, e como autoridade ORTOGRÁFICA está invertido: "harmônico" acende e
"contacto" passa (medido, 19/07). Aqui ele é usado só como ORÁCULO DE
EXISTÊNCIA para o teste de corte, onde as duas metades são iguais nas duas
variantes ("voltar", "contra"). Nenhuma regra deste arquivo pergunta ao
dicionário se uma grafia é a correta — só se a sequência existe.

Cada regra abaixo foi MEDIDA sobre os 2183 tokens únicos dos dois
relatórios antes de entrar.
"""
import os
import re
import unicodedata

_AQUI = os.path.dirname(os.path.abspath(__file__))

# Prefixos produtivos: sem eles, "autoafirmação", "autoexigência" e
# "contraintuitivo" viravam falso positivo na regra de palavra colada
# (medido: 3 falsos em 2183 tokens; com a lista, zero).
_PREFIXOS = {
    "auto", "contra", "anti", "sobre", "semi", "inter", "extra", "pos", "pré",
    "pre", "pós", "multi", "micro", "super", "sub", "infra", "hiper", "mini",
    "macro", "re", "co", "des", "in", "im", "não", "nao", "bem", "mal",
}

_sp = None
_dom = None


def _dicionario():
    """Carrega o dicionário e o léxico de domínio uma vez só."""
    global _sp, _dom
    if _dom is None:
        try:
            from spellchecker import SpellChecker
            _sp = SpellChecker(language="pt")
        except Exception:
            _sp = None
        _dom = set()
        try:
            with open(os.path.join(_AQUI, "domain_lexicon.txt"), encoding="utf-8") as fh:
                _dom = {l.strip().lower() for l in fh if l.strip()
                        and not l.startswith("#")}
        except OSError:
            pass
    return _sp, _dom


def _variantes_pt(w):
    """Grafias europeias da MESMA palavra brasileira.

    Sem isto, "harmônico", "contato" e "gênero" — corretos — cairiam como
    inexistentes, que é exatamente a inversão pela qual o spell_lint foi
    desligado. Se qualquer variante europeia é conhecida, a palavra existe.
    """
    v = {w}
    for a, b in (("ô", "ó"), ("ê", "é"), ("â", "á")):
        v |= {x.replace(a, b) for x in v}
    # PLURAIS (19/07, 2ª correção). A primeira versão só cobria singular:
    # `to\b` não alcança "conta{TOS}". Consequência medida — existe(
    # "contatos") era False, e por isso a regra de PALAVRA PARTIDA não via
    # "conta tos": ela exige que a JUNÇÃO seja palavra conhecida.
    for suf, eur in (("to", "cto"), ("ta", "cta"), ("ção", "cção"),
                     ("to", "pto"), ("ção", "pção"),
                     ("tos", "ctos"), ("tas", "ctas"), ("ções", "cções"),
                     ("tos", "ptos"), ("ções", "pções")):
        v.add(re.sub(rf"{suf}\b", eur, w))
    v.add(re.sub(r"tiv", "ctiv", w))
    return v


def existe(w):
    """A sequência existe em português (BR ou PT) ou no léxico de domínio?"""
    sp, dom = _dicionario()
    wl = w.lower()
    if wl in dom:
        return True
    if sp is None:
        return True          # sem dicionário, não inventa acusação
    return any(x in sp for x in _variantes_pt(wl))


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def _dist1(a, b):
    """Distância de edição ≤ 1 (inserção, remoção ou troca)."""
    if abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    curto, longo = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    pulou = False
    while i < len(curto) and j < len(longo):
        if curto[i] != longo[j]:
            if pulou:
                return False
            pulou = True
            j += 1
            continue
        i += 1
        j += 1
    return True


# ---------------------------------------------------------------
# R1 — PALAVRA COLADA
# "se voltarcontra você". Token que não existe mas corta em dois que
# existem. Medido: 1 acusação em 2183 tokens, e é a verdadeira.
# ---------------------------------------------------------------
# Sufixos produtivos que TAMBÉM são palavra solta. Sem esta lista,
# "gradualidade" (pt-BR correto, ausente do dicionário europeu) era cortada
# em "gradual" + "idade" e acusada como palavra colada — falso positivo
# medido nos 5 mapas de QA, 19/07.
_SUFIXOS = {"idade", "mente", "ismo", "ista", "ente", "ante", "ação", "acao",
            "agem", "eiro", "eira", "oso", "osa", "ico", "ica", "ivo", "iva"}


def _corte(w):
    if len(w) < 8 or existe(w):
        return None
    for i in range(3, len(w) - 2):
        a, b = w[:i], w[i:]
        if len(b) < 3 or a in _PREFIXOS or _sem_acento(a) in _PREFIXOS:
            continue
        if b in _SUFIXOS or _sem_acento(b) in _SUFIXOS:
            continue
        if existe(a) and existe(b):
            return a, b
    return None


# ---------------------------------------------------------------
# R2 — CONTRAÇÃO QUE NÃO ACONTECEU
# "a clareza de o que você pode dar" → "do que". Preposição + artigo
# soltos onde o português obriga a contração.
# ---------------------------------------------------------------
_CONTRACOES = [
    (r"\bde\s+o\b", "do"), (r"\bde\s+a\b", "da"),
    (r"\bde\s+os\b", "dos"), (r"\bde\s+as\b", "das"),
    (r"\bde\s+ele\b", "dele"), (r"\bde\s+ela\b", "dela"),
    (r"\bde\s+eles\b", "deles"), (r"\bde\s+elas\b", "delas"),
    (r"\bde\s+isso\b", "disso"), (r"\bde\s+isto\b", "disto"),
    (r"\bde\s+aquilo\b", "daquilo"), (r"\bde\s+esse\b", "desse"),
    (r"\bde\s+essa\b", "dessa"), (r"\bde\s+este\b", "deste"),
    (r"\bde\s+esta\b", "desta"),
    # "de um" NÃO entra: "dum" não é obrigatório em pt-BR. Medido —
    # sozinho produzia 19 falsos positivos nos dois relatórios.
    (r"\bem\s+o\b", "no"), (r"\bem\s+a\b", "na"),
    (r"\bem\s+os\b", "nos"), (r"\bem\s+as\b", "nas"),
    (r"\bem\s+ele\b", "nele"), (r"\bem\s+ela\b", "nela"),
    (r"\bem\s+isso\b", "nisso"), (r"\bem\s+esse\b", "nesse"),
    (r"\bem\s+essa\b", "nessa"), (r"\bem\s+este\b", "neste"),
    (r"\bem\s+esta\b", "nesta"), (r"\bem\s+aquilo\b", "naquilo"),
    (r"\ba\s+o\b", "ao"), (r"\ba\s+os\b", "aos"),
    (r"\bpor\s+o\b", "pelo"), (r"\bpor\s+a\b", "pela"),
    (r"\bpor\s+os\b", "pelos"), (r"\bpor\s+as\b", "pelas"),
]


# ---------------------------------------------------------------
# R3 — QUASE-ACERTO DE PALAVRA DO DOMÍNIO
# "a rigidez saturninan gravada" (saturnina + n), "a retrogradiação de
# Saturno" (retrogradação). Palavra inexistente a UMA edição de um termo
# do vocabulário astrológico. O vocabulário é a autoridade — não o
# dicionário europeu, que desconhece as duas formas.
# ---------------------------------------------------------------
def _vocab_dominio():
    sp, dom = _dicionario()
    v = set(dom)
    try:
        import text_verifier as tv
        v |= {k.lower() for k in tv._SIGN_ADJECTIVE_ERRORS}
        v |= {x.lower() for x in tv._SIGN_ADJECTIVE_ERRORS.values()}
        v |= {k.lower() for k in tv._PLANET_ADJECTIVE_ERRORS if not k.endswith("2")}
        v |= {x.lower() for x in tv._PLANET_ADJECTIVE_ERRORS.values()}
    except Exception:
        pass
    return {w for w in v if len(w) >= 6}


# ---------------------------------------------------------------
# R4 — CONCORDÂNCIA DE ADJETIVO DE SIGNO/PLANETA
# "expandindo a zona de conforto arianas". O conjunto desses adjetivos é
# FECHADO e é meu — então a concordância deles é verificável sem análise
# sintática: adjetivo no plural logo depois de palavra no singular.
# ---------------------------------------------------------------
# Palavras funcionais: não são o núcleo com que o adjetivo concorda. Sem
# esta lista, "formas que você não reconheça como leoninas" acusava — o
# núcleo é "formas", plural, longe dali.
_FUNCIONAIS = {"como", "de", "em", "a", "o", "por", "que", "e", "ou", "mas",
               "ser", "são", "é", "com", "sem", "para", "entre", "sobre",
               "quando", "se", "já", "também", "mais", "menos", "muito",
               "tão", "bem", "mal", "não", "nem", "ainda", "sempre"}

_PLURAL_OK = {"seus", "suas", "dos", "das", "aos", "nas", "nos", "pelos",
              "pelas", "esses", "essas", "estes", "estas", "outros", "outras",
              "muitos", "muitas", "vários", "várias", "todos", "todas"}


def _adjetivos_fechados():
    v = set()
    try:
        import text_verifier as tv
        v |= {x.lower() for x in tv._SIGN_ADJECTIVE_ERRORS.values()}
        v |= {x.lower() for x in tv._PLANET_ADJECTIVE_ERRORS.values()}
    except Exception:
        pass
    # As formas corretas correntes que o glossário registra como destino.
    v |= {"ariano", "ariana", "taurino", "taurina", "geminiano", "geminiana",
          "canceriano", "canceriana", "leonino", "leonina", "virginiano",
          "virginiana", "libriano", "libriana", "escorpiano", "escorpiana",
          "sagitariano", "sagitariana", "capricorniano", "capricorniana",
          "aquariano", "aquariana", "pisciano", "pisciana"}
    return {w for w in v if w}



# ---------------------------------------------------------------
# R5 — ACENTO FALTANDO EM SUBSTANTIVO APÓS INFINITIVO
# "fincar ancoras" → "âncoras". O caso é traiçoeiro porque "ancoras"
# EXISTE (2ª pessoa de "ancorar") — nenhum dicionário acusa. O que
# denuncia é a posição: depois de um infinitivo vem objeto, não outro
# verbo conjugado.
#
# O critério que separa o joio: a forma ACENTUADA tem de ser MAIS
# frequente que a sem acento. Medido sobre 13.337 pares consecutivos dos
# dois relatórios — sem ele, 34 acusações ("olhar para" → "pará",
# "sentir fica" → "ficá"); com ele, 1 acusação, que é a verdadeira.
# ---------------------------------------------------------------
_NOMES_CORPO = {
    "sol", "lua", "mercúrio", "mercurio", "vênus", "venus", "marte",
    "júpiter", "jupiter", "saturno", "urano", "netuno", "plutão", "plutao",
    "quíron", "quiron", "lilith", "ceres", "palas", "pallas", "juno",
    "vesta", "ascendente", "descendente", "opera", "operar",
}
_ACENTOS = {"a": "áàâã", "e": "éê", "i": "í", "o": "óôõ", "u": "ú"}


def _com_acento(w):
    for i, c in enumerate(w):
        for o in _ACENTOS.get(c, ""):
            yield w[:i] + o + w[i + 1:]


def _acento_faltando(ant, w):
    """(ant, w) consecutivos: w perdeu um acento e virou outra palavra?"""
    sp, _ = _dicionario()
    if sp is None or len(ant) <= 3 or not ant.endswith(("ar", "er", "ir")):
        return None
    # "JúpitER", "obsERvar UranO", "poder opera": nome de corpo terminado em
    # -er/-ir NÃO é infinitivo. 5 dos 29 falsos positivos no cru eram isto.
    import re as _re2
    if _re2.sub(r"\s+", " ", ant.strip().lower()) in _NOMES_CORPO:
        return None
    if _re2.sub(r"\s+", " ", w.strip().lower()) in _NOMES_CORPO:
        return None
    if len(w) < 4 or not existe(w):
        return None
    f = sp.word_frequency[w]
    mel = [c for c in _com_acento(w) if existe(c) and sp.word_frequency[c] > f]
    return mel[0] if mel else None


# ---------------------------------------------------------------
# R6 — GÊNERO DO DETERMINANTE (19/07)
# "acende um inquietação que é quase física" (Lucca). A R4 só cobria
# adjetivos de signo/planeta; concordância de ARTIGO com substantivo comum
# não tinha regra — por isso passou.
#
# O gênero de um substantivo não está em lugar nenhum que eu possa
# consultar, mas as TERMINAÇÕES decidem com segurança: -ção, -são, -dade,
# -tude, -agem, -ice, -eza, -ência, -ância são femininas; -mento e -ismo
# masculinas. Medido no corpus: 1 acusação, a verdadeira, zero falso
# positivo nas duas direções.
# "a" e "o" SAÍRAM das listas (19/07, 2ª correção). Em "esbarrar numa ferida
# relacionada A PERTENCIMENTO" o "a" é PREPOSIÇÃO, não artigo — e a regra
# acusava concordância. Determinante ambíguo não serve para decidir gênero;
# os inequívocos bastam ("um inquietação", o caso real, segue pego).
_DET_M = r"(?:um|este|esse|aquele|seu|nosso|meu|algum|nenhum|todo)"
_DET_F = r"(?:uma|esta|essa|aquela|sua|nossa|minha|alguma|nenhuma|toda)"
_FIM_F = r"[a-zà-ÿ]+(?:ção|são|dade|tude|agem|ice|eza|ência|ância)"
_FIM_M = r"[a-zà-ÿ]+(?:mento|ismo)"
# Masculinos apesar da terminação feminina. Lista curta e explícita.
_EXCECOES_GENERO = {"coração", "quinhão", "verão"}


def _genero_errado(text):
    out = []
    for det, fim, g in ((_DET_M, _FIM_F, "feminino"), (_DET_F, _FIM_M, "masculino")):
        for m in re.finditer(rf"\b{det}\s+({fim})\b", text, flags=re.IGNORECASE):
            if m.group(1).lower() in _EXCECOES_GENERO:
                continue
            out.append((m, g))
    return out


# ---------------------------------------------------------------
# R7 — PALAVRA PARTIDA (19/07)
# "os conta tos cotidianos", "a autoe stima", "quase sempre manda tório".
# A R1 pega palavra COLADA; partida não tinha regra nenhuma — só o detector
# semântico via, e ele NÃO É DETERMINÍSTICO: achou "conta tos" numa rodada
# e não na seguinte. Esta é a rede determinística para a classe.
#
# Critério medido em 12 relatórios: acusa só quando a junção É palavra
# conhecida E pelo menos uma das metades NÃO é. A variante frouxa ("metade
# curta") dava 4 falsos positivos — "sobre tudo", "amor nem", "conversas
# sem", "perguntas sem", todos português correto.
_COMUNS_CURTAS = {
    "de", "da", "do", "a", "o", "e", "em", "um", "uma", "que", "se", "com",
    "por", "na", "no", "os", "as", "dos", "das", "ao", "aos", "mais", "meu",
    "seu", "sua", "ela", "ele", "tem", "ser", "ter", "foi", "era", "nos",
    "lhe", "me", "te", "já", "só", "não", "nem", "sem", "tudo", "isso",
}


def _partida(a, b):
    """(a, b) adjacentes são uma palavra partida? Devolve a junção."""
    if len(a) < 3 or len(b) < 2:
        return None
    if a.lower() in _COMUNS_CURTAS or b.lower() in _COMUNS_CURTAS:
        return None
    junta = (a + b).lower()
    if not existe(junta):
        return None
    if not (existe(a) and existe(b)):
        return junta                   # metade que não é palavra: partida
    # As DUAS metades são palavras — "conta"+"tos", "manda"+"tório". O que
    # separa de português correto ("sobre tudo", "amor nem") é FREQUÊNCIA.
    # Medido: nas partidas reais o min das frequências é ≤485; no correto o
    # menor é 6.709. O limiar de 1.000 fica no meio do vão.
    sp, _ = _dicionario()
    if sp is None:
        return None
    if min(sp.word_frequency[a.lower()], sp.word_frequency[b.lower()]) < 1000:
        return junta
    return None


def word_lint(text):
    """Todas as regras de palavra. Devolve lista de achados (flag-only)."""
    out = []

    def add(kind, match, offset, sug):
        out.append({"kind": kind, "match": match, "offset": offset,
                    "suggestion": sug})

    # R1
    for m in re.finditer(r"\b[a-zà-ÿ]{8,}\b", text, flags=re.IGNORECASE):
        w = m.group(0)
        if w[0].isupper():
            continue                       # nome próprio
        c = _corte(w.lower())
        if c:
            add("palavra:colada", w, m.start(),
                f"'{w}' não existe; são duas palavras coladas: "
                f"'{c[0]} {c[1]}'. Separar.")

    # R2
    for pat, certo in _CONTRACOES:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            add("palavra:contracao_faltando", m.group(0), m.start(),
                f"'{m.group(0)}' → '{certo}'. Em português a contração é "
                f"obrigatória aqui.")

    # R3 DESLIGADA (19/07, medida contra texto CRU).
    #
    # 19 acusações em 5 relatórios crus, TODAS falsas: rupturas, acessa,
    # trígonos, internaliza, saturnianos, gerencial, apertadíssimo,
    # registros. O critério — palavra a uma edição de termo do domínio —
    # não distingue erro de digitação de palavra brasileira ausente do
    # dicionário europeu. É o spell_lint outra vez, com outro nome.
    #
    # A medição anterior deu "2 acusações, ambas verdadeiras" porque rodou
    # sobre relatórios ENTREGUES, onde o verifier já havia reescrito os
    # falsos positivos. Corpus contaminado pelo efeito medido.
    # R4
    adjs = _adjetivos_fechados()
    plurais = "|".join(sorted({a + "s" for a in adjs}, key=len, reverse=True))
    if plurais:
        for m in re.finditer(rf"\b([a-zà-ÿ]+)\s+({plurais})\b", text,
                             flags=re.IGNORECASE):
            ant = m.group(1).lower()
            if ant.endswith("s") or ant in _PLURAL_OK or ant in _FUNCIONAIS:
                continue
            # SUJEITO COMPOSTO: "Vênus e Marte librianos" é plural legítimo —
            # o núcleo são os DOIS, não só o último. Sem isto era falso
            # positivo (medido no texto cru do carlos_ed, 19/07).
            _antes30 = text[max(0, m.start() - 32):m.start()]
            if re.search(r"\b[A-Za-zÀ-ÿ]{3,}\s+e\s+$", _antes30):
                continue
            add("palavra:concordancia_adjetivo", m.group(0), m.start(),
                f"'{m.group(2)}' está no plural e '{ant}' no singular. "
                f"Concordar: '{ant} {m.group(2)[:-1]}'.")

    # R6
    for m, g in _genero_errado(text):
        add("palavra:genero_determinante", m.group(0), m.start(),
            f"'{m.group(1)}' é {g} e o determinante não concorda. "
            f"Corrigir o determinante em '{m.group(0)}'.")

    # R7 — palavra partida, sobre os mesmos pares consecutivos da R5.
    _tk = [(mm.group(0), mm.start()) for mm in
           re.finditer(r"\b[a-zà-ÿ]+\b", text, flags=re.IGNORECASE)]
    for (a1, _p1), (b1, p2) in zip(_tk, _tk[1:]):
        j = _partida(a1, b1)
        if j:
            add("palavra:partida", f"{a1} {b1}", p2,
                f"'{a1} {b1}' é a palavra '{j}' partida ao meio. Juntar.")

    # R5 — varre PARES CONSECUTIVOS. re.finditer de dois tokens não serve:
    # ele não sobrepõe, então testar "fincar ancoras" dependia da paridade
    # dos tokens anteriores. Isso me deu uma medição errada antes.
    toks = [(mm.group(0), mm.start()) for mm in
            re.finditer(r"\b[a-zà-ÿ]+\b", text, flags=re.IGNORECASE)]
    for (a, _pa), (w, pw) in zip(toks, toks[1:]):
        cert = _acento_faltando(a.lower(), w.lower())
        if cert:
            add("palavra:acento_faltando", f"{a} {w}", pw,
                f"'{w}' aqui é substantivo e leva acento: '{cert}'. "
                f"Depois do infinitivo '{a}' vem objeto, não verbo conjugado.")

    return out
