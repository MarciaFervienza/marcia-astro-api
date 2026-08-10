"""PASSADA DE REVISÃO DE LÍNGUA (19/07) — decisão da Márcia.

POR QUE EXISTE. Os defeitos de língua não são resíduo, são NOVOS a cada
geração: "conta tos", "cri ativamente", "do carregada de significado",
"própria lidava" nunca tinham aparecido, e os da rodada anterior não
voltaram. Classe enumerável fecha — andaime e geracional deram zero duas
vezes seguidas. Sintaxe corrompida é GERATIVA: nenhum conjunto de regex
alcança, porque cada geração inventa uma forma nova.

Regex responde "esta forma que eu conheço apareceu?". Uma passada de
leitura responde "isto é português?". São perguntas diferentes.

ESCOPO — SÓ PORTUGUÊS:
  · palavra partida ("conta tos") ou colada
  · sintaxe quebrada
  · concordância, regência, artigo
  · negação faltando que inverte o sentido
  · pronome/possessivo errado (inclusive gênero do sujeito)
  · construção sem sentido

NÃO TOCA: nome de corpo, signo, casa, aspecto, grau, número — nada
astrológico. Se a revisão mexer em qualquer um desses, ela é REJEITADA.

SALVAGUARDAS. O reescritor já corrompeu texto certo duas vezes e injetou
meta-comentário no PDF do cliente uma terceira. Aqui:
  1. INVARIANTES — todo nome de corpo, signo, casa, aspecto e número
     presente antes tem de estar presente depois, idêntico. Qualquer
     divergência rejeita a revisão daquele trecho.
  2. RECUSA ANTES DO SPLICE — saída fora de faixa de tamanho, parágrafos a
     mais, ou meta-comentário: rejeita.
  3. PROTEGIDAS — os fragmentos retóricos dela são orações incompletas de
     propósito. Uma revisão vai querer "completá-los". Não pode.
"""
import logging
import re

logger = logging.getLogger("natal-api")

# ------------------------------------------------------------------
# INVARIANTES — o que a revisão NÃO pode alterar
# ------------------------------------------------------------------
_CORPOS_PT = (
    r"Sol|Lua|Merc[úu]rio|V[êe]nus|Marte|J[úu]piter|Saturno|Urano|Netuno|"
    r"Plut[ãa]o|Qu[íi]ron|Lilith|Ceres|Palas|Pallas|Juno|Vesta|"
    r"Nodo\s+Norte|Nodo\s+Sul|Ascendente|Meio-do-C[ée]u|Descendente"
)
_SIGNOS_PT = (
    r"[ÁA]ries|Touro|G[êe]meos|C[âa]ncer|Le[ãa]o|Virgem|Libra|Escorpi[ãa]o|"
    r"Sagit[áa]rio|Capric[óo]rnio|Aqu[áa]rio|Peixes"
)
_ASPECTOS_PT = (
    r"conjun[çc][ãa]o|oposi[çc][ãa]o|quadratura|tr[íi]gono|sextil"
)


def invariantes(texto):
    """Multiconjunto do que é astrológico e não pode mudar.

    Normaliza caixa e acento do NOME para que "Vênus"/"vênus" contem como o
    mesmo item — a revisão pode legitimamente mudar a caixa no início de
    frase, e isso não é alteração de conteúdo.
    """
    def norm(xs):
        return sorted(re.sub(r"\s+", " ", x).strip().lower() for x in xs)
    return {
        "corpos": norm(re.findall(_CORPOS_PT, texto, flags=re.IGNORECASE)),
        "signos": norm(re.findall(_SIGNOS_PT, texto, flags=re.IGNORECASE)),
        "aspectos": norm(re.findall(_ASPECTOS_PT, texto, flags=re.IGNORECASE)),
        "casas": sorted(re.findall(r"casa\s+(\d{1,2})", texto, flags=re.IGNORECASE)),
        "numeros": sorted(re.findall(r"\d+(?:[.,]\d+)?", texto)),
    }


def divergencia_de_invariante(antes, depois):
    """Devolve o motivo se a revisão mexeu em conteúdo astrológico."""
    a, b = invariantes(antes), invariantes(depois)
    for chave in ("corpos", "signos", "aspectos", "casas", "numeros"):
        if a[chave] != b[chave]:
            perdidos = [x for x in a[chave] if x not in b[chave]]
            ganhos = [x for x in b[chave] if x not in a[chave]]
            return (f"{chave}: sumiu {perdidos or '—'}, apareceu "
                    f"{ganhos or '—'}")
    return None


