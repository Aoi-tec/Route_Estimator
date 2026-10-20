#!/bin/sh
set -eu
route_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$route_root"
for route_python in python3.14 python3.13 python3.12 python3; do
  if command -v "$route_python" >/dev/null 2>&1 && "$route_python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)' >/dev/null 2>&1; then
    exec "$route_python" "$route_root/deploy/setup.py" "$@"
  fi
done
printf '%s\n' 'Python 3.12以上が必要です。https://www.python.org/downloads/ から導入して再実行してください。' >&2
exit 2
