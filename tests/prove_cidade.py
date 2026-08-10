"""CIDADE: não-encontrada, ambígua, e o city_id assinado (19/07).

O problema medido: `geocode(exactly_one=True)` devolve o PRIMEIRO
resultado, em silêncio. "Santa Rosa" resolve para a CALIFÓRNIA — Santa Rosa
(RS) é a terceira opção. Muda o fuso, muda a hora sideral, muda o
Ascendente e as doze casas. E o relatório sai LIMPO por todos os
detectores, porque não há nada errado com o texto.

Erro silencioso da pior espécie: o produto parece perfeito e descreve outra
pessoa.
"""
import warnings; warnings.filterwarnings("ignore")
import inspect
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

falhas = 0


def chk(rot, cond, det=""):
    global falhas
    if cond:
        print(f"  OK      {rot}")
    else:
        falhas += 1
        print(f"  ERRADO  {rot}" + (f"  → {det}" if det else ""))


print("=" * 66)
print("A) city_id AUTOCONTIDO E ASSINADO")
print("=" * 66)
_id = app._empacota_cidade(-27.8702, -54.4795, "America/Sao_Paulo",
                           "Santa Rosa, Rio Grande do Sul, Brasil")
d = app._desempacota_cidade(_id)
chk("empacota e desempacota sem perda",
    d is not None and abs(d[0] + 27.8702) < 1e-4 and d[2] == "America/Sao_Paulo")
chk("carrega o rótulo completo", d is not None and "Rio Grande do Sul" in d[3])
chk("id ADULTERADO é rejeitado",
    app._desempacota_cidade(_id[:-4] + "0000") is None)
chk("payload trocado sem reassinar é rejeitado",
    app._desempacota_cidade("QUFB." + _id.rsplit(".", 1)[1]) is None)
chk("lixo é rejeitado sem levantar", app._desempacota_cidade("nada") is None)
chk("é AUTOCONTIDO — não depende de cache nem de TTL",
    "cache" not in inspect.getsource(app._desempacota_cidade).lower())

print()
print("=" * 66)
print("B) AMBIGUIDADE REAL x HOMÔNIMA NO MESMO LUGAR")
print("=" * 66)
_amb = [{"rotulo": "Santa Rosa, Califórnia, Estados Unidos da América",
         "tz": "America/Los_Angeles"},
        {"rotulo": "Santa Rosa, Rio Grande do Sul, Brasil",
         "tz": "America/Sao_Paulo"}]
chk("dois PAÍSES → ambígua", bool(app._ambiguidade_real(_amb)))
_mesmo = [{"rotulo": "Santo André, São Paulo, Brasil", "tz": "America/Sao_Paulo"},
          {"rotulo": "Santo André, São Paulo, Brasil", "tz": "America/Sao_Paulo"}]
chk("mesma cidade repetida → NÃO é ambígua (muda metros, não o mapa)",
    app._ambiguidade_real(_mesmo) is None)
chk("uma opção só → não é ambígua", app._ambiguidade_real(_amb[:1]) is None)
chk("lista vazia não quebra", app._ambiguidade_real([]) is None)

print()
print("=" * 66)
print("C) O ENDPOINT: recusa, alerta e não geocodifica de novo")
print("=" * 66)
_src = inspect.getsource(app.generate_report_endpoint)
chk("aceita city_id", "city_id" in _src)
chk("com city_id NÃO geocodifica de novo",
    "_escolhida = _desempacota_cidade" in _src
    and "lat, lng, tz_str, birth_city = _escolhida" in _src)
chk("city_id inválido responde 400 e não gera",
    "Identificador de cidade inválido" in _src)
chk("NÃO ENCONTRADA dispara alerta (era return limpo)",
    "geocode_nao_encontrado" in _src and "_send_failure_alert" in _src)
chk("AMBÍGUA responde 422 e não gera", "cidade_ambigua" in _src and "422" in _src)
chk("AMBÍGUA alerta com as opções", '"opcoes":' in _src)
_i_amb = _src.index("cidade_ambigua")
# O BLOCO da ambiguidade, não o endpoint inteiro. Duas armadilhas aqui, as
# duas do TESTE e não da mensagem:
#   · "desculpas" aparece noutra mensagem do mesmo endpoint;
#   · a frase está PARTIDA pela quebra de linha no fonte ("…estado e o " /
#     "país de nascimento…"), então buscar o trecho inteiro nunca casa.
_bloco_amb = _src[_i_amb:_i_amb + 1200]
chk("a mensagem pede precisão, não se desculpa",
    "informando o estado" in _bloco_amb
    and "de nascimento e eu gero" in _bloco_amb
    and "desculp" not in _bloco_amb.lower())
for _marca, _rot in (("rg.generate_report", "geração"),
                     ("pg.generate_pdf", "PDF"),
                     ("send_report_email", "e-mail")):
    chk(f"recusa por ambiguidade acontece ANTES de: {_rot}",
        _src.index(_marca) > _i_amb)

print()
print("=" * 66)
print("D) A ORDEM DO NOMINATIM É PRESERVADA (decisão da Márcia)")
print("=" * 66)
_sb = inspect.getsource(app.buscar_cidades)
chk("não reordena por país", "sort" not in _sb and "Brasil" not in _sb.split('"""')[2])
chk("devolve rótulo completo", '"rotulo": rotulo' in _sb)
chk("devolve id, lat, lng e tz", all(k in _sb for k in ('"id"', '"lat"', '"lng"', '"tz"')))
chk("busca com menos de 3 letras devolve vazio", app.buscar_cidades("ab")[0] == [])

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("CIDADE 19/07: TUDO PROVADO")
