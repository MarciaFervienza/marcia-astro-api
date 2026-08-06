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
| cúspide sumida/torta/cortada | — | **0** |

1000 mapas sintéticos (500 × 2 seeds), 19 corpos, **8 propriedades** no packing
(a de cúspide entrou em 16/07 com o item 5.1). Reproduzir:
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

### R5. Um módulo, uma instância — ou o uninstall restaura o próprio patch

`import packing` (script em wheel_renderer/) e `from wheel_renderer import
packing` (app.py) criam **duas instâncias** do mesmo arquivo. Se a segunda
importa com os patches da primeira já instalados, ela captura
`_ORIG_* = dm._draw_planet_ring` **já patchado** — e o `uninstall()` dela
"restaura" para o patch. A fábrica medida vira o packing.

> Aconteceu em 16/07: censo importou `props` → `app` → `install()`, depois
> `import packing` (2ª instância). Resultado: **FABRICA 0/15 defeitos** — a
> fábrica não tem 0 defeitos nunca; o instrumento estava medindo o packing
> duas vezes. Pego porque um resultado bom demais é tão suspeito quanto um
> ruim demais.

Blindagem em `packing.py`: cada função patchada carrega o original verdadeiro
em `_packing_orig`; a captura de `_ORIG_*` desembrulha via `_unwrap()`.
Qualquer instância restaura o original real.

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
| 8 | 12 cúspides inteiras no ângulo real | linha sumida, torta ou cortada |

**Prop 8** vive em `CUSP_PROPS` e entra no censo só no modo packing: mede o
contrato do desenho REFORÇADO (as linhas da fábrica existem, mas a 0.07u).
`geometry.py` guarda a geometria medida da coluna — hoje só `CUSP_CENTER` é
consumido; o resto fica como registro de medição (R2).

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

### 5.1 ~~Cúspides quase invisíveis~~ — RESOLVIDO 16/07 (2 iterações da Márcia)

Implementado em `packing.py` (`_reinforced_houselines`): **as 12 linhas de
divisão, todas inteiras**, `0.25u #8a8a9e` (fábrica: 0.07u quase branco);
angulares mantêm 0.6u. **Corpos sentam sobre a cúspide** — a legibilidade vem
da ordem de desenho da fábrica: as linhas saem ANTES dos glifos dentro de
`_draw_planet_ring`, o texto do planeta fica por cima.

Como se chegou aqui (2 decisões da Márcia na impressão, mesma noite):

1. **Interrupção colinear (Opção A)** — a linha cedia passagem onde cruzava a
   coluna de um planeta. Morreu na primeira checagem impressa: o rótulo do
   ASC/MC senta NA própria cúspide, então a linha fugia do próprio indicador
   e o eixo ASC/MC sumia em **100% dos mapas**.
2. **Exceção por par (casa 1 ↔ ASC, casa 10 ↔ MC)** — restaurou o eixo, mas
   criou assimetria: cúspide comum cedia a corpo, angular não. A Márcia
   escolheu coerência: *"restabelece todas as cúspides e deixa corpos
   sentarem sobre a cúspide."*

A propriedade 8 acompanhou o contrato: **12 cúspides inteiras no ângulo real**
(`prop_cusp_lines_whole_and_true`) — pega linha sumida (o defeito que a Márcia
caçou), linha torta (cúspide falsa) e linha cortada (regressão da
interrupção). As três mordidas provadas em `prove_bite.py`. Gate: zero em
1000×2 com 8 propriedades.

> Se algum dia a interrupção voltar (ex.: cliente reclamar da linha sob o
> texto), o histórico completo está nos commits `0e1635e` e `cf91a9c` —
> incluindo o corte 1-D, o piso de toco e a exceção por par. Não reescrever
> do zero.

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

## 9. DECISÕES VISUAIS — registro (criado 17/07)

**Por que esta seção existe.** A fonte da coluna de orbe reapareceu TRÊS vezes
como pedido, e o tamanho da mandala divergiu do código sem ninguém notar. O
ESTADO tinha só duas entradas visuais (§6.1 cores, §6.2 layout da mandala);
tipografia, tabela, capa, pull-quotes e pós-processamento viviam só na
conversa. Decisão visual sem registro volta como pedido.

