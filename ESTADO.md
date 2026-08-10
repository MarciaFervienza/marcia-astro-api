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

### R6. "Mordeu no teste" ≠ "classe coberta"
Todo detector é provado **também com frase INVENTADA**, não só com a real
que o motivou. O gate confirma o exemplo; a classe é outra coisa.

> Prova: em 18/07, três detectores foram criados e provados com as frases
> reais da leitura de cliente. Os três falharam em produção, em variações
> da mesma classe — "conjunção com o Sol" (aspecto afirmado inexistente,
> classe que nem existia), "cúspide da casa 10" (o detector só conhecia
> nomes de ângulo, não "cúspide da casa N") e a casa do Plutão (ancoragem
> no primeiro nome do trecho).

### R7. A prova de ontem não protege o código de hoje
Detector pode MORRER por edição futura, e detector morto devolve vazio —
indistinguível de "está limpo".

> Prova: `_detect_rulership` rodou morto por uma edição minha que apagou a
> tabela `_REGENCIA`. Foi encontrado **por acaso**, não por teste.

**A rede:** `tests/canario.py` no gate. Uma frase sabidamente defeituosa
por classe; se qualquer detector parar de acender, o gate falha.
Estado em 18/07: **48 vivos, 0 mortos**.

> O canário já pagou na estreia: revelou que `_detect_count_mismatch` só
> via a forma colada ("três conjunções:") e passava batido em qualquer
> frase com adjetivo ("três conjunções CENTRAIS:"). Buraco encontrado por
> teste, não por relatório.

### R8. Instrução de reescrita carrega a resposta — ou não dispara reescrita
Apontar o conflito sem dizer qual lado é o certo transforma detector em
CORRUPTOR: o reescritor resolve como pode, e "como pode" pode ser para o
lado errado.

> Prova: `casa_inconsistente` dizia "atribui mais de uma casa ([4, 5]) —
> contradição interna". O texto dizia casa 5 (correta) e saiu casa 4. O log
> registrou "corrected". Pior que não detectar: estraga texto certo.

Quando não há resposta possível — como na contagem anunciada, em que não
se sabe se falta um item ou sobra no anúncio — o detector **sinaliza sem
reescrever** (`no_rewrite: True`).


### R9. Falso NEGATIVO não acende alarme nenhum (19/07)

O verifier tem duas direções de erro, e elas custam coisas diferentes:

| direção | o que acontece | como aparece |
|---|---|---|
| **falso positivo** | o detector acusa frase certa → o reescritor mexe em texto bom | aparece em `PERSISTIU`, `INTRODUZIDA`, `failed_kept_original`. **Barulhento.** |
| **falso negativo** | o detector se abstém de frase errada → o defeito vai para o PDF | **não aparece em lugar nenhum.** Meta zerado, log limpo, gate verde. |

Em 19/07 quatro rodadas seguidas terminaram com alarme, e nas quatro a
causa era falso positivo meu. Isso cria uma ilusão perigosa: parece que o
sistema só erra para mais. Ele erra para menos também, e essa metade é
invisível por construção.

**Só varredura deliberada encontra falso negativo.** Levantamento de 19/07:
as 73 ocorrências de nome de aspecto nos dois relatórios, classificadas por
qual ramo do detector as trata. Resultado: **nenhuma** caía mais na regra
genérica (a perigosa para falso positivo) — mas **7 das 15 abstenções eram
par afirmado que o detector ignorava em silêncio**. Quatro formas novas
saíram daí: `a oposição Sol-Plutão`, `a conjunção de Mercúrio com Júpiter`,
`o trígono que Saturno forma em Quíron`, `O Sol com Plutão em sextil`.
Pares resolvidos: **66% → 74%**. Das 9 restantes, 8 são anafóricas
("esse sextil", "o trígono") e 1 é a abstenção deliberada sobre Nodos.

**Regra (Márcia, 19/07): repetir a varredura a cada mudança grande no
detector — não só quando ela pedir.** O script mede o corpus real e
classifica por ramo; um ramo que engorda ou uma abstenção que cresce são o
sinal. O canário protege o que já se sabe; a varredura é o que descobre o
que ainda não se sabe.

### R12. Medir no corpus que a camada já alterou não vale (19/07)

**A lição mais forte do projeto.** Medi a taxa de falso positivo do
`word_lint` sobre relatórios ENTREGUES e reportei **zero**, com convicção,
mais de uma vez. A taxa real, medida contra o texto CRU, era **29
acusações e 1 verdadeira — 97% falso.**

Por que o zero apareceu: o verifier já havia REESCRITO os falsos positivos
antes de eu medir. Eles não estavam mais lá para serem contados. **O corpus
estava contaminado pelo efeito que eu queria medir.**

