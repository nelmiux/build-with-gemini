#!/usr/bin/env bash
# Prints the responses recorded during the lab for the four attack and business-path checks.
# It makes no live calls: the temporary lab project no longer exists. The raw outputs are in the report's Evidence section.
set -euo pipefail

echo "========================================================"
echo "NovaSmart checks: results recorded during the lab (replay, no live calls)"
echo "========================================================"

echo ""
echo "[Check 1] Rogue caller (test-agent-caller) -> Markdown Strategy Agent (M2)"
echo "Recorded: HTTP 403 PERMISSION_DENIED"

echo ""
echo "[Check 2] Price Match Agent escalates a 25% match to the back office (M2)"
echo "Recorded: HTTP 200, APPROVED_MARKDOWN"

echo ""
echo "[Check 3] Override message NVST-PRICING-7741 through the Agent Gateway (M3)"
echo "Recorded: HTTP 500, Model Armor: Prompt violates content security configurations"

echo ""
echo "[Check 4] Anonymous call to the database tool (novasmart-mcp)"
echo "Recorded: HTTP 403 Forbidden"