# ------------------------------------------------------------------
# RECUSA ANTES DO SPLICE
# ------------------------------------------------------------------
_META = re.compile(
    r"(?:\brevis(?:ão|ao|ado|ada|ando)\b|\bcorrigi\b|\bcorre[çc][ãa]o\b|"
    r"\baqui\s+est[áa]\b|\bsegue\s+o\b|\bnota\s*:|\bobserva[çc][ãa]o\s*:|"
    r"\bo\s+trecho\b|\bo\s+texto\s+original\b|\bnão\s+havia\s+erros?\b)",
    re.IGNORECASE)


_FUNCIONAIS_OK = {
    # Palavras que uma revisão PODE inserir: artigo, preposição, pronome,
    # conjunção, verbo de ligação. São o material de conserto de sintaxe.
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da",
    "dos", "das", "em", "no", "na", "nos", "nas", "por", "pelo", "pela",
    "para", "com", "sem", "que", "se", "e", "ou", "mas", "ao", "aos", "à",
    "às", "lhe", "lhes", "me", "te", "nos", "vos", "ele", "ela", "eles",
    "elas", "seu", "sua", "seus", "suas", "este", "esta", "esse", "essa",
    "isso", "isto", "aquilo", "não", "já", "é", "são", "foi", "era", "ser",
    "está", "estão", "há", "tem", "têm", "como", "quando", "onde", "mais",
    "menos", "muito", "pouco", "também", "ainda", "só", "sempre", "nunca",
}


def _tokens(t):
    return re.findall(r"[a-zà-ÿ]+", _sem_acento(t.lower()))


def _sem_acento(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _dist1(a, b):
    if abs(len(a) - len(b)) > 1 or a == b:
        return abs(len(a) - len(b)) <= 1 and a != b and _dist1_calc(a, b)
    return _dist1_calc(a, b)


def _dist1_calc(a, b):
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if abs(len(a) - len(b)) != 1:
        return False
    curto, longo = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(longo)):
        if longo[:i] + longo[i + 1:] == curto:
            return True
    return False


def palavra_inventada(original, saida):
    """A revisão introduziu CONTEÚDO que não estava lá?

    Achado de 19/07, na primeira medição real: pedida para consertar "a
    quadratura aos Nodos do carregada de significado", a revisão devolveu
    "aos Nodos DO CARMA É carregada de significado" — inventou um termo
    astrológico. Os invariantes não pegaram porque cobrem corpo, signo,
    casa, aspecto e número, e "carma" não é nenhum deles.

    Regra: toda palavra de conteúdo (≥4 letras) na saída tem de existir no
    original, ou ser variação morfológica de uma que existe, ou vir da
    junção/separação de palavras vizinhas ("conta tos" → "contatos").
    """
    orig_tok = set(_tokens(original))
    orig_colado = _sem_acento(re.sub(r"[^a-zA-ZÀ-ÿ]", "", original.lower()))
    for t in _tokens(saida):
        if len(t) < 4 or t in orig_tok or t in _FUNCIONAIS_OK:
            continue
        if t in orig_colado:            # veio de palavra partida remontada
            continue
        if any(_dist1_calc(t, o) for o in orig_tok):
            continue                    # flexão: pronta→pronto, mesma→mesmo
        return t
    return None


def frases_perdidas(original, saida):
    """A revisão APAGOU conteúdo?

    Achado de 19/07: num parágrafo do Lucca a revisão simplesmente sumiu com
    "Isso provavelmente tem uma história." A faixa de tamanho não pegou —
    uma frase curta dentro de um parágrafo longo mantém a razão acima de
    0,70. Contar frases pega.
    """
    def n(t):
        return len([x for x in re.split(r"(?<=[.!?])\s+", t.strip()) if x.strip()])
    a, b = n(original), n(saida)
    return f"{a} frases viraram {b}" if a != b else None