E o efeito era pior que ruído. O `word_lint` acusava palavra correta, o
reescritor obedecia, e o resultado era o defeito:

| palavra correta no cru | virou |
|---|---|
| `autossacrifício` | `auto sacrifício` |
| `monitorando` | `estava a monitorar` (gerundial portuguesa) |
| `entediante` | frase reescrita inteira |

**Eu criava as palavras partidas que passei o dia caçando.** E o defeito
criado era INVISÍVEL para os meus próprios lints — `auto sacrifício` não
aciona nenhuma regra.

**Regra: para medir uma camada, o corpus tem de ser anterior a ela.** Se o
texto já passou por qualquer coisa que reescreve, o número mede o resíduo,
não a taxa. Vale para detector, para lint, para o verifier e para a
revisão.

**Como cumprir:** a captura por estágio (`debug_estagios`) existe para
isso. `1_cru` é o único corpus válido para medir qualquer camada de
detecção ou correção.

**Sintoma de que a regra foi violada:** o número dá bom demais. Zero falso
positivo num detector heurístico sobre linguagem natural é resultado
improvável — quando aparecer, a primeira hipótese é corpus contaminado.

### R11. Reinjeção que não derruba o canário é reinjeção INCOMPLETA (19/07)

Ao provar o canário de salvaguardas, removi o teto de tentativas trocando
`range(1, max_tentativas + 1)` por `range(1, 99)` — e o canário seguiu
verde. A leitura tentadora é "a proteção resistiu". A leitura correta era
outra: **havia DUAS travas** (o `range` e um `break` interno) e eu tinha
tirado só uma. Removendo as duas, o canário caiu na hora.

**Regra: quando a reinjeção não derruba o teste, a primeira hipótese é que
a reinjeção foi incompleta — não que a proteção funcionou.**

É a mesma família do detector morto, um nível acima: lá o detector devolvia
vazio e isso parecia "limpo"; aqui a reinjeção falha e isso parece
"protegido". Nos dois casos o silêncio é lido como aprovação.

Procedimento: antes de concluir que a proteção resistiu, listar TODOS os
pontos do código que implementam aquela proteção e confirmar que a
reinjeção atingiu todos.

### R10. Um único ponto de saída para cada serviço externo (19/07)

`messages.create` aparece UMA vez no código inteiro, dentro de
`call_claude`, que tem retry. Um segundo ponto de saída escaparia do retry
sem que nada acusasse — por isso `prove_retry.py` conta as ocorrências e
reprova se aparecer outra.

Vale para o que já mordeu: `call_claude` **não tinha retry nenhum** até
19/07. Um relatório faz 16+ chamadas de seção mais uma reescrita por frase
violada (18 no Lucca naquela rodada); um único 429 derrubava a geração
inteira e o cliente recebia erro. As reescritas do verifier herdam o retry
porque `run_verifier` recebe a mesma `call_claude` por parâmetro — provado
por comportamento, não por leitura.


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

## 502 do Railway — DIAGNOSTICADO, NÃO MEDIDO (19/07, item de verificação)

**Não é item fechado.** A Márcia foi explícita: acompanhar os próximos
deploys e só fechar com evidência.

**Observado:** 502 "Application failed to respond" em três de quatro
rodadas de 19/07, sempre na PRIMEIRA requisição após um deploy, aos 15s a
80s da requisição. Nunca em regime estável.

**Diagnóstico:** o `--timeout 900` estava no start command e o
`--graceful-timeout` **não** — valia o padrão de 30s do gunicorn. As
gerações levam ~90s. No deploy o gunicorn manda SIGTERM, espera 30s e mata
a requisição em voo. O intervalo observado (15–80s) é compatível.

**Correção:** `--graceful-timeout 900` no `Procfile` e no `railway.json`.

**Por que ainda não está confirmado:** o valor só passa a valer no deploy
SEGUINTE ao que o introduziu — quem é morto no deploy N é o container da
versão N-1, que ainda roda o comando antigo. O 502 observado logo após
`16d50e9` (o commit que introduziu a correção) era portanto esperado e não
desmente nada. E o padrão de 30s do gunicorn é o **documentado**: não pôde
ser medido nesta máquina, onde gunicorn não está instalado.

**Como fechar:** se os 502 logo-após-deploy sumirem nas próximas rodadas,
o diagnóstico estava certo. Se persistirem, a causa é outra — próximas
hipóteses a testar: healthcheck do Railway roteando antes de o worker estar
pronto (`healthcheckTimeout: 60` com `--preload` e carga de ephemeris), ou
ausência de deploy sobreposto (`--workers 1`, sem instância antiga
servindo durante a troca).

**Registro das rodadas** (atualizar a cada deploy):

