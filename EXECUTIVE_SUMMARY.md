# Executive Briefing & Work Team Recommendation

> [!NOTE]
> **Earlier draft, kept for reference.** The current assessment, recommendation and presentation are in the report at [nelmiux.github.io/build-with-gemini](https://nelmiux.github.io/build-with-gemini/). The report rates the workshop 9 out of 10, not the 10 / 10 below, and offers the five controls as a recommendation, not a mandatory baseline. Mission M4 was not available at the workshop: the M4 section below describes my own note on tool-call inspection, not work performed in the lab.

## Purpose of this Document
This document is designed for the user to present directly to their engineering team, architecture board, and CISO / Security leadership at work. It synthesizes the technical, architectural, and operational outcomes from the **Google Cloud "Build with Gemini" Agent Platform Track (M0–M5)**.

---

## 1. Executive Summary & The Problem
As enterprises transition from simple chatbots to **autonomous multi-agent systems**, traditional cloud security architectures fail:
1. **Agents are given tools and credentials**: An agent can query internal customer databases, execute SQL, or call backend microservices.
2. **Natural language is the control plane**: An attacker does not need an exploit payload; they use prompt injection to trick the model into misusing its authorized tools (the classic **Confused Deputy** problem).
3. **Shared identities mask attacks**: When multiple agents share a generic service account, audit logs cannot determine which model, user prompt, or container initiated an action.

---

## 2. What We Accomplished Across the Track (M0 to M5)

### • M0: Discovery & Situational Awareness
- Audited the unhardened NovaSmart retail estate in Google Cloud (`us-central1`).
- Identified a shadow marketing agent (`promo-agent-shadow`), an overprivileged service account with project-wide `roles/bigquery.admin`, a publicly exposed MCP tool container (`allUsers`), and an orphaned test account authorized on the back-office pricing engine.

### • M1: Identity Decoupling & Least Privilege
- Catalogued the shadow service in **Agent Registry** under Marketing ownership.
- Decoupled identities: Created dedicated `promo-agent-sa` (zero database rights) and provisioned native **SPIFFE Agent Identities** (`principal://agents.global.org...`) for Reasoning Engines.
- Stripped `roles/bigquery.admin` and scoped BigQuery access to dataset-level `READER` ACLs directly on `customer_data`.

### • M2: Inter-Agent Perimeter Lockdown & Tool Sealing
- Locked the **Markdown Strategy Agent** resource IAM policy via the REST API (`:setIamPolicy` with etags), restricting callers strictly to the front-desk Price Match Agent SPIFFE identity.
- Evicted orphaned rogue caller `test-agent-caller` and verified live **HTTP 403 PERMISSION_DENIED** rejection.
- Revoked public `allUsers` invocation on `novasmart-mcp` Cloud Run; locked invoker rights to the personalization agent.

### • M3: Content Screening & Gateway-Attached Model Armor
- Discovered prompt backdoor (`NVST-PRICING-7741`) in the Price Match Agent instruction allowing 90% unauthorized markdowns.
- Deployed **Agent Gateway** with inline **Model Armor** screening on `:streamQuery`, verifying automated refusal of backdoor injection attacks (`HTTP 500: Model Armor: Prompt violates content security configurations`).
- Proved why project-wide floorsettings fail (false positives on assembled calls) vs Gateway inline screening.

### • M4: Semantic Tool Governance & Data Leak Defense
- Addressed the authorized data exfiltration vector: prevented agents from using valid read permissions to execute bulk table dumps (`SELECT * FROM customers`).
- Implemented parameter validation requiring explicit equality constraints on `customer_id` and result-set caps.

### • M5: Evaluation Flywheel & Go/No-Go Launch Certification
- Evaluated the hardened Price Match Agent using the **Gen AI Evaluation Service** against standardized test scenarios.
- Distinguished model reasoning near-misses from security screen refusals.
- Certified a **100% Pass Rate** across baseline policy scenarios and issued a formal **GO** launch decision.

---

## 3. How Useful Was This Lab? (Value Assessment)

### Score: 10 / 10 — Exceptional Enterprise Value
Unlike conventional training that focuses on basic prompt engineering or API tutorials, this track addressed the **hard, messy realities of enterprise cloud infrastructure**:
- **Real GCP IAM & Security Primitives**: Working with actual SPIFFE identities, dataset ACLs, REST API etags, and Cloud Run IAM bindings.
- **Hands-On Failure Modes**: Discovering that project-wide Model Armor floorsettings break legitimate traffic, and learning the exact architectural pattern (Agent Gateway attachment) that works.
- **Audit-Grade Verification**: Insisting that every change be proven through live system logs, status codes, and reversal commands.

---

## 4. Professional Recommendation to My Team

### Recommendation: STRONGLY ADOPT (Grade: A+)
I strongly recommend that our enterprise AI platform team adopt this exact **5-Layer Defense-in-Depth Model** as a mandatory baseline before deploying any generative AI agents into staging or production:

1. **Mandatory Agent Cataloging**: No AI agent or container may run without registration in an Agent Registry with designated business ownership.
2. **Decoupled Cryptographic Identity**: Ban shared service accounts. Every agent must execute under a unique SPIFFE identity badge.
3. **Resource-Level Caller Allow-Lists**: Sensitive back-office agents must enforce resource IAM policies, refusing unlisted callers with HTTP 403.
4. **Gateway-Attached Model Armor**: Ingress channels must screen user prompts inline before they reach the model context.
5. **Continuous Evaluation Flywheel**: Establish automated test suites scored by LLM judges against written policy to catch regressions and prompt drift before release.
