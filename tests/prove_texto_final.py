"""ASSERÇÕES SOBRE O RELATÓRIO MONTADO — o buraco do gate (10/11).

POR QUE EXISTE. A máquina de remissão entrou em 11/08 com o gate verde e
NUNCA rodou uma geração completa. A primeira rodou em 10/11 e falhou
fechada: 7 marcadores `[[ref:…]]` CRUS no texto e 12 inserções do molde
empilhadas, sem verbo.

O gate não viu porque, medido em 10/11:
  · NENHUM teste montava um relatório inteiro e afirmava sobre o texto
    final — zero, em 17 suítes;
  · 82 asserções do gate são "string na fonte". A que devia provar a
    fiação da remissão era literalmente:
        checa("o montador RESOLVE", "_rem.resolver(" in _src_gen)
    verdadeira se a chamada roda antes, depois, ou dentro de `if False`.

É a terceira vez na mesma classe: a salvaguarda que procurava
`result.get("falha_lingua")` como substring, a asserção do worker que
casou `SystemExit` num COMENTÁRIO, e esta. Texto não é comportamento, e
presença de chamada não é fiação.

REGRA que este arquivo encarna: estágio que transforma o texto do
relatório é afirmado sobre o TEXTO FINAL, com o pipeline de língua
REGENERANDO no meio — porque era aí, e só aí, que o defeito aparecia.

Claude e Pinecone são dublados; o montador, o verificador e o pipeline de
língua são os DE PRODUÇÃO.
"""
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

import _fixture  # noqa: E402
import remissao as R  # noqa: E402
import report_generator as rg  # noqa: E402
import revisao_lingua as rl  # noqa: E402

falhas = []


def checa(nome, cond, detalhe=""):
    print(f"{'OK   ' if cond else 'FALHA'} {nome}" + (f"   → {detalhe}" if detalhe else ""))
    if not cond:
        falhas.append(nome)


_CH = _fixture.build_chart()

# Texto de seção com um marcador, como o modelo escreveria.
_COM_MARCADOR = (
    "Este é um parágrafo de seção com corpo suficiente para o pipeline "
    "tratar como texto real e não como fragmento.\n\n"
    "Isto se liga ao resto do mapa [[ref:sol_saturno]] de um jeito que "
    "importa para a leitura inteira."
)
# O texto REGENERADO também traz marcador — é exatamente o que aconteceu
# em produção, e o que a resolução pré-pipeline não alcança.
_REGEN_COM_MARCADOR = (
    "Parágrafo regenerado, já corrigido no plano da língua, que ainda "
    "remete a outra seção [[ref:lua]] como o prompt sugeriu."
)


def _monta(remissao_ativa, regenerar_com_marcador=True, texto_regen=None):
    """Monta um relatório pelo caminho DE PRODUÇÃO, dublando só o que fala
    com a rede. Devolve o dicionário de resultado."""
    orig = (rg.generate_section, rg.generate_fio_condutor,
            rl.detectar_sem_sentido, rg.REMISSAO_ATIVA)
    _n = {"regen": 0}
    try:
        rg.REMISSAO_ATIVA = remissao_ativa
        rg.generate_section = lambda *a, **k: (_COM_MARCADOR, [])
        rg.generate_fio_condutor = lambda *a, **k: (
            "Fecho do relatório, com corpo bastante para não parecer um "
            "fragmento solto no fim do texto.")

        def _detector(texto, call_claude_fn=None, max_workers=6,
                      granularidade="paragrafo"):
            # Acende UMA vez, para forçar exatamente UMA regeneração. Sem
            # regeneração no meio, o defeito de ordem não aparece — foi
            # assim que o gate passou em agosto.
            if _n["regen"] >= 1:
                return []
            m = re.search(r"Isto se liga[^\n]+", texto)
            return ([{"frase": m.group(0), "motivo": "forcado pelo teste"}]
                    if m else [])

        rl.detectar_sem_sentido = _detector
        _orig_pipe = rl.pipeline_lingua

        def _pipe(full, chart, regenerar_secao_fn, **kw):
            def _regen(txt):
                _n["regen"] += 1
                if texto_regen is not None:
                    return texto_regen
                return (_REGEN_COM_MARCADOR if regenerar_com_marcador
                        else "Parágrafo regenerado, sem marcador nenhum.")
            return _orig_pipe(full, chart, _regen, **kw)

        rl.pipeline_lingua = _pipe
        try:
            # nome e genero vem do CHART, nao por argumento
            # `_revisao_lingua` e posta pelo ENDPOINT, nao pelo chart. Sem
            # ela o pipeline de lingua nem roda — e sem regeneracao no meio
            # este teste nao exercita o defeito de ORDEM, que e a unica
            # coisa que ele existe para provar. Faltou na primeira versao e
            # as asercoes 2b e 6 cairam, corretamente.
            _payload = dict(_CH, name="Helena Penteado", gender="feminino",
                            _revisao_lingua=True, _lingua_tentativas=3)
            return rg.generate_report(_payload, write_file=False,
                                      verbose=False)
        finally:
            rl.pipeline_lingua = _orig_pipe
    finally:
        (rg.generate_section, rg.generate_fio_condutor,
         rl.detectar_sem_sentido, rg.REMISSAO_ATIVA) = orig


