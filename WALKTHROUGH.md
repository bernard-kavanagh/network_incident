# POC Walkthrough — Network Incident Agents on a TiDB Cognitive Foundation

A demo script + handoff guide. Follow it top-to-bottom to stand the POC up on a
fresh TiDB cluster and run the three-agent story. Every step has a **"say this"**
talking point tying the action back to *why one TiDB cluster replaces three Google
services*.

> Numbers below are from a real run on TiDB Cloud Starter (v8.5.3 serverless, AWS
> `eu-central-1`). Yours will vary slightly (alarm timing is randomised).

---

## The one-sentence pitch

> Devoteam's agent leans on **BigQuery + Vertex semantic-ranker + Vertex Agent
> Engine Memory Bank**. This POC keeps the ADK + Gemini agent unchanged and
> collapses those three services into **one TiDB cluster** — which is also what
> makes it portable to a sovereign / telco-edge deployment.

| Devoteam today | Replaced by (one TiDB cluster) |
|---|---|
| BigQuery (history / volumes) | `tickets`, `incidents`, `network_alarms` + **TiFlash HTAP** |
| Vertex `semantic-ranker-default@latest` | `VECTOR(384)` + HNSW + FULLTEXT hybrid rerank, one SQL query |
| Vertex Agent Engine Memory Bank | 3-tier memory: `agent_reasoning` / `incident_memory` / `runbook_memory` |