**Regra:** toda decisão visual entra aqui COM O VALOR REAL DO CÓDIGO ao lado
e o arquivo onde vive. Ao mudar o código, atualizar aqui no mesmo commit.

### 9.1 Mandala
| decisão | valor no código | onde |
|---|---|---|
| tamanho na página | `WHEEL_SIZE_CM = 14.5` | `pdf_generator.py` |
| conteúdo do viewBox | 92 de 100 unidades | `wheel_renderer` |
| mandala sozinha na página | sim, `PageBreak` ao fim | `_wheel_page_flowables` |
| bloco de dados | canto superior esquerdo, antes da mandala | idem |

> **DIVERGÊNCIA CORRIGIDA (17/07):** o §6.2 dizia "wheel a 18cm" e TODOS os
> bancos de teste renderizavam a 18cm, enquanto produção usava 14.5cm — o
> cliente recebia **19% menor** do que a Márcia julgava. Tamanhos reais
> medidos no PDF final: grau **7.56pt** a 14.5cm contra **9.39pt** a 18cm;
> minutos 7.00 vs 8.68; RX 6.05 vs 7.51. `clientes.py` agora IMPORTA
> `WHEEL_SIZE_CM` — banco de teste não define tamanho próprio (regra R3).

### 9.2 Tabela de aspectos
| decisão | valor no código |
|---|---|
| planeta A / planeta B | `EBGaramond-Regular` |
| aspecto | `EBGaramond-Italic` |
| **orbe** | `EBGaramond-Regular` (era Inter — reaplicado 17/07, 3ª vez pedido) |
| cabeçalho | `Inter-Medium` 8.5pt — **único elemento em sans; não confirmado pela Márcia** |
| corpo | 10pt |
| padding vertical | 2pt (densidade de tabela técnica) |
| separadores de linha | `show_row_separators=False` (sem réguas entre linhas) |
| ordenação | orbe crescente (mais apertados no topo) |
| sem negrito em orbes apertados | todos os aspectos com o mesmo peso |
| nota de rodapé | texto fixo aprovado; whitelisted de todos os scans |

### 9.3 Cores
`COLOR_IVORY #F8F5EF` (fundo) · `COLOR_CHARCOAL #2F2F2F` (texto) ·
`COLOR_GOLD #C7A66A` (filetes, número de página) · `COLOR_TABLE_GRID #E6DFCE`.
Aspectos: sextil verde, trígono azul, quadratura/oposição vermelha, conjunção
cinza (`ASPECT_COLORS` em `app.py`).

### 9.4 Pull-quotes (páginas de respiro)
- Sempre em **página própria**: `CondPageBreak(24cm)` na entrada + `PageBreak`
  na saída. A quebra de ENTRADA faltava até 17/07 — a citação caía na página
  onde o parágrafo anterior terminava (pego pela Márcia no PDF da Helena).
- Espaço acima: 5cm. Inseridas a cada 4 seções; nunca após Abertura nem antes
  do Fio Condutor.
- Duplicam texto por DESIGN — fora do `repetition_lint`.

### 9.5 Pós-processamento do SVG (`app.py` ~392-440)
| elemento | decisão |
|---|---|
| símbolo de aspecto no miolo (`#orbN`) | **REMOVIDO** — só as linhas coloridas |
| leader lines (`kr:node='Indicator'`) | **REMOVIDAS** — comportamento Astro Gold |
| glifo da Lua Negra Lilith | **ESPELHADO** (o do Kerykeion estava invertido) |

> Os três são frágeis a atualização do Kerykeion: viram no-op silencioso se o
> marcador semântico mudar. `clientes.py` replica os três — se mudar num,
> mudar no outro (foi assim que a Márcia quase aprovou um PDF que não era o
> produto, 16/07).

### 9.7 Tabela de posições, painel de elementos e Nodo Sul (17/07)

**Tabela de posições** (`positions_table.py`): glifo + nome do corpo · glifo +
nome do signo · casa. Sem grau nem minuto (a mandala e a tabela de aspectos
já dão). Duas colunas, EB Garamond 8.5pt, sem separadores.
- A CASA é a **geométrica** — a que a mandala desenha. `points[*]["house"]`
  já vem mutado pela regra dos 5°, então `app.py` preserva a original em
  `house_geometric` ANTES da mutação. Sem isso a tabela contradiria o desenho.
