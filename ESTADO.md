# ESTADO — mandala com layout de labels (stellium)

**Última atualização:** 2026-07-15
**Leia isto primeiro** se a sessão anterior caiu. Este arquivo é a fonte de verdade
sobre decisões fechadas e estado do trabalho. Atualize-o ao fim de cada checkpoint.

---

## 1. O problema

O renderer `modern` do Kerykeion 5.12.8 resolve colisão entre planetas próximos
**espalhando os glifos angularmente** (`_resolve_planet_collisions`, min_separation
8.0°). Consequência: um planeta pode ser desenhado **na casa ou no signo errado**.

Censo (500 mapas sintéticos, 12 corpos, seed 42): **70% dos mapas** têm ao menos um
corpo cruzando fronteira de signo ou de cúspide. Não é questão estética — o mapa
mente. **Bloqueante para lançamento.**

Caso de referência: Andreia Filipa Cardoso (20/11/1994 16:45 Lisboa) — Sol 28°05'
Escorpião empurrado ~19° e desenhado visualmente em Sagitário.

### Caminhos já descartados (não repetir)

| Tentativa | Por que morreu |
|---|---|
| Monkey-patch de `PLANET_MIN_SEPARATION` | Constante é ligada em `_draw_planet_ring.__defaults__` no import. Mudar depois é no-op silencioso. |
| Baixar min_separation (0/2/4°) | 500 mapas × 4 valores: sep=0 → 0% mente mas **78% com glifos colados**; sep=4 → 27% mente E 92% colados. Nenhum valor fecha os dois eixos. |
| Pós-processar transforms do SVG | 6 invariantes por planeta + 2 transforms superiores (`rotate(-90)` global, `scale(0.92)` do anel zodiacal). Frágil e acertava por acidente. |
| Empilhamento radial de **coluna vertical completa** | Coluna completa = **17u**; anel = **21.5u**. Duas colunas exigiriam scale 0.63 → grau a 4.76pt. **Ilegível já em N=2.** O que parecia funcionar era sobreposição (step 6u vs coluna 17u = 11u de encavalamento). |
| Subclassar `ChartDrawer` p/ trocar só o PlanetRing | A chamada a `_draw_planet_ring` dentro de `draw_modern_horoscope` é fixa — sem parâmetro de renderer. Override de `_generate_modern_content` sozinho não substitui o anel. |

---

## 2. Decisões FECHADAS (não reabrir sem motivo novo)

### 2.1 Semântica — define tudo

- **O tick radial na longitude real É a posição astrológica.** Marcador inequívoco.
- **A label** (glifo + grau + glifo do signo + minutos + R) **é anotação deslocada**,
  ligada ao tick por **leader line contínua**.
- Como a label é anotação, ela pode alinhar em coluna.
- Regras das leader lines: **não se cruzam**; labels em ordem zodiacal; cada linha
  termina junto ao seu glifo; associação legível em P&B.

### 2.2 Cores

- Tick: **neutro sempre** (`#4A4A4A`). Tick significa longitude, não movimento.
- Glifos de planeta: **charcoal uniforme** (`#2F2F2F`) em todas as mandalas.
- Retrogradação: **glifo vermelho (`#B94A48`) + sufixo `R`**. Nunca só cor (impressão P&B).
- Linhas de aspecto: mantêm as cores atuais (sextil verde, trígono azul, quadratura/oposição vermelha).
- Anel zodiacal externo: **idêntico ao baseline**. Símbolos originais dos `<defs>`
  ficam **intocados byte a byte**; a uniformização usa **clones `#<id>__mono`** com
  `currentColor`. Nunca reescrever o símbolo original — ele é reusado em outros lugares.

### 2.3 Overflow

**Defeito, não opção.** O bbox completo da label cabe na área permitida (anchor por
quadrante / lado interno / deslocamento / redução até o mínimo legível). Se nenhuma
posição válida existir → **fail-closed**.

### 2.4 Política N > 7 — FECHADA

`MAX_LANES = 7`. Cluster com N > 7 → **fail-closed**: exceção explícita, log dos
corpos e longitudes, **zero entrega parcial**.

**Procedimento manual:** alerta por e-mail para `executivo@marciafervienza.com` com os
dados do mapa. Márcia gera e envia manualmente. Cada disparo é logado.

Incidência medida (500 mapas, **18 corpos = conjunto real de produção**):
- seed 42: maior cluster **8** → **1/500 = 0.2%**
- seed 1337: maior cluster **10** → **2/500 = 0.4%**

