#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
temporary_parent="${TMPDIR:-/tmp}"
validation_root="$(mktemp -d "$temporary_parent/agentboard-quality.XXXXXX")"

cleanup() {
  case "$validation_root" in
    "$temporary_parent"/agentboard-quality.*)
      rm -rf -- "$validation_root"
      ;;
    *)
      printf 'Refusing to remove unexpected validation path: %s\n' "$validation_root" >&2
      ;;
  esac
}
trap cleanup EXIT

uv venv "$validation_root/venv" --python ">=3.11" --quiet
uv pip install \
  --python "$validation_root/venv/bin/python" \
  -e "${repo_root}[dev]" \
  --quiet

(
  cd "$repo_root"
  "$validation_root/venv/bin/python" -m pytest -q
  "$validation_root/venv/bin/python" -m ruff check .
)

mkdir -p "$validation_root/dist"
uv build \
  --wheel \
  --out-dir "$validation_root/dist" \
  "$repo_root" \
  --quiet
wheel_path="$(find "$validation_root/dist" -maxdepth 1 -type f -name '*.whl' -print -quit)"
test -n "$wheel_path"
"$validation_root/venv/bin/python" - "$wheel_path" <<'PY'
from pathlib import Path
import sys
import zipfile

wheel = Path(sys.argv[1])
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())

assert "agentboard/static/index.html" in names
assert any(
    name.startswith("agentboard/static/assets/") and name.endswith(".js")
    for name in names
)
assert any(
    name.startswith("agentboard/static/assets/") and name.endswith(".css")
    for name in names
)
PY

mkdir -p "$validation_root/project/web" "$validation_root/project/src/agentboard"
tar \
  -C "$repo_root/web" \
  --exclude=node_modules \
  --exclude=dist \
  --exclude='*.tsbuildinfo' \
  -cf - . |
  tar -C "$validation_root/project/web" -xf -

(
  cd "$validation_root/project/web"
  npm ci --quiet
  npm test
  npm run lint
  npm run build
  npm audit
)

test -f "$validation_root/project/src/agentboard/static/index.html"
printf 'unix_quality_gate=ok\n'