- A lista de corpos vem de `app.ACTIVE_POINTS` (R3). A primeira versão tinha
  lista própria e trouxe o Nodo Sul quando produção não o desenhava.
- Os glifos são os `<symbol>` do MESMO SVG da mandala, já pós-processado
  (Lilith espelhada). Nada é redesenhado.

**Painel de elementos e modalidades**: contagem pura, sem interpretação.
Conta 10 planetas tradicionais + Ascendente + MC = 12 (sem asteroides, nodos,
Lilith ou Quíron). Property test: soma 12 nos dois eixos, cada corpo no
elemento/modalidade certo; mordida provada (incluir Ceres faz virar 13).
- Posição: **abaixo da mandala, alinhado à ESQUERDA** (escolha da Márcia).
- Largura 4.04cm — medida com `stringWidth`, era 6.60cm com ~3.2cm de vazio.
- Respiro de **0.15cm** entre mandala e painel: é o MAIOR que ainda mantém
  uma página com o wheel a 18cm (0.20 já joga para a segunda). Medido no
  build, não estimado.

**Nodo Sul DESENHADO** na mandala. Ele já era calculado e usado em `points`,
nos aspectos manuais e no texto — faltava só em `ACTIVE_POINTS`, que é o que
alimenta o renderer. Com 20 corpos: censo 1000 mapas × 2 seeds = **0
violações** nas 8 propriedades.

**Defeito de glifo corrigido:** a maioria dos símbolos do Kerykeion é
desenhada por TRAÇO (`stroke: #x; fill: none`), não por preenchimento.
Recolorir tudo para `fill` apagava Lua, Mercúrio, Vênus, Marte, Júpiter e
Saturno da tabela — o `fill: none` seguinte anulava. Cada atributo é
recolorido no seu lugar.


### 9.6 Pendente de confirmação da Márcia
- ~~Tamanho da mandala~~: **18cm**, decidido 17/07. Nota: a 18cm a mandala
  ultrapassa a margem de texto em 1.3cm de cada lado (frame 15.4cm). Cabe no
  papel; rompe o alinhamento com o resto do relatório — é efeito desejado.
- **Cabeçalho da tabela** em Inter-Medium: unificar em EB Garamond ou manter
  o contraste sans/serif?
- **Legenda de glifos**: (a) página da tabela ou (b) página da mandala.

---

## Decisões da Márcia — 16/07/2026 (adendo pós-revisão)

- **"O Pai e as Ferramentas da Vida"** (título de seção): MANTÉM. Decisão
  dela, não sobra de template. Não renomear.
- **"Tesão"**: fica. É a voz dela — não entra em filtro de registro nenhum.
- **"Aquilombada"**: no léxico proibido (`termo_rejeitado`). Origem rastreada:
  grep em TODOS os transcripts (Consultas 2019–2025, CFCA Ano 1/2), nos
  chunks de aula e nos autorais — zero ocorrências. A fonte está limpa;
  veio da síntese. O léxico basta.
- **REGRA DOS 5° (leitura de casa)**: planeta a menos de 5° da cúspide da
  casa seguinte é LIDO na casa seguinte. Interpretativa, não visual:
  · a MANDALA NÃO MUDA — o wheel desenha na longitude real, do lado real
    da cúspide; nenhuma property do renderer é tocada;
  · aplicada na FONTE (`points[*]["house"]`, app.py, antes da síntese) —
    seções, clusters parentais, queries do RAG e partial_coverage herdam;
  · vale para as 12 cúspides incluindo ângulos (corpo a <5° do ASC lê na
    casa 1) — assunção da Márcia, confirmada em uso (Juno da Gisela 12→1);
  · não existe verifier de casa de planeta para atualizar — a validação de
    cúspide confere signo de cúspide e não é afetada;
  · auditoria por geração no meta: `house_reading_moves`;
  · medida: média 2.95 corpos/mapa no censo de 500 (mediana 3, máx 8;
    22 mapas movem zero). Júpiter da Helena 10→11 como previsto.

## Adjudicações da Márcia — 17/07/2026 (inventário GPT + spec final)

