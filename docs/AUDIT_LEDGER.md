# Master Audit Ledger & Rollback Matrix

Every modification made to the Google Cloud estate across all missions is recorded here with resource paths, UTC timestamps, and exact rollback commands.

---

| # | Phase | Change Description | Target Resource | Timestamp (UTC) | Rollback / Undo Command |
| :-: | :--- | :--- | :--- | :---: | :--- |
| **1** | M1 | Registered Shadow Agent in catalog under Marketing | `services/promo-agent` | `2026-09-25T21:00:52Z` | `gcloud agent-registry services delete promo-agent --location=us-central1 --quiet` |
| **2** | M1 | Created dedicated service account for Promo Agent | `promo-agent-sa` | `2026-09-25T21:12:22Z` | `gcloud iam service-accounts delete promo-agent-sa@qwiklabs-gcp-02-3408357845ee.iam.gserviceaccount.com --quiet` |
| **3** | M1 | Bound boot logging & metric roles to promo SA | `promo-agent-sa` | `2026-09-25T21:12:24Z` | `gcloud projects remove-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="serviceAccount:promo-agent-sa@..." --role="roles/logging.logWriter"` |
| **4** | M1 | Re-pointed Cloud Run `promo-agent-shadow` to dedicated SA | Cloud Run `promo-agent-shadow` | `2026-09-25T21:13:15Z` | `gcloud run services update promo-agent-shadow --region=us-central1 --service-account="novasmart-customer-sa@..."` |
| **5** | M1 | Provisioned native SPIFFE Agent Identity for CPA | Reasoning Engine `365571...` | `2026-09-25T21:12:45Z` | *(Platform SPIFFE badge generation is immutable)* |
| **6** | M1 | Bound `roles/bigquery.jobUser` to CPA Identity | Project IAM Policy | `2026-09-25T21:14:02Z` | `gcloud projects remove-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="principal://agents.global.org-.../365571..." --role="roles/bigquery.jobUser"` |
| **7** | M1 | Bound dataset-level `READER` to CPA on `customer_data` | BigQuery Dataset ACL | `2026-09-25T21:14:24Z` | `bq show --format=prettyjson customer_data \| jq '.access \|= map(select(.iamMember != "principal://.../365571..."))' > /tmp/rev.json && bq update --source /tmp/rev.json customer_data` |
| **8** | M1 | Revoked project-wide `roles/bigquery.admin` from shared SA | Project IAM Policy | `2026-09-25T21:14:37Z` | `gcloud projects add-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="serviceAccount:novasmart-customer-sa@..." --role="roles/bigquery.admin"` |
| **9** | M2 | Locked Back-Office MSA Resource IAM to Front-Desk PMA | Reasoning Engine `724958...` | `2026-09-25T21:49:17Z` | `curl -s -X POST -H "Authorization: Bearer $TOKEN" -d @/tmp/revert_msa_iam.json https://us-central1-aiplatform.googleapis.com/.../reasoningEngines/7249585387520131072:setIamPolicy` |
| **10** | M2 | Revoked `allUsers` invoker on `novasmart-mcp` Cloud Run | Cloud Run `novasmart-mcp` | `2026-09-25T21:57:18Z` | `gcloud run services add-iam-policy-binding novasmart-mcp --region=us-central1 --member="allUsers" --role="roles/run.invoker"` |
| **11** | M2 | Bound CPA SPIFFE Identity to `novasmart-mcp` invoker | Cloud Run `novasmart-mcp` | `2026-09-25T21:57:31Z` | `gcloud run services remove-iam-policy-binding novasmart-mcp --region=us-central1 --member="principal://.../365571..." --role="roles/run.invoker"` |
| **12** | IAM | Revoked residual `roles/aiplatform.user` from vacated SA | Project IAM Policy | `2026-09-25T21:59:23Z` | `gcloud projects add-iam-policy-binding qwiklabs-gcp-02-3408357845ee --member="serviceAccount:novasmart-customer-sa@..." --role="roles/aiplatform.user"` |
