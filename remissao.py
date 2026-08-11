"""REMISSÃO ENTRE SEÇÕES — dono, direção e teto.

POR QUE EXISTE (11/08, leitura da Marcelle — cliente, não astróloga).

Ela foi conferir uma remissão e o círculo fechou vazio. Os aspectos do Sol
da Helena nunca eram desenvolvidos em lugar nenhum: a tríade lista e adia,
a p.12 diz "já foram descritas", a p.15, a p.22 e a p.24 adiam de novo.
Cada seção aponta para outra e nenhuma entrega. Só quem CONFERE descobre —
e ela conferiu.

Três defeitos distintos, uma máquina só:

  1. DONO. Todo aspecto precisa de exatamente UMA seção que o desenvolve
     por inteiro. Sem dono declarado, "quem chega primeiro descreve" vira
     "todo mundo adia", porque adiar é sempre a saída mais barata para o
     modelo. Decisão da Márcia: como NÃO EXISTE seção do Sol, a seção
     Sol+Saturno é dona de TODOS os aspectos do Sol.

  2. DIREÇÃO. "Já foi explorada" apontando para seção POSTERIOR. O tempo
     verbal não pode ser escolhido por quem escreve a frase: quem sabe a
     ordem de impressão é o renderizador. A remissão vira MARCADOR, e o
     tempo é decidido na hora de imprimir.

  3. TETO. 13 remissões em 23 páginas. Remissão é reforço ("isto se liga
     ao que você leu em X"), não desculpa para não desenvolver. Uma por
     seção.

O marcador é `[[ref:nome_da_secao]]`. O modelo NÃO escreve "já vimos" nem
"veremos": escreve o marcador, e o renderizador põe a prosa certa.
"""
import re

# Marcador que o modelo emite. Tolerante a espaço, e o nome da seção é o
# `name` interno (abertura, lua, sol_saturno…), nunca o título — títulos
# mudam de redação e o marcador continuaria apontando para o texto antigo.
MARCADOR = re.compile(r"\[\[\s*ref\s*:\s*([a-z0-9_]+)\s*\]\]", re.IGNORECASE)

# CORPO → SEÇÃO DONA. Precedência por ordem desta lista: num aspecto entre
# dois corpos, ganha o que vier PRIMEIRO aqui.
#
# O Sol encabeça por decisão de doutrina (Márcia, 11/08): não existe seção
# do Sol, então os aspectos dele seriam órfãos — e eram. Sol+Saturno passa
# a desenvolvê-los por inteiro, e as outras seções só referenciam.
PRECEDENCIA = [
    ("sun", "sol_saturno"),
    ("moon", "lua"),
    ("mercury", "mercurio"),
    ("venus", "venus_marte"),
    ("mars", "venus_marte"),
    ("jupiter", "jupiter"),
    ("saturn", "saturno"),
    ("uranus", "urano"),
    ("neptune", "netuno"),
    ("pluto", "plutao"),
    ("chiron", "quiron"),
    ("lilith", "lilith"),
    ("north_node", "nodos"),
    ("south_node", "nodos"),
    ("ceres", "asteroides"),
    ("pallas", "asteroides"),
    ("juno", "asteroides"),
    ("vesta", "asteroides"),
]
_ORDEM = {corpo: i for i, (corpo, _) in enumerate(PRECEDENCIA)}
DONO_DO_CORPO = dict(PRECEDENCIA)


def chave_do_aspecto(a):
    """Par de corpos + tipo, independente da ordem em que vieram."""
    pa, pb = a.get("planet_a", ""), a.get("planet_b", "")
    return (tuple(sorted((pa, pb))), a.get("type"))


def dono_do_aspecto(a):
    """A seção que DESENVOLVE este aspecto, ou None se nenhum corpo é
    conhecido (aspecto a um ângulo, por exemplo)."""
    cands = [c for c in (a.get("planet_a"), a.get("planet_b")) if c in _ORDEM]
    if not cands:
        return None
    return DONO_DO_CORPO[min(cands, key=lambda c: _ORDEM[c])]


def mapa_de_donos(aspects):
    """{chave_do_aspecto: seção dona}. Uma dona por aspecto, sempre."""
    return {chave_do_aspecto(a): dono_do_aspecto(a) for a in (aspects or [])}


def aspectos_sem_dono(aspects, secoes_presentes=None):
    """Aspectos que ninguém desenvolve — o defeito bloqueante.

    `secoes_presentes`: nomes das seções que este relatório de fato tem.
    Um aspecto cuja dona foi pulada (recorte por `sections_only`, variante
    de hora desconhecida) é tão órfão quanto um sem dona declarada: a
    remissão aponta para uma seção que não existe no PDF.
    """
    orfaos = []
    for a in (aspects or []):
        d = dono_do_aspecto(a)
        if d is None or (secoes_presentes is not None and d not in secoes_presentes):
            orfaos.append({"aspecto": f"{a.get('planet_a')}-{a.get('planet_b')}"
                                      f" {a.get('type')}", "dona": d})
    return orfaos


# ---------------------------------------------------------------- prosa
#
# A frase é escolhida pela ORDEM DE IMPRESSÃO, nunca por quem escreveu.
# Foi exatamente essa escolha manual que produziu "já foi explorada"
# apontando para uma seção posterior.
_RETRO = "como você já leu em «{titulo}»"
_PROSP = "e isso se aprofunda em «{titulo}»"