| commit | 502 na 1ª tentativa? | tempo até o 502 |
|---|---|---|
| 3e337ff | não (429 de geoloc) | — |
| a443f9c | não | — |
| 32b8c66 | sim (Helena ×2, Lucca ×2) | 54s, 80s, 16s, 15s |
| 86eaece | sim (Helena) | 54s |
| 3224c1f | não | — |
| 3420d57 | sim (Helena) | 45s |
| 16d50e9 | **primeiro com graceful-timeout no container ANTIGO** | — |
| c18dd23 | não | — |
| d48180a | **não** — 1ª rodada em que o container morto JÁ tinha graceful-timeout | — |
| b651788 | **SIM** (Helena) | 45s |
| ecacf8d | **não** — 1ª rodada esperando o SHA no /health | — |
| 16bbc44 | **não** — 2ª rodada com espera pelo SHA (deploy: 57s) | — |

**FECHAMENTO PROVÁVEL (19/07, noite).** O `/health` levou **133s** para
reportar o commit novo; meu script dormia **100s** fixos. Ou seja: TODAS
as requisições que tomaram 502 foram enviadas COM O DEPLOY AINDA EM
CURSO. Trocando a espera fixa pela espera do SHA, os dois relatórios
passaram de primeira.

Isso reclassifica o item: era **o instrumento**, não o produto. O cliente
não faz push antes de gerar — ele só veria 502 se a Márcia deployasse no
minuto exato em que alguém gera. Risco real, muito mais estreito, e
mitigável não deployando durante o horário de uso.

**Ainda não é prova:** uma rodada. Manter a tabela. O que aumenta a
confiança é a coerência — a explicação cobre TODAS as observações
anteriores, inclusive as rodadas limpas (deploys mais rápidos que 100s).
Duas hipóteses minhas foram descartadas por medição antes desta:
graceful-timeout (melhorou, não eliminou) e worker lento na partida
(import 1,3s, primeiro chart 0,2s — não se sustenta).


**Atualização 19/07 à noite:** o 502 VOLTOU com o container morto já
tendo graceful-timeout. Pós-correção: 1 de 3 deploys com 502 (antes: 3
de 4). Melhorou, mas **não eliminou** — o diagnóstico está incompleto.
Próxima hipótese a testar, agora a mais provável: o 502 não vem do
worker antigo sendo morto, e sim do NOVO ainda não estar pronto quando
a requisição chega (`--preload` + carga de ephemeris + healthcheck).
Nesse caso graceful-timeout não teria como ajudar, e o caminho é o
healthcheck só liberar tráfego depois do primeiro request quente.


**Leitura em 19/07 à noite:** 1 rodada limpa desde que a correção passou a
valer de fato. Insuficiente para fechar — `3224c1f` também foi limpo SEM a
correção, então uma rodada não distingue as hipóteses. Precisa de 3 a 4
deploys seguidos sem 502 na primeira tentativa.


## Decisões de 19/07 — camadas de língua e de palavra

### spell_lint DESLIGADO (decisão da Márcia)
O dicionário "pt" do pyspellchecker é EUROPEU e, como autoridade
ortográfica, está **invertido** na classe que mais importa. Medido numa
frase com as duas grafias lado a lado: "contacto", "facto" e "harmónico"
passaram limpos e **"harmônico" ACENDEU**. Para pegar contaminação pt-PT
ele é pior que ausente.

`spell_lint_out = []` fixo em `report_generator`. O léxico explícito fica e
cresceu: de 28/48 para **31/31** das grafias pt-PT testadas, com zero falso
positivo nas 32 formas brasileiras. ("rapaz" ficou de fora de propósito: em
pt-BR é palavra corrente, não marca de Portugal.)

### word_lint.py — a camada de PALAVRA que faltava
O GPT achou sete corrupções que todos os lints deixaram passar. **A
hipótese de splice não se confirmou**: seis das sete estão no MEIO da
frase, e o splice só emenda em limite de frase; nenhuma aparece na saída
registrada de cleanup, correction_rewrite ou sign_divergence. São erros do
modelo, e passavam porque `pdf_lint` vê frase colada e o léxico vê termos
enumerados — nenhum olha a PALAVRA.

Cinco regras, cada uma medida ANTES de entrar:

| regra | medição | pega |
|---|---|---|
| colada | 1 acusação em 2.183 tokens | `voltarcontra` |
| contração | "de um" fora: 19 falsos sozinho | `de o que` |
| corrompida | a 1 edição do vocabulário astrológico | `saturninan`, `retrogradiação` |
| concordância | conjunto FECHADO de adjetivos | `conforto arianas` |
| acento | 13.337 pares: sem o critério de frequência 34, com ele 1 | `fincar ancoras` |

**Não é o spell_lint de volta.** O dicionário entra como ORÁCULO DE
EXISTÊNCIA, nunca como autoridade ortográfica, e `_variantes_pt()` absolve
as grafias brasileiras.

