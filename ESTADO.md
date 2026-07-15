# ESTADO — mandala natal

**Última atualização:** 2026-07-16
**Leia isto primeiro** se a sessão anterior caiu. Fonte de verdade sobre decisões
fechadas e estado do trabalho. Atualize ao fim de cada checkpoint.

---

## 0. STATUS: RESOLVIDO E EM PRODUÇÃO

O defeito de corpos desenhados na casa/signo errado está **corrigido**. A correção é
`wheel_renderer/packing.py`, instalada pelo `app.py` em volta do
`save_wheel_only_svg_file`. Aprovada pela Márcia em 16/07/2026 nos 5 mapas de
clientes reais.

| | fábrica | packing |
|---|---|---|
| mapas com ao menos 1 defeito | 966/1000 | **0/1000** |
| corpos em signo errado | 2.070 | **0** |
| corpos em casa errada | 2.217 | **0** |
| desenho comprimindo corpos | 0 | **0** |

1000 mapas sintéticos (500 × 2 seeds), 19 corpos, 7 propriedades. Reproduzir:
`cd wheel_renderer && python3 censo.py 500`.

---

## 1. O problema (histórico)

O renderer `modern` do Kerykeion 5.12.8 resolve colisão entre planetas próximos
**espalhando os glifos angularmente** com separação FIXA de 8°
(`_resolve_planet_collisions`), sem olhar em que casa ou signo o corpo cai.
Consequência: o mapa **mente** sobre a posição. Não é estética.

Caso de referência: Andreia Filipa Cardoso (20/11/1994 16:45 Lisboa) — Sol 28°05'
Escorpião empurrado ~19° e desenhado em Sagitário.

### A correção — `wheel_renderer/packing.py`

Substitui **um número**, não o renderer. Continua sendo o desenho da fábrica, com os
glifos da fábrica, as cúspides Placidus reais e o `_draw_single_planet_in_ring`
original. Patch em três funções de módulo do `draw_modern` (resolvidas por lookup
global, portanto substituíveis):

- `_draw_planet_ring` — wrapper que só passa as cúspides adiante (o resolver original
  não as recebe).
- `_resolve_planet_collisions` — **a correção**.
- `_draw_single_planet_in_ring` — hoje um passthrough (`SCALE_GLYPHS = False`).

**Como funciona:** uma **cadeia global** de todos os corpos em ordem zodiacal (o
círculo vira reta no maior vão real), cada corpo confinado à sua caixa
`casa ∩ signo`, separação alvo de 8° valendo entre vizinhos **no círculo**.
Formulação: minimizar Σ(display − real)² sujeito a caixa + separação. A substituição
`e_i = d_i − Σ_{j<i} sep_j` troca separação por monotonicidade → regressão isotônica
com caixa → **PAVA + clip**. Onde 8° não cabe, `_fit_seps` encolhe **só a janela que
estoura**, na proporção exata do que falta.

> **Por que a cadeia global, e não por grupo (casa, signo).** Empacotar cada grupo
> isolado foi o defeito de 15/07: dois grupos vizinhos empurram seus corpos contra a
> **mesma fronteira** e eles colidem na costura. No mapa da Monica, Sol (118.91°, fim
> do grupo Câncer) e Marte (127.11°, início do grupo Leão) estão a **8.2° reais** e
> foram desenhados a **0.30°**. As 6 propriedades da época passavam: cada um estava na
> sua casa e no seu signo, esmagado contra a parede. Casa certa, signo certo, ilegível.

**Limite honesto:** onde a geometria não comporta 8°, o packing entrega o ótimo, não
um milagre. Na Monica, 6 corpos vivem entre 104.53° e 133.15° (caixas Câncer ∩ casa 12
e Leão ∩ casa 12): 28.6° / 5 vãos = **5.72° cada, e não existe arranjo melhor** sem
tirar Vênus da casa 12. Menor vão desenhado nos 5 mapas de clientes: 5.10°–8.00°.

