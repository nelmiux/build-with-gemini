# NovaSmart AI Estate — Architecture Evolution & Security Hardening Report

This report documents the M0–M2 work on the NovaSmart AI estate: the initial architecture, the changes made in each phase, and the M1–M2 end state.

> [!NOTE]
> **Scope.** This report covers M0–M2 and the cleanup of a residual project role. Content screening appears below only as an identified requirement: the Agent Gateway with Model Armor that M3 added and verified is not drawn in the target-state diagram, and M5 (evaluation) is not covered. See [M3](M3_CONTENT_SCREENING.md), [M5](M5_EVALUATION_DECISION.md), and the field report for those.

The field report at [nelmiux.github.io/build-with-gemini](https://nelmiux.github.io/build-with-gemini/) (source: [`index.html`](../index.html)) tells the whole story for colleagues who were not at the workshop.

---

## 1. Executive Summary & Security Posture Scorecard

Before remediation, the estate had two agents sharing one login, an unregistered shadow AI service with full administrative rights to all BigQuery datasets, a publicly accessible MCP microservice, and a rogue test account authorized on the core markdown strategy agent.

After M0 (Discovery), M1 and M2 brought the estate much closer to least privilege, though broad project-wide roles can still call agents around their caller lists, the store application calls the back office directly, and the store portal runs under an Owner-level login.

| Security Dimension | Initial State (M0) | Remediated State (M1 + M2) | Status |
| :--- | :--- | :--- | :---: |
| **Catalog & Governance** | `promo-agent-shadow` unregistered; unowned | Registered in Agent Registry under Marketing ownership | **REMEDIATED** |
| **Workload Identity** | Shared legacy SA (`novasmart-customer-sa`) across multiple workloads | Per-agent SPIFFE identity (`principal://...`) & dedicated Cloud Run SA | **REMEDIATED** |
| **Data Access (BigQuery)** | Project-wide `roles/bigquery.admin` (read/write/delete any dataset) | Scoped `READER` on `customer_data` dataset; `jobUser` at project level | **REMEDIATED** |
| **Tool / MCP Exposure** | `novasmart-mcp` exposed to `allUsers` (`roles/run.invoker`) | Public access revoked; restricted strictly to authorized Agent Identity | **REMEDIATED** |
| **Inter-Agent Boundary** | Back-office MSA authorized `test-agent-caller`; front-desk PMA missing | MSA Resource IAM restricted exclusively to PMA; rogue caller receives HTTP 403 | **REMEDIATED** |
| **Ambient IAM Grants** | Vacated `novasmart-customer-sa` held project-wide `roles/aiplatform.user` | Pruned `roles/aiplatform.user` from vacated account; other broad roles remain | **PARTIAL** |
| **Content Screening** | Backdoor prompt override in PMA system instruction (`NVST-PRICING-7741`) | Identified Layer 2 requirement (Model Armor prompt screening) | **IDENTIFIED (M3)** |

---

## 2. Initial General Architecture (Before Remediation)

In the initial state, the system suffered from identity conflation, excessive privilege grants, and missing perimeter boundaries.

```mermaid
flowchart TD
    subgraph PublicInternet ["Public Internet"]
        Customer[Shopper / Public Internet]
        Rogue[Rogue Caller / Test Credential]
    end

    subgraph "Publicly Exposed Cloud Run Services (us-central1)"
        ShadowRun["promo-agent-shadow (Cloud Run)<br/>[UNREGISTERED SHADOW AGENT]"]
        MCPRun["novasmart-mcp (Cloud Run)<br/>[roles/run.invoker: allUsers]"]
    end

    subgraph "Managed Agent Runtime (Vertex AI Reasoning Engines)"
        CPA["Customer Personalization Agent<br/>(ID: 3655712884878475264)"]
        PMA["Price Match Agent<br/>(ID: 2824798753628618752)"]
        MSA["Markdown Strategy Agent<br/>(ID: 7249585387520131072)"]
    end

    subgraph "Shared IAM Identity"
        SharedSA["novasmart-customer-sa<br/>[PROJECT-LEVEL roles/bigquery.admin]<br/>[PROJECT-LEVEL roles/aiplatform.user]"]
    end

    subgraph "Data Store"
        BQ["BigQuery: customer_data.customers<br/>(20 customer profiles)"]
        AllBQ["All Other Project BigQuery Datasets<br/>(Exposed to deletion/tampering)"]
    end

    %% Connections
    Customer -->|Public invocation| ShadowRun
    Customer -->|Chat query| PMA
    Rogue -->|roles/aiplatform.user on MSA| MSA
    
    ShadowRun -.->|Runs as| SharedSA
    CPA -.->|Runs as| SharedSA
    
    SharedSA ==>|Full Admin / Delete| BQ
    SharedSA ==>|Full Admin / Delete| AllBQ

    Customer -.->|Public unauthenticated call| MCPRun
    MCPRun -->|query_database| BQ

    PMA -.->|Cannot verify / No direct binding| MSA
    
    classDef danger fill:#ffdddd,stroke:#d93025,stroke-width:2px,color:#900;
    classDef warning fill:#fff3cd,stroke:#f9ab00,stroke-width:2px,color:#856404;
    classDef safe fill:#e6f4ea,stroke:#137333,stroke-width:2px,color:#0d652d;

    class ShadowRun,MCPRun,SharedSA,Rogue danger;
    class CPA,MSA warning;
    class PMA,BQ safe;
```

### Critical Vulnerabilities in the Initial Architecture
1. **Identity Conflation & Impersonation:** Both `promo-agent-shadow` (unmanaged Cloud Run) and `customer-personalization-agent` (Reasoning Engine) signed in as `novasmart-customer-sa`. In BigQuery access logs, their operations were completely indistinguishable.
2. **Catastrophic Privilege Blast Radius:** `novasmart-customer-sa` held `roles/bigquery.admin` at the Google Cloud project level. Any prompt injection or vulnerability in the shadow agent could drop or rewrite every dataset in the corporate project.
3. **Public Microservice Attack Surface:** `novasmart-mcp` had `roles/run.invoker` bound to `allUsers`. Anyone with the URL could execute arbitrary database queries against the backend.
4. **Inverted Resource Permissions:** The back-office Markdown Strategy Agent (`reasoningEngines/7249585387520131072`) authorized a rogue `test-agent-caller` account, while the legitimate front-desk `Price Match Agent` had no binding on it.

---

## 3. Architecture Evolution: Phase by Phase

### Phase 1: Shadow Agent Registration & Identity Decoupling (M1)

In this phase, we brought the shadow agent into official governance and decoupled the shared service account into distinct, single-purpose identities.

```mermaid
flowchart TD
    subgraph "Agent Registry (Catalog & Governance)"
        Reg["Promotional Marketing Agent<br/>(services/promo-agent)<br/>Owner: Marketing Team"]
    end

    subgraph "Cloud Run"
        PromoRun["promo-agent-shadow<br/>(Cloud Run Service)"]
        PromoSA["promo-agent-sa<br/>[Dedicated Service Account]<br/>Roles: logWriter, metricWriter only"]
    end

    subgraph "Managed Agent Runtime"
        CPA["Customer Personalization Agent"]
        CPA_SPIFFE["Native Agent Identity (SPIFFE):<br/>principal://agents.global.org-.../reasoningEngines/3655712884878475264"]
    end

    subgraph "Vacated Legacy Identity"
        VacatedSA["novasmart-customer-sa<br/>[VACATED - No Workloads Attached]"]
    end

    Reg -->|Catalogs & Governs| PromoRun
    PromoRun -->|Runs as dedicated| PromoSA
    CPA -->|Runs as native| CPA_SPIFFE
    
    classDef changed fill:#e8f0fe,stroke:#1a73e8,stroke-width:2px,color:#174ea6;
    classDef retired fill:#f1f3f4,stroke:#5f6368,stroke-dasharray: 5 5,color:#5f6368;
    class PromoRun,PromoSA,CPA_SPIFFE,Reg changed;
    class VacatedSA retired;
```

#### What Changed
- **Registry Entry:** Created `services/promo-agent` in Agent Registry (`us-central1`), assigning formal marketing ownership.
- **Dedicated Service Account:** Provisioned `promo-agent-sa@qwiklabs-gcp-02-3408357845ee.iam.gserviceaccount.com` with zero database permissions.
- **Native Agent Identity:** Executed REST `PATCH` on Customer Personalization Agent reasoning engine to enable `AGENT_IDENTITY_TYPE_DEFAULT`. The runtime issued a verifiable SPIFFE principal.
- **Decoupling:** `novasmart-customer-sa` was completely vacated.

---

### Phase 2: Least-Privilege Data Access (M1) & Tool Lockdown (M2)

Here, we eliminated broad project-wide administrator privileges (M1). The MCP tool container shown here was locked down later, in M2 (change log rows 10–11).

```mermaid
flowchart TD
    subgraph "Managed Agent Identity"
        CPA_SPIFFE["Customer Personalization Agent<br/>principal://.../reasoningEngines/3655712884878475264"]
    end

    subgraph "Cloud Run Tooling"
        MCP["novasmart-mcp (Cloud Run)<br/>[REMOVED allUsers]<br/>[ADDED CPA SPIFFE invoker]"]
    end

    subgraph "BigQuery IAM & ACL"
        ProjectIAM["Project IAM Binding<br/>roles/bigquery.jobUser<br/>(Can execute query jobs)"]
        DatasetACL["customer_data Dataset ACL<br/>roles/bigquery.dataViewer (READER)<br/>(Scoped strictly to customer_data)"]
        Revoked["novasmart-customer-sa<br/>[REVOKED: roles/bigquery.admin]"]
    end

    subgraph "Protected Database"
        CustomersTable["customer_data.customers<br/>(20 rows protected)"]
        OtherDatasets["Other Project Datasets<br/>(No admin rights from the shared login)"]
    end

    CPA_SPIFFE ==>|1. Authenticated invoke| MCP
    CPA_SPIFFE -.->|Project scope| ProjectIAM
    CPA_SPIFFE ==>|2. Scoped dataset read| DatasetACL
    DatasetACL --> CustomersTable
    
    classDef secure fill:#e6f4ea,stroke:#137333,stroke-width:2px,color:#0d652d;
    classDef blocked fill:#fce8e6,stroke:#c5221f,stroke-dasharray: 5 5,color:#c5221f;
    class CPA_SPIFFE,MCP,ProjectIAM,DatasetACL,CustomersTable secure;
    class Revoked,OtherDatasets blocked;
```

#### What Changed
- **Revoked BigQuery Admin:** Removed `roles/bigquery.admin` from `novasmart-customer-sa`.
- **Dataset-Level Authorization:** Scoped `READER` permissions directly on the `customer_data` dataset ACL to CPA's SPIFFE identity. CPA cannot see or touch other datasets.
- **Job Execution Right-Sizing:** Bound `roles/bigquery.jobUser` to CPA's SPIFFE identity at the project level, allowing query jobs without table access.
- **MCP Perimeter (M2, rows 10–11):** Stripped `roles/run.invoker` from `allUsers` on `novasmart-mcp`. Bound `roles/run.invoker` strictly to CPA's SPIFFE principal.

---

### Phase 3: Inter-Agent Perimeter Lockdown (M2 Resource IAM)

In this phase, we enforced least privilege across agent-to-agent (A2A) communications, securing the back-office margin logic against unauthorized callers.

```mermaid
flowchart TD
    subgraph "Callers"
        PMA["Price Match Agent (Front Desk)<br/>principal://.../reasoningEngines/2824798753628618752"]
        Rogue["test-agent-caller (Legacy SA)<br/>test-agent-caller@..."]
    end

    subgraph "Back-Office Perimeter"
        subgraph MSA ["Markdown Strategy Agent (reasoningEngines/7249585387520131072)"]
            direction TB
            MSA_IAM["Resource IAM Policy (etag: BwZcVeoymlE=)<br/>roles/aiplatform.user:<br/>- principal://.../reasoningEngines/2824798753628618752"]
            MSA_Core["Margin & Pricing Strategy Logic<br/>(Confidential Business Rules)"]
        end
    end

    PMA ==>|"Authorized A2A Call (HTTP 200 OK)"| MSA
    MSA_IAM --> MSA_Core

    Rogue -.->|"Unauthorized Call (HTTP 403 PERMISSION_DENIED)"| MSA
    
    classDef approved fill:#e6f4ea,stroke:#137333,stroke-width:2px,color:#0d652d;
    classDef denied fill:#fce8e6,stroke:#c5221f,stroke-width:2px,color:#c5221f;
    class PMA,MSA_IAM,MSA_Core approved;
    class Rogue denied;
```

#### What Changed
- **Resource IAM Query:** Read the existing policy using `reasoningEngines/7249585387520131072:getIamPolicy` via REST.
- **Etag-Safe Atomic Update:** Called `:setIamPolicy` passing the verified etag, replacing `test-agent-caller` with the Price Match Agent's SPIFFE principal.
- **Refusal Verification:** Simulated caller invocation with `test-agent-caller` credentials; verified immediate HTTP 403 `PERMISSION_DENIED` rejection.
- **Legitimate Verification:** Invoked escalation flow with Price Match Agent identity; verified successful HTTP 200 query response.

---

### Phase 4: Residual Grant Cleanup (M2) & Threat Boundary Definition (Ahead of M3)

In this phase, we removed the vacated shared login’s project-wide right to call agents (other broad roles remain), and mapped the defense boundary between identity controls and content screening.

```mermaid
flowchart TD
    subgraph "Layer 1: Identity & Perimeter Control (M1–M2 CHANGES MADE)"
        direction TB
        L1_Identity["Per-Agent SPIFFE Badges<br/>(Cryptographically verifiable)"]
        L1_Resource["Resource-Level ACLs<br/>(MSA locked to PMA)"]
        L1_Tool["Service IAM<br/>(MCP locked to CPA)"]
        L1_Prune["Project IAM Pruning<br/>(aiplatform.user removed from novasmart-customer-sa)"]
    end

    subgraph "Layer 2: Content Screening & Guardrails (M3 FRONTIER)"
        direction TB
        L2_Input["Customer / Attacker Prompt"]
        L2_Backdoor["Known Prompt Vulnerability:<br/>Backdoor Override (Policy NVST-PRICING-7741)<br/>Bypasses discount logic if triggered"]
        L2_Defense["Target Defense:<br/>Model Armor Prompt Classifier & Sanitizer<br/>Screens input before LLM execution"]
    end

    L1_Identity --> L1_Resource --> L1_Tool --> L1_Prune
    L2_Input --> L2_Backdoor -.->|Needs activation| L2_Defense
    
    classDef l1 fill:#e6f4ea,stroke:#137333,stroke-width:2px,color:#0d652d;
    classDef l2 fill:#fef7e0,stroke:#b06000,stroke-width:2px,color:#b06000;
    class L1_Identity,L1_Resource,L1_Tool,L1_Prune l1;
    class L2_Input,L2_Backdoor,L2_Defense l2;
```

#### What Changed
- **Tier 1 Project Grant Pruning:** Removed `roles/aiplatform.user` from `novasmart-customer-sa` at the project level. The login was already vacated, so no running workload was affected; other broad project-wide roles remain.
- **Threat Boundary Mapping:** Confirmed that while identity/IAM prevents unauthorized actors from calling endpoints (HTTP 403), it does not inspect natural language text sent by authorized users. Identified the prompt override backdoor in PMA (`NVST-PRICING-7741`) as the primary target for Layer 2 Model Armor content guardrails.

---

## 4. Final Hardened Architecture (Target State)

The target architecture shows the M1–M2 end state across the identity, resource, and data tiers (the M3 gateway is not drawn; see the scope note).

```mermaid
flowchart TD
    subgraph Users ["Users & External Systems"]
        StoreAssociate[Store Associate / Customer Portal]
        MarketingTeam[Marketing Operations]
        Attacker[Unauthorized Third Party / Rogue Caller]
    end

    subgraph "Agent Catalog & Governance (Agent Registry)"
        CatalogPMA["Price Match Agent Record"]
        CatalogMSA["Markdown Strategy Agent Record"]
        CatalogCPA["Customer Personalization Agent Record"]
        CatalogPromo["Promotional Marketing Agent<br/>(Owner: Marketing Team)"]
    end

    subgraph "Workload Execution Tier"
        subgraph "Cloud Run"
            PromoRun["promo-agent-shadow<br/>Identity: promo-agent-sa<br/>Perms: logWriter, metricWriter"]
            MCPService["novasmart-mcp<br/>Auth: IAM Required<br/>Invoker: CPA SPIFFE only"]
        end

        subgraph "Vertex AI Managed Agent Runtime"
            PMA["Price Match Agent (Front Desk)<br/>Reasoning Engine: 2824798753628618752<br/>Identity: principal://.../2824798753628618752"]
            MSA["Markdown Strategy Agent (Back Office)<br/>Reasoning Engine: 7249585387520131072<br/>Resource IAM: Scoped to PMA only"]
            CPA["Customer Personalization Agent<br/>Reasoning Engine: 3655712884878475264<br/>Identity: principal://.../3655712884878475264"]
        end
    end

    subgraph "Data Storage Tier (BigQuery)"
        BQ_Customer["BigQuery Dataset: customer_data<br/>Table: customers (20 rows)<br/>ACL: READER -> CPA SPIFFE"]
        BQ_Restricted["All Other BigQuery Datasets<br/>(No admin rights from the shared login)"]
    end

    %% Interactions
    StoreAssociate ==>|Chat Query| PMA
    MarketingTeam -->|Campaign Config| PromoRun
    Attacker -.->|"Direct Call (HTTP 403 BLOCKED)"| MSA
    Attacker -.->|"Unauthenticated Call (HTTP 403 BLOCKED)"| MCPService

    PMA ==>|"A2A Escalation > 10% discount (HTTP 200 APPROVED)"| MSA
    CPA ==>|Authenticated Tool Call| MCPService
    MCPService ==>|query_database| BQ_Customer
    
    classDef actor fill:#f1f3f4,stroke:#3c4043,stroke-width:2px,color:#202124;
    classDef agent fill:#e8f0fe,stroke:#1a73e8,stroke-width:2px,color:#174ea6;
    classDef data fill:#e6f4ea,stroke:#137333,stroke-width:2px,color:#0d652d;
    classDef blocked fill:#fce8e6,stroke:#c5221f,stroke-dasharray: 4 4,color:#c5221f;

    class StoreAssociate,MarketingTeam actor;
    class Attacker blocked;
    class PMA,MSA,CPA,PromoRun,MCPService agent;
    class BQ_Customer data;
    class BQ_Restricted blocked;
```

---

## 5. Comprehensive Audit Ledger & Rollback Matrix

The 12 changes recorded for M1 and M2 are listed below with timestamps, target resources, and, for 11 of them, an undo command. The M3 gateway attachment was made in the lab but is not in this log.

| # | Phase | Change Description | Target Resource | UTC Timestamp | Exact Reversal / Rollback Command |
| :-: | :--- | :--- | :--- | :---: | :--- |
| **1** | M1 | Registered shadow agent under Marketing ownership | `services/promo-agent` | `2026-09-25T21:00:52Z` | `gcloud agent-registry services delete promo-agent --location=us-central1 --quiet` |
| **2** | M1 | Created dedicated service account for Promo Agent | `promo-agent-sa` | `2026-09-25T21:12:22Z` | `gcloud iam service-accounts delete promo-agent-sa@qwiklabs-gcp-02-3408357845ee.iam.gserviceaccount.com --quiet` |
| **3** | M1 | Bound minimal boot logging & metric roles | `promo-agent-sa` | `2026-09-25T21:12:24Z` | `gcloud projects remove-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="serviceAccount:promo-agent-sa@..." --role="roles/logging.logWriter" && gcloud projects remove-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="serviceAccount:promo-agent-sa@..." --role="roles/monitoring.metricWriter"` |
| **4** | M1 | Re-pointed Cloud Run `promo-agent-shadow` to dedicated SA | Cloud Run `promo-agent-shadow` | `2026-09-25T21:13:15Z` | `gcloud run services update promo-agent-shadow --region=us-central1 --service-account="novasmart-customer-sa@..."` |
| **5** | M1 | Provisioned native SPIFFE Agent Identity for CPA | Reasoning Engine `3655712884878475264` | `2026-09-25T21:12:45Z` | *Irreversible platform SPIFFE badge generation (read-only attribution token)* |
| **6** | M1 | Bound BigQuery `roles/bigquery.jobUser` to CPA identity | Project IAM Policy | `2026-09-25T21:14:02Z` | `gcloud projects remove-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="principal://agents.global.org-.../3655712884878475264" --role="roles/bigquery.jobUser"` |
| **7** | M1 | Bound dataset-level `READER` to CPA on `customer_data` | BigQuery Dataset ACL | `2026-09-25T21:14:24Z` | `bq show --format=prettyjson customer_data \| jq '.access |= map(select(.iamMember != "principal://.../3655712884878475264"))' > /tmp/revert.json && bq update --source /tmp/revert.json customer_data` |
| **8** | M1 | Revoked project-wide `roles/bigquery.admin` from shared SA | Project IAM Policy | `2026-09-25T21:14:37Z` | `gcloud projects add-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="serviceAccount:novasmart-customer-sa@..." --role="roles/bigquery.admin"` |
| **9** | M2 | Locked back-office MSA Resource IAM to front-desk PMA | Reasoning Engine `7249585387520131072` | `2026-09-25T21:49:17Z` | `TOKEN=$(gcloud auth print-access-token) && ETAG=$(curl -sSf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' https://us-central1-aiplatform.googleapis.com/v1beta1/projects/qwiklabs-gcp-02-3408357845ee/locations/us-central1/reasoningEngines/7249585387520131072:getIamPolicy \| jq -er .etag) && curl -sS --fail-with-body -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"policy":{"version":1,"etag":"'$ETAG'","bindings":[{"role":"roles/aiplatform.user","members":["serviceAccount:test-agent-caller@qwiklabs-gcp-02-3408357845ee.iam.gserviceaccount.com"]}]}}' https://us-central1-aiplatform.googleapis.com/v1beta1/projects/qwiklabs-gcp-02-3408357845ee/locations/us-central1/reasoningEngines/7249585387520131072:setIamPolicy` |
| **10** | M2 | Revoked `allUsers` invoker on `novasmart-mcp` Cloud Run | Cloud Run `novasmart-mcp` | `2026-09-25T21:57:18Z` | `gcloud run services add-iam-policy-binding novasmart-mcp --region=us-central1 --member="allUsers" --role="roles/run.invoker"` |
| **11** | M2 | Bound CPA SPIFFE identity to `novasmart-mcp` invoker | Cloud Run `novasmart-mcp` | `2026-09-25T21:57:31Z` | `gcloud run services remove-iam-policy-binding novasmart-mcp --region=us-central1 --member="principal://.../3655712884878475264" --role="roles/run.invoker"` |
| **12** | M2 (cleanup) | Revoked residual `roles/aiplatform.user` from vacated SA | Project IAM Policy | `2026-09-25T21:59:23Z` | `gcloud projects add-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="serviceAccount:novasmart-customer-sa@..." --role="roles/aiplatform.user"` |

---

## 6. The Field Report

The report is at <https://nelmiux.github.io/build-with-gemini/>, and this document and the other guides are readable with rendered diagrams at <https://nelmiux.github.io/build-with-gemini/docs.html>. Locally, clone the repository and open `index.html` in a browser, or run `npm start` and visit `http://localhost:8080`.

The report includes:
1. **The scenario and its cast,** written for readers who were not at the workshop, with a glossary.
2. **An interactive architecture** with five stages, one per mission (M0, M1, M2, M3, M5), and a component inspector that shows each component's state, connections, and technical details.
3. **The checks recorded during the lab,** with their details.
4. **The change log** with timestamps and rollback commands.
5. **The assessment and recommendation,** and a presentation mode for a team meeting.