**Cobertura 6/7.** "como você mente pensa" é erro de SINTAXE — nenhuma
regra lexical alcança. Registrado como NÃO COBERTO; é o caso da passada de
revisão de língua.

### Última auditoria antes dos testers (19/07) — duas classes factuais novas

**Regência de ÂNGULO.** "o seu Meio-do-Céu em Leão, com o Sol em Virgem
como seu REGENTE DE ASCENDENTE" (Helena). O Ascendente dela é Sagitário,
regido por Júpiter; o Sol rege Leão, que é o signo do MC. O texto trocou
regente do MC por regente do Ascendente.

`_detect_rulership` confere corpo↔SIGNO. Não conferia corpo↔ÂNGULO — o
ângulo tem um signo, e o regente afirmado tem de ser o regente daquele
signo. IC e Descendente saem do oposto de MC e Ascendente (o chart guarda
só os dois).

**Elemento / modalidade compartilhados.** "quando o Sol e a Lua estão no
mesmo elemento, como acontece aqui" (Lucca). Sol em Câncer é ÁGUA, Lua em
Leão é FOGO — e o painel de elementos, na página anterior do mesmo PDF,
mostra isso. Nenhum detector olhava elemento nem modalidade. Reusa
`positions_table.element_of/modality_of` para não haver segunda tabela (R3).

**Terceira do GPT — over-capture, ignorada.** As quadraturas de Mercúrio
aos Nodos: Mercúrio quadratura Nodo Norte E Nodo Sul são as duas aspectos
reais na tabela computada, então o texto está consistente com os dados.
(Questão de doutrina em aberto, não de fato: os Nodos são um EIXO, e contar
as duas pontas como dois aspectos é decisão da Márcia.)

### Achados de língua da mesma auditoria
| achado | classe | regra |
|---|---|---|
| "travão" | pt-PT | léxico |
| "entre safra" | grafia | léxico (entressafra) |
| "acende um inquietação" | concordância | `word_lint` R6 — gênero do determinante por TERMINAÇÃO (-ção/-dade/-agem femininas; -mento/-ismo masculinas). A R4 só cobria adjetivos de signo/planeta: por isso passou |
| "um Sol canceriano a alimentar" | pt-PT | gerundial portuguesa, com lista de verbos que LICENCIAM "a + infinitivo" em pt-BR |
| "alguém que ainda aprendeu" | sentido invertido | "ainda" + pretérito exige negação |
| "o que me faz eu" | agramatical | "faz eu" — a 1ª pessoa da Márcia é por design, então o detector ancora na construção, não no pronome |
| "poucos graus de Ceres" | preposição | "a poucos graus de" |
| "uma ópera de proteção" | **NÃO COBERTO** | metáfora sem referente — não é mecanizável por regra lexical |

### PENDENTE DE DOUTRINA: as faixas de velocidade não cobrem todos os corpos

Único defeito real que sobrou no par entregue aos testers (Helena):

> "Lilith mora na sua casa 4, e ela está em Aquário. **Aquário carrega uma
> qualidade que atravessa toda uma coorte — a geração que cresceu entre
> dois séculos**…"

É violação de doutrina genuína — signo como agente geracional — e o
reescritor falhou duas vezes; `failed_kept_original` preservou o texto.

**Mecanismo:** `_detect_sign_as_generational_agent` classifica por faixa de
velocidade, e **Lilith, Quíron, Ceres, Palas, Juno, Vesta e os Nodos não
estão em faixa nenhuma**. Com nenhum corpo reconhecido no parágrafo, a
regra cai em "nenhum corpo sustenta 'geração'" e acusa — corretamente,
neste caso, mas por ausência de dado, não por análise.

**A decisão é da Márcia, não minha**, porque é doutrina:
- Lilith (média) percorre o zodíaco em ~8,85 anos → ~9 meses por signo:
  perto de um planeta SOCIAL;
- Quíron leva ~50 anos → ~4 anos por signo: entre social e transpessoal;
- Nodos: ~18,6 anos → ~1,5 ano por signo: social;
- asteroides: rápidos, perto dos PESSOAIS.

Enquanto a decisão não vem, o comportamento atual é conservador — acusa
quando não reconhece o corpo — e isso é o lado seguro.

## `coloram` — a prova de que existe classe que só LEITURA pega (19/07)

Registro permanente, a pedido da Márcia. Esta é a justificativa de o
detector semântico existir, e ela não expira.

> "os aspectos de Juno com Vênus e com Netuno — já abordados em outra seção
> — precisam ser lembrados aqui: eles **coloram** esse compromisso com
> tanto encantamento…"

O verbo certo é **colorem** (de *colorir*). Mas `coloram` **existe** — é
forma de *colorar*, verbo raro — e o dicionário a aceita.

