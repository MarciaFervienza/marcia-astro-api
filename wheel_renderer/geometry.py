"""Geometria compartilhada entre o desenho (packing) e os testes (props).

Módulo SEM imports de propósito: packing → geometry e props → geometry,
nunca um do outro. É o que impede o ciclo app → packing → props → app
(props lê ACTIVE_POINTS de produção; packing é importado pelo app).

Estes números são o ESPECIFICADOR da coluna do planeta, medidos do SVG de
fábrica (Kerykeion 5.12.8, wheel-only, modern):
  · a coluna empilha glifo(r=39) grau(35.5) signo(32) minutos(28) RX(25)
    → faixa radial [24, 40];
  · o elemento mais largo mede 2.70u no raio menor (RX, r=25)
    → 2·asin(1.35/25) = 6.19° de arco; meia-largura 3.10°, arredondada a 3.2°.

prop_cusp_no_overlap acusa qualquer linha de cúspide a menos de
COLUMN_HALF_WIDTH_DEG do centro de uma coluna dentro da faixa radial; o
packing interrompe as linhas usando OS MESMOS números. Teste e desenho
concordam por construção sobre o que é "colar no glifo".
"""

CUSP_CENTER = 50.0            # centro do wheel (viewBox 100x100)
COLUMN_R_INNER = 24.0         # borda interna da coluna do planeta
COLUMN_R_OUTER = 40.0         # borda externa
COLUMN_HALF_WIDTH_DEG = 3.2   # meia-largura angular da coluna
