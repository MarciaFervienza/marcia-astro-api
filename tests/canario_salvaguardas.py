"""CANÁRIO DE SALVAGUARDAS — cada proteção tem um caso que a força a disparar.

POR QUE EXISTE (19/07, pedido da Márcia). Duas salvaguardas morreram em
duas semanas e as DUAS foram achadas por acaso:

  · `_detect_rulership` (18/07) — uma edição minha apagou a tabela
    `_REGENCIA`; o detector rodava e devolvia sempre vazio;
  · a FALHA FECHADA de língua (19/07) — o endpoint lia
    `result["meta"]["falha_lingua"]` e `generate_report` devolve a chave no
    TOPO. Lia None sempre. Nunca teria disparado.

O canário de detectores existe porque detector acende no caminho NORMAL.
Salvaguarda só roda quando algo dá errado — então, em operação saudável,
ela NUNCA é exercitada. Silêncio ali é indistinguível de proteção.

Aqui cada salvaguarda tem um caso que a obriga a disparar. Se qualquer uma
parar, o gate falha na hora em vez de a gente descobrir num relatório.

Inclui também a checagem de FIAÇÃO — as chaves que uma função devolve batem
com as que o consumidor lê. Foi exatamente essa lacuna que matou a falha
fechada.
"""
import warnings; warnings.filterwarnings("ignore")
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app
import report_generator as rg
APP_SRC = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
import revisao_lingua as rl
import text_verifier as tv

mortas = []
vivas = 0


def salvaguarda(nome, disparou, detalhe=""):
    global vivas
    if disparou:
        vivas += 1
        print(f"  DISPARA  {nome}")
    else:
        mortas.append((nome, detalhe))
        print(f"  MORTA    {nome}")
        if detalhe:
            print(f"             → {detalhe}")


print("=" * 70)
print("CANÁRIO DE SALVAGUARDAS — cada uma forçada a disparar")
print("=" * 70)

# ------------------------------------------------------------------ 1
# FALHA FECHADA: defeito que não limpa dentro do teto → relatório não sai.
_T = ("# Mapa Natal — X\n\n## Abertura\n\nTexto bom.\n\n"
      "## Lua: raízes\n\nA frase quebrada mora aqui.\n\n## Saturno\n\nOutro bom.\n")
_orig_det = rl.detectar_sem_sentido
try:
    def _sempre_quebrado(t, call_claude_fn=None, max_workers=6,
                         granularidade="paragrafo"):
        m = re.search(r"## Lua[^\n]*\n\n([^\n]+)", t)
        return [{"frase": m.group(1), "motivo": "não fecha"}] if m else []
    rl.detectar_sem_sentido = _sempre_quebrado
    _n = {"i": 0}

    def _regen(_t):
        _n["i"] += 1
        return f"Tentativa {_n['i']}: segue quebrada."

    _, _lg, _falha = rl.pipeline_lingua(_T, None, _regen,
                                        call_claude_fn=lambda *a, **k: "OK",
                                        max_tentativas=3)
    salvaguarda("falha fechada quando o defeito não limpa", bool(_falha),
                "pipeline_lingua devolveu motivo_falha vazio")
    salvaguarda("teto de tentativas para o ciclo (3 rodadas, sem loop)",
                len(_lg["rodadas"]) == 3 and _lg["regeneracoes"] == 2,
                f"rodadas={len(_lg['rodadas'])} regeneracoes={_lg['regeneracoes']}")

    # teto configurável — um teto que ignora o parâmetro é teto morto
    _n["i"] = 0
    _, _lg2, _f2 = rl.pipeline_lingua(_T, None, _regen,
                                      call_claude_fn=lambda *a, **k: "OK",
                                      max_tentativas=2)
    salvaguarda("teto respeita o parâmetro (2 → 2 rodadas)",
                len(_lg2["rodadas"]) == 2 and bool(_f2),
                f"rodadas={len(_lg2['rodadas'])}")
finally:
    rl.detectar_sem_sentido = _orig_det

# ------------------------------------------------------------------ 2
# RECUSA ANTES DO SPLICE — reescrita de frase (text_verifier)
_orig = "O perigo concreto é entrar num acordo enxergando o potencial alheio."
for _nome, _saida in [
    ("recusa: meta-comentário do reescritor",
     'Ainda há "ela" — corrijo completamente:\n\nO perigo é outro.'),
    ("recusa: saída em blocos",
     "Primeira versão.\n\n---\n\nSegunda versão."),
    ("recusa: saída vazia", ""),
    ("recusa: inchaço", _orig * 4),
]:
    salvaguarda(_nome, bool(tv._motivo_reescrita_invalida(_orig, _saida)))

