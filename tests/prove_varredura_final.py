"""A varredura final vê as DUAS famílias que não seguram o relatório.

O DEFEITO (14/11, medido no relatório real da Marcelle):

    "O retrograu aprofunda esse processo: antes de falar, você revisita."

"retrograu" não existe — é "retro" + "grau". O `palavra:colada`
identifica isso corretamente. Mesmo assim a palavra foi entregue, e o
meta do relatório não a mencionava em lugar nenhum.

Duas causas somadas:

  1. O verifier roda ANTES do pipeline de língua. A seção de Mercúrio foi
     REGENERADA depois, e o texto novo nunca voltou ao verifier. Prova no
     log entregue: `palavra:colada` disparou UMA vez, em "celebratório",
     e nunca em "retrograu".

  2. A varredura final existe desde 10/11 justamente para isso — mas só
     registrava `KINDS_FLAG_ONLY`, os detectores de língua REBAIXADOS. O
     `word_lint` usa OUTRA marca para a mesma ideia: `no_rewrite`. Duas
     listas para um conceito, e uma família inteira invisível.

Esta prova afirma sobre o TEXTO FINAL, não sobre a fonte.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import text_verifier as tv  # noqa: E402

FALHAS = []


def checa(nome, cond, detalhe=""):
    if cond:
        print(f"  ok   {nome}")
    else:
        print(f"  FALHOU  {nome}  {detalhe}")
        FALHAS.append(nome)


# A frase real, do relatório entregue.
TEXTO = ("\n## Mercúrio: como você pensa\n\n"
         "Com Mercúrio retrógrado na casa 11, esse pensamento meticuloso "
         "opera no território dos grupos. O retrograu aprofunda esse "
         "processo: antes de falar, você revisita.\n")
CHART = {"gender": "feminino"}


print("[1] o detector identifica a palavra inventada")
_v = tv._detectar_tudo(TEXTO, CHART)
_colada = [x for x in _v if x["kind"] == "palavra:colada"]
checa("1a. palavra:colada acende em 'retrograu'",
      any("retrograu" in (x.get("match") or "") for x in _colada),
      f"{[(x['kind'], x['match']) for x in _v]}")
checa("1b. e vem marcado no_rewrite (não reescreve, só aponta)",
      bool(_colada) and _colada[0].get("no_rewrite") is True,
      f"no_rewrite={_colada[0].get('no_rewrite') if _colada else None}")


print("\n[2] ele NÃO está em KINDS_FLAG_ONLY — eram listas diferentes")
checa("2a. palavra:colada fora de KINDS_FLAG_ONLY",
      "palavra:colada" not in tv.KINDS_FLAG_ONLY,
      "se entrou na lista, esta prova mudou de alvo")
checa("2b. e KINDS_FLAG_ONLY segue com os rebaixados de língua",
      "lingua:gerundial_portuguesa" in tv.KINDS_FLAG_ONLY)


print("\n[3] o critério da varredura final cobre as DUAS famílias")
# Reproduz exatamente a condição do report_generator, que é o ponto onde
# o defeito morava. Se alguém voltar a filtrar só por KINDS_FLAG_ONLY,
# esta asserção cai.
def _criterio(v):
    return v["kind"] in tv.KINDS_FLAG_ONLY or v.get("no_rewrite")


_pegos = [x for x in _v if _criterio(x)]
checa("3a. a palavra inventada entra na varredura final",
      any("retrograu" in (x.get("match") or "") for x in _pegos),
      f"{[(x['kind'], x['match']) for x in _pegos]}")

# E o critério ANTIGO não a pegava — é isso que torna a prova honesta.
_antigo = [x for x in _v if x["kind"] in tv.KINDS_FLAG_ONLY]
checa("3b. o critério ANTIGO deixava passar",
      not any("retrograu" in (x.get("match") or "") for x in _antigo),
      "o critério antigo já pegava — então o defeito era outro")


print("\n[4] a fiação em report_generator usa o critério novo")
_src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "report_generator.py"), encoding="utf-8").read()
_i = _src.find("lingua_flags = []")
_trecho = _src[_i:_i + 1800] if _i >= 0 else ""
checa("4a. a varredura final existe", _i >= 0)
checa("4b. e testa no_rewrite, não só KINDS_FLAG_ONLY",
      '_v.get("no_rewrite")' in _trecho,
      "a varredura voltou a filtrar só pelos rebaixados de língua")


print()
if FALHAS:
    print(f"PROVE_VARREDURA_FINAL FALHOU: {len(FALHAS)} asserção(ões)")
    for f in FALHAS:
        print(f"  - {f}")
    sys.exit(1)
print("prove_varredura_final: todas as asserções passaram")