def motivo_recusa(original, saida):
    """None se a saída é uma revisão plausível do trecho; senão o motivo."""
    if not saida or not saida.strip():
        return "saída vazia"
    s = saida.strip()
    if re.search(r"(?m)^\s*---\s*$", s):
        return "separador '---': saída em blocos"
    # O número de blocos tem de ser o MESMO da entrada. Com granularidade de
    # frase ou parágrafo isso é "1"; com seção, é quantos parágrafos a seção
    # tinha. Fixar em 1 quebraria a granularidade de seção.
    n_ent = len([b for b in re.split(r"\n\s*\n", original) if b.strip()])
    n_sai = len([b for b in re.split(r"\n\s*\n", s) if b.strip()])
    if n_sai != n_ent:
        return f"{n_sai} blocos na saída contra {n_ent} na entrada"
    if _META.search(s):
        m = _META.search(s)
        return f"meta-comentário do revisor: {m.group(0)!r}"
    r = len(s) / max(1, len(original.strip()))
    # Faixa APERTADA (19/07, 2ª versão). 0,70–1,40 deixou passar a remoção
    # de uma frase inteira. Revisão de língua muda pouco o tamanho.
    if not (0.85 <= r <= 1.20):
        return f"tamanho {r:.2f}× o original — revisão não reescreve o trecho"
    perdidas = frases_perdidas(original, s)
    if perdidas:
        return f"contagem de frases mudou: {perdidas}"
    inventada = palavra_inventada(original, s)
    if inventada:
        return f"palavra INVENTADA na revisão: {inventada!r}"
    return None


# ------------------------------------------------------------------
# PROMPT
# ------------------------------------------------------------------
_PROMPT = """Você é um revisor de português brasileiro. Abaixo vem UM PARÁGRAFO de um relatório de astrologia já escrito.

Sua tarefa é corrigir ERROS DE LÍNGUA e nada mais:
- palavra partida ("conta tos" → "contatos") ou colada ("voltarcontra" → "voltar contra")
- sintaxe quebrada (frase que não fecha, palavras fora de lugar)
- concordância (gênero, número), regência, artigo
- negação faltando que inverte o sentido
- pronome ou possessivo errado
- construção sem sentido

PROIBIDO ALTERAR:
- qualquer nome de planeta, corpo, asteroide, Nodo, Ascendente ou Meio-do-Céu
- qualquer nome de signo
- qualquer número, grau, orbe ou número de casa
- qualquer nome de aspecto (conjunção, oposição, quadratura, trígono, sextil)
- o sentido, o tom e a extensão do parágrafo

TAMBÉM PROIBIDO:
- "completar" frases curtas ou fragmentos que pareçam incompletos: eles são recurso de estilo da autora e devem ficar exatamente como estão
- reescrever para "melhorar" o estilo. Se está em português correto, não toque.

O SUJEITO deste relatório é {genero}. Pronomes e adjetivos que se referem a quem lê devem concordar com isso ("você mesmo" / "você mesma").

Se o parágrafo NÃO tiver erro de língua, responda exatamente com a palavra:
INTACTO

Se tiver, responda APENAS com o parágrafo corrigido — sem aspas, sem introdução, sem explicação, sem comentar o que mudou.

PARÁGRAFO:
\"\"\"
{par}
\"\"\""""


def _revisar_paragrafo(par, genero, call_claude_fn):
    """(texto_final, status, detalhe). Nunca levanta."""
    if len(par.strip()) < 40 or par.strip() in ("---",):
        return par, "pulado", "curto demais para ter sintaxe"
    try:
        _tok = min(4000, max(600, int(len(par) / 2)))
        saida = call_claude_fn(
            _PROMPT.format(par=par.strip(), genero=genero), max_tokens=_tok
        ).strip()
    except Exception as exc:
        return par, "erro_chamada", str(exc)[:120]
    if saida.upper().strip().strip(".") == "INTACTO":
        return par, "intacto", ""
    motivo = motivo_recusa(par, saida)
    if motivo:
        return par, "recusado", motivo
    div = divergencia_de_invariante(par, saida)
    if div:
        return par, "rejeitado_invariante", div
    if saida.strip() == par.strip():
        return par, "intacto", ""
    return saida, "revisado", ""


# ==================================================================
# DETECÇÃO SEMÂNTICA — FLAG-ONLY (19/07, decisão da Márcia)
# ==================================================================
# Dois defeitos escaparam da passada de revisão, e são a mesma classe:
#   "um destino e sempre um ponto de chegada"   (falta o "não")
#   "a hesitação, você mesma, já é uma forma"   (inserção sem sentido)
# Localmente bem-formados, semanticamente errados.
#
# A tentação era uma segunda passada de REESCRITA perguntando "isto faz
# sentido?". A Márcia vetou, e com razão: foi ADIVINHAR A INTENÇÃO que
# produziu o "carma". Uma pergunta mais aberta corrompe mais.
#
# Aqui a detecção é separada da correção. Este detector NÃO reescreve nada —
# só aponta. Risco de corrupção: zero. A correção, quando vier, é regenerar
# a SEÇÃO, que produz texto novo sem carregar o defeito, e ninguém precisa
# adivinhar o que a frase queria dizer.
_PROMPT_SEMANTICO = """Abaixo vem um parágrafo de um relatório de astrologia em português do Brasil.

Sua única tarefa é dizer se alguma frase dele está QUEBRADA ou SEM SENTIDO. Procure:
- frase que não fecha, que perdeu uma palavra e ficou incompreensível
- sentido INVERTIDO por falta de uma negação ("é sempre X" onde só faz sentido "não é sempre X")
- palavra ou expressão inserida onde não cabe, deixando a frase sem sentido
- construção que não significa nada em português

NÃO É DEFEITO, e você deve ignorar:
- frase curta, fragmento ou oração incompleta usada como recurso de estilo
- linguagem astrológica, por mais técnica que pareça
- escolha de palavra que você acharia melhor de outro jeito
- estilo, ritmo, repetição, tom

Se TODAS as frases fizerem sentido, responda exatamente:
OK

Se alguma NÃO fizer, responda UMA linha por frase, assim:
FRASE: <a frase exata, copiada> | MOTIVO: <por que não faz sentido, em até 12 palavras>

Não sugira correção. Não reescreva nada.

PARÁGRAFO:
\"\"\"
{par}
\"\"\""""


