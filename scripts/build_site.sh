#!/usr/bin/env bash
# Assemble the static site that GitHub Pages serves: the interactive app, the docs viewer,
# and the Markdown sources the viewer fetches. Nothing is compiled; files are copied as-is.
#
# Usage: bash scripts/build_site.sh [output-dir]   (default: _site)
# The output directory is wiped first, so it must be absent, empty, or a previous output of this script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
OUT="${1:-_site}"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac
MARKER=".build_site_output"

# Never delete the repository (or anything containing it), the home directory or the filesystem root.
if [ -e "$OUT" ]; then
  OUT_REAL="$(cd "$OUT" 2>/dev/null && pwd -P || echo "$OUT")"
  case "$OUT_REAL" in
    "/"|"${HOME:-/nonexistent}"|"$ROOT") echo "refusing to delete $OUT_REAL" >&2; exit 1 ;;
  esac
  case "$ROOT/" in "$OUT_REAL"/*) echo "refusing to delete $OUT_REAL: it contains the repository" >&2; exit 1 ;; esac
  if [ ! -d "$OUT_REAL" ]; then echo "refusing to delete $OUT_REAL: not a directory" >&2; exit 1; fi
  if [ -n "$(ls -A "$OUT_REAL")" ] && [ ! -f "$OUT_REAL/$MARKER" ]; then
    echo "refusing to delete $OUT_REAL: it is not empty and was not produced by this script" >&2; exit 1
  fi
  rm -rf "$OUT_REAL"
fi
mkdir -p "$OUT/docs"

cd "$ROOT"
cp index.html docs.html 404.html .nojekyll LICENSE \
   README.md EXECUTIVE_SUMMARY.md ASSESSMENT_AND_RECOMMENDATIONS.md "$OUT/"
cp docs/*.md "$OUT/docs/"
: > "$OUT/$MARKER"

echo "Site assembled in $OUT:"
(cd "$OUT" && find . -type f ! -name "$MARKER" | sort | sed 's|^\./|  |')
