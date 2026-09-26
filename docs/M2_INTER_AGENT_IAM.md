# M2: Control the Connections — Inter-Agent Boundary & Resource IAM

## Overview
In Module 2, we addressed **inter-agent access control** and **tool microservice protection**.

In multi-agent systems, sensitive "back-office" agents (e.g. pricing, margin strategy, financial transactions) must only accept invocations from authorized front-desk agents, never directly from external users or orphan test accounts.

---

## Architecture Diagram (Post-M2)

```mermaid
flowchart TD
    subgraph Callers [Callers]
        PMA["Price Match Agent (Front Desk)<br/>SPIFFE: principal://.../2824798753628618752"]
        Rogue["test-agent-caller (Legacy SA)<br/>test-agent-caller@..."]
    end

    subgraph BackOffice [Back-Office Perimeter]
        subgraph MSA [Markdown Strategy Agent (reasoningEngines/7249585387520131072)]
            MSA_IAM["Resource IAM Policy (etag: BwZcVeoymlE=)<br/>roles/aiplatform.user:<br/>- PMA SPIFFE Principal ONLY"]
            MSA_Logic["Confidential Margin Calculation & Rules"]
        end
    end

    subgraph ToolLayer [Cloud Run Tool Layer]
        MCP["novasmart-mcp<br/>roles/run.invoker:<br/>- CPA SPIFFE Principal ONLY<br/>[allUsers REVOKED]"]
    end

    PMA ==>|Authorized A2A Escalation (HTTP 200 OK)| MSA_IAM
    MSA_IAM --> MSA_Logic

    Rogue -.->|Unauthorized Call (HTTP 403 PERMISSION_DENIED)| MSA_IAM
```

---

## Key Remediations & Technical Mechanics

### 1. Vertex AI Reasoning Engine Resource IAM
Unlike standard Google Cloud resources, Vertex AI Reasoning Engines do not support `gcloud ai reasoning-engines set-iam-policy`. Modifications must be made via REST API calls with strict etag concurrency management:

```bash
# 1. Fetch current policy and etag
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN"   https://us-central1-aiplatform.googleapis.com/v1/projects/82075562614/locations/us-central1/reasoningEngines/7249585387520131072:getIamPolicy > /tmp/msa_iam.json

# 2. Update policy: Evict test-agent-caller and bind PMA SPIFFE principal
cat << 'EOF' > /tmp/updated_msa_iam.json
{
  "policy": {
    "bindings": [
      {
        "role": "roles/aiplatform.user",
        "members": [
          "principal://agents.global.org-616463121992.system.id.goog/resources/aiplatform/projects/82075562614/locations/us-central1/reasoningEngines/2824798753628618752"
        ]
      }
    ],
    "etag": "BwZcVeoymlE="
  }
}
EOF

# 3. Apply policy atomically
curl -s -X POST -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d @/tmp/updated_msa_iam.json   https://us-central1-aiplatform.googleapis.com/v1/projects/82075562614/locations/us-central1/reasoningEngines/7249585387520131072:setIamPolicy
```

### 2. Live Verification Results
1. **Rogue Caller Refusal**: Attempted invocation by `test-agent-caller` returned:
   ```json
   {
     "error": {
       "code": 403,
       "message": "Permission 'aiplatform.reasoningEngines.query' denied on resource 'reasoningEngines/7249585387520131072'",
       "status": "PERMISSION_DENIED"
     }
   }
   ```
2. **Legitimate Escalation**: When Price Match Agent escalated discounts > 10%, Markdown Strategy Agent responded with `HTTP 200 OK` and approved markdown impact.
3. **MCP Microservice Lockdown**: Revoked `roles/run.invoker: allUsers` on `novasmart-mcp` and bound strictly to Customer Personalization Agent's SPIFFE badge. Anonymous requests now receive `HTTP 403 Forbidden`.