# ------------------------------------------------------------------ 1
# O INTERRUPTOR NASCE DESLIGADO. É o piso de segurança da Black Friday.
checa("1. REMISSAO_ATIVA desligada por padrão",
      rg.REMISSAO_ATIVA is False or bool(os.environ.get("REMISSAO_ATIVA")),
      f"REMISSAO_ATIVA={rg.REMISSAO_ATIVA}")

# ------------------------------------------------------------------ 2
# DESLIGADA: o texto FINAL não tem marcador cru — inclusive quando a
# regeneração do pipeline escreve um DEPOIS da montagem. É o caso que o
# gate de agosto não tinha.
_r = _monta(remissao_ativa=False, regenerar_com_marcador=True)
_rep = _r.get("report") or ""
_crus = R.MARCADOR.findall(_rep)
checa("2. DESLIGADA: nenhum marcador cru no texto FINAL",
      not _crus, f"{len(_crus)} marcador(es): {_crus[:4]}")
checa("2b. e a regeneração do pipeline de fato aconteceu",
      (_r.get("revisao_lingua") or {}).get("regeneracoes"),
      "sem regeneração o teste não exercita o defeito de ordem")
checa("2c. o resíduo é CONTADO, não escondido",
      _r.get("remissao_marcadores_removidos", 0) >= 1,
      f"removidos={_r.get('remissao_marcadores_removidos')}")

# ------------------------------------------------------------------ 3
# DESLIGADA: nenhuma prosa do molde entra no texto. Era ela que produzia
# "como você já leu em «X» como você já leu em «Y»", sem verbo.
for _molde in ("como você já leu em", "se aprofunda em «"):
    checa(f"3. DESLIGADA: o molde {_molde[:22]!r} não aparece",
          _molde not in _rep)

# ------------------------------------------------------------------ 4
# E o relatório SAI: a doutrina da dona continua, e nada ficou órfão. O
# piso de segurança não pode reabrir o defeito da Marcelle.
checa("4. o relatório é montado", len(_rep) > 500, f"{len(_rep)} caracteres")
checa("4b. nenhum aspecto sem dona",
      not (_r.get("aspectos_sem_dono") or []),
      str((_r.get("aspectos_sem_dono") or [])[:3]))
import inspect  # noqa: E402

_src_fmt = inspect.getsource(rg.fmt_filtered_aspects)
checa("4c. a declaração de DONA fica mesmo com a remissão desligada",
      "VOCÊ É A SEÇÃO DONA" in _src_fmt
      and "_como_remeter" in _src_fmt)
checa("4d. desligada, o prompt PROÍBE marcador",
      "NÃO escreva marcador nenhum" in _src_fmt)