> ⚠️ O censo antigo dizia "maior cluster 6, fail-closed 0%". **Estava errado**: rodava
> `AstrologicalSubjectFactory` **sem `active_points`**, então Ceres/Palas/Juno/Vesta e
> os nodos nunca eram computados e sumiam em silêncio no `getattr(..., None)`.
> **Qualquer censo novo tem que passar `active_points=ACTIVE` (os 18 corpos do app.py).**

### 2.5 Fail-closed geral

Se o renderer custom falhar, **a geração para**. Nunca cai no renderer antigo, nunca
omite planeta, nunca renderiza parcial. Falha explícita > PDF aparentemente válido.

### 2.6 Layout / página

- Wheel a **18cm**, mandala sozinha na página.
- Margem lateral **1.5cm** só na página do mapa (verificado: bloco de dados no canto
  superior esquerdo cabe — linha mais larga ~9cm).
- Glifo do signo **fica** na label (redundância proposital: é o que confirma a posição
  por escrito).
- Glifos de cluster **podem** ficar menores que os de planetas isolados (opção "a"
  aprovada). Em N=6, ~35% menores.

---

## 3. Estado do CP1 — **20/20 PASSANDO** (2026-07-15 20:20)

Bateria: **20 passaram · 0 falharam** (19 casos + N=8 fail-closed).

**Código no repo** (não mais só no scratchpad — foi assim que quase perdemos o CP1):
- `api/wheel_renderer/renderer.py` — renderer + asserções
- `api/wheel_renderer/battery.py` — bateria de cobertura
- `api/wheel_renderer/kerykeion_defs.svg` — defs extraídos do Kerykeion

Rodar: `cd api/wheel_renderer && SP=. python3 battery.py`
PDFs: `~/Desktop/mapa-natal-pdfs/CP1_v2/`

### Provado por asserção (não por inspeção)

