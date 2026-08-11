#!/usr/bin/env bash
# GATE DE DEPLOY — obrigatório antes de qualquer push (decisão da Márcia, 17/07).
#
# Motivo: em 17/07 subiu para produção um `section['name']` num escopo onde a
# variável se chama `section_name`. Toda geração retornou HTTP 500 e o produto
# ficou fora do ar. Eu validei com `ast.parse`, que só confere SINTAXE —
# NameError só aparece em EXECUÇÃO.
#
# Este gate roda tudo e LÊ o resultado. Falhou em qualquer etapa, não deploya.
# Uso:  ./gate.sh          (sem geração de fumaça — rápido)
#       ./gate.sh --smoke  (com geração real contra produção — antes do push)
set -uo pipefail
cd "$(dirname "$0")"
FALHAS=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗ %s\033[0m\n" "$1"; FALHAS=$((FALHAS+1)); }

echo "=============================================="
echo "GATE DE DEPLOY"
echo "=============================================="

# ---- 0. DEPENDÊNCIAS: o gate roda contra o que SOBE? --------------------
echo
echo "0. requirements fixo, e o instalado é o que sobe?"
if python3 tests/checa_deps.py > /tmp/gate_deps.txt 2>&1; then
  ok "$(tail -1 /tmp/gate_deps.txt)"
else
  bad "dependências divergem do que sobe"; cat /tmp/gate_deps.txt
fi

# ---- 1. IMPORT REAL de cada módulo (pega NameError de topo) -------------
echo
echo "1. Módulos importam?"
for m in app report_generator text_verifier pdf_generator positions_table; do
  if python3 -c "import warnings;warnings.filterwarnings('ignore');import $m" 2>/dev/null; then
    ok "$m"
  else
    bad "$m NÃO importa"
    python3 -c "import warnings;warnings.filterwarnings('ignore');import $m" 2>&1 | tail -3
  fi
done

# ---- 2. CAMINHO DE EXECUÇÃO das seções (pega NameError de dentro) -------
# ast.parse não pegaria: a função precisa ser CHAMADA. O chart vem de um
# subject Kerykeion real — fixture não inventa dado (regra R1).
echo
echo "2. As 16 seções executam com chart real?"
if python3 tests/smoke_sections.py >/tmp/gate_sec.txt 2>&1; then
  ok "$(tail -1 /tmp/gate_sec.txt)"
else
  bad "seções falharam"; tail -8 /tmp/gate_sec.txt
fi

# ---- 3. SUÍTES DE MORDIDA ----------------------------------------------
echo
echo "3. Provas de mordida (detectores reprovam o passado?)"
for t in prove_text prove_doutrina prove_positions prove_ausencia_aspecto prove_fatos prove_indice prove_fonte_unica prove_retry prove_casa_unica prove_palavra prove_cidade prove_fila prove_extracao prove_geocode audita_estado; do
  # Código de saída PRIMEIRO — as provas fazem raise SystemExit(1). O grep
  # por /Error/ sozinho reprovava prove_retry, que IMPRIME "HTTPError 502"
  # como rótulo da tabela de classificação: texto do teste passando, não erro.
  OUT=$(python3 "tests/$t.py" 2>&1); RC=$?
  # O grep casa a LINHA DE FALHA (">>> N FALHOU"), não a palavra solta.
  # Ele já disparou falso duas vezes por texto legítimo do próprio teste:
  # "HTTPError 502" numa tabela de classificação, e "vira FALHOU" num
  # rótulo — FALHOU é nome de estado da fila. O código de saída é o
  # critério primário; este grep é só rede para teste que erre e saia 0.
  if [ $RC -ne 0 ] || echo "$OUT" | grep -qE "^>>>.*FALHOU|Traceback \(most recent"; then
    bad "$t"
    echo "$OUT" | grep -E "FALHOU|ERRADO|Traceback" | head -4
  else
    ok "$t — $(echo "$OUT" | tail -1)"
  fi
done

# ---- 3b. SUÍTE-CANÁRIO: algum detector morreu? ---------------------------
echo
echo "3b. Canário — todo detector acende na sua frase?"
OUT=$(python3 tests/canario.py 2>&1)
if echo "$OUT" | grep -q "MORTOS: 0"; then
  ok "$(echo "$OUT" | grep 'VIVOS:')"
else
  bad "detector(es) MORTO(S)"
  echo "$OUT" | grep -E "MORTO" | head -6
fi

# ---- 3c. CANÁRIO DE SALVAGUARDAS ----------------------------------------
# Detector acende no caminho normal; SALVAGUARDA só roda quando algo dá
# errado — então nunca é exercitada em operação saudável. Duas morreram em
# duas semanas (_detect_rulership e a falha fechada de língua) e as duas
# foram achadas por acaso.
echo
echo "3c. Salvaguardas — cada uma disparou no seu caso?"
OUT=$(python3 tests/canario_salvaguardas.py 2>&1)
if echo "$OUT" | grep -q "MORTAS: 0"; then
  ok "$(echo "$OUT" | grep 'VIVAS:')"
else
  bad "salvaguarda(s) MORTA(S)"
  echo "$OUT" | grep "MORTA:" | head -6
fi

# ---- 4. PROPRIEDADES DA MANDALA ----------------------------------------
echo
echo "4. Mandala: 8 propriedades num censo curto"
OUT=$(cd wheel_renderer && python3 censo.py 15 2>&1 | grep -E "PACKING")
if echo "$OUT" | grep -q "violacoes totais: 0"; then
  ok "packing sem violações"
else
  bad "packing COM violações"; echo "$OUT"
fi

# ---- 5. PDF de verdade -------------------------------------------------
echo
echo "5. PDF gera?"
if python3 tests/smoke_pdf.py >/tmp/gate_pdf.txt 2>&1; then
  ok "$(tail -1 /tmp/gate_pdf.txt)"
else
  bad "PDF falhou"; tail -6 /tmp/gate_pdf.txt
fi

# ---- 5b. BUILD LIMPO (só com --build; caro) ----------------------------
if [ "${1:-}" = "--build" ]; then
  echo
  echo "5b. Build limpo — venv novo, resolução do zero"
  if OUT=$(./tests/build_limpo.sh 2>&1); then
    ok "$OUT"
  else
    bad "build limpo FALHOU"; echo "$OUT" | sed 's/^/      /'
  fi
fi

# ---- 6. RELATÓRIO DE FUMAÇA contra produção (opcional) -----------------
if [ "${1:-}" = "--smoke" ]; then
  echo
  echo "6. Relatório real contra produção"
  if python3 tests/smoke_report.py >/tmp/gate_rep.txt 2>&1; then
    ok "$(tail -1 /tmp/gate_rep.txt)"
  else
    bad "geração real falhou"; tail -6 /tmp/gate_rep.txt
  fi
fi

echo
echo "=============================================="
if [ "$FALHAS" -eq 0 ]; then
  echo "GATE PASSOU — pode deployar"
  exit 0
else
  echo "GATE FALHOU em $FALHAS etapa(s) — NÃO DEPLOYAR"
  echo
  echo "  ATENÇÃO: se você encadeou este gate com '&& git push', confira que"
  echo "  o && está no GATE e não num grep depois dele. Em 19/07 eu empurrei"
  echo "  com o gate reprovando porque o && pegou o sucesso do grep."
  exit 1
fi