# ------------------------------------------------------------------ 5
# REINJEÇÃO: a varredura final é o que segura. Movida para antes do
# pipeline — onde ela estava na minha primeira tentativa de hoje — o
# marcador da regeneração volta a escapar, e a asserção 2 tem de cair.
_src_gen = inspect.getsource(rg._generate_report_locked)
_i_pipe = _src_gen.index("_rl.pipeline_lingua(")
_i_varre = _src_gen.index("_marcadores_finais = len(")
checa("5. a varredura final roda DEPOIS do pipeline de língua",
      _i_varre > _i_pipe,
      "antes do pipeline ela não alcança o texto regenerado")

# ------------------------------------------------------------------ 6
# LIGADA, a varredura NÃO apaga: conta, para o gate poder reprovar.
# Esconder o resíduo mascararia o defeito que vamos consertar.
_r2 = _monta(remissao_ativa=True, regenerar_com_marcador=True)
_rep2 = _r2.get("report") or ""
checa("6. LIGADA: marcador residual é CONTADO",
      _r2.get("remissao_marcadores_crus", 0) >= 1,
      f"crus={_r2.get('remissao_marcadores_crus')}")
checa("6b. LIGADA: o resíduo NÃO é apagado em silêncio",
      bool(R.MARCADOR.findall(_rep2)),
      "apagar com a máquina ligada esconderia o defeito")

print()
# ------------------------------------------------------------------ 7
# FLAG-ONLY (13/11). Prova COMPORTAMENTAL, não asserção sobre a fonte:
# em 11/08 uma salvaguarda ficou verde porque a asserção procurava uma
# substring que aparecia duas vezes no caminho. Aqui o gerundial entra
# numa REGENERAÇÃO — o único lugar onde o verifier já não olha mais — e
# o teste exige as duas coisas ao mesmo tempo: aparece no flag, e não
# derruba o relatório.
_GER = ("Há nisso um Sol canceriano a alimentar e proteger quem está "
        "por perto, e isso organiza o resto da leitura.")
_r7 = _monta(False, texto_regen=_GER)
_flags7 = _r7.get("lingua_flags") or []
_kinds7 = [f.get("kind") for f in _flags7]

checa("7a. gerundial REAL numa regeneração aparece em lingua_flags",
      "lingua:gerundial_portuguesa" in _kinds7,
      f"flags={_kinds7}")
_falha7 = str(_r7.get("falha_lingua") or "")
checa("7b. o gerundial NÃO está entre os motivos da falha fechada",
      "gerundial" not in _falha7,
      f"falha_lingua={_falha7[:90]!r}")
_ult7 = ((_r7.get("revisao_lingua") or {}).get("rodadas") or [{}])[-1]
checa("7b2. nem entre os achados da última rodada do pipeline",
      not any("gerundial" in str(a.get("motivo", ""))
              for a in (_ult7.get("achados") or [])),
      f"achados={[str(a.get('motivo'))[:40] for a in (_ult7.get('achados') or [])]}")
checa("7c. o texto do gerundial sobrevive no relatório (não foi apagado)",
      "a alimentar" in (_r7.get("report") or ""))
checa("7d. a frase do flag é localizada, não vem vazia",
      bool(_flags7 and (_flags7[0].get("frase") or "").strip()),
      f"frase={(_flags7[0].get('frase') if _flags7 else None)!r}")
# A varredura tem que vir DEPOIS da ultima regeneracao, como a do marcador.
_src = inspect.getsource(rg._generate_report_locked)
checa("7e. varredura flag-only vem depois do pipeline de língua",
      _src.index("lingua_flags = []") > _src.index("pipeline_lingua"),
      "ancora: `lingua_flags = []` só existe na varredura; "
      "KINDS_FLAG_ONLY aparece 2x (filtro antes, varredura depois)")

if falhas:
    print(f">>> {len(falhas)} FALHOU: {falhas}")
    sys.exit(1)
print(">>> TEXTO FINAL: TUDO PROVADO")
