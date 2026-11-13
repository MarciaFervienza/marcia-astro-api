"""Cabeçalho de seção é estrutura, não prosa — e nenhum detector o toca.

O DEFEITO (13/11, medido no relatório real da Helena):

    código:    "Júpiter: onde você acredita em si mesmo"
    entregue:  "Júpiter: onde você acredita em si mesma"

Helena é feminina. O `voz:genero_do_sujeito` acende em "si mesmo" DENTRO
DO CABEÇALHO, o verifier obedece e reescreve, e a partir daí o título do
relatório deixa de bater com `sections`. O `_regenera` procura o título
flexionado na lista canônica, não acha, devolve None — e aquela seção
nunca mais pode ser regenerada. O encanamento gasta as 3 rodadas e falha
fechado sem nunca ter tentado consertá-la.

Atinge QUALQUER mapa feminino, e era invisível antes de o motivo da
falha passar a nomear as seções não regeneráveis.
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


CABECALHO = "## Júpiter: onde você acredita em si mesmo"
# O corpo traz um defeito que os detectores DETERMINÍSTICOS pegam — a
# mesma classe que acende no título, mas aqui ela é defeito de verdade,
# porque fala da cliente. É isso que separa "máscara" de "cegueira".
CORPO = ("Júpiter em Virgem ocupa a sua casa 10. "
         "A expansão chega quando você acredita em si mesmo sem pedir licença.")
TEXTO = f"\n{CABECALHO}\n\n{CORPO}\n"
CHART = {"gender": "feminino"}


print("[1] o detector ACENDE no cabeçalho quando o lê sozinho")
# Esta é a prova de que o perigo é real, não hipotético: sem máscara, a
# linha de título é texto como qualquer outro para o detector.
_solto = tv._detect_genero_do_sujeito(CABECALHO, CHART)
checa("1a. voz:genero_do_sujeito acende no título cru", bool(_solto),
      "se isto falhar, o defeito mudou de forma e esta prova está cega")


print("\n[2] a máscara preserva o comprimento (offsets continuam válidos)")
_m = tv._mascara_cabecalhos(TEXTO)
checa("2a. mesmo comprimento", len(_m) == len(TEXTO),
      f"{len(_m)} != {len(TEXTO)}")
checa("2b. a marca ## sobrevive", "## " in _m)
# O trecho do título, na posição dele, tem de estar em branco depois do "## ".
_ini_cab = TEXTO.index(CABECALHO)
_regiao = _m[_ini_cab + 3:_ini_cab + len(CABECALHO)]
checa("2c. o conteúdo do título virou espaço", _regiao.strip() == "",
      f"sobrou {_regiao!r}")
checa("2d. o corpo NÃO foi mascarado", "sem pedir licença" in _m)


print("\n[3] com a máscara, nenhuma violação cai dentro do cabeçalho")
_ini = TEXTO.index(CABECALHO)
_fim = _ini + len(CABECALHO)
_v = tv._detectar_tudo(TEXTO, CHART)
_no_cab = [x for x in _v if _ini <= (x.get("offset") or 0) < _fim]
checa("3a. zero violações ancoradas no cabeçalho", not _no_cab,
      f"{[(x['kind'], x['match']) for x in _no_cab]}")

# E o corpo continua sendo verificado — máscara não pode virar cegueira.
_no_corpo = [x for x in _v if (x.get("offset") or 0) >= _fim]
checa("3b. o corpo SEGUE sendo verificado", bool(_no_corpo),
      "nenhuma violação no corpo — a máscara apagou demais?")
checa("3c. e é o MESMO detector que acendia no título",
      any(x["kind"] == "voz:genero_do_sujeito" for x in _no_corpo),
      f"{[(x['kind'], x['match']) for x in _no_corpo][:4]}")


print("\n[4] o título sobrevive idêntico — é ele que casa com `sections`")
# A consequência que importa: depois de toda a verificação, o título tem
# de continuar sendo a string literal do código.
checa("4a. o título do texto é o literal do código",
      CABECALHO in TEXTO and "si mesmo" in CABECALHO)
_v2 = tv._detectar_tudo(TEXTO, CHART)
_titulo_tocado = any(_ini <= (x.get("offset") or 0) < _fim for x in _v2)
checa("4b. nada pede reescrita do título", not _titulo_tocado)


print()
if FALHAS:
    print(f"PROVE_CABECALHO FALHOU: {len(FALHAS)} asserção(ões)")
    for f in FALHAS:
        print(f"  - {f}")
    sys.exit(1)
print("prove_cabecalho: todas as asserções passaram")
