#!/usr/bin/env bash
# Runs four checks on M1-M2 changes against the lab project. Every check must be confirmed by a live command;
# nothing is reported as verified when a command fails. Exits 1 if any check fails or cannot run.
# Note: the lab ran in a temporary Qwiklabs project that has since been deleted, so today this reports
# that the checks cannot be verified.
set -uo pipefail

PROJECT="qwiklabs-gcp-02-3408357845ee"
REGION="us-central1"
CPA_ENGINE="reasoningEngines/3655712884878475264"
failures=0
pass() { echo "  OK    $1"; }
fail() { echo "  FAIL  $1"; failures=$((failures + 1)); }

echo "NovaSmart estate checks (project $PROJECT, region $REGION)"
echo ""

echo "1. Promo agent registered in Agent Registry"
if gcloud agent-registry services describe promo-agent --location="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
  pass "services/promo-agent exists"
else
  fail "could not confirm services/promo-agent"
fi

echo "2. Promo agent runs as its own service account"
sa=$(gcloud run services describe promo-agent-shadow --region="$REGION" --project="$PROJECT" --format="value(spec.template.spec.serviceAccountName)" 2>/dev/null) || sa=""
if [ "$sa" = "promo-agent-sa@${PROJECT}.iam.gserviceaccount.com" ]; then pass "promo-agent-shadow runs as $sa"; else fail "promo-agent-shadow identity is '${sa:-unknown}'"; fi

echo "3. Database tool closed to the public and open to the personalization agent"
policy=$(gcloud run services get-iam-policy novasmart-mcp --region="$REGION" --project="$PROJECT" --format=json 2>/dev/null) || policy=""
if [ -z "$policy" ]; then fail "could not read the novasmart-mcp policy"
elif printf '%s' "$policy" | grep -q '"allUsers"'; then fail "allUsers can still invoke novasmart-mcp"
elif printf '%s' "$policy" | grep -q "$CPA_ENGINE"; then pass "allUsers removed; the personalization agent identity is bound"
else fail "the personalization agent identity is not bound"; fi

echo "4. Customer dataset readable by the personalization agent"
acl=$(bq show --project_id="$PROJECT" --format=prettyjson customer_data 2>/dev/null) || acl=""
if [ -z "$acl" ]; then fail "could not read the customer_data ACL"
elif printf '%s' "$acl" | grep -q "$CPA_ENGINE"; then pass "the personalization agent identity is on the customer_data ACL"
else fail "the personalization agent identity is not on the customer_data ACL"; fi

echo ""
if [ "$failures" -eq 0 ]; then echo "All checks confirmed."; else echo "$failures check(s) could not be confirmed."; exit 1; fi
