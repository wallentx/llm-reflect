#!/usr/bin/env sh
set -eu
repo_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$repo_dir/tools/install.py" "$@"
fi
printf '%s\n' 'Python 3.11+ is required. On Termux: pkg install python' >&2
exit 1
