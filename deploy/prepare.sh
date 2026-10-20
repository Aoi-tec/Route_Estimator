#!/bin/sh
# 互換エントリーポイント。全国道路データと環境の準備まで（前処理はしない）。
set -eu
route_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec sh "$route_root/scripts/unix_entry.sh" prepare "$@"