**Por que nenhum lint pode alcançar isso:**
- `word_lint` R1 (palavra colada): não é colada;
- R3 (quase-acerto de termo do domínio): a palavra EXISTE, então nem entra
  no teste;
- léxico explícito: só pega o que está enumerado, e ninguém enumera
  conjugações erradas de verbos raros;
- `spell_lint`: aceitaria — a palavra está no dicionário.

A pergunta que um lint faz é **"esta sequência existe?"**. A pergunta que o
defeito exige é **"esta palavra cabe aqui?"** — e essa depende do sentido
da frase, não da existência da forma.

**Companheiros da mesma classe**, todos achados pelo detector e por nada
mais: `librar` (não cabe no contexto), `turnam` (por *tornam*), `Hay` (por
*Há* — espanhol), `outro camada`, `que se atritos`, `o chão firme` sem
verbo, e uma contradição lógica no Fio Condutor da Gisela — o parágrafo
anuncia uma TENSÃO e a frase seguinte diz que as duas coisas "se integram
de forma natural".

**Consequência de projeto:** nenhuma quantidade de regex fecha a língua.
Regex fecha CLASSE ENUMERÁVEL — andaime, geracional e regência deram zero
duas vezes seguidas depois de cobertas por forma. Sintaxe e sentido são
gerativos. São camadas diferentes e as duas ficam.

## Geração ASSÍNCRONA — decidida, com GATILHO (19/07)

**Não entra antes dos testers.** É mudança de arquitetura no caminho do
cliente, com o Wix acoplado (hoje ele espera a resposta HTTP). Dez testers
não geram concorrência, e o pior caso cabe.

**Gatilho para entrar** — qualquer um dos dois:
  · o pior caso do encanamento voltar a passar de **250s**;
  · o volume justificar (concorrência real, mais de um cliente por vez).

**Quando entrar, com fila DE VERDADE, não improvisada.** Fila em processo
perde o job num restart, que é justamente o que se quer evitar.

**A favor:** o cliente já recebe por e-mail — a resposta HTTP só carrega o
PDF porque o fluxo é síncrono. Assíncrono remove o teto de 300s do proxy de
vez, e também os 502 de deploy: requisição enfileirada sobrevive a restart.

**Custo:** segundo serviço no Railway ou fila persistida; endpoint passa a
devolver 202 + id; o Wix precisa mudar; e o alerta ao executivo@ vira o
ÚNICO canal de erro, porque ninguém está escutando a resposta.

### Orçamento de tempo (medido, 19/07)
| etapa | tempo |
|---|---|
| geração base | 88–95s |
| fase de seções (17, paralela) | 59–65s |
| uma seção isolada | ~11s |
| detector de língua (92 chamadas) | ~45s em produção |

Pior caso do encanamento (teto 3, 2 rodadas de regeneração), **com a
regeneração PARALELA**: **~253s, fixo**, independente de quantas seções
forem apontadas. Em série era 253s com 1 seção e **299s com 3** — a um
segundo de o cliente perder a requisição com o relatório pronto.

Margem contra o teto de 300s: **3,2×** na geração limpa de hoje, **2,1×**
com uma rodada de detecção, **1,2×** no pior caso.

## LINHA DE BASE: o primeiro relatório limpo de origem (19/07)

**Lucca, 19/07, commit b435ffc: ZERO achados do detector semântico em 96
parágrafos.** Uma rodada, nenhuma regeneração, nenhuma falha fechada, 132s.

É o primeiro relatório do projeto que não precisou de **nenhuma** correção
de língua. E é exatamente o que a medição do texto CRU previa: ~1 defeito
por relatório na geração, com variância que inclui o zero.

**Por que isto é a linha de base, e não uma boa notícia isolada.** Durante
semanas eu reportei três ou mais defeitos por relatório. Aquele número era
inflado por duas coisas:
  · o `word_lint` CRIAVA palavras partidas que depois eu encontrava (R12);
  · eu media sobre texto entregue, já alterado pelas camadas (R12).

Com as duas causas removidas, a taxa real apareceu. **Os dez relatórios dos
testers devem ser lidos contra este número**, não contra o histórico: uma
geração que precisa de 2 regenerações é acima da média, não normal.

Contraprova na mesma rodada: a Helena precisou de 2 regenerações e saiu
limpa. As duas pontas do intervalo estão medidas.

## Revisão de língua — MEDIDO em 7 relatórios (19/07)

A Márcia fechou: não lança enquanto erro de língua puder sair. Os dados
sustentam — os defeitos são NOVOS a cada geração, não resíduo.

