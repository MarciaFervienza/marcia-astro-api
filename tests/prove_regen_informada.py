"""A regeneração sabe o que deu errado — e as órfãs não somem.

Três propriedades, todas offline (dublês, sem chave de API):

  1. `pipeline_lingua` PASSA os achados da seção para o regenerador.
  2. Um regenerador CEGO (aridade 1) não recebe nada — e é por isso que
     o mesmo defeito sobrevive às 3 rodadas.
  3. Frase apontada que não se localiza em nenhuma seção, HAVENDO outras
     seções localizadas, entra em `log["orfas"]` em vez de sumir.

Contexto (13/11): 4 de 10 mapas falharam fechados, e o mesmo mapa passava
numa geração e falhava na seguinte. A causa não era texto difícil — era
`_regenera(titulo)` chamar `generate_section` com os mesmos argumentos da
primeira vez. Re-rolagem, não correção.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import revisao_lingua as rl  # noqa: E402

FALHAS = []


def checa(nome, cond, detalhe=""):
    if cond:
        print(f"  ok   {nome}")
    else:
        print(f"  FALHOU  {nome}  {detalhe}")
        FALHAS.append(nome)


TEXTO = (
    "\n## Como Você Pensa\n\n"
    "Mercurio em Gemeos da agilidade ao seu pensamento. "
    "Peixes e o signo onde Saturno se encontra, e e um signo que sua geracao carrega.\n"
    "\n## Onde Você Amadurece\n\n"
    "Saturno na casa 3 pede que a voz pare de esperar pela perfeicao. "
    "Isso amadurece com o tempo e com a pratica.\n"
)

QUEBRADA = ("Peixes e o signo onde Saturno se encontra, e e um signo que "
            "sua geracao carrega.")
MOTIVO = "geracional:signo_como_agente — o signo age como sujeito"


def faz_detector(frases_por_chamada):
    """call_claude falso. Devolve defeito enquanto o parágrafo ainda
    contiver a frase quebrada; OK quando ela sumiu."""
    def _call(prompt, max_tokens=400, **kw):
        for frase in frases_por_chamada:
            if frase[:40] in prompt:
                return f"FRASE: {frase} | MOTIVO: {MOTIVO}"
        return "OK"
    return _call


# ---------------------------------------------------------------- 1 e 2
print("\n[1] a regeneração recebe os achados")

recebido = {}


def regen_informado(titulo, achados=None):
    recebido[titulo] = achados
    # Consertou: o texto novo não traz a frase quebrada.
    return "Saturno em Peixes pertence a uma coorte inteira, e a casa e que individualiza."


_t, log, falha = rl.pipeline_lingua(
    TEXTO, None, regen_informado,
    call_claude_fn=faz_detector([QUEBRADA]),
    max_tentativas=3, log_fn=lambda *a, **k: None)

checa("1a. pipeline detecta a aridade 2", log.get("regeneracao_informada") is True,
      f"regeneracao_informada={log.get('regeneracao_informada')}")
checa("1b. o regenerador foi chamado", bool(recebido), f"recebido={recebido!r}")
_ach = next(iter(recebido.values()), None)
checa("1c. recebeu achados NÃO VAZIOS", bool(_ach), f"achados={_ach!r}")
checa("1d. o achado carrega a frase real",
      bool(_ach) and QUEBRADA[:30] in (_ach[0].get("frase") or ""),
      f"frase={_ach[0].get('frase') if _ach else None!r}")
checa("1e. o achado carrega o motivo",
      bool(_ach) and "geracional" in (_ach[0].get("motivo") or ""))
checa("1f. com o conserto aplicado, não falha fechado", falha is None, f"falha={falha!r}")


print("\n[2] REINJEÇÃO — regenerador cego (aridade 1) não recebe nada")

cego_chamadas = []


def regen_cego(titulo):
    """Como era antes: só o título. Devolve texto NOVO que repete o
    defeito — porque nada lhe disse qual era."""
    cego_chamadas.append(titulo)
    return QUEBRADA + " E segue valendo."


_t2, log2, falha2 = rl.pipeline_lingua(
    TEXTO, None, regen_cego,
    call_claude_fn=faz_detector([QUEBRADA]),
    max_tentativas=3, log_fn=lambda *a, **k: None)

checa("2a. aridade 1 é detectada como CEGA",
      log2.get("regeneracao_informada") is False)
checa("2b. o cego foi chamado mesmo assim", bool(cego_chamadas))
checa("2c. o defeito SOBREVIVE às 3 rodadas → falha fechada",
      falha2 is not None, f"falha={falha2!r}")
checa("2d. o alerta cita a frase INTEIRA, sem cortar no meio da palavra",
      falha2 is not None and QUEBRADA in falha2,
      "a frase aparece truncada no motivo da falha")


# -------------------------------------------------------------------- 3
print("\n[3] frase órfã não some em silêncio")

ORFA = "Esta frase nao existe em lugar nenhum do relatorio montado."


def det_com_orfa(prompt, max_tokens=400, **kw):
    """A órfã é reportada SEMPRE — é uma frase que o detector cita e que
    não existe no texto, então nenhuma regeneração pode removê-la. É esse
    o caso que gastava as 3 rodadas em silêncio."""
    out = []
    if QUEBRADA[:40] in prompt:
        out.append(f"FRASE: {QUEBRADA} | MOTIVO: {MOTIVO}")
    if "Saturno" in prompt:
        out.append(f"FRASE: {ORFA} | MOTIVO: inventada pelo detector")
    return "\n".join(out) if out else "OK"


regen_ok_chamadas = []


def regen_ok(titulo, achados=None):
    regen_ok_chamadas.append((titulo, achados))
    return "Saturno em Peixes pertence a uma coorte, e a casa individualiza."


_t3, log3, falha3 = rl.pipeline_lingua(
    TEXTO, None, regen_ok, call_claude_fn=det_com_orfa,
    max_tentativas=3, log_fn=lambda *a, **k: None)

checa("3a. a órfã foi registrada", bool(log3.get("orfas")),
      f"orfas={log3.get('orfas')!r}")
checa("3b. o registro nomeia a frase",
      bool(log3.get("orfas")) and ORFA[:30] in log3["orfas"][0]["frase"])
checa("3c. a órfã derruba o relatório (não é ignorada)", falha3 is not None)
checa("3d. o motivo da falha nomeia o problema",
      falha3 is not None and ("nunca foram regeneradas" in falha3
                              or "não foi localizada" in falha3),
      f"falha={falha3!r}")
# A órfã convive com uma seção localizável na rodada 1. O trabalho útil
# não pode ser bloqueado por ela — a seção boa tem de ser regenerada do
# mesmo jeito, e informada.
checa("3e. a seção localizável foi regenerada apesar da órfã",
      bool(regen_ok_chamadas), f"chamadas={regen_ok_chamadas!r}")
checa("3f. e foi regenerada COM os achados dela",
      bool(regen_ok_chamadas) and bool(regen_ok_chamadas[0][1]))


# -------------------------------------------------------------------- 4
# A FIAÇÃO DO LADO DE PRODUÇÃO.
#
# As três acima provam o COMPORTAMENTO de `pipeline_lingua` com dublês.
# Nada nelas impede alguém de voltar `_regenera` para aridade 1 — e aí o
# pipeline detectaria "cega", degradaria em silêncio e os testes
# continuariam verdes. É exatamente a classe da salvaguarda morta de
# 19/07 ("string na fonte" não é comportamento), então aqui a asserção é
# sobre a ÁRVORE SINTÁTICA, não sobre uma substring.
print("\n[4] fiação em report_generator (AST, sem importar deps)")

import ast  # noqa: E402

_src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "report_generator.py")).read()
_arv = ast.parse(_src)

_regenera_def = None
_gs_def = None
_fio_def = None
for _n in ast.walk(_arv):
    if isinstance(_n, ast.FunctionDef):
        if _n.name == "_regenera":
            _regenera_def = _n
        elif _n.name == "generate_section":
            _gs_def = _n
        elif _n.name == "generate_fio_condutor":
            _fio_def = _n

checa("4a. _regenera existe", _regenera_def is not None)
checa("4b. _regenera aceita os achados (aridade >= 2)",
      _regenera_def is not None and len(_regenera_def.args.args) >= 2,
      f"args={[a.arg for a in _regenera_def.args.args] if _regenera_def else None}")
checa("4c. generate_section aceita evitar=",
      _gs_def is not None and "evitar" in [a.arg for a in _gs_def.args.args])
checa("4d. generate_fio_condutor aceita evitar=",
      _fio_def is not None and "evitar" in [a.arg for a in _fio_def.args.args])

# E que o valor REALMENTE chega: dentro de _regenera, as duas chamadas de
# geração passam evitar=. Procurado como keyword de chamada na árvore.
_chamadas_com_evitar = 0
if _regenera_def is not None:
    for _n in ast.walk(_regenera_def):
        if isinstance(_n, ast.Call):
            _nome = getattr(_n.func, "id", None) or getattr(_n.func, "attr", None)
            if _nome in ("generate_section", "generate_fio_condutor"):
                if any(k.arg == "evitar" for k in _n.keywords):
                    _chamadas_com_evitar += 1
checa("4e. as DUAS chamadas de geração passam evitar=",
      _chamadas_com_evitar == 2, f"encontradas={_chamadas_com_evitar}")

# O bloco é andaime: tem de se declarar invisível, senão volta o defeito
# de 08/08 — "A passagem 8 do seu mapa" saiu no PDF da cliente.
_bloco = None
for _n in ast.walk(_arv):
    if isinstance(_n, ast.FunctionDef) and _n.name == "bloco_correcao_dirigida":
        _bloco = ast.get_source_segment(_src, _n) or ""
checa("4f. o bloco se declara ANDAIME (não pode vazar no texto)",
      bool(_bloco) and "ANDAIME" in _bloco.upper())
checa("4g. o bloco não aparece quando não há achados",
      bool(_bloco) and "if not achados" in _bloco)


print()
if FALHAS:
    print(f"PROVE_REGEN_INFORMADA FALHOU: {len(FALHAS)} asserção(ões)")
    for f in FALHAS:
        print(f"  - {f}")
    sys.exit(1)
print("prove_regen_informada: todas as asserções passaram")
