"""PROVA DA MÁQUINA DE REMISSÃO (11/08, leitura da Marcelle).

Ela é cliente, não astróloga, e foi CONFERIR uma remissão. Os aspectos do
Sol da Helena não eram desenvolvidos em lugar nenhum: a tríade listava e
adiava, a p.12 dizia "já foram descritas", a p.15, a p.22 e a p.24
adiavam de novo. Cada seção apontava para outra e o círculo fechava
vazio. Nenhum detector via, porque cada frase, isolada, é impecável — o
defeito só existe na RELAÇÃO entre seções.

Três defeitos, uma máquina: dono declarado, direção decidida na
impressão, teto de densidade.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

import _fixture  # noqa: E402
import remissao as R  # noqa: E402

falhas = []


def checa(nome, cond, detalhe=""):
    print(f"{'OK   ' if cond else 'FALHA'} {nome}" + (f"   → {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


_ch = _fixture.build_chart()
_ASP = _ch["aspects"]
SECOES = ["abertura", "lua", "mercurio", "triade", "casa_4", "sol_saturno",
          "venus_marte", "jupiter", "saturno", "quiron", "urano", "netuno",
          "plutao", "lilith", "nodos", "asteroides", "fio_condutor"]
ORDEM = {n: i for i, n in enumerate(SECOES)}
TITULOS = {n: n.replace("_", " ").title() for n in SECOES}

print(f"{len(_ASP)} aspectos no mapa de teste\n")

# ------------------------------------------------------------------ 1
# TODO ASPECTO TEM DONA. O defeito bloqueante.
_orfaos = R.aspectos_sem_dono(_ASP, set(SECOES))
checa("1. nenhum aspecto sem dona", not _orfaos, str(_orfaos[:3]))

# Cada aspecto tem EXATAMENTE uma — não zero, não duas.
_donos = R.mapa_de_donos(_ASP)
checa("1b. a dona é única por aspecto",
      all(isinstance(v, str) for v in _donos.values()),
      f"{sum(1 for v in _donos.values() if not isinstance(v, str))} sem dona única")

# ------------------------------------------------------------------ 2
# DOUTRINA DA MÁRCIA: não existe seção do Sol, então Sol+Saturno é dona de
# TODOS os aspectos do Sol. É a decisão que fecha o círculo vazio.
_sol = [a for a in _ASP if "sun" in (a["planet_a"], a["planet_b"])]
_erradas = [(a["planet_a"], a["planet_b"], R.dono_do_aspecto(a))
            for a in _sol if R.dono_do_aspecto(a) != "sol_saturno"]
checa(f"2. os {len(_sol)} aspectos do Sol são de sol_saturno",
      not _erradas, str(_erradas))

# E o Sol ganha de QUALQUER outro corpo, inclusive dos que vêm antes no
# relatório. Se a precedência fosse a ordem de impressão, sol-lua seria da
# Lua e a doutrina cairia sem ninguém ver.
checa("2b. o Sol tem precedência sobre a Lua",
      R.dono_do_aspecto({"planet_a": "moon", "planet_b": "sun",
                         "type": "trine"}) == "sol_saturno")
checa("2c. e sobre os asteroides",
      R.dono_do_aspecto({"planet_a": "juno", "planet_b": "sun",
                         "type": "square"}) == "sol_saturno")

# ------------------------------------------------------------------ 3
# DIREÇÃO. O tempo verbal sai da ORDEM DE IMPRESSÃO, nunca de quem
# escreveu. Foi a escolha manual que produziu "já foi explorada"
# apontando para uma seção posterior.
_t, _oc = R.resolver("Isto se liga [[ref:jupiter]] de perto.",
                     "lua", ORDEM, TITULOS)
checa("3. alvo POSTERIOR vira prospectivo",
      "se aprofunda" in _t and _oc[0]["direcao"] == "prospectiva", _t)
_t, _oc = R.resolver("Isto se liga [[ref:lua]] de perto.",
                     "jupiter", ORDEM, TITULOS)
checa("3b. alvo ANTERIOR vira retrospectivo",
      "já leu" in _t and _oc[0]["direcao"] == "retrospectiva", _t)

# O MESMO marcador, nas duas seções, produz tempos opostos. É isto que
# torna a direção impossível de errar: ela não é escrita, é derivada.
_a, _ = R.resolver("x [[ref:plutao]] y", "lua", ORDEM, TITULOS)
_b, _ = R.resolver("x [[ref:lua]] y", "plutao", ORDEM, TITULOS)
checa("3c. o mesmo par produz tempos opostos conforme a seção",
      ("se aprofunda" in _a) and ("já leu" in _b))

# ------------------------------------------------------------------ 4
# ALVO DESCONHECIDO NÃO SOME EM SILÊNCIO. Apagar o marcador produziria
# uma frase truncada que ninguém liga ao defeito — a mesma classe do
# filtro de aspectos que devolvia zero sem reclamar.
_t, _oc = R.resolver("Veja [[ref:secao_que_nao_existe]].", "lua", ORDEM, TITULOS)
checa("4. alvo desconhecido PERMANECE no texto para o lint achar",
      "[[ref:secao_que_nao_existe]]" in _t and not _oc[0]["resolvida"], _t)

# ------------------------------------------------------------------ 5
# LINT: alvo inexistente, auto-referência, alvo que não desenvolve nada.
_DON = {"sol_saturno": {("a", "b")}, "lua": {("c", "d")}}
_ach = R.lint_remissoes(
    [("lua", "texto [[ref:nao_existe]] fim"),
     ("jupiter", "texto [[ref:jupiter]] fim"),
     ("plutao", "texto [[ref:abertura]] fim")],
    ORDEM, TITULOS, donos=_DON)
_kinds = {a["kind"] for a in _ach}
checa("5. lint acusa alvo inexistente",
      "remissao:alvo_inexistente" in _kinds, str(_kinds))
checa("5b. lint acusa remissão para a própria seção",
      "remissao:aponta_para_si" in _kinds)
checa("5c. lint acusa alvo que não desenvolve aspecto nenhum",
      "remissao:alvo_nao_e_dono" in _kinds)

# ------------------------------------------------------------------ 6
# TETO. 13 remissões em 23 páginas foi o que a Marcelle contou. Remissão é
# reforço, não desculpa para não desenvolver.
_ach = R.lint_remissoes([("lua", "a [[ref:jupiter]] b [[ref:plutao]] c")],
                        ORDEM, TITULOS)
checa("6. duas remissões na mesma seção estouram o teto",
      any(a["kind"] == "remissao:acima_do_teto" for a in _ach))
_ach = R.lint_remissoes([("lua", "a [[ref:jupiter]] b")], ORDEM, TITULOS)
checa("6b. uma remissão NÃO estoura",
      not any(a["kind"] == "remissao:acima_do_teto" for a in _ach))

# ------------------------------------------------------------------ 7
# REMISSÃO À MÃO. São exatamente as frases que a Marcelle seguiu, e as
# únicas que podem apontar para lugar nenhum — nada as resolve.
for _frase in ["já foram descritas na seção anterior",
               "como já vimos, isso se repete",
               "será retomada adiante",
               "veremos isso mais adiante",
               "essa questão já foi explorada"]:
    checa(f"7. acusa remissão solta: {_frase[:34]!r}",
          bool(R.remissoes_soltas(_frase)))

# Frases que NÃO são remissão não podem acender.
for _limpa in ["você já foi capaz de sustentar isso",
               "o que já foi dito por outros sobre você",
               "essa dor já foi sua companheira"]:
    _r = R.remissoes_soltas(_limpa)
    checa(f"7b. não acusa: {_limpa[:34]!r}", not _r,
          str([x["match"] for x in _r]))

# ------------------------------------------------------------------ 8
# REINJEÇÃO. Cada peça derrubada tem de fazer uma asserção cair — senão
# elas passam por vacuidade (R11).
# A primeira versão desta reinjeção tirava o Sol do mapa e esperava 7
# órfãos. Deu 0 — e a resposta estava CERTA: sem o Sol, sol-mercúrio não
# fica órfão, passa a ser de Mercúrio. Eu tinha reinjetado a coisa errada.
# R11: quando a reinjeção não derruba, a primeira hipótese é a reinjeção.
#
# Órfão de verdade é o aspecto cujos DOIS corpos são desconhecidos, e o
# aspecto cuja dona não está NESTE relatório — os dois casos em que a
# remissão apontaria para uma seção que não existe no PDF.
_desconhecido = {"planet_a": "vertex", "planet_b": "part_of_fortune",
                 "type": "conjunction"}
checa("8. aspecto de corpo desconhecido é acusado como órfão",
      len(R.aspectos_sem_dono([_desconhecido], set(SECOES))) == 1)

_sem_a_secao = set(SECOES) - {"sol_saturno"}
_o = R.aspectos_sem_dono(_ASP, _sem_a_secao)
checa("8a. com a seção dona AUSENTE do relatório, os aspectos dela são órfãos",
      len(_o) == len(_sol), f"{len(_o)} órfão(s), esperado {len(_sol)}")

# Precedência invertida: a doutrina cai e a asserção 2 tem de acusar.
_orig_prec = list(R.PRECEDENCIA)
try:
    R._ORDEM.clear()
    R._ORDEM.update({c: i for i, (c, _) in enumerate(reversed(_orig_prec))})
    _err = [a for a in _sol if R.dono_do_aspecto(a) != "sol_saturno"]
    checa("8b. reinjeção: precedência invertida quebra a doutrina do Sol",
          bool(_err), f"{len(_err)} aspecto(s) do Sol saíram de sol_saturno")
finally:
    R._ORDEM.clear()
    R._ORDEM.update({c: i for i, (c, _) in enumerate(_orig_prec)})

# ------------------------------------------------------------------ 9
# FIAÇÃO: o gerador de fato chama a máquina, e o prompt de fato declara a
# dona. Uma máquina correta que ninguém liga é a salvaguarda morta de
# novo — e essa classe já me mordeu duas vezes esta semana.
import inspect  # noqa: E402

import report_generator as rg  # noqa: E402

_src_fmt = inspect.getsource(rg.fmt_filtered_aspects)
checa("9. o prompt declara a seção DONA",
      "VOCÊ É A SEÇÃO DONA" in _src_fmt)
checa("9b. o prompt proíbe adiar",
      "NÃO adie" in _src_fmt and "já foram vistos" in _src_fmt)
checa("9c. o prompt manda usar o marcador, não prosa livre",
      "[[ref:nome_da_secao]]" in _src_fmt)
_src_gen = inspect.getsource(rg._generate_report_locked)
checa("9d. o montador RESOLVE os marcadores", "_rem.resolver(" in _src_gen)
checa("9e. e roda o lint da remissão", "_rem.lint_remissoes(" in _src_gen)
checa("9f. o resultado expõe remissao_lint e aspectos_sem_dono",
      '"remissao_lint": remissao_lint' in _src_gen
      and '"aspectos_sem_dono": aspectos_orfaos' in _src_gen)

print()
if falhas:
    print(f">>> {len(falhas)} FALHOU: {falhas}")
    sys.exit(1)
print(">>> REMISSÃO: TUDO PROVADO")
