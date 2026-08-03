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

# A linha da cúspide angular NÃO cede passagem ao rótulo do próprio ângulo.
# O Ascendente e o Meio-do-Céu são desenhados como ChartPoints em cima da
# própria cúspide (casa 1 e casa 10) — o rótulo grau/signo/minuto deles É o
# indicativo daquela cúspide. Sem esta exceção, a interrupção apagava o eixo
# ASC/MC em 100% dos mapas: a linha fugia do próprio rótulo (visto pela
# Márcia em 16/07). A exceção vale só para o PAR (cúspide, seu ângulo);
# qualquer outro corpo perto da linha continua ganhando passagem.
CUSP_ANGLE_SLUGS = {1: "Ascendant", 10: "Medium_Coeli"}
