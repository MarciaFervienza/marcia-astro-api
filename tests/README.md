# tests/ — provas de mordida e censos

Estes scripts NÃO são testes unitários. Eles são a memória de verificação do
projeto: cada um prova que um detector **morde** (reintroduz o defeito real e
mostra que o detector grita) ou mede uma distribuição sobre mapas sintéticos.

Regra do projeto (ESTADO §2, R4): **a propriedade tem que reprovar o passado.**
Um detector que só aprova o presente não vale nada.

| script | o que prova |
|---|---|
| `prove_text.py` | montagem do PDF, template de aspecto, léxico, voz 2ª/3ª pessoa, age gate |
| `prove_doutrina.py` | doutrina 17/07: Netuno-Plutão, signo geracional, falsa ausência de aspecto, clítico, IC, glossários, muleta |
| `prove_positions.py` | tabela de posições e painel: soma 12 nos dois eixos, corpo no elemento/modalidade certo, lista == produção |
| `censo_aspectos.py` | distribuição da contagem de aspectos (piso e teto) em 500 mapas |
| `censo_regra5.py` | quantos corpos a regra dos 5° move, com a condição de signo |
| `medir_fontes_pdf.py` | mede o tamanho de fonte REAL no PDF final (percorre o content stream) |

As provas do renderer da mandala vivem em `wheel_renderer/` (`props.py`,
`prove_bite.py`, `censo.py`) — 8 propriedades, censo de 1000 mapas × 2 seeds.

Rodar: `python3 tests/<script>.py` a partir da raiz do projeto.
