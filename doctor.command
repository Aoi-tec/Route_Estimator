#!/bin/sh
route_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$route_dir/scripts/unix_entry.sh" doctor "$@"
