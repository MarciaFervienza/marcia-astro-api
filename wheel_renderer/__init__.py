"""Correção da mandala natal do Kerykeion.

O único módulo usado em produção é `packing` (importado por app.py). Os demais
são o aparato de prova, rodados à mão:

  packing.py     — a correção: separação calculada por cadeia global, cada corpo
                   confinado à interseção casa ∩ signo das cúspides Placidus reais.
  props.py       — as 7 propriedades. Conferem o SVG contra o MODELO do Kerykeion,
                   nunca contra o que o renderer pretendia fazer (regra R2).
  prove_bite.py  — prova que cada propriedade morde: injeta o defeito que ela deve
                   pegar e verifica que ela grita sozinha. Inclui a regressão real:
                   o packing antigo (por grupo) reprovado no mapa da Monica.
  censo.py       — 500 mapas × 2 seeds, fábrica vs packing. Critério: zero.
  clientes.py    — os 5 mapas de clientes reais aprovados, com o pós-processamento
                   idêntico ao de produção.

Uma propriedade que nunca falha não é teste, é decoração — foi assim que 34/34
passaram com os glifos sumidos do PDF.
"""
