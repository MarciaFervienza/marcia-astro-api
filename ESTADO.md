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

## 3. Estado do CP1 — **REPROVADO no gate** (2026-07-15)

Bateria: **6 passaram · 14 falharam** de 19 casos.
Arquivos: `cp1_v2.py` (renderer + asserções), `cp1_battery.py` (bateria).
Local: scratchpad da sessão (`$SP`). PDFs dos casos que passaram: `~/Desktop/mapa-natal-pdfs/CP1_v2/`.

### Provado correto (não refazer)

- **Ticks**: `tick_renderizado == wheel_angle(abs_pos)` dentro de 0.01° — **zero erro em 19 casos**.
- **Labels**: grau/signo/minutos/retro conferem com o dado real — **zero erro**.
- **Bbox dentro do anel**: zero erro nos casos que colocaram.
- **Defs**: `originals_present: True` (byte a byte), 24 clones `__mono`.
- **N=8 fail-closed**: correto, exceção explícita.
- **Mapeamento flag→corpo**: verificado contra retrógrados reais da Helena
  (ground truth Kerykeion: Saturno 312.44°, Urano 284.05°, Netuno 286.20°).

### Falhas abertas

1. **Leader lines cruzam — 13 dos 14 fracassos.** Causa: âncora escolhida por
   proximidade (`attach_left = tx < centro da coluna`), o que não preserva ordem
   zodiacal, e a ordem vertical das linhas inverte conforme o quadrante.
   **Correção aprovada:** âncora por **ordem zodiacal** (rank i ↔ i-ésimo corpo em
   longitude), **lado da coluna escolhido pelo quadrante** (um lado só por coluna).
2. **Fail-closed espúrio** (`isolated_adjacent`): coluna de 3 corpos em 96–104° não
   acha posição, enquanto 3 corpos em 210° (`near_cusp`) acham. Bbox axis-aligned no
   papel testado contra um anel → falha em ângulos diagonais. **Correção aprovada.**

**CP1 fecha quando os 19 casos passarem.**

### Bug já corrigido (registrado p/ não reincidir)

**Sol retrógrado nas fixtures.** A versão antiga marcava retrógrado **por índice na
lista**, e `names_pool[0] = "Sun"` (`CP1_cluster_N1.pdf` diz "retrogrados: 1" com um
corpo só). Sol e Lua **nunca** retrogradam. Corrigido com `NEVER_RETROGRADE = {"Sun",
"Moon"}` + `validate_fixture()`, que **levanta ValueError** em dado impossível.
Dado de teste impossível invalida o teste — a validação é obrigatória.

---

## 4. O que falta

### CP1 (em andamento)
- [ ] Roteamento de leader por ordem zodiacal + lado por quadrante
- [ ] Corrigir fail-closed espúrio (bbox por quadrante)
- [ ] 19/19 casos passando
- [ ] Impressão física a 100% — 6.10pt é limítrofe, precisa de aprovação com os olhos

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
