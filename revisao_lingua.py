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


def motivo_recusa(original, saida):
    """None se a saída é uma revisão plausível do trecho; senão o motivo."""
    if not saida or not saida.strip():
        return "saída vazia"
    s = saida.strip()
    if re.search(r"(?m)^\s*---\s*$", s):
        return "separador '---': saída em blocos"
    n_blocos = len([b for b in re.split(r"\n\s*\n", s) if b.strip()])
    if n_blocos > 1:
        return f"{n_blocos} blocos — o trecho enviado era um parágrafo"
    if _META.search(s):
        m = _META.search(s)
        return f"meta-comentário do revisor: {m.group(0)!r}"
    r = len(s) / max(1, len(original.strip()))
    if not (0.70 <= r <= 1.40):
        return f"tamanho {r:.2f}× o original — revisão não reescreve o trecho"
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
    if len(par.strip()) < 40 or par.lstrip().startswith("#"):
        return par, "pulado", "curto ou cabeçalho"
    try:
        saida = call_claude_fn(
            _PROMPT.format(par=par.strip(), genero=genero), max_tokens=1400
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


def revisar_texto(texto, genero="feminino", call_claude_fn=None,
                  max_workers=6):
    """Revisa o relatório inteiro, parágrafo a parágrafo.

    A APLICAÇÃO preserva o separador de cauda de cada parágrafo — a mesma
    armadilha que colou frases no PDF em 16/07.
    """
    if call_claude_fn is None:
        from report_generator import call_claude as call_claude_fn
    partes = re.split(r"(\n\s*\n)", texto)
    idx_par = [i for i, p in enumerate(partes) if i % 2 == 0 and p.strip()]

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