### Caminhos descartados (não repetir)

| Tentativa | Por que morreu |
|---|---|
| Monkey-patch de `PLANET_MIN_SEPARATION` | Constante ligada em `_draw_planet_ring.__defaults__` no import. Mudar depois é no-op silencioso. |
| Baixar min_separation (0/2/4°) | 500 mapas × 4 valores: sep=0 → 0% mente mas 78% com glifos colados; sep=4 → 27% mente E 92% colados. Nenhum valor fecha os dois eixos. |
| Estilo `classic` | 63.5%. Não zera. |
| Pós-processar transforms do SVG | 6 invariantes por planeta + 2 transforms superiores. Frágil, acertava por acidente. |
| **Renderer custom (CP1)** | Fabricava `cusps = asc + i*30`. Placidus varia **6.79°–115.50°** → 87.6–90% dos mapas com corpo em casa errada — **pior que o defeito original**. Apagado em 16/07 (`renderer.py`, `battery.py`). |
| Empilhamento radial de coluna completa | Coluna = 17u, anel = 21.5u. Duas colunas → scale 0.63 → grau a 4.76pt. Ilegível já em N=2. |
| Encolher glifos (3 rodadas) | Troca "ilegível por sobreposição" por "ilegível por tamanho". Reprovado pela Márcia nas três. |
| Engrossar cúspides | A linha de casa vai de y=6.5 a y=28 = raio **43.5→22**, que é **exatamente** a coluna do planeta (glifo 39, grau 35.5, signo 32, minutos 28, RX 25). Engrossar = engrossar por cima do texto. Ver §5.1. |
| Packing por grupo `(casa, signo)` | Grupos vizinhos colidem na costura. Ver acima. |

---

## 2. REGRAS DE TESTE — inegociáveis

Cada uma custou um defeito grave. Não reabrir.

### R1. Nenhuma fixture inventa dado que o Kerykeion pode fornecer

Cúspides, casas, posições, retrogradação: **tudo vem do `AstrologicalSubjectFactory`**.
A fixture sintética define **quais corpos e onde** — nunca a geometria derivada.

> Violação: o renderer CP1 fabricava `cusps = asc + i*30`. Resultado: 28% de todos os
> corpos na casa errada; em Reykjavik, 18 de 18. O Sol da Helena está na casa 11 e era
> desenhado na 10.

### R2. Toda asserção confere contra fonte externa, nunca contra premissa compartilhada

Se o teste e o código partem do mesmo pressuposto, **o teste não testa nada**.

> Violações: `row_width` declarava 2.5u para um glifo que mede **4.0u** — bateria 20/20
> com as labels sobrepostas no PDF. E as cúspides fabricadas: fixture e renderer
> partiam de "casas de 30°", então nenhuma asserção podia detectar.

**Corolário:** medir com svglib/`stringWidth`/Kerykeion. **Nunca estimar.** Todo número
estimado neste projeto estava errado — largura de glifo, altura de linha, tamanho do
wheel, margem do anel, arco de casa. Inclusive um comentário que dizia "MEDIDA, não
estimada" sobre um número que era nominal.

### R3. Uma lista, um dono

Toda lista que descreve "o conjunto de corpos" tem **uma** definição. Quem precisa dela
**importa**, não copia.

> Três violações, todas silenciosas:
> · Duas listas de símbolos (12 vs 18) → Nodos/Ceres/Palas/Juno/Vesta emitiam
>   `<use href="#X__mono">` para símbolos inexistentes. **SVG não dá erro: não desenha
>   nada.** A Márcia viu no PDF antes de mim.
> · Censo antigo rodava `AstrologicalSubjectFactory` **sem `active_points`** → asteroides
>   e nodos nunca computados, sumiam no `getattr(..., None)`. "maior cluster 6" era falso.
> · **16/07:** o censo validava 18 pontos enquanto produção desenhava **19** (sem Nodo
>   Sul, **com Ascendente e Meio-do-Céu**). Ascendente e MC ficaram sem teste nenhum —
>   e o packing antigo comprimia Vesta/Ascendente de 2.50° para 0.30° na Monica, defeito
>   que só apareceu quando a lista foi corrigida.

