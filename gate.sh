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
for t in prove_text prove_doutrina prove_positions; do
  OUT=$(python3 "tests/$t.py" 2>&1)
  if echo "$OUT" | grep -qE "FALHOU|Traceback|Error"; then
    bad "$t"
    echo "$OUT" | grep -E "FALHOU|Error" | head -4
  else
    ok "$t — $(echo "$OUT" | tail -1)"
  fi
done

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
  exit 1
fi