### Granularidade: parágrafo (medido, não escolhido)
| granularidade | chamadas/rel | segundos | pega (dos 7) |
|---|---|---|---|
| seção | 17 | 13–15 | **0** |
| **parágrafo** | **92–93** | **22–29** | **5** (3 depois das guardas) |
| frase | 251–254 | 63–73 | 3 |

Parágrafo pega MAIS que frase com um terço das chamadas. Frase perde
contexto; seção dilui (14 de 17 voltaram INTACTO).

### As guardas custam acerto — e mesmo assim ficam
Com as três guardas novas a passada caiu de 5/7 para **3/7**: elas recusam
correção legítima junto com a corrompida (o modelo devolve 4 parágrafos
onde entrou 1, e tudo daquele trecho se perde). É o preço, e vale — a
alternativa é o "carma".

### Taxa de corrupção nos 5 mapas de QA
**5 edições aplicadas, 7 recusadas pelas guardas, 0 corrupção escapando.**
As 5 aplicadas: `turnam`→`tornam`, `netuniana`→`netunianas`, `conta
tos`→`contatos`, `uma certa magnetismo`→`um certo magnetismo`, e uma
correção parcial de sintaxe. Nenhuma inventa nem apaga. A guarda de
contagem de frases pegou uma remoção real ("4 frases viraram 3").

### Encanamento final: detectar → regenerar → redetectar → falhar fechado
A passada de REESCRITA saiu do encanamento, por medição: corrige 5 dos 16,
precisou de seis guardas para chegar a zero corrupção numa amostra de só 5
edições, e as guardas recusam correção legítima junto com a corrompida.

  · **detectar** — flag-only, uma chamada por parágrafo, risco zero;
  · **regenerar a SEÇÃO** apontada — texto novo não carrega o defeito, e
    ninguém precisa adivinhar o que a frase quebrada queria dizer (foi
    adivinhar que produziu o "carma");
  · **redetectar** com teto de tentativas (padrão 3). Sem teto, um defeito
    teimoso vira loop e cada volta custa uma geração de seção;
  · **falhar fechado** acima do teto: HTTP 422, sem PDF, sem e-mail ao
    cliente, alerta ao executivo@ com mapa, seção e frase. A verificação
    vem ANTES da nota da Lua, do SVG, do PDF e do envio — nada é construído
    a partir de texto que o sistema já sabe que está quebrado.

### O detector semântico é o instrumento forte
| | parágrafos | apontadas | verdadeiras | falsas |
|---|---|---|---|---|
| Helena + Lucca | 185 | 5 | 5 | **0** |
| 5 mapas de QA | 465 | 11 | 11 | **0** |

**16 de 16 verdadeiras em ~650 parágrafos.** Achou o que a passada não
pega (negação faltando, "você mesma" inserido), o que os lints não podem
pegar (`coloram`, `librar`, `turnam` — palavras que o dicionário aceita ou
desconhece sem estar perto de termo do domínio), e vazamento de espanhol
(`Hay`) que o léxico não listava.

**Todos os 7 relatórios têm de 1 a 3 defeitos de língua.** Nenhuma geração
sai limpa. É o dado que sustenta a decisão de não lançar.

### Negação-substituição — varredura de formas sobre as 22 reais
A Márcia mediu: dos ~22 casos genuínos do inventário do GPT, os padrões
antigos pegavam **~35%**. E o meu instrumento de MEDIÇÃO tinha o MESMO viés
do detector — achei 5 das 22 e reportei "70% de cobertura", número
produzido pelo próprio viés. Por isso a enumeração saiu das 22 FRASES
REAIS, não do que os padrões conseguem enxergar.

**A cegueira não era o travessão.** A forma dominante é a INVERTIDA —
afirmação primeiro, negação depois ("como ferramenta, não como filtro") —
**8 das 22**, e não havia NENHUMA cobertura dela. Separador: 18 vírgula,
1 travessão, 2 ponto.

| forma | exemplo | nº no corpus |
|---|---|---|
| A `não X, mas Y` | "não por falta de clareza, mas por" | 9 |
| B `X, não Y` **(invertida)** | "como ferramenta, não como filtro" | 10 |
| C `não porque X, mas porque Y` | "não porque você as nega, mas porque" | 2 |
| D `não V X. V Y` | "não pede… Pede que você se conheça" | 1 |
| E `não V X, V Y` | "não vem de fora para dentro, vem" | 1 |
| F `não V X — V Y` | "não ficam do lado de fora — entram" | 3 |
| G `X em vez de Y` | "alimenta em vez de drenar" | 6 |
| H `não X, não Y. É Z` | "não era leve, não era superficial. Era" | 1 |

**Medido:** 22/22 das reais acendem; 12/12 dos contrastes comuns ficam
limpos. No corpus entregue: 33 ocorrências onde antes se via **zero**.