Hoje `props.ACTIVE_POINTS` **importa de `app.py`** e levanta se não conseguir.

### R4. A propriedade tem que reprovar o passado

Um teste que só aprova o código atual não prova nada — pode ter sido afrouxado até
passar. `prove_bite.py` reinstala o **packing antigo** no mapa real da Monica e exige
que a propriedade o condene.

> Aconteceu de verdade em 16/07: escrevi a propriedade de compressão exigindo 8° e ela
> acusou a Monica **injustamente** (5.72° é o ótimo geométrico). Corrigi **o teste**,
> não o código — mas só soube que a correção era honesta porque ela continuou reprovando
> o defeito de 0.30°.

---

## 3. As 7 propriedades — `wheel_renderer/props.py`

Leem **só** o modelo do Kerykeion e o SVG emitido. Se discordam, o desenho mente.

| # | propriedade | pega |
|---|---|---|
| 1 | todos os corpos desenhados | glifo que some em silêncio |
| 2 | `abs_pos` do SVG == modelo | metadado mentindo |
| 3 | display dentro do SIGNO | **o defeito original** |
| 4 | display dentro da CASA | **o defeito original** |
| 5 | tick na longitude real | tether apontando para o lugar errado |
| 6 | cúspides == modelo | cúspides fabricadas (R1) |
| 7 | desenho não comprime | **o defeito da costura entre grupos** |

**Prop 7** exige, por par vizinho, `min(vão_real, 8°, ótimo_geométrico)`. O terceiro
termo é o que a torna justa: para toda janela [a,b] de corpos consecutivos, todos cabem
apenas entre o piso da caixa de `a` e o teto da caixa de `b`; esse espaço / (b−a) é o
máximo que qualquer arranjo honesto alcança. Calculado das cúspides, **não do packing**.

**Prop 5** aplica-se ao SVG **cru**. O `app.py` apaga as linhas-guia depois (§4); num
SVG pós-processado não sobra indicador e a propriedade se cala. A remoção é atacadista:
some UM só e ela pega.

`python3 prove_bite.py` → prova que cada uma morde (injeção + regressão real na Monica).

---

## 4. Pós-processamento do SVG — `app.py` linhas ~392-428

Duas remoções, **ambas decisão fechada da Márcia**:

- **Símbolos de aspecto** (`<use xlink:href='#orbN'>`) — o △/□/☌ no meio de cada linha
  de aspecto. A linha colorida basta. Escopado a `#orbN`; nunca toca glifo de planeta
  ou signo.
- **Linhas-guia** (`<g kr:node='Indicator'>`) — comportamento Astro Gold: sem o fio; o
  grau ao lado do glifo revela a posição. Aprovado 16/07: *"Muito mais clean!!"*

> **FRÁGIL:** os dois regexes dependem do padrão do Kerykeion 5.12.8. Se `kr:node` mudar,
> viram **no-op silencioso** e as linhas voltam ao PDF sem quebrar nada. Antes de
> atualizar kerykeion: rodar `clientes.py` e conferir visualmente.

> **`clientes.py` replica este pós-processamento** (`como_producao()`). Em 16/07 ele
> não replicava, e os 5 PDFs de teste saíam com o símbolo de aspecto e com as linhas-guia
> **engrossadas** — a Márcia quase aprovou um PDF que não era o produto. Se mudar o
> `app.py`, mudar o `clientes.py` junto.

---

## 5. Aberto (nada bloqueia cliente)

### 5.1 Cúspides quase invisíveis — a Márcia quer, e é viável