Reasoning stays on **Gemini 2.5 Flash** (Google's remit). Embeddings are
**local all-MiniLM-L6-v2** (384-dim) — no external API, runs air-gapped.

---

## 0. Prerequisites

- A TiDB cluster (TiDB Cloud Starter is fine) with a database named
  `network_incident`.
- **`python3.12`** — ⚠️ not `python3`. On the demo machine `python3` is 3.14 and
  is missing `sentence-transformers`; `python3.12` has all deps. Verify:
  ```bash
  python3.12 -c "import pymysql,dotenv,sentence_transformers; print('deps OK')"
  ```
- `.env` filled in (copy from `.env.example`). TiDB Cloud Starter requires the
  **prefixed** username: `TIDB_USER=<prefix>.root`. TLS is on by default
  (`TIDB_SSL_CA=/etc/ssl/cert.pem` on macOS).
- *(ADK agent only)* `pip install google-adk` + `gcloud auth application-default login`.

Connectivity check:
```bash
python3.12 -c "from lib.tidb import query; print(query('SELECT VERSION() v, DATABASE() db')[0])"
```

---

## 1. One-time setup

```bash
python3.12 apply_schema.py                       # 10 tables + 4 TiFlash replicas (idempotent)
python3.12 seed/seed_network_elements.py         # 200 elements
python3.12 seed/seed_incident_catalog.py         # catalog + semantic memory + runbooks
python3.12 seed/seed_tickets.py                  # ~6,300 tickets, with a recent RAN-PRB spike
python3.12 seed/stream_alarms.py --burst-element NE-00007   # raw alarms + a correlated burst
python3.12 embedding/embedding_service.py --once # fill ALL vectors locally (first run ~30–60s)
```

> ⚠️ **Ordering:** run `stream_alarms` and the triage step (below) *before* the
> final embedding pass if you want incident vectors populated — triage creates
> the `incidents` rows. Simplest: run `embedding_service --once` again after
> triage. **Say this:** "embeddings are generated on our own hardware — nothing
> leaves the cluster's trust boundary."

---

## 2. The demo — three agents, one substrate

All three import the **same** `lib/` foundation + `adapters/network_incident`.
Swapping the adapter is how the other 49 agents get built. **Say this:** "the
database is the shared brain; each agent is a thin adapter over it."

### Pillar 1 — Triage & Correlation (anomaly at ingestion + HTAP)

```bash
python3.12 agents/triage.py
```
Expected:
```
🚨 INC-NE-00007-…  critical RAN-PRB-Congestion   anom=0.882  (19 alarms)
🚨 INC-NE-00026-…  major    Transport-SCTP-Flap   anom=0.729  (9 alarms)
…
✅ Triage created/updated 12 incidents.
```
**Say this:** "Raw alarms are correlated into incidents and scored *at ingestion*
— the `anomaly_breakdown` JSON explains *why* (per-KPI), so it's auditable, not a
black box. No stream processor, no Kafka — just SQL + a scoring function."

### Pillar 2 — Deviation detection (TiFlash HTAP, replaces BigQuery)

```bash
python3.12 - <<'PY'
from lib.tidb import query
def dev(sub, r=3, b=60):
    row = query("""SELECT /*+ read_from_storage(tiflash[tickets]) */
      rc.t/%s ra,(SELECT AVG(d) FROM (SELECT DATE(created_at) x,COUNT(*) d FROM tickets
        WHERE subcategory=%s AND created_at<NOW()-INTERVAL %s DAY
          AND created_at>=NOW()-INTERVAL %s DAY GROUP BY DATE(created_at)) z) m,
      (SELECT STDDEV_SAMP(d) FROM (SELECT DATE(created_at) x,COUNT(*) d FROM tickets
        WHERE subcategory=%s AND created_at<NOW()-INTERVAL %s DAY
          AND created_at>=NOW()-INTERVAL %s DAY GROUP BY DATE(created_at)) z) s
      FROM (SELECT COUNT(*) t FROM tickets WHERE subcategory=%s
        AND created_at>=NOW()-INTERVAL %s DAY) rc""",
      (r,sub,r,b+r,sub,r,b+r,sub,r))[0]
    ra,m,s=float(row['ra']),float(row['m'] or 0),float(row['s'] or 0)
    z=round((ra-m)/s,2) if s else None
    print(f"  {sub:22} recent/day={ra:5.1f} baseline={m:4.1f}±{s:3.1f} z={z}")
for s in ["RAN-PRB-Congestion","Core-DNS-Failure","Transport-SCTP-Flap"]: dev(s)
PY
```
Expected: **RAN-PRB-Congestion z≈19.8 (SEVERE)**, controls ≈0.
**Say this:** "This analytic query runs on TiFlash columnar replicas *while alarms
are still writing to TiKV* — HTAP, no ETL, no data warehouse. In Devoteam's design
this is a BigQuery scan; here it's the same cluster the OLTP writes land in."

### Pillar 3 — Remediation (3-tier memory + routing + write-back)

```bash
python3.12 agents/remediation.py --latest
# or target the PRB incident explicitly:
python3.12 agents/remediation.py --incident INC-NE-00007-<stamp>
```
On the PRB incident you'll see:
- `context_budget: 198/3600` — context assembled from memory tiers under a hard budget
- `route: SHORTCUT` — top semantic match cleared the gates (sim ≈0.60 ≥ 0.55, conf 0.92 ≥ 0.85)
- `chosen_runbook: "RAN congestion mitigation for PRB overload"` (correct)
- `write_back_actions: [recorded runbook success, incident_memory pattern stored]`

On an incident with **no** matching runbook (e.g. Environmental-HighTemp) it
**escalates** instead of applying a wrong one (`top_runbook_similarity 0.31 < floor 0.45`).

**Say this:** "The substrate assembles context and *decides which path to take*
before the model runs — a confident memory match takes the cheap shortcut, a novel
one explores. Confirmed outcomes are written back as new memory under a write-control
gate, so the system compounds knowledge. This is what replaces Vertex Agent Engine
Memory Bank — and it's plain SQL you can audit."

### Pillar 3b — Investigation agent (ADK + Gemini) *(needs google-adk + gcloud)*

```bash
adk web            # or: adk run network_incident_agent
```
Root → Filter (AllSubcategories → Subcategory) → Deviation → RerankTickets. The
`fetch_all_tickets_and_rerank` tool does hybrid **vector + FULLTEXT** ranking in one
SQL query — **this is the direct replacement for the Vertex semantic-ranker.**

---

## 3. Verify anytime

```bash
python3.12 - <<'PY'
from lib.tidb import query
for t in ["network_elements","network_alarms","incidents","tickets","incident_memory","runbook_memory","agent_reasoning"]:
    print(f"  {t:18}", query(f"SELECT COUNT(*) c FROM {t}")[0]["c"])
print("  tickets w/ vectors:", query("SELECT COUNT(*) c FROM tickets WHERE embedding IS NOT NULL")[0]["c"])
PY
```
Multi-agent reuse (all share one substrate):
```bash
grep -rl "from lib" agents network_incident_agent
```

---

## 4. Architecture recap

```
        ADK Root Agent (Gemini 2.5 Flash)
   ┌──────────┼───────────────┐
 Triage   Investigation   Remediation
   └──────────┼───────────────┘
   shared lib/ (tidb · embeddings · memory) + adapter
              │
        ONE TiDB CLUSTER
  data:   network_elements · network_alarms · incidents · incident_catalog · tickets
  memory: agent_reasoning (episodic) · incident_memory (semantic) · runbook_memory (procedural)
  TiKV (OLTP) + TiFlash (OLAP) · VECTOR(384)+HNSW · FULLTEXT
```

---

## 5. Known gaps (deliberate — POC scope)

- **ADK path unverified locally** — needs `google-adk` + `gcloud auth`. Triage /
  Remediation / Deviation run without any GCP setup.
- **Runbook coverage** — only 4 runbooks seeded (PRB, sleeping-cell, SCTP, power-loss).
  Subcategories without one correctly **escalate** (relevance floor) rather than
  mis-apply. Add runbooks to grow coverage.
- **Incident vectors** — populate only after triage has created the incidents (see the
  ordering note in §1).
- **Synthetic data** — ticket schema + taxonomy are our best-guess; swap for Devoteam's
  real ticket schema when available.

## 6. Deploy

Standard GKE now (`k8s/`, MetalLB-ready Service); GDC bare-metal lift via
`deploy/tidbcluster.yaml` (TiDB Operator + CSI StorageClass) with no app change.
See [README](README.md#deploy).
