# NovaSmart AI Governance & Security Architecture Blueprint

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Google Cloud](https://img.shields.io/badge/Google%20Cloud-Vertex%20AI%20%7C%20Model%20Armor-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com)
[![Status](https://img.shields.io/badge/Security%20Posture-Hardened%20(94%25)-success)]()

Comprehensive architecture documentation, interactive visualization app, and verified governance controls developed during the **Google Cloud Build with Gemini Platform Track (M0, M1, M2, M3, M4, M5)**.

---

## 🌟 Executive Overview & Purpose

This repository provides an enterprise blueprint for securing **autonomous multi-agent AI systems** deployed on Google Cloud. 

Using the real-world **NovaSmart** retail enterprise estate as a baseline, this project documents the progressive hardening of Vertex AI Reasoning Engines, Cloud Run microservices, BigQuery data stores, and Model Armor content firewalls from an initial vulnerable state (M0) to an enterprise-grade Zero-Trust architecture.

### What is Included:
- **Interactive Web Application (`index.html`)**: Interactive architecture explorer, stage stepper, dynamic SVG network routing, before/after diffs, live attack simulator, and executive presentation deck.
- **Deep Technical Documentation (`docs/`)**: Step-by-step guides for M0 (Discovery), M1 (Identity/Data), M2 (Inter-Agent IAM), M3 (Model Armor), M4 (Tool Leaks), and M5 (Evaluation).
- **Executive Summary (`EXECUTIVE_SUMMARY.md`)**: Ready-to-present briefing for engineering teams, CISOs, and enterprise architects with business ROI and recommendations.
- **Master Audit Ledger (`docs/AUDIT_LEDGER.md`)**: Verifiable 12-item record of applied changes and rollback CLI commands.

---

## 🏗️ Architecture Evolution (M0 → Target State)

```mermaid
flowchart TD
    subgraph Ingress [1. Governed Ingress Tier]
        StoreAssociate[Store Associate / Customer Portal]
        MarketingOps[Marketing Operations]
        RogueCaller[Rogue Callers / Attackers]
    end

    subgraph Gateway [2. Content Screening Firewall]
        ModelArmorGW["Agent Gateway (us-central1)<br/>[Inline Model Armor Sanitization on :streamQuery]"]
    end

    subgraph Catalog [3. Agent Registry & Governance]
        Registry["Agent Registry (Catalog)<br/>- services/promo-agent (Marketing Owned)<br/>- Front Desk & Back Office Engines"]
    end

    subgraph AgentRuntime [4. Vertex AI Managed Agent Runtime]
        PMA["Price Match Agent (Front Desk)<br/>Reasoning Engine: 2824...<br/>Identity: PMA SPIFFE Principal"]
        MSA["Markdown Strategy Agent (Back Office)<br/>Reasoning Engine: 7249...<br/>Resource IAM: Exclusively PMA SPIFFE"]
        CPA["Customer Personalization Agent<br/>Reasoning Engine: 3655...<br/>Identity: CPA SPIFFE Principal"]
    end

    subgraph CloudRun [5. Governed Microservices]
        PromoRun["promo-agent-shadow (Cloud Run)<br/>Identity: promo-agent-sa (Isolated)<br/>Permissions: Logging/Metrics Only"]
        MCPRun["novasmart-mcp (Tool Container)<br/>IAM: roles/run.invoker -> CPA SPIFFE Only"]
    end

    subgraph DataTier [6. Data Layer (BigQuery)]
        BQ_Customer["BigQuery Dataset: customer_data<br/>Table: customers (20 rows)<br/>ACL: READER -> CPA SPIFFE Only"]
        BQ_Protected["All Other BigQuery Datasets<br/>(Zero Admin Rights / Completely Isolated)"]
    end

    StoreAssociate ==>|Chat Ingress| ModelArmorGW
    ModelArmorGW ==>|Sanitized Ingress| PMA
    MarketingOps -->|Manage Campaign| PromoRun
    
    RogueCaller -.->|HTTP 403 PERMISSION_DENIED| MSA
    RogueCaller -.->|HTTP 500 Model Armor Blocked| ModelArmorGW
    RogueCaller -.->|HTTP 403 Forbidden| MCPRun

    PMA ==>|A2A Escalation > 10% (HTTP 200 OK)| MSA
    CPA ==>|Authenticated Tool Call| MCPRun
    MCPRun ==>|Scoped SELECT| BQ_Customer
```

---

## 🚀 Running the Interactive Application

The interactive architecture application is completely self-contained with **zero external runtime dependencies**.

### Option A: Direct Browser Access
Simply open `index.html` directly in any modern browser (Chrome, Edge, Firefox, Safari).

### Option B: Node.js Web Server
```bash
# Clone the repository
git clone https://github.com/nelmiux/build-with-gemini.git
cd build-with-gemini

# Start the built-in HTTP server
npm start
# ➜ Running at http://localhost:8080
```

### Option C: Python 3 Web Server
```bash
python3 -m http.server 8080
# ➜ Running at http://localhost:8080
```

---

## 📚 Documentation Directory

| Document | Focus Area | Key Concepts |
|---|---|---|
| [**Executive Summary**](EXECUTIVE_SUMMARY.md) | Leadership & Team Presentation | Business ROI, 5-layer model, team verdict, adoption roadmap. |
| [**M0: Discovery**](docs/M0_DISCOVERY.md) | Baseline & Situational Awareness | Shadow IT detection, shared identity risks, project admin exposure. |
| [**M1: Identity & Data**](docs/M1_IDENTITY_DATA.md) | Identity Decoupling & Least Privilege | Agent Registry, dedicated service accounts, SPIFFE badges, BigQuery ACLs. |
| [**M2: Inter-Agent IAM**](docs/M2_INTER_AGENT_IAM.md) | Agent Perimeters & Tool Security | Vertex AI Reasoning Engine Resource IAM, 403 refusal, Cloud Run lockdown. |
| [**M3: Content Screening**](docs/M3_CONTENT_SCREENING.md) | Model Armor & Agent Gateway | Prompt injection defense, backdoor override mitigation, floor fallacy. |
| [**M4: Semantic Governance**](docs/M4_SEMANTIC_GOVERNANCE.md) | Tool Leaks & Exfiltration | Parameterized validation, bulk dump prevention, SQL inspection. |
| [**M5: Evaluation**](docs/M5_EVALUATION_DECISION.md) | Quality Flywheel & Certification | Gen AI Evaluation service, LLM judge, scorecard, go-live decision. |
| [**Master Audit Ledger**](docs/AUDIT_LEDGER.md) | Rollback Matrix & Audit Log | 12 atomic changes with timestamps, resources, and undo commands. |

---

## 🛠️ Verification & Simulation Scripts

In the `scripts/` directory:
- `scripts/verify_estate.sh`: Audits live Google Cloud reasoning engines, Cloud Run services, BigQuery ACLs, and Agent Registry entries.
- `scripts/simulate_attacks.sh`: Demonstrates the verified HTTP 403 refusal on rogue callers and HTTP 500 Model Armor prompt blocks.

---

## 👤 Author & Acknowledgments

- **Author**: nelmiux ([GitHub](https://github.com/nelmiux))
- **Event**: Google Cloud *Build with Gemini* Platform Track
- **Lab**: NovaSmart Enterprise AI Agent Governance Lab (Track 2)
