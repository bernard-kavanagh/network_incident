# Network Incident Investigation Agents — on a TiDB Cognitive Foundation

A POC for **Devoteam × Google Cloud** (the 50-agent telecom incident program).
It takes Devoteam's [network-incident-investigation-agent](https://github.com/devoteamgcloud/network-incident-investigation-agent)
(Google **ADK** + **Gemini 2.5 Flash**) and **swaps its backend to a single TiDB
cluster** — the "cognitive foundation" pattern from
[`ev_charger_anomaly_detection`](https://github.com/bernard-kavanagh/ev_charger_anomaly_detection)
and [`tidb_fraud_detection`](https://github.com/bernard-kavanagh/tidb_fraud_detection).

## Why

Devoteam's agent depends on **three** Google services. TiDB collapses them into one:

| Devoteam today | Replaced by (one TiDB cluster) |
|---|---|
| **BigQuery** (historical tickets / volumes) | `tickets`, `incidents`, `network_alarms` + **TiFlash HTAP** |
| **Vertex AI `semantic-ranker-default@latest`** | `VECTOR(384)` + **HNSW** + **FULLTEXT** hybrid rerank, one SQL query |
| **Vertex AI Agent Engine Memory Bank** | three-tier memory: `agent_reasoning` / `incident_memory` / `runbook_memory` |

Reasoning **stays on Gemini/Vertex** (Google's remit). One substrate is also what
makes the stack portable to a **sovereign / telco-edge** deployment on **GDC bare
metal** — identical code, only the connection endpoint changes.

## Three agents, one substrate (the 50-agent blueprint)

All three import the **same** `lib/` foundation + `adapters/network_incident` — swap
the adapter to build any of the other 49 agents.

1. **Triage & Correlation** — [`agents/triage.py`](agents/triage.py). Correlates raw
   alarms into incidents, **scores anomalies at ingestion** (HTAP + episodic memory).
2. **Investigation** (ADK, ported from Devoteam) — [`network_incident_agent/`](network_incident_agent).
   Root → Filter (AllSubcategories → Subcategory) → Deviation → RerankTickets, all
   against TiDB (vector + full-text + HTAP + semantic memory).
3. **Remediation** — [`agents/remediation.py`](agents/remediation.py). Recalls runbooks +
   similar patterns, routes (warm/cold), proposes a fix, **writes the outcome back**
   (procedural memory + the five custodial duties).

```
        ADK Root Agent (Gemini 2.5 Flash)
   ┌──────────┼───────────────┐
 Triage   Investigation   Remediation
   └──────────┼───────────────┘
   shared lib/ (tidb · embeddings · memory) + adapter
              │
        ONE TiDB CLUSTER
   data plane:  network_elements · network_alarms · incidents · incident_catalog · tickets
   context plane (3-tier memory):  agent_reasoning · incident_memory · runbook_memory
   TiKV (OLTP) + TiFlash (OLAP) · VECTOR(384)+HNSW · FULLTEXT
   embeddings: all-MiniLM-L6-v2 (local, 384-dim — runs air-gapped)
```

## Quickstart (TiDB Cloud Starter)

```bash
cp .env.example .env          # fill in TIDB_* and GOOGLE_CLOUD_PROJECT
pip install -r requirements.txt
gcloud auth application-default login   # for the ADK/Gemini agent

# 1. Schema (idempotent)
python apply_schema.py

# 2. Seed the substrate
python seed/seed_network_elements.py --count 200
python seed/seed_incident_catalog.py            # catalog + semantic memory + runbooks
python seed/seed_tickets.py                     # historical tickets (+ a recent spike)
python seed/stream_alarms.py --burst-element NE-00007

# 3. Embed everything (fills NULL vectors locally)
python embedding/embedding_service.py --once

# 4. Run the agents
python agents/triage.py                         # alarms -> incidents (anomaly scored)
python agents/remediation.py --latest           # recall + remediate + write-back
poetry run adk web                              # or: adk run network_incident_agent
```

## Verification

| Claim | How to check |
|---|---|
| Vectors populated | `SELECT COUNT(*) FROM tickets WHERE embedding IS NOT NULL;` |
| **Vector rerank replaces Vertex ranker** | Investigation agent → `fetch_all_tickets_and_rerank`; returns `RERANK_RETRIEVE_TOP_N` tickets with hybrid scores, no Vertex ranker call |
| **HTAP deviation** | Run `stream_alarms.py` (TiKV writes) while DeviationAgent runs `get_ticket_counts_for_deviation` (TiFlash) → spiked subcategory returns z≥2 |
| **Anomaly at ingestion** | `triage.py` → `incidents.anomaly_breakdown` JSON explains the score |
| **Cold vs warm** | Run `remediation.py` before vs after memory is populated — context tiers + route path change |
| **Multi-agent reuse** | `grep -rl "from lib" agents network_incident_agent` — all share one substrate |

## Deploy

**Standard GKE now** (bare-metal-portable):
```bash
docker build -t REGION-docker.pkg.dev/PROJECT/REPO/network-incident-agent:v1 .
docker push REGION-docker.pkg.dev/PROJECT/REPO/network-incident-agent:v1
# edit k8s/deployment.yaml image; create the secret (see k8s/secret.example.yaml)
kubectl apply -f k8s/
```

**GDC bare-metal lift** (sovereign/edge): the `LoadBalancer` Service is MetalLB-ready;
reach Gemini via **Private Service Connect**; flip TiDB Cloud → in-cluster TiDB with
[`deploy/tidbcluster.yaml`](deploy/tidbcluster.yaml) (TiDB Operator + a CSI StorageClass).
App code, schema, and `.env` contract are unchanged.

## Layout

```
schema.sql                     unified two-plane TiDB schema
lib/                           cognitive foundation (tidb, embeddings, text_bander, memory)
adapters/network_incident/     telecom domain seam (anomaly scoring, tiers, seed catalog)
network_incident_agent/        ADK Investigation agent (root + sub_agents, TiDB tools)
agents/                        triage.py, remediation.py
seed/ · embedding/             data generators + embedding backfill
k8s/ · deploy/ · Dockerfile    GKE now, GDC-bare-metal-ready
```

> `_reference/` (gitignored) holds the three source repos this POC ports from.
