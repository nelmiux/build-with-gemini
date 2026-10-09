# M0: See Everything — Discovery & Baseline Architecture

## Overview
During the initial discovery phase (Module 0), we conducted a comprehensive architectural audit of the AI agent estate for the retail enterprise **NovaSmart**, deployed within Google Cloud project `qwiklabs-gcp-02-3408357845ee`.

The primary objective was to establish complete situational awareness of all running workloads, service identities, IAM permissions, and network exposure surfaces prior to executing any remediation strategies. This zero-trust baseline is critical for secure multi-agent governance.

---

## Initial Architecture Diagram

```mermaid
flowchart TD
    subgraph Ingress [Ingress & External Callers]
        Associate[Store Associate / Shopper]
        Rogue[Rogue Test Caller / test-agent-caller]
    end

    subgraph CloudRun ["Cloud Run Workloads (us-central1)"]
        ShadowPromo["promo-agent-shadow (Cloud Run)<br/>[UNREGISTERED SHADOW IT]"]
        MCP["novasmart-mcp (Cloud Run)<br/>[PUBLIC INVOKER: allUsers]"]
    end

    subgraph Runtime [Vertex AI Reasoning Engines]
        PMA["Price Match Agent<br/>(Front Desk: 2824798753628618752)"]
        MSA["Markdown Strategy Agent<br/>(Back Office: 7249585387520131072)"]
        CPA["Customer Personalization Agent<br/>(Personalization: 3655712884878475264)"]
    end

    subgraph Identity [Shared Legacy Identity]
        SharedSA["novasmart-customer-sa<br/>roles/bigquery.admin (Project-wide)<br/>roles/aiplatform.user (Project-wide)"]
    end

    subgraph Data [Data Store]
        BQ["BigQuery Dataset: customer_data<br/>Table: customers (20 customer profiles)"]
        OtherBQ["Other Project Datasets<br/>(Exposed to permanent deletion)"]
    end

    Associate -->|Chat Ingress| PMA
    Associate -->|Direct Ingress| ShadowPromo
    Rogue -->|Authorized on MSA| MSA
    
    ShadowPromo -.->|Runs as| SharedSA
    CPA -.->|Runs as| SharedSA
    
    SharedSA ==>|Project Admin| BQ
    SharedSA ==>|Project Admin| OtherBQ
    
    Associate -.->|Public Invoke| MCP
    MCP -->|query_database| BQ
```

---

## Critical Baseline Findings

| # | Component | Observed Vulnerability | Impact / Risk |
|---|---|---|---|
| **1** | `promo-agent-shadow` | Unregistered Cloud Run microservice operating outside the official catalog. | **Shadow IT Risk**: Bypasses governance and compliance protocols, creating an ownership blind spot. |
| **2** | `novasmart-customer-sa` | Shared service account utilized concurrently by `promo-agent-shadow` and `Customer Personalization Agent`. | **Identity Conflation**: Forensic audit logs cannot distinguish marketing workloads from customer-facing queries. |
| **3** | `novasmart-customer-sa` | Broadly bound to `roles/bigquery.admin` across the entire Google Cloud project. | **Catastrophic Blast Radius**: A prompt injection payload or logic flaw could permanently drop or maliciously alter all datasets within the project. |
| **4** | `novasmart-mcp` | Cloud Run service exposing a `query_database` tool bound to `roles/run.invoker: allUsers`. | **Public Exposure**: Anonymous HTTP requests originating from any external source can query internal databases without authentication. |
| **5** | `Markdown Strategy Agent` | Resource IAM policy authorized an orphaned service account (`test-agent-caller`) while lacking an explicit binding for the `Price Match Agent`. | **Inverted Access Control**: Unapproved test accounts maintain access to confidential margin data, whereas legitimate front-desk agent escalations are blocked. |

---

## Verification Commands Executed During M0
```bash
# 1. Discover Cloud Run services
gcloud run services list --project=qwiklabs-gcp-02-3408357845ee --region=us-central1

# 2. Inspect Reasoning Engines
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  https://us-central1-aiplatform.googleapis.com/v1/projects/qwiklabs-gcp-02-3408357845ee/locations/us-central1/reasoningEngines

# 3. Check IAM Bindings of Shared Service Account
gcloud projects get-iam-policy qwiklabs-gcp-02-3408357845ee \
  --flatten="bindings[].members" \
  --filter="bindings.members:novasmart-customer-sa"
```
