#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

require_file() {
  [ -f "$1" ] || fail "missing required file: $1"
}

require_file AGENTS.md
require_file ARCHITECTURE.md
require_file README.md
require_file pyproject.toml
require_file toolburn
require_file install.sh
require_file docs/adapter-contract.md
require_file docs/product-story.md
require_file docs/quality.md
require_file docs/runbook.md
require_file docs/exec-plans/active/README.md
require_file docs/exec-plans/active/toolburn-3-phase-plan.md
require_file docs/exec-plans/completed/README.md
require_file src/toolburn/__init__.py
require_file src/toolburn/activity.py
require_file src/toolburn/cli.py
require_file src/toolburn/pass_receipt.py
require_file src/toolburn/report.py
require_file src/toolburn/scan.py
require_file src/toolburn/schema.py
require_file src/toolburn/semantics.py
require_file tests/test_cli.py
require_file tests/test_pass_receipt.py
require_file tests/test_schema.py
require_file scripts/install-local.sh
require_file scripts/validate.sh
require_file site/AGENTS.md
require_file site/public/index.php
require_file site/public/render_readme.py
require_file site/public/assets/toolburn-logo.png
require_file site/public/assets/toolburn-logo-text.png

if find . \( -path '*/__pycache__' -o -name '*.pyc' -o -name '.pytest_cache' \) -print | grep -q .; then
  echo "generated Python cache files must not be left in the repo" >&2
  find . \( -path '*/__pycache__' -o -name '*.pyc' -o -name '.pytest_cache' \) -print >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
for path in sorted(Path("src").rglob("*.py")) + sorted(Path("tests").rglob("*.py")) + sorted(Path("site").rglob("*.py")):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("python syntax ok")
PY
sh -n install.sh
php -l site/public/index.php >/dev/null
python3 site/public/render_readme.py --readme README.md >/tmp/toolburn-site-render.html
grep -q '<h1 id="toolburn">Toolburn</h1>' /tmp/toolburn-site-render.html

PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
./toolburn --help >/tmp/toolburn-cli-help.txt
grep -q 'Local-first token burn profiler' /tmp/toolburn-cli-help.txt
./toolburn recent --help >/tmp/toolburn-recent-help.txt
grep -q 'lookback window' /tmp/toolburn-recent-help.txt
./toolburn pass --help >/tmp/toolburn-pass-help.txt
grep -q 'current, previous' /tmp/toolburn-pass-help.txt
./toolburn inspect --help >/tmp/toolburn-inspect-help.txt
grep -q 'exact hotspot ID' /tmp/toolburn-inspect-help.txt
./toolburn compare --help >/tmp/toolburn-compare-help.txt
grep -q 'baseline session UUID' /tmp/toolburn-compare-help.txt
tmp_bin="$(mktemp -d /tmp/toolburn-bin-XXXXXX)"
TOOLBURN_BIN_DIR="$tmp_bin" ./scripts/install-local.sh >/tmp/toolburn-install.txt
"$tmp_bin/toolburn" --help >/tmp/toolburn-installed-help.txt
grep -q 'Local-first token burn profiler' /tmp/toolburn-installed-help.txt
rm -rf "$tmp_bin"

tmp_db="$(mktemp -u /tmp/toolburn-validate-XXXXXX.sqlite)"
./toolburn schema --db "$tmp_db" >/tmp/toolburn-schema.txt
grep -q '^actors$' /tmp/toolburn-schema.txt
grep -q '^token_events$' /tmp/toolburn-schema.txt
rm -f "$tmp_db"

if grep -RInE 'cp_live_[A-Za-z0-9]+|assistant\.env|telegram|session-key|webhook|BEGIN (RSA|OPENSSH|PRIVATE) KEY|github_pat_' \
  AGENTS.md ARCHITECTURE.md README.md docs scripts site src tests pyproject.toml \
  | grep -v '^scripts/validate.sh:' >/tmp/toolburn-secretish.txt; then
  echo "secret-shaped or private-runtime strings found:" >&2
  cat /tmp/toolburn-secretish.txt >&2
  exit 1
fi

if grep -RInE 'CodePager/ToolBurn|/srv/pager/(repos/toolburn|sites/toolburn\.com)' \
  AGENTS.md ARCHITECTURE.md README.md docs scripts site src tests pyproject.toml \
  | grep -v '^scripts/validate.sh:'; then
  echo "stale ToolBurn ownership reference found" >&2
  exit 1
fi

echo "toolburn validation ok"