# ------------------------------------------------------------------ 3
# RECUSA ANTES DO SPLICE — passada de revisão (revisao_lingua)
_p = ("A hesitação já é uma forma de se calar, e isso pesa mais do que "
      "parece no cotidiano de quem escolhe as palavras.")
for _nome, _saida in [
    ("recusa da revisão: palavra INVENTADA",
     _p.replace("hesitação", "hesitação cármica")),
    ("recusa da revisão: frase APAGADA",
     "A hesitação já é uma forma de se calar."),
    ("recusa da revisão: meta-comentário",
     "Aqui está o parágrafo revisado: a hesitação já é uma forma de se calar "
     "e isso pesa mais do que parece no cotidiano de quem escolhe."),
    ("recusa da revisão: blocos a mais",
     _p + "\n\nOutro parágrafo que não estava lá."),
]:
    salvaguarda(_nome, bool(rl.motivo_recusa(_p, _saida)))

# ------------------------------------------------------------------ 4
# INVARIANTES — a revisão não pode tocar no astrológico
_A = "Saturno em Aquário na casa 3, com orbe de 2.4 graus na quadratura com Vênus."
for _nome, _B in [
    ("invariante: troca de SIGNO",
     _A.replace("Aquário", "Peixes")),
    ("invariante: troca de CASA", _A.replace("casa 3", "casa 4")),
    ("invariante: troca de CORPO", _A.replace("Vênus", "Marte")),
    ("invariante: troca de ASPECTO", _A.replace("quadratura", "trígono")),
    ("invariante: troca de NÚMERO", _A.replace("2.4", "3.4")),
    ("invariante: corpo REMOVIDO", "Saturno em Aquário na casa 3."),
]:
    salvaguarda(_nome, bool(rl.divergencia_de_invariante(_A, _B)))

# ------------------------------------------------------------------ 5
# VERIFICAÇÃO PÓS-APLICAÇÃO do verifier: violação que persiste vira alarme.
_src_rv = inspect.getsource(tv.run_verifier)
salvaguarda("pós-aplicação: PERSISTIU_APOS_CORRECAO existe no caminho",
            "PERSISTIU_APOS_CORRECAO" in _src_rv)
salvaguarda("pós-aplicação: INTRODUZIDA_PELA_REESCRITA existe no caminho",
            "INTRODUZIDA_PELA_REESCRITA" in _src_rv)
salvaguarda("pós-aplicação: violação sem frase NÃO some em silêncio",
            "SEM_FRASE_NAO_CORRIGIDA" in _src_rv)
salvaguarda("pós-aplicação: re-roda _detectar_tudo sobre o texto CORRIGIDO",
            "_detectar_tudo(corrected" in _src_rv)

# ------------------------------------------------------------------ 6
# FIAÇÃO — as chaves devolvidas batem com as lidas.
# Foi esta lacuna que matou a falha fechada: o endpoint lia
# result["meta"]["falha_lingua"] e a função devolve a chave no TOPO.
_src_gr = inspect.getsource(rg._generate_report_locked)
_devolvidas = set(re.findall(r'"([a-z_]+)":', _src_gr[_src_gr.rindex("return {"):]))
_src_ep = inspect.getsource(app.generate_report_endpoint)

salvaguarda("fiação: generate_report NÃO devolve chave 'meta'",
            "meta" not in _devolvidas,
            "se passar a devolver, a leitura antiga volta a ser possível")
for _k in ("falha_lingua", "revisao_lingua", "verifier_log", "repetition_lint",
           "crutch_lint", "sign_divergences"):
    salvaguarda(f"fiação: '{_k}' é devolvida E lida do TOPO de result",
                _k in _devolvidas and f'result.get("{_k}"' in _src_ep,
                f"devolvida={_k in _devolvidas} "
                f"lida={f'result.get(chr(34){_k}' in _src_ep}")
salvaguarda("fiação: ninguém lê result['meta'][...]",
            'result.get("meta")' not in _src_ep,
            "leitura impossível — generate_report não devolve 'meta'")

# ------------------------------------------------------------------ 7
# FALHA FECHADA no ENDPOINT: ordem e efeito.
_i = _src_ep.index("FALHA FECHADA DE LÍNGUA")
for _marca, _rot in (("_apply_moon_note", "nota da Lua"),
                     ("_generate_chart_svg", "SVG"),
                     ("pg.generate_pdf", "PDF"),
                     ("send_report_email", "e-mail")):
    salvaguarda(f"falha fechada acontece ANTES de: {_rot}",
                _src_ep.index(_marca) > _i)
_bloco = _src_ep[_i:_src_ep.index("_apply_moon_note", _i)]
salvaguarda("falha fechada responde 422", "422" in _bloco)
salvaguarda("falha fechada alerta o executivo@",
            "_send_failure_alert" in _bloco)