Dois defeitos aparecidos na construção, ambos de padrão ANTIGO:
- `y_e_nao_x` acusava negação COORDENADA ("não pede permissão, e não há
  nada de errado") — língua normal, não substituição;
- e ele resistia a duas correções porque o match **começava no próprio
  "não"**: a lookahead negativa só valia do 2º caractere em diante, e a
  checagem de oração lia um trecho vazio. Corrigido com a lookahead
  ancorada no início.

**Limite de escopo (Márcia, 19/07):** esta é a última rodada de estilo
antes dos testers. Os factuais estão fechados. Negação-substituição é
estilo e é gerativa — se cair de novo, é bom o suficiente para dez
testers. O teste existe para saber se as pessoas gostam do relatório e
pagariam por ele, não para validar métrica de prosa.

### QUARTA falha da camada de reescrita: meta-comentário no produto (19/07)

O relatório do Lucca saiu com **quatro injeções do raciocínio do próprio
corretor**, emendadas no texto, no PDF e no e-mail:

> `--- Aguarda — vou corrigir corretamente.`
> `--- Ainda há "ela" — corrijo completamente:`
> `A violação de voz dizia respeito a … Revisando:`

seguidas de duas ou três versões alternativas da mesma frase.

**É a única das quatro falhas desta camada que injeta texto NOVO no
produto.** As três anteriores: offset na frase anterior, instrução ambígua,
descarte mudo.

O prompt JÁ mandava "Retorne APENAS a frase reescrita, sem introdução, sem
explicação". Não bastou — **instrução não é garantia**. A saída ia direto
para o splice sem ninguém perguntar se aquilo era uma frase.

A verificação pós-aplicação PEGOU (`INTRODUZIDA_PELA_REESCRITA`) — mas
depois de o PDF ter sido gerado e enviado. **Detectar não basta: tem de
recusar ANTES do splice.**

Duas camadas:
1. `_motivo_reescrita_invalida()` recusa na origem — separador `---`, saída
   em mais de um bloco (uma frase reescrita não tem parágrafos: este
   critério sozinho pega as quatro), meta-comentário, inchaço > 2,5×. A
   recusa conta como tentativa gasta; duas recusas mantêm o original.
2. `lint_final_text` ganha `meta_comentario_do_corretor` — se a recusa
   falhar, o artefato trava antes do cliente.

Defeito na construção: o `\b` final do grupo caía DEPOIS do travessão em
"Aguarda —", e a alternativa nunca casava. Só a prova pegou.

### Detectores reescritos por FORMA, não por exemplo
Três no mesmo dia, todos pela mesma causa — o detector cobria a frase que o
motivou e não a classe:
- **aspecto afirmado**: levantamento das 73 ocorrências reais; 4 formas
  novas; pares resolvidos 66% → 74% (as 9 restantes: 8 anafóricas, 1
  abstenção deliberada sobre Nodos);
- **regência**: 7 formas. A frase real "O Nodo Norte em Libra tem Marte
  como seu regente" não acendia porque nesta construção **os papéis se
  invertem** (alvo antes, regente depois) e a palavra é "regente", que o
  regex do verbo não alcançava. Não era o Nodo — era a forma;
- **vazamento de pessoa**: `_detect_third_person_leak` procura PRONOME, e
  num relatório de astrologia quase todo "ela/ele" é planeta ou aspecto —
  31 ocorrências na Helena, UMA é o vazamento. O que denuncia é a
  INSTANCIAÇÃO ("há uma pessoa aqui que…"), não o pronome.

### Retry no produto (bloqueante, decisão da Márcia)
O retry existia no meu script e não no produto — mesma classe de "o
instrumento não é o produto". `retry_util._com_retry`, backoff 1/2/4s com
jitter, só erro TRANSITÓRIO. Cobre geolocalização (Nominatim limita a 1
req/s) e `call_claude`, que **não tinha retry nenhum**: 16+ chamadas de
seção mais uma reescrita por frase violada (17 no Lucca) — perto de 34
pontos de falha por relatório, nenhum protegido. As reescritas do verifier
herdam porque `run_verifier` recebe a mesma `call_claude` (provado por
comportamento). Ver R10.

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
| tamanho na página | `WHEEL_SIZE_CM = 18.0` | `pdf_generator.py` |
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
- A CASA é a de **LEITURA** — `points[*]["house"]`, já re-atribuída pela
  regra dos 5° (decisão da Márcia, pedida três vezes, implementada em
  19/07). Índice, tabela e texto dão a MESMA casa; `prove_casa_unica.py` é
  property test sobre todos os corpos de todos os mapas de QA.
  `house_geometric` continua preservado em `app.py` antes da mutação —
  serve à mandala e ao diagnóstico, não à tabela.
  · Consequência aceita: num corpo de fronteira o glifo é desenhado 1º a 2º
    antes da cúspide enquanto a tabela nomeia a casa seguinte. Diferença de
    milímetros, e o texto nomeia a fronteira explicitamente.
  · ANTES de 19/07 a tabela usava a geométrica, e o relatório se
    contradizia: índice "Júpiter · Casa 11", tabela "10".
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

## Varredura de expressões distintivas — PARCIALMENTE EXECUTADA (18/07)

**Classe de defeito:** a imagem É da Márcia, o RAG recuperou CERTO, e o
texto colou TORTO. Não é alucinação (o dado existe) nem contaminação de
chunk (a fonte está correta) — é deformação na superfície.

Caso de origem, confirmado por grep: transcript da Valquiria Zampirolli —
*"é uma autoestima que ela é BARULHENTA"*. O relatório escreveu
"barulhosa" e "circunda a casa 5". Corrigida a FORMA, preservada a imagem.

### O que FOI coberto: troca de sufixo adjetival
`tests/varredura_expressoes.py`. Mesmo radical, sufixo adjetival diferente
do que ela usa (barulh-ENTA → barulh-OSA). Flexão (plural, gênero,
particípio, advérbio em -mente) NÃO conta: é gramática normal.

**Instrumento VALIDADO no caso conhecido antes de rodar** — ele detecta
`barulhenta`/`barulhosa` (radical `barulh`, a fonte só usa -ento/-enta, 112
ocorrências, zero de -oso). É essa validação que dá valor ao zero: sem ela,
"nenhuma deformação" poderia significar só "instrumento cego". Mesma
disciplina que reprovou o lint por similaridade no mesmo dia.

**Resultado:** 3.230 radicais adjetivais extraídos de 3.710 transcripts +
autorais, casamento por PALAVRA INTEIRA. **Nenhuma deformação real nos dois
relatórios** — o único achado (`intelectuais`) é plural, falso positivo.

> A v1 desta varredura foi descartada: casava SUBSTRING (deu "fio"/*Fionta*,
> "motor"/*promotor*) e confundia flexão com deformação.

### O que NÃO foi coberto — pendência SEGUE ABERTA
A varredura cobre UM mecanismo. Ficam sem medição:
- **verbo trocado** — o próprio caso de origem tinha "circunda a casa",
  que o detector de sufixo não veria;
- **regência alterada** (preposição/complemento errados);
- **imagem invertida** (mesmo elemento, sentido oposto);
- **metáfora aplicada ao corpo errado**.

**Limitação de amostra:** dois relatórios. Uma imagem só pode ser deformada
se for RECUPERADA — chunk não trazido pelo RAG não teve chance de aparecer.

**Não fechar esta pendência** com base na varredura de sufixo.


## Repetição por paráfrase — decisão de 18/07: fica com o GPT

**O lint por similaridade NÃO foi ligado.** A medição reprovou o
instrumento antes de qualquer implementação.

**Por que:** Jaccard sobre palavras de conteúdo não distingue CONTEÚDO de
MOLDURA. Medido nos dois relatórios:

| par | similaridade |
|---|---|
| conteúdo repetido de verdade (o par que o GPT achou: "quando você age por competência genuína e quando age para silenciar aquela voz interna" × "…uma cobrança interna") | **0,56** |
| falso positivo ("A casa 9 é o território onde isso se encontra" × "A casa 12 é o território do que não se vê facilmente") | **0,50** |

**0,06 de separação não é critério** — qualquer limiar entre os dois é
sorte. O par verdadeiro compartilha `age, competência, genuína, interna,
silenciar` (conteúdo); o falso compartilha `casa, território` (moldura).
Contar palavras não vê a diferença.

Varredura completa nos dois relatórios (241 e 234 frases, 17 seções cada):
a 0,60 → 0 pares; a 0,50 → 1; a 0,40 → 1; a 0,30 → 5 e 4. Os dois acima de
0,40 são falsos positivos pelos critérios da Márcia.

**Decisão:** a repetição por paráfrase fica com o GPT, que já é gate e já
achou os cinco pares LENDO — leitura entende conteúdo.

### PENDÊNCIA PÓS-LANÇAMENTO: gate por embeddings
Hoje o GPT lê os dois relatórios de teste. **Com clientes reais ninguém vai
ler cada um**, e aí um gate automático passa a valer. O caminho medido como
viável é similaridade SEMÂNTICA por embeddings (a infraestrutura já existe —
o Pinecone usa embeddings): separa "mesma ideia com palavras diferentes" de
"mesma fôrma com ideias diferentes", que é exatamente o que o Jaccard não
faz. Custo estimado: ~240 embeddings por relatório, centavos e poucos
segundos.

**Não reabrir a investigação do zero:** os números de hoje (0,56 vs 0,50)
já provam que a abordagem lexical não serve.


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
