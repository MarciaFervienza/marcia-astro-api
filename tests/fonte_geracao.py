"""FONTE DO CAMINHO DE GERAÇÃO — uma peça só para os testes que fazem
asserção sobre o CÓDIGO da geração, não sobre o resultado dela.

Por que existe (19/07): três suítes liam `inspect.getsource(
app.generate_report_endpoint)`. Quando o núcleo foi extraído para
`executar_geracao`, as três quebraram de uma vez — não porque o produto
tivesse mudado (nenhum trecho procurado sumiu; isso foi verificado
trecho a trecho), mas porque o instrumento estava amarrado ao NOME da
função em vez de ao CAMINHO.

Um teste que quebra quando o código muda de lugar sem mudar de
comportamento é um teste que vai ser silenciado na próxima refatoração.
Este helper devolve o caminho inteiro — admissão + núcleo — para que a
asserção continue sendo sobre o que ela quer dizer.

Se a geração for repartida de novo, acrescente a função nova a
`_PARTES`. O teste 4 de prove_extracao.py garante que a repartição não
pode deixar o worker chamando função inexistente.
"""
import inspect

_PARTES = ("generate_report_endpoint", "executar_geracao")


def fonte(app_mod=None):
    """Código-fonte concatenado de todo o caminho de geração."""
    if app_mod is None:
        import app as app_mod
    pedacos = []
    for nome in _PARTES:
        fn = getattr(app_mod, nome, None)
        if fn is None:
            raise AssertionError(
                f"tests/fonte_geracao.py: app.{nome} não existe mais. "
                f"O caminho de geração foi repartido — atualize _PARTES.")
        pedacos.append(inspect.getsource(fn))
    return "\n".join(pedacos)