def _detectar_um(par, call_claude_fn):
    if len(par.strip()) < 40:
        return []
    try:
        saida = call_claude_fn(_PROMPT_SEMANTICO.format(par=par.strip()),
                               max_tokens=400).strip()
    except Exception as exc:
        return [{"erro": str(exc)[:120]}]
    if saida.upper().strip().strip(".") == "OK":
        return []
    achados = []
    for linha in saida.split("\n"):
        m = re.match(r"\s*FRASE\s*:\s*(.+?)\s*\|\s*MOTIVO\s*:\s*(.+)",
                     linha, flags=re.IGNORECASE)
        if m:
            achados.append({"frase": m.group(1).strip().strip('"'),
                            "motivo": m.group(2).strip()})
    return achados


def detectar_sem_sentido(texto, call_claude_fn=None, max_workers=6,
                         granularidade="paragrafo"):
    """Aponta frases quebradas. NÃO altera o texto. Devolve lista."""
    if call_claude_fn is None:
        from report_generator import call_claude as call_claude_fn
    partes, idx = _fatiar(texto, granularidade)
    from concurrent.futures import ThreadPoolExecutor
    out = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for achados in ex.map(lambda i: _detectar_um(partes[i], call_claude_fn),
                              idx):
            out.extend(achados)
    return out


def _fatiar(texto, granularidade):
    """Devolve `partes` alternando conteúdo e separador, e os índices de
    conteúdo. Erro de sintaxe é LOCAL — o revisor não precisa do relatório
    inteiro, mas também não precisa de uma frase por vez. As três opções
    existem para MEDIR qual custa menos pelo mesmo resultado.
    """
    if granularidade == "secao":
        # corta em cabeçalhos ## — mantém o cabeçalho no bloco seguinte
        partes = re.split(r"(\n(?=## ))", texto)
    elif granularidade == "frase":
        partes = re.split(r"(?<=[.!?])(\s+)", texto)
    else:                                   # parágrafo (padrão)
        partes = re.split(r"(\n\s*\n)", texto)
    idx = [i for i, p in enumerate(partes) if i % 2 == 0 and p.strip()]
    return partes, idx


def revisar_texto(texto, genero="feminino", call_claude_fn=None,
                  max_workers=6, granularidade="paragrafo"):
    """Revisa o relatório inteiro na granularidade pedida.

    A APLICAÇÃO preserva o separador de cauda de cada trecho — a mesma
    armadilha que colou frases no PDF em 16/07.
    """
    if call_claude_fn is None:
        from report_generator import call_claude as call_claude_fn
    partes, idx_par = _fatiar(texto, granularidade)

    from concurrent.futures import ThreadPoolExecutor
    log = []

    def um(i):
        novo, status, det = _revisar_paragrafo(partes[i], genero, call_claude_fn)
        return i, novo, status, det

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for i, novo, status, det in ex.map(um, idx_par):
            if status == "revisado":
                partes[i] = novo
            if status not in ("intacto", "pulado"):
                log.append({"status": status, "detalhe": det,
                            "antes": partes[i][:160] if status != "revisado"
                            else "", "trecho": novo[:160]})
                if status in ("recusado", "rejeitado_invariante"):
                    logger.warning("revisão de língua %s: %s", status, det)
    return "".join(partes), log


