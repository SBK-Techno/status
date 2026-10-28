#!/usr/bin/env bash
# Fail with an actionable message when a pinned toolchain checkout is missing.
#
# Usage: require_checkout.sh <label> <resolved-path> <default-path>
#   label         platform or brand; also names the overridable make variable
#   resolved-path absolute path make will pass to the Go tools
#   default-path  conventional relative checkout documented in README.md
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <label> <resolved-path> <default-path>" >&2
  exit 2
fi

label=$1
path=$2
default=$3

if [ -d "$path" ]; then
  exit 0
fi

env_var=$(printf '%s' "$label" | tr '[:lower:]' '[:upper:]')
printf '%s checkout not found: %s\n' "$label" "$path" >&2
printf 'Clone it to %s or pass %s=/path/to/checkout.\n' "$default" "$env_var" >&2
printf 'The CI pin is recorded in .github/actions/toolchain/action.yml.\n' >&2
exit 1