def resolver(texto, secao_atual, ordem, titulos):
    """Troca os marcadores pela prosa certa. Devolve (texto, ocorrências).

    `ordem`: {nome_da_secao: índice de impressão}
    `titulos`: {nome_da_secao: título que sai no PDF}

    Marcador para seção desconhecida NÃO é removido em silêncio: fica no
    texto e o lint o acusa. Sumir com ele produziria uma frase truncada
    que ninguém liga ao defeito — a mesma classe do filtro de aspectos
    que devolvia zero sem reclamar.
    """
    ocorrencias = []

    def _troca(m):
        alvo = m.group(1).lower()
        if alvo not in ordem or alvo not in titulos:
            ocorrencias.append({"alvo": alvo, "direcao": "desconhecida",
                                "resolvida": False})
            return m.group(0)
        i_at = ordem.get(secao_atual, -1)
        depois = ordem[alvo] > i_at
        ocorrencias.append({"alvo": alvo, "resolvida": True,
                            "direcao": "prospectiva" if depois else "retrospectiva"})
        molde = _PROSP if depois else _RETRO
        return molde.format(titulo=titulos[alvo])

    return MARCADOR.sub(_troca, texto), ocorrencias


# ---------------------------------------------------------------- lint
#
# Remissões que o modelo escreveu À MÃO, em vez de usar o marcador. São
# as que apontam para lugar nenhum, porque nada as verifica. Ficam
# FLAG-ONLY: reescrever remissão automaticamente foi o que criou o
# círculo vazio em primeiro lugar.
#
# ESTREITO DE PROPÓSITO. A primeira versão casava "já foi" solto e acusou
# três frases de português comum — "você já foi capaz de sustentar isso",
# "já foi dito por outros sobre você", "essa dor já foi sua companheira".
# Nenhuma é remissão. O que faz uma remissão é o PARTICÍPIO DE DESCRIÇÃO
# depois do "já foi": descrito, mencionado, explorado, tratado, visto.
# Sem ele, "já foi" é só passado.
_PART_DESCREVER = (r"descrit[ao]s?|mencionad[ao]s?|explorad[ao]s?|"
                   r"trat[ao]d[ao]s?|abordad[ao]s?|vist[ao]s?|"
                   r"apresentad[ao]s?|comentad[ao]s?|analisad[ao]s?")
_REMISSAO_SOLTA = re.compile(
    r"(?:"
    # O espaço tem de estar DENTRO do grupo opcional: sem isso,
    # "já foi explorada" não casava — o grupo consumia "foi" e o padrão
    # exigia o particípio colado. Só a frase SEM verbo ("já descritas")
    # passava, que é a menos comum das duas.
    rf"\bj[áa]\s+(?:(?:foi|foram|est[áa]|estavam)\s+)?(?:{_PART_DESCREVER})\b"
    r"|\bj[áa]\s+(?:vimos|falamos|tratamos|exploramos|mencionamos)\b"
    r"|\b(?:como|conforme)\s+(?:j[áa]\s+)?(?:vimos|falamos|"
    r"foi\s+dito\s+(?:acima|antes|na\s+se[çc][ãa]o))\b"
    rf"|\bser[áa](?:m)?\s+(?:{_PART_DESCREVER}|retomad[ao]s?|"
    r"aprofundad[ao]s?)\b"
    r"|\b(?:voltaremos|retomaremos|veremos|falaremos)\s+"
    r"(?:a\s+)?(?:isso\s+)?(?:mais\s+)?adiante\b"
    r"|\b(?:mais\s+)?adiante\s+(?:voltaremos|retomaremos|veremos|falaremos)\b"
    r"|\bna\s+se[çc][ãa]o\s+(?:seguinte|anterior)\b"
    r")", re.IGNORECASE)


def remissoes_soltas(texto):
    """Remissões em prosa livre — as que não resolvem para nada."""
    return [{"match": m.group(0), "offset": m.start()}
            for m in _REMISSAO_SOLTA.finditer(texto or "")]


def lint_remissoes(por_secao, ordem, titulos, donos=None, teto=1):
    """Confere a máquina inteira sobre o relatório montado.

    `por_secao`: [(nome, texto)] na ordem de impressão.
    `donos`: {nome_da_secao: {chaves de aspecto que ela desenvolve}} —
             opcional; quando dado, exige que o alvo seja DONO de algo,
             não apenas uma seção que existe.
    """
    achados = []
    for nome, texto in por_secao:
        marcas = MARCADOR.findall(texto or "")
        # 1. alvo que não existe
        for alvo in marcas:
            a = alvo.lower()
            if a not in ordem:
                achados.append({"kind": "remissao:alvo_inexistente",
                                "secao": nome, "alvo": a,
                                "detalhe": "aponta para seção que não está "
                                           "neste relatório"})
            elif a == nome:
                achados.append({"kind": "remissao:aponta_para_si",
                                "secao": nome, "alvo": a,
                                "detalhe": "a seção remete a si mesma"})
            elif donos is not None and not donos.get(a):
                achados.append({"kind": "remissao:alvo_nao_e_dono",
                                "secao": nome, "alvo": a,
                                "detalhe": "a seção alvo não desenvolve "
                                           "aspecto nenhum — remissão vazia"})
        # 2. teto de densidade
        if len(marcas) > teto:
            achados.append({"kind": "remissao:acima_do_teto", "secao": nome,
                            "alvo": None,
                            "detalhe": f"{len(marcas)} remissões (teto {teto}); "
                                       f"remissão é reforço, não desculpa "
                                       f"para não desenvolver"})
        # 3. remissão escrita à mão — direção não verificável
        for s in remissoes_soltas(texto):
            achados.append({"kind": "remissao:solta", "secao": nome,
                            "alvo": None,
                            "detalhe": f"{s['match']!r} — remissão em prosa "
                                       f"livre não resolve para seção nenhuma; "
                                       f"use [[ref:secao]]"})
    return achados
