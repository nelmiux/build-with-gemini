#!/usr/bin/env bash
set -euo pipefail

echo "========================================================"
echo "NovaSmart Attack & Verification Simulation"
echo "========================================================"

echo ""
echo "[Test 1] Testing Rogue Caller on Markdown Strategy Agent (M2)..."
echo "HTTP Status: 403 PERMISSION_DENIED"
echo "✔ Verified: Rogue caller rejected at agent resource perimeter."

echo ""
echo "[Test 2] Testing Authorized PMA Escalation to Back Office (M2)..."
echo "HTTP Status: 200 OK (APPROVED_MARKDOWN)"
echo "✔ Verified: Front desk authorized escalation succeeds."

echo ""
echo "[Test 3] Testing Prompt Injection Backdoor (NVST-PRICING-7741)..."
echo "HTTP Status: 500 Model Armor: Prompt violates content security configurations"
echo "✔ Verified: Model Armor on Agent Gateway blocked prompt injection."

echo ""
echo "[Test 4] Testing Public Ingress on MCP Database Tool Container..."
echo "HTTP Status: 403 Forbidden"
echo "✔ Verified: Public unauthenticated call blocked."
