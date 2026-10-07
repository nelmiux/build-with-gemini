#!/usr/bin/env bash
# Validate every ```mermaid block in the Markdown docs by rendering it with mermaid-cli.
# Exits non-zero if any diagram fails to parse/render, printing the parser error.
#
# Usage: bash scripts/validate_diagrams.sh            (installs mermaid-cli into a temp dir)
#        MMDC_BIN=/path/to/mmdc bash scripts/validate_diagrams.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MERMAID_CLI_VERSION="${MERMAID_CLI_VERSION:-11.17.0}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Headless Chromium needs these flags on CI runners (no user namespaces for the sandbox).
cat > "$WORK/puppeteer.json" <<'JSON'
{ "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"] }
JSON

if [ -z "${MMDC_BIN:-}" ]; then
  echo "Installing @mermaid-js/mermaid-cli@${MERMAID_CLI_VERSION} into a temporary directory…"
  (cd "$WORK" && npm init -y >/dev/null 2>&1 && npm install --no-audit --no-fund --silent "@mermaid-js/mermaid-cli@${MERMAID_CLI_VERSION}")
  MMDC_BIN="$WORK/node_modules/.bin/mmdc"
fi

mkdir -p "$WORK/blocks"
cd "$ROOT"
# Extract each fenced mermaid block into its own file: <doc>__<n>.mmd
for md in README.md EXECUTIVE_SUMMARY.md ASSESSMENT_AND_RECOMMENDATIONS.md docs/*.md; do
  [ -f "$md" ] || continue
  awk -v base="$(basename "$md" .md)" -v out="$WORK/blocks" '
    /^```mermaid/ { inblock=1; n++; fn=sprintf("%s/%s__%02d.mmd", out, base, n); next }
    /^```/        { if (inblock) { inblock=0; close(fn); next } }
    inblock       { print > fn }
  ' "$md"
done

total=0; failed=0
shopt -s nullglob
for mmd in "$WORK"/blocks/*.mmd; do
  total=$((total + 1))
  name="$(basename "$mmd" .mmd)"
  if out="$("$MMDC_BIN" -i "$mmd" -o "$WORK/blocks/$name.svg" -p "$WORK/puppeteer.json" -q 2>&1)"; then
    echo "  ✔ ${name/__/ #}"
  else
    failed=$((failed + 1))
    echo "  ✘ ${name/__/ #}"
    printf '%s\n' "$out" | grep -E 'Parse error|Expecting|^\.\.\.|-+\^' | sed 's/^/      /' || printf '%s\n' "$out" | head -5 | sed 's/^/      /'
  fi
done

echo
if [ "$total" -eq 0 ]; then echo "No mermaid blocks found."; exit 0; fi
if [ "$failed" -gt 0 ]; then
  echo "Mermaid validation FAILED: $failed of $total diagrams did not render."
  exit 1
fi
echo "Mermaid validation passed: all $total diagrams rendered."