salvaguarda("o alerta carrega seção, frase e motivo",
            all(k in _bloco for k in ('"secao"', '"frase"', '"motivo"')))

# ------------------------------------------------------------------ 7b
# CONTRATO DA FUNÇÃO INJETADA (19/07). As provas do encanamento usavam um
# `regenerar_secao_fn` falso que devolvia STRING. A função REAL,
# `generate_section`, devolve (texto, chunks) — TUPLA. O encanamento
# estourou em 4 dos 5 mapas de QA com "'tuple' object has no attribute
# 'strip'". O dublê não batia com o contrato do original: "o instrumento
# não é o produto", agora dentro do próprio canário de salvaguardas.
_src_reg = inspect.getsource(rg._generate_report_locked)
_i_reg = _src_reg.index("def _regenera(")
# A janela era de 900 chars e ficou curta quando o ramo do Fio Condutor
# entrou — o teste caiu por tamanho de janela, não por defeito. Delimita
# pelo FIM da função aninhada em vez de por um número mágico.
_fim_reg = _src_reg.index("full_report, revisao_log, falha_lingua", _i_reg)
_bloco_reg = _src_reg[_i_reg:_fim_reg]
salvaguarda("contrato: _regenera desembrulha a TUPLA de generate_section",
            "isinstance(_res, tuple)" in _bloco_reg,
            "generate_section devolve (texto, chunks); passar a tupla "
            "adiante estoura no .strip()")
salvaguarda("contrato: o FIO CONDUTOR é regenerável (não é uma 'section')",
            "fio condutor" in _bloco_reg.lower()
            and "generate_fio_condutor" in _bloco_reg,
            "sem este ramo a regeneração do Fio vira no-op silencioso e o "
            "ciclo falha fechado sem nunca ter tentado")
_src_pl0 = inspect.getsource(rl.pipeline_lingua)
salvaguarda("no-op de regeneração é REGISTRADO, não silencioso",
            "NÃO É REGENERÁVEL" in _src_pl0 and "nao_regeneraveis" in _src_pl0)
_sig_gs = inspect.getsource(rg.generate_section)
salvaguarda("contrato: generate_section AINDA devolve tupla (se mudar, revisar)",
            "return text, chunks" in _sig_gs,
            "se passar a devolver só texto, o desembrulho vira ruído — mas "
            "isinstance() continua correto")

# ------------------------------------------------------------------ 8
# REGENERAÇÃO EM PARALELO — em série o pior caso crescia com o número de
# seções (299s com 3, contra o teto de 300s do proxy).
_src_pl = inspect.getsource(rl.pipeline_lingua)
salvaguarda("regeneração de seções roda em PARALELO",
            "ThreadPoolExecutor" in _src_pl,
            "em série o pior caso chega a 299s com 3 seções")
salvaguarda("aplicação das seções é de TRÁS PARA FRENTE (offsets)",
            "key=lambda x: -x[1]" in _src_pl,
            "substituir de frente desloca os offsets das seguintes")

# ------------------------------------------------------------------ 9
# O word_lint NÃO PODE REESCREVER (19/07). Ele foi a FONTE dos defeitos:
# acusou "autossacrifício" de palavra colada e o reescritor PARTIU em
# "auto sacrifício"; "monitorando" virou "estava a monitorar".
import word_lint as _wlm
_src_dt = inspect.getsource(tv._detectar_tudo)
_i_wl = _src_dt.index("from word_lint import")
_bloco_wl = _src_dt[_i_wl:_i_wl + 2200]
salvaguarda("word_lint é FLAG-ONLY — nunca reescreve",
            'violations_all[-1]["no_rewrite"] = True' in _bloco_wl
            and 'if v["kind"] == "palavra:corrompida"' not in _bloco_wl,
            "se voltar a reescrever, volta a PARTIR palavra correta")
salvaguarda("R3 voltou mas é FLAG-ONLY (nunca reescreve)",
            "palavra:corrompida" in inspect.getsource(_wlm.word_lint)
            and "FLAG-ONLY" in inspect.getsource(_wlm.word_lint),
            "com ~19 falsos por 5 relatórios, reescrever seria corrupção")
salvaguarda("encanamento de língua LIGADO por padrão",
            'body.get("revisao_lingua", True) is not False' in APP_SRC,
            "desligado por padrão significa cliente sem proteção")
salvaguarda("nome de corpo não é tratado como infinitivo (R5)",
            "_NOMES_CORPO" in inspect.getsource(_wlm._acento_faltando),
            "'Júpiter opera' e 'mover Urano' eram 5 dos 29 falsos")

print()
print("=" * 70)
print(f"VIVAS: {vivas}    MORTAS: {len(mortas)}")
print("=" * 70)
if mortas:
    for n, d in mortas:
        print(f"  MORTA: {n}")
    raise SystemExit(1)
