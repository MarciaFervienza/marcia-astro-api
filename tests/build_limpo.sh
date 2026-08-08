#!/usr/bin/env bash
# BUILD DE VERDADE — venv novo, resolução do zero, e o código importa?
# É a única etapa que reproduz o que o Railway faz. Cara (minutos), então só
# roda com `./gate.sh --build`, antes de mexer em requirements.txt.
set -uo pipefail
cd "$(dirname "$0")/.."
BT=$(mktemp -d)
trap 'rm -rf "$BT"' EXIT
python3 -m venv "$BT" >/dev/null 2>&1 || { echo "venv falhou"; exit 1; }
if ! "$BT/bin/pip" install -q -r requirements.txt >/tmp/gate_build.txt 2>&1; then
  echo "INSTALL FALHOU — é o que o Railway faria:"
  tail -15 /tmp/gate_build.txt
  exit 1
fi
if ! "$BT/bin/python" -c "
import warnings; warnings.filterwarnings('ignore')
import sys; sys.path.insert(0,'.')
import app, report_generator, text_verifier, pdf_generator
import positions_table, word_lint, retry_util
print('venv limpo instala e o codigo importa')
" 2>/tmp/gate_imp.txt; then
  echo "INSTALOU MAS NÃO IMPORTA:"; tail -6 /tmp/gate_imp.txt; exit 1
fi
exit 0