A fábrica desenha as 8 casas não-angulares com `NORMAL_STROKE_WIDTH = 0.07u` = **0.39pt**
a 18cm, em `#d4d4d8`. Não dá para ver em que casa o planeta está. As angulares (1,4,7,10)
vão a 0.6u = 3.33pt — 8.5× mais grossas.

Engrossar direto **não funciona**: a linha atravessa a coluna do glifo (raio 43.5→22).
Verificado: nenhum planeta se move (18/18 idênticos) — o que a Márcia viu foi a linha
escura cortando o meio dos grupos, lida como mais um elemento amontoado.

**Mas medido em 200 mapas:** só **32.8%** das linhas cruzam a coluna de um planeta
(média 3.9 de 12). **8 das 12 podem ser grossas de graça.** Caminho: grossa onde está
livre, cedendo passagem (gap radial) onde cruza o texto. *Não é preço a pagar — é
trabalho pendente.*

### 5.2 Rótulos de cúspide acima de 55°N

`_draw_cusp_ring` usa offset fixo de ±4.669° (~9.3° por rótulo) sem resolução de
colisão. Defeito de fábrica. **0/300 mapas Brasil/Portugal afetados**; 37/300 em alta
latitude. Nenhum cliente afetado.

---

## 6. Decisões FECHADAS (não reabrir sem motivo novo)

### 6.1 Cores
- Glifos de planeta e signo: **os da fábrica**, intocados.
- Retrogradação: sufixo **`RX`** (nunca só cor — impressão P&B).
- Linhas de aspecto: sextil verde, trígono azul, quadratura/oposição vermelha,
  conjunção cinza (`ASPECT_COLORS` no `app.py`).

### 6.2 Layout
- Wheel a **18cm**, mandala sozinha na página. Conteúdo ocupa **92 units** do viewBox
  de 100 → **5.546 pt/unit**.
- Margem lateral 1.5cm na página do mapa.
- Glifo do signo **fica** na label (redundância proposital: confirma a posição por escrito).

### 6.3 Escala de glifo: NÃO MEXER
`SCALE_GLYPHS = False`. Três rodadas de encolhimento, cada uma pior. `GLYPH_SCALE_MAP`
da fábrica (Sol 1.1, Júpiter/Saturno/Urano/Netuno/Marte/Quíron/Nodos 0.95) fica como
está. Se algum dia reativar: só `planet_scale_base` é dividido pelo multiplicador do
planeta; as **fontes levam `k` puro** (dividi-las também derrubou o Sol abaixo do piso
de 6pt: `2 × 0.54/1.1` = 5.45pt).

### 6.4 Fail-closed
O patch é **global** no módulo `draw_modern`. `app.py` desinstala em `finally` — sem
isso vaza para a próxima requisição do processo Flask. `_constrained_resolve` levanta
`RuntimeError` se rodar sem cúspides no contexto: **não há fallback silencioso**.

---

## 7. Superfície de dependência do Kerykeion 5.12.8

O packing depende de:
- `draw_modern._draw_planet_ring`, `._resolve_planet_collisions`,
  `._draw_single_planet_in_ring` — funções de **módulo**, resolvidas por lookup global.
  Se virarem métodos ou forem inlined, o patch morre (loud: o wrapper deixa de rodar e
  `_constrained_resolve` levanta).
- `dm._zodiac_to_wheel_angle(z, seventh)`
- Os atributos `kr:node='ChartPoint'|'Cusp'|'Indicator'`, `kr:slug`,
  `kr:absoluteposition` — de que **todas as 7 propriedades** dependem para ler o SVG.

**Se o Kerykeion for atualizado:** rodar `prove_bite.py` e `censo.py 500` antes de
aceitar. Critério: **zero**.

---

## 8. Contexto de release

- **Não** liberar 10 testers. No melhor caso 1–2 canários.
- Prazo removido pela Márcia em 15/07: *"não há mais pressa... o trabalho continua até
  acabar, com ou sem Orkney."*
