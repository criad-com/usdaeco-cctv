#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLCHAIN_DIR="${TOOLCHAIN_DIR:-$HERE/../usdaeco-toolchain}"
CORE_DIR="${AECO_CORE_ROOT:-${CORE_DIR:-$HERE/../usdaeco-core}}"
exec bash "$TOOLCHAIN_DIR/build.sh" usdAecoCctv "$HERE" \
    --dep "${CORE_PLUGIN_DIR:-$CORE_DIR/out/plugins/usdAeco/resources}" "$@"