- **Ticks**: `tick_renderizado == wheel_angle(abs_pos)` dentro de 0.01° — zero erro.
- **Labels**: grau/signo/minutos/retro conferem com o dado real — zero erro.
- **Leaders**: zero cruzamento; nenhuma atravessa a coluna de labels.
- **Bbox**: cada LINHA dentro do anel (não só a coluna — ver bug do anel não-convexo).
- **Defs**: `originals_present: True` (byte a byte), 24 clones `__mono`.
- **N=8 fail-closed**: correto, exceção explícita.
- **Mapeamento flag→corpo**: verificado contra retrógrados reais da Helena
  (ground truth Kerykeion: Saturno 312.44°, Urano 284.05°, Netuno 286.20° → saem
  12°♒26'R, 14°♑03'R, 16°♑12'R).

### Legibilidade medida (wheel a 18cm REAL)

| N | scale | grau | minutos | R |
|---|---|---|---|---|
| 1–4 | 1.00 | 12.46pt | 11.53pt | 9.97pt |
| 5 | 0.83 | 10.33pt | 9.56pt | 8.27pt |
| 6 | 0.69 | 8.61pt | 7.97pt | 6.89pt |
| 7 | 0.59 | 7.38pt | 6.83pt | **5.91pt** |

Só o sufixo `R` em N=7 fica abaixo de 6pt. **Falta a validação por impressão física.**

### Bugs encontrados e corrigidos neste CP1 (todos com evidência)

1. **Leaders cruzavam (13 casos).** Âncora por proximidade não preserva ordem, e o
   wheel inverte o sentido vertical por quadrante. **Correção:** o roteamento virou
   restrição de BUSCA (`route_leaders` dentro de `place_column`), não teste posterior.
   Ordem só pode ser zodiacal ascendente ou descendente — a checagem escolhe qual.
2. **Fail-closed espúrio.** Não era bbox: era **conflito de empacotamento** — o cluster
   de 1 corpo pegava o melhor lugar antes do de 3. **Correção:** empacotar clusters
   MAIORES primeiro + busca rica (escalas até MIN_SCALE, raios finos, nudge tangencial).
3. **`row_width` subestimava a largura.** Dizia 2.5u para o glifo do signo, que mede
   **4.0u** (medido via svglib). Os minutos colidiam com o glifo do signo. Isso também
   fazia caixas caberem onde não cabem — o "20/20" antes desta correção era artefato.
   **Correção:** `row_layout()` virou fonte única de verdade (render e row_width usam
   a MESMA função) + larguras medidas + `stringWidth` para texto.
4. **`bbox_fits` só na coluna era insuficiente — o anel é NÃO-CONVEXO.** Uma coluna
   alta atravessando o anel pode ter o meio mergulhando no círculo interno, com os
   cantos ainda válidos. **Correção:** validar linha a linha, igual ao `assert_render`.
5. **`compute_scale` ignorava `BBOX_MARGIN`.** Usava o anel bruto (21.5u) em vez do útil
   (19.9u) → a coluna nascia 1.6u alta demais, sempre.
6. **`ROW_PITCH=5.5` era chute.** Glifo mais alto medido (Saturno @0.2) = 4.44u → 4.8.
7. **O wheel tinha 14.7cm, não 18cm.** O ratio era aplicado ao viewBox (100 units), mas
   o wheel ocupa 81.9 (r=44.5 × 2 × wedge 0.92). As fontes encolhiam junto. **Correção:**
   `ratio = TARGET_WHEEL_CM / (2*R_OUTER*0.92)`. Ganho de ~22% em todos os pt — foi o
   que tirou N=6 de 6.10pt para 8.61pt.
8. **Nudge de ±18° era insuficiente.** Quando o cluster fica no topo/base do wheel o arco
   de ticks vira horizontal (12.9u × 0.5u) e a coluna precisa de ~23° para sair de baixo
   dele. **Correção:** ±36°.

**Lição transversal:** todo número que era estimado (largura de glifo, altura de linha,
tamanho do wheel) estava errado. Medir com svglib/stringWidth, nunca estimar.

### Bug já corrigido (registrado p/ não reincidir)

**Sol retrógrado nas fixtures.** A versão antiga marcava retrógrado **por índice na
lista**, e `names_pool[0] = "Sun"` (`CP1_cluster_N1.pdf` diz "retrogrados: 1" com um
corpo só). Sol e Lua **nunca** retrogradam. Corrigido com `NEVER_RETROGRADE = {"Sun",
"Moon"}` + `validate_fixture()`, que **levanta ValueError** em dado impossível.
Dado de teste impossível invalida o teste — a validação é obrigatória.

---

## 4. O que falta

### CP1
- [x] Roteamento de leader por ordem zodiacal + lado por quadrante
- [x] Corrigir fail-closed espúrio
- [x] 20/20 casos passando
- [ ] **Impressão física a 100% — aguardando aprovação com os olhos.** N=6 ficou em
      8.61pt (era 6.10pt); N=7 tem o sufixo R em 5.91pt.

### CP2
- [ ] Integração num mapa real: PDF final, ângulos preservados, sem clipping,
      resto do relatório intacto
- [ ] Se ainda estiver ajustando geometria básica aqui → a estimativa não está sob
      controle; avisar

### CP3 (antes de qualquer deploy)
- [ ] 5 QA maps + **3 holdout não usados no desenvolvimento**
- [ ] Censo 500 (com `active_points`!): zero corpo em signo/casa errada, zero
      clipping, zero fallback silencioso
- [ ] **Segunda seed** — passar só no corpus de desenvolvimento é overfit
- [ ] Adversariais: dois corpos na mesma longitude; cluster cruzando 0° Áries;
      29°59'; <0.1° de cúspide; 7+ corpos; múltiplos retrógrados

### Operacional (antes de deploy)
- [ ] Feature flag
- [ ] Rollback testado
- [ ] Versão do layout logada em cada relatório
- [ ] Alerta de falha de renderização
- [ ] Alerta N>7 → `executivo@marciafervienza.com` + log

---

## 5. Contexto de release

- **Não** liberar 10 testers. No melhor caso 1–2 canários.
- Convites provavelmente só depois da viagem.
- O que está em jogo: canário antes da viagem, ou tudo em agosto.

---

## 6. Superfície de dependência do Kerykeion 5.12.8

O renderer novo é **independente** do `draw_modern` (constrói o wheel do zero), mas
**reusa os `<defs>`** (símbolos dos planetas e signos). Dependências:

- `<symbol id='Sun'>`, `'Moon'`, … `'Mean_Lilith'` — glifos de planeta
- `<symbol id='Ari'>` … `'Pis'` — glifos de signo
- Convenção de orientação: Ascendente a 9 o'clock; `wheel_angle(z) = (z - seventh + 180) % 360`

Constantes geométricas replicadas (não importadas): `CENTER=50`, `R_OUTER=44.5`,
`R_ZODIAC_INNER=41.5`, `R_PLANET_OUTER=40`, `R_PLANET_INNER=18.5`, wedge
`translate(4 4) scale(0.92)`, global `rotate(-90 50 50)`.

**Se o Kerykeion for atualizado:** rodar a bateria antes de aceitar. As asserções
quebram loud, não silenciam.