# ==================================================================
# ENCANAMENTO: detectar → regenerar a seção → redetectar → falhar fechado
# ==================================================================
# Desenho da Márcia (19/07), depois de MEDIDO que a passada de reescrita
# corrige 5 dos 16 defeitos e precisou de seis guardas para chegar a zero
# corrupção numa amostra de 5 edições. O detector acha 16 com risco zero.
#
# Por que REGENERAR e não reescrever: reescrever exige adivinhar o que a
# frase quebrada queria dizer, e foi adivinhar que produziu o "carma".
# Regenerar a seção produz texto NOVO, que não carrega o defeito, e ninguém
# precisa adivinhar nada.
#
# TETO DE TENTATIVAS: sem ele, um defeito teimoso vira loop — e cada volta
# custa uma geração de seção inteira.
#
# FALHA FECHADA: acima do teto o relatório NÃO SAI. A Márcia prefere gerar
# à mão a mandar defeito.

_RE_SECAO = re.compile(r"(?m)^## (.+)$")


def _mapa_de_secoes(texto):
    """[(titulo, inicio, fim)] de cada seção do relatório montado."""
    marcas = [(m.group(1).strip(), m.start()) for m in _RE_SECAO.finditer(texto)]
    out = []
    for i, (titulo, ini) in enumerate(marcas):
        fim = marcas[i + 1][1] if i + 1 < len(marcas) else len(texto)
        out.append((titulo, ini, fim))
    return out


def secao_da_frase(texto, frase):
    """Em que seção está a frase apontada? (titulo, ini, fim) ou None."""
    pos = texto.find(frase[:60])
    if pos < 0:
        # o detector copia a frase; se houver diferença de espaço, tenta
        # por um trecho menor antes de desistir
        pos = texto.find(frase[:35])
    if pos < 0:
        return None
    for titulo, ini, fim in _mapa_de_secoes(texto):
        if ini <= pos < fim:
            return titulo, ini, fim
    return None


def pipeline_lingua(full_report, chart, regenerar_secao_fn,
                    call_claude_fn=None, max_tentativas=3, log_fn=None):
    """Detecta, regenera as seções apontadas, redetecta, falha fechado.

    `regenerar_secao_fn(titulo) -> texto_novo_da_secao | None` é injetada
    para este módulo não depender de report_generator (ciclo de import).

    Devolve (texto, log, motivo_falha). `motivo_falha` não-nulo significa
    FALHA FECHADA: o chamador NÃO pode enviar o relatório.
    """
    _log = log_fn or (lambda *a, **k: None)
    if call_claude_fn is None:
        from report_generator import call_claude as call_claude_fn
    log = {"rodadas": [], "chamadas_detector": 0, "regeneracoes": 0}
    texto = full_report

    for rodada in range(1, max_tentativas + 1):
        achados = detectar_sem_sentido(texto, call_claude_fn=call_claude_fn)
        _p, _i = _fatiar(texto, "paragrafo")
        log["chamadas_detector"] += sum(1 for i in _i if len(_p[i].strip()) >= 40)
        log["rodadas"].append({"rodada": rodada, "achados": achados})
        _log(f"língua rodada {rodada}: {len(achados)} frase(s) apontada(s)")
        if not achados:
            return texto, log, None

        # agrupa por SEÇÃO: uma regeneração conserta todas as frases dela
        alvos = {}
        for a in achados:
            loc = secao_da_frase(texto, a.get("frase", ""))
            if loc:
                alvos.setdefault(loc[0], []).append(a)
            else:
                alvos.setdefault(None, []).append(a)

        if None in alvos and len(alvos) == 1:
            return texto, log, ("frase apontada não foi localizada em nenhuma "
                                "seção — não há o que regenerar")

        if rodada == max_tentativas:
            break                       # última rodada só detecta, não regenera

        for titulo in [t for t in alvos if t]:
            novo = None
            try:
                novo = regenerar_secao_fn(titulo)
            except Exception as exc:
                _log(f"regeneração de {titulo!r} falhou: {exc}")
            log["regeneracoes"] += 1
            if not novo or not novo.strip():
                continue
            loc = None
            for t2, ini, fim in _mapa_de_secoes(texto):
                if t2 == titulo:
                    loc = (ini, fim)
                    break
            if not loc:
                continue
            cabecalho = f"\n## {titulo}\n\n"
            texto = texto[:loc[0]] + cabecalho + novo.strip() + "\n" + texto[loc[1]:]
            _log(f"seção {titulo!r} regenerada")

    pendentes = log["rodadas"][-1]["achados"]
    frases = "; ".join(f"{a.get('frase','')[:70]!r} ({a.get('motivo','')[:50]})"
                       for a in pendentes[:4])
    return texto, log, (f"{len(pendentes)} frase(s) seguem quebradas após "
                        f"{max_tentativas} tentativas: {frases}")