**Estilo protegido — NUNCA corrigir nem criar detector:**
- Fragmentos retóricos ("Porque o Sol, que deveria ser o motor de tudo
  isso, está em Câncer." como frase isolada). Se algum lint pegar, whitelist.
- "independente de" (por "independentemente") e "as coisas saem errado" —
  registro falado, voz dela. Não formalizar.
- Nota de rodapé da tabela de aspectos: texto fixo de template aprovado —
  mascarado de todos os scans (`_FIXED_TEMPLATE_WHITELIST`).
- Pull-quotes (páginas de respiro) duplicam por DESIGN — fora da regra de
  repetição (e fora por construção: vivem na camada do PDF). Seletor fraco
  delas = item pós-lançamento, já filado.

**Negação-substituição — três baldes (spec fechada):**
- Balde 1 (verifier, regex): só a família que NEGA e SUBSTITUI — 11
  variantes novas + as 8 antigas em `_NEGATION_SUBSTITUTION_PATTERNS`,
  incluindo o padrão geral de VERBO REPETIDO ("não pede X. Pede Y").
  12 frases reais do inventário mordidas na prova; 6 protegidas limpas.
- Balde 2 (só prompt): densidade de contraste — máx 2 pivôs antitéticos
  por seção, formas variadas; "aprender a distinguir X de Y" conta como
  fórmula de fechamento e segue as regras de variação dela.
- Balde 3 (não toca): "mas" simples, comparativos, língua.

**Repetição seção-a-seção (spec fechada):**
- Proibido quase-verbatim entre seções; Fio recapitula COMPRIMIDO, nunca
  colado. Lint objetivo: janela de 12 palavras idêntica em 2 seções →
  `repetition_lint` no meta (gate exige []). Prompt: dono da leitura
  (sol_saturno = eixo parental; saturno = Saturno adulto; casa_4 = só
  ambiente, 1-2 frases por corpo) + regra geral de não-releitura.

**Detectores novos:** slot de gênero (maternidade↔paternidade vs gênero do
sujeito — caso real do Lucca provado), reencuadrar, tenciona→tensiona,
"conjunção cerrada"→apertada/justa, gramática pontual (te exilaram,
confiança profissional, quarto sozinho).

**Gate da regeneração única:** GPT extração de novo + leitura da Márcia +
Marcelle. Pendências antes de regenerar: regras de reescrita por idade (F).

## Voz e idade desacoplados — arquitetura F (decisão da Márcia, 17/07 tarde)

**Dois interruptores independentes:**
- **VOZ** (formulário, campo `report_for` + `relationship` opcional):
  (a) meu / (b) presente para outra pessoa ler → segunda pessoa ("você");
  (c) sobre outra pessoa, para o remetente ler → TERCEIRA pessoa com o nome
  ("Lucca tem…"). Parentesco informado pode ser usado ("seu filho Lucca");
  sem ele, nome puro — NUNCA se assume parentesco. Parentesco que contradiz
  o gênero do sujeito é descartado com aviso no meta.
- **CONTEÚDO** (idade da data de nascimento): criança ≤12 / adolescente
  13–17 / adulto ≥18. Faixa computada e exposta no meta
  (`voice.age_bracket`); as REGRAS POR SEÇÃO estão pendentes — a Márcia
  define sobre a lista de seções com conteúdo adulto.
- **TRAVA:** sujeito menor de 18 → voz forçada em (c), independente do
  formulário (`voice.forced_minor: true` no meta). A tubulação serve aos
  dois casos — menor e adulto-sobre-outro. Recomendação Wix: quando a data
  indicar menor, o formulário nem oferece a escolha de voz.

**Implementação:** `app.py` resolve modo+idade+trava → `body["_voice"]`;
`report_generator.voice_rules_block` injeta as regras nos DOIS prompts
(seções e Fio), prevalecendo sobre o "você" padrão; detectores conscientes
no MESMO commit (`_detect_voice_violations`): em 2ª pessoa, verbo+nele/nela
é o defeito; em 3ª, o defeito INVERTE — possessivo de 2ª + termo astrológico
("sua Lua") só pode estar se dirigindo ao sujeito, e artigo com gênero
errado antes do nome ("a Lucca") cobre os pronomes do sujeito. O "você"
dirigido ao LEITOR (orientação ao responsável) continua permitido e não
casa os padrões. Provas: 6 casos de voz + 7 de trava/parsing, tudo verde.

## Item F — escopo final (decisão da Márcia, 17/07 à tarde)

**Não haverá versão para menores.** "Mapa infantil" é disciplina própria
(orientação de pais, metodologia distinta) que a Márcia não oferece; o
produto é leitura de autoconhecimento para adultos. Consequências:

- Degraus de idade e regras de conteúdo por seção: **mortos**. Se um dia
  existir um produto mapa infantil, nasce separado.
- **Trava nova (fail-closed): menor de 18 NÃO GERA.** HTTP 403 com
  mensagem clara + alerta `age_gate_refusal` para o executivo@ (reembolso
  se algum pagamento escapar). `voice.age_bracket` fica no meta como
  registro. O `forced_minor` (terceira pessoa forçada) foi removido.
- **Bypass interno de QA:** mesmo mecanismo da isenção de rate limit
  (`_RATE_EXEMPT_EMAILS`). O Lucca segue como mapa de teste — as frases
  defeituosas catalogadas estão no relatório dele; a regeneração dele prova
  que as correções mordem. `voice.age_gate_bypassed: true` no meta.
- **Interruptor de voz FICA** (commit 22ab318): report_for a/b/c +
  relationship — serve adultos comprando sobre outros adultos. A inversão
  dos detectores por VOZ fica; a inversão condicionada a MENOR não existe
  mais (era só a trava do app; detectores nunca dependeram de idade).

### Arquivo de referência: seções com conteúdo adulto (censo de 17/07)
(Sem uso no produto atual. Referência caso um produto infantil nasça.)
- Núcleo adulto: venus_marte (parceiro/desejo), lilith (erótico/poder),
  asteroides (Juno casamento; Ceres maternidade).
- Enquadramento adulto: plutao ("se um dia você tiver filhos"), netuno
  (idealização relacional), casa_4 (para criança seria a casa atual, não
  memória), casa_5/urano com hora (filhos).
- Atenção máxima: lua, sol_saturno e Fio Condutor — leitura parental
  descreve pais presentes e vivos; Fio tem amarração parental obrigatória.

## Leitura completa da Márcia — 17/07/2026 (21 achados)

### Diagnósticos
1. **A regra dos 5° NÃO vazou para a mandala.** Provado: SVG byte-idêntico
   com `points[*].house` intacto e mutado (o renderer reconstrói o subject
   via `from_birth_data` e nunca lê `body["points"]`). O que a Márcia viu é
   **o packing**: Júpiter está a 0.41° da cúspide 11 e é desenhado a 7.85°
   (19× mais longe), enquanto o Sol fica a 0.15° dela. Causa: Sol e Júpiter
   a 0.58° um do outro; o packing separa e empurra Júpiter para o único
   lado com espaço. **Tensão em aberto, decisão dela:** com a regra dos 5°,
   o texto lê Júpiter na casa 11 e a mandala o desenha fundo na 10 — cada
   um correto pela sua regra, contraditórios para o leitor.
2. **Fio Condutor em 3ª pessoa:** `voice_rules_block` retornava "" no modo
   segunda pessoa, e o template do Fio nunca instruiu pessoa — ainda
   enquadra o sujeito em 3ª ("relatório para {name}"). Corrigido: o bloco
   de voz é SEMPRE emitido (2ª ou 3ª), com regra explícita para a síntese.

### Regra dos 5° — condição de signo
Corpo só é lido na casa seguinte se **corpo e cúspide seguinte no mesmo
signo**. Fronteira de signo barra (regências diferentes). Efeito medido:
- Juno da Helena (Gêmeos vs cúspide 8 em Câncer) — **barrada, fica na 7**
  (o erro que a Márcia pegou);
- Urano do Carlos (Libra vs Escorpião) — barrado, caso novo;
- censo 500: média 2.95 → **2.75** corpos/mapa (97 movimentos barrados).

### Camadas de lint — divisão de trabalho (importante)
- `spell_lint` (novo, **flag-only**): corretor pt-BR + `domain_lexicon.txt`.
  Pega **o que não é palavra**: mutable, orgullo, saturina, reencuadrar.
- **Glossário de signo** (`_SIGN_ADJECTIVE_ERRORS`): pega **o que é palavra
  mas está errada no domínio** e dá a correção certa: virgiliana →
  virginiana. NOTA FACTUAL: o dicionário pt do pyspellchecker é lista de
  frequência e também flagrou "virgiliana" — mas só como "desconhecida".
  Um dicionário mais completo a deixaria passar; o glossário é a camada
  autoritativa. As duas não se substituem.
- `pyspellchecker` **não estava no requirements.txt** — a rodada 2e descrita
  no docstring nunca rodou em produção. Adicionado.
- `crutch_lint` (novo): palavra-muleta acima de 4 ocorrências por seção
  ("real" foi a desta rodada). Reporta, não reescreve — pega a PRÓXIMA.

### Doutrina de síntese (regras permanentes)
- **Casa 4 = ambiente**, nunca figura de cuidado (figura de cuidado é a Lua).
- **Planeta primeiro**: casa com planeta começa pelo planeta; signo da
  cúspide compõe.
- **Saturno-Quíron em oposição**: síntese obrigatória (ferida + autoridade),
  independente de signo.
- **Netuno-Plutão**: nunca mencionar (geracional) — prompt + detector.
- **Signo não é agente geracional** — só planeta transpessoal em signo.

### Pendente de decisão dela
- **Item 18** (títulos por função): opções propostas, aguardando escolha.
- **Tensão mandala × regra dos 5°** (diagnóstico 1).

## Mandala: por que Sol e Júpiter da Helena se afastaram (17/07)

Pergunta da Márcia — a memória dela estava certa. Medido nas três versões:

| versão | Júpiter → cúspide 11 | separação Sol–Júpiter |
|---|---|---|
| fábrica (8° fixo) | 0.41° | 8.00° |
| packing POR GRUPO (até 16/07) | 0.41° | **0.56°** |
| cadeia global (atual, `84b1843`) | 7.85° | 8.00° |

Mudou no `84b1843` — a correção do amontoamento da Monica. O packing por
grupo agrupava por (casa, signo); Júpiter em (10, Virgem) e Sol em (11,
Virgem) são grupos DIFERENTES e não se enxergavam, então cada um ficava na
posição real. Era a mesma cegueira que desenhava Sol e Marte da Monica a
0.30°. **Na versão que ela lembra, Sol e Júpiter estavam a 0.56° — a mesma
sobreposição, num par que não foi examinado de perto.** Não houve
regressão: houve troca de um defeito por outro, e a cadeia global é a
escolha correta.

### Protótipo (b) — DESCARTADO (decisão dela: fica na (a))
- **(b) como especificada é um no-op.** Peso na mesma otimização (PAVA já
  minimiza Σ w·desloc²) não muda nada com K = 1…16: Sol e Mercúrio
  preenchem a caixa casa 11 ∩ Virgem (4.32°) e não há folga para
  redistribuir. Preferir um lado só funciona quando existe escolha.
- **(b') — reduzir a separação** é o único lever que move Júpiter
  (7.85° → 3.83°). Custo medido: **43 violações de compressão em 41 mapas
  de 1000**; 16 deles abaixo de 3° (onde as colunas colidem), pior caso
  2.10°. No mapa da Helena o b' é limpo (0 violações), mas o Sol/Júpiter/
  Mercúrio encosta visualmente.
- Três refinamentos tentados (só pares que atravessam a cúspide; teto nunca
  abaixo do exigido pela propriedade; posições verdadeiras em vez das
  clampadas) reduziram mas não zeraram — a redistribuição do solver acaba
  dando a algum par fatia menor que o ótimo geométrico.

## Mandala — investigação FECHADA (17/07, decisão: fica no atual)

Três tentativas de aproximar da cúspide um corpo colado nela. Todas medidas
em 1000 mapas × 2 seeds, todas descartadas. **Não reabrir sem dado novo.**

| tentativa | resultado |
|---|---|
| peso de afinidade de cúspide (PAVA com w) | **no-op**: sem folga para redistribuir (Sol+Mercúrio preenchem a caixa casa 11 ∩ Virgem, 4.32°) |
| teto de separação no par que atravessa a cúspide | teto 4°: mapas com vão < 3° vão de **37 → 81**, 43 violações. Teto 6°: 40 mapas, **18 violações** |
| anti-inversão (monotonicidade não-estrita) | **165 violações**, 37 mapas com vão mínimo pior (3.79° → 1.84°) |

Critério da Márcia que decidiu: mesmo a 6° as 9 propriedades saem de zero.
Zero é zero.

**Baseline do estado aprovado:** 37 mapas em 1000 (3,7%) já têm vão mínimo
abaixo de 3°. Não é defeito — é o piso geométrico com casa ∩ signo respeitada.

**Ruído conhecido e aceito:** corpo a menos de ~1° de uma cúspide pode ser
desenhado vários graus para dentro da casa anterior (Helena: Júpiter a 0.41°
da cúspide 11, desenhado a 7.85°). A mandala é geometria; a leitura segue a
regra dos 5°. Divergem visualmente nesses casos.

**Pista para o futuro (não implementada):** o TEXTO pode absorver a
divergência — quando a regra dos 5° re-atribui um corpo, a seção registra
que ele está no fim da casa anterior e é lido na seguinte. Custo zero na
mandala, zero risco de encavalamento. Não medido.

**Nota de método:** a "versão antiga sem afastamento" que a Márcia lembrava
era o PDF do protótipo b' gerado nesta mesma sessão (12:02), não uma versão
de produção. Só existiram duas em produção: fábrica (até `84b1843`) e cadeia
global (daí em diante).

## Glifo da Lua Negra Lilith — corrigido (17/07, pego pela Márcia)

**O corpo sempre esteve certo.** Verificado direto no Swiss Ephemeris: o
`Mean_Lilith` que a API pede devolve exatamente `swe.MEAN_APOG` (apogeu
médio da órbita lunar = Lua Negra Lilith média). Não é Selena/White Moon.
Nenhuma leitura do texto foi afetada — a seleção da API está correta.

**O glifo estava espelhado.** O Kerykeion desenha o símbolo com a barriga
da lua à direita; a Lua Negra Lilith tem a barriga à esquerda. Corrigido em
`packing.fix_lilith_glyph`, chamado do pós-processamento do `app.py` e do
banco de testes (`clientes.py`) — uma função, dois chamadores, para não
repetir a divergência do tether de 16/07.

Verificado: posição e display_angle **idênticos** (326.8188° antes e
depois), 9 propriedades em zero, idempotente.

**Guarda contra no-op silencioso:** a função confere a assinatura do path
conhecido. Se o Kerykeion mudar o glifo — inclusive para corrigi-lo — o
espelhamento NÃO é aplicado e o chamador loga aviso. Espelhar conteúdo
desconhecido reintroduziria o erro em silêncio, que é exatamente como este
defeito sobreviveu tanto tempo.

## Spellcheck europeu — defeito que EU introduzi (17/07, 2ª rodada)

**Causa:** existia em `text_verifier` um detector dormente (rodada 2e,
`_detect_unknown_words`) que devolvia `[]` porque `pyspellchecker` não
estava instalado. Ao adicionar a lib ao `requirements.txt` em `c93a9ae`
— para o `spell_lint` novo — o detector ACORDOU.

**Efeito:** o dicionário "pt" do pyspellchecker é português EUROPEU.
Ele não conhece "contato", "bônus", "perspectiva", "harmônico" e sugere
"contacto", "bónus", "perspetiva", "harmónico". O verifier passou a
reescrever pt-BR em pt-PT e a APLICAR: 49 "correções" no relatório da
Helena (contra 4 na rodada anterior). **A contaminação PT-PT que a Márcia
reportou no relatório foi produzida por mim, não pelo modelo.**

**Correção:** detector 2e DESLIGADO (no scan e na re-verificação por
frase). Não bastava ignorar a sugestão — a palavra brasileira correta
seria flagrada como violação e mandada para reescrita mesmo assim.

**O que faz o trabalho de léxico:** listas explícitas (família espanhola,
família PT-PT, termos em inglês, gramática pontual) e glossários fechados
de signo e de planeta — todos com a forma CORRETA na sugestão, que é o que
o reescritor precisa. Um corretor genérico não tem isso.

**`spell_lint` continua**, flag-only, com a limitação documentada: com este
dicionário ele acusa palavra brasileira correta ("conhecê-lo",
"sustentá-la", "ruptura", "coorte"). Só vira gate depois de a whitelist
crescer muito — ou de existir dicionário pt-BR de verdade.

**Lição de método:** instalar uma dependência pode ATIVAR código dormente.
Antes de adicionar lib ao requirements, procurar o que já a importa.
