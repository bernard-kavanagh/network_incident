-- ============================================================================
-- TELECOM NETWORK INCIDENT — UNIFIED TiDB SCHEMA
-- Single cluster = the "Cognitive Foundation" for Devoteam's ADK agents.
--
-- Collapses three Google dependencies into one substrate:
--   BigQuery (history)           -> tickets / incidents / network_alarms (+ TiFlash HTAP)
--   Vertex semantic-ranker       -> VECTOR(384) + HNSW + FULLTEXT hybrid rerank
--   Vertex Agent Engine Memory   -> agent_reasoning / incident_memory / runbook_memory
--
-- Ported from bernard-kavanagh/ev_charger_anomaly_detection (two-plane design,
-- anomaly_breakdown, supersession, FULLTEXT) and tidb_fraud_detection (HTAP,
-- semantic memory, custodial duties). VECTOR(384) = all-MiniLM-L6-v2 output.
--
-- Operator-ready: identical DDL on TiDB Cloud now and on in-cluster TiDB
-- (TiDB Operator) on GDC bare metal later. Idempotent (IF NOT EXISTS).
-- ============================================================================

-- ============================================================================
-- DATA PLANE: network elements, raw alarms, correlated incidents, catalog, tickets
-- ============================================================================

-- Inventory of network elements (← charger_registry)
CREATE TABLE IF NOT EXISTS network_elements (
  element_id       VARCHAR(48)  PRIMARY KEY,
  site_id          VARCHAR(64)  NOT NULL,
  element_type     ENUM('cell_site','enodeb','gnodeb','bts','router','switch',
                        'transport','core','firewall','dns','other') NOT NULL,
  vendor           VARCHAR(64),
  model            VARCHAR(64),
  sw_version       VARCHAR(32),
  region           VARCHAR(64),
  lat              DECIMAL(9,6),
  lon              DECIMAL(9,6),
  install_date     DATE,
  last_maintenance DATE,
  criticality      ENUM('gold','silver','bronze') DEFAULT 'silver',
  INDEX idx_site   (site_id),
  INDEX idx_type   (element_type, vendor, model),
  INDEX idx_region (region)
);

-- Raw alarm / event stream (← charger_telemetry)
CREATE TABLE IF NOT EXISTS network_alarms (
  id               BIGINT AUTO_RANDOM PRIMARY KEY,
  element_id       VARCHAR(48)  NOT NULL,
  site_id          VARCHAR(64),
  ts               TIMESTAMP(3) NOT NULL,
  severity         ENUM('critical','major','minor','warning','info','clear') NOT NULL,
  alarm_type       VARCHAR(64)  NOT NULL,
  probable_cause   VARCHAR(128),
  specific_problem VARCHAR(255),
  managed_object   VARCHAR(128),
  additional_text  TEXT,
  metrics          JSON          COMMENT 'KPI snapshot at alarm time, e.g. {"prb_util":0.97,"latency_ms":210}',
  correlated_incident VARCHAR(48) COMMENT 'incident_ref once the alarm is grouped',
  INDEX idx_element_ts (element_id, ts),
  INDEX idx_sev_ts     (severity, ts),
  INDEX idx_type_ts    (alarm_type, ts),
  INDEX idx_corr       (correlated_incident)
);

-- Correlated incidents with anomaly scoring at ingestion (← charger_windows)
CREATE TABLE IF NOT EXISTS incidents (
  id                BIGINT AUTO_RANDOM PRIMARY KEY,
  incident_ref      VARCHAR(48)  NOT NULL UNIQUE,
  element_id        VARCHAR(48),
  site_id           VARCHAR(64),
  region            VARCHAR(64),
  window_start      TIMESTAMP    NOT NULL,
  window_end        TIMESTAMP    NOT NULL,
  alarm_count       INT          DEFAULT 0,
  severity          ENUM('critical','major','minor','warning') NOT NULL,
  category          VARCHAR(64),
  subcategory       VARCHAR(64),
  title             VARCHAR(255),
  description       TEXT,
  anomaly_score     DECIMAL(4,3) DEFAULT 0.000,
  -- per-feature anomaly breakdown for explainability (← anomaly_breakdown)
  anomaly_breakdown JSON         COMMENT 'e.g. {"prb_overload":0.31,"sctp_flap":0.22}',
  status            ENUM('open','investigating','resolved','closed') DEFAULT 'open',
  signature_vec     VECTOR(384),
  created_at        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_element_win (element_id, window_start),
  INDEX idx_anomaly     (anomaly_score DESC, window_start),
  INDEX idx_status      (status, severity),
  INDEX idx_subcat      (subcategory, window_start),
  VECTOR INDEX idx_incident_vec ((VEC_COSINE_DISTANCE(signature_vec))),
  FULLTEXT INDEX ft_incident_desc (description)
);

-- Curated known-failure + runbook catalog (← outage_catalog)
CREATE TABLE IF NOT EXISTS incident_catalog (
  id               BIGINT AUTO_RANDOM PRIMARY KEY,
  pattern_id       VARCHAR(32)  NOT NULL UNIQUE,
  pattern_name     VARCHAR(128) NOT NULL,
  category         ENUM('radio','transport','core','power','environmental',
                        'congestion','config','security','hardware','unknown') NOT NULL,
  root_cause       TEXT         NOT NULL,
  symptoms         JSON         NOT NULL,
  resolution       TEXT         NOT NULL COMMENT 'remediation runbook text',
  severity         ENUM('critical','major','minor','warning') NOT NULL,
  affected_vendors JSON,
  affected_models  JSON,
  occurrence_count INT          DEFAULT 1,
  last_seen        TIMESTAMP    NULL,
  created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  signature_vec    VECTOR(384),
  INDEX idx_category (category, severity),
  VECTOR INDEX idx_catalog_vec ((VEC_COSINE_DISTANCE(signature_vec))),
  -- one FULLTEXT column per index (TiDB limitation)
  FULLTEXT INDEX ft_catalog_root_cause (root_cause),
  FULLTEXT INDEX ft_catalog_resolution (resolution)
);

-- Historical tickets — replaces Devoteam's BigQuery TABLE_ID.
-- The deviation source AND the rerank target. embedding replaces Vertex ranker.
CREATE TABLE IF NOT EXISTS tickets (
  ticket_id     VARCHAR(48)  PRIMARY KEY,
  created_at    TIMESTAMP    NOT NULL,
  resolved_at   TIMESTAMP    NULL,
  element_id    VARCHAR(48),
  site_id       VARCHAR(64),
  region        VARCHAR(64),
  category      VARCHAR(64),
  subcategory   VARCHAR(64),
  priority      ENUM('P1','P2','P3','P4') DEFAULT 'P3',
  status        ENUM('open','in_progress','resolved','closed') DEFAULT 'closed',
  summary       VARCHAR(512) NOT NULL,
  description   TEXT,
  resolution    TEXT,
  embedding     VECTOR(384),
  INDEX idx_subcat_created (subcategory, created_at),
  INDEX idx_region_created (region, created_at),
  INDEX idx_created        (created_at),
  VECTOR INDEX idx_ticket_vec ((VEC_COSINE_DISTANCE(embedding))),
  FULLTEXT INDEX ft_ticket_summary (summary),
  FULLTEXT INDEX ft_ticket_description (description)
);


-- ============================================================================
-- CONTEXT PLANE: shared three-tier agent memory (reused by ALL 50 agents)
-- ============================================================================

-- EPISODIC — outcomes-only investigation checkpoints (← agent_reasoning)
CREATE TABLE IF NOT EXISTS agent_reasoning (
  id            BIGINT AUTO_RANDOM PRIMARY KEY,
  incident_ref  VARCHAR(48),
  element_id    VARCHAR(48),
  site_id       VARCHAR(64),
  session_id    VARCHAR(64)  NOT NULL,
  created_at    TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3),
  observation   TEXT         NOT NULL,
  hypothesis    TEXT,
  evidence_refs JSON,
  confidence    DECIMAL(3,2) DEFAULT 0.50,
  resolution    ENUM('confirmed','dismissed','escalated','promoted') NOT NULL,
  resolved_at   TIMESTAMP    NULL,
  tags          JSON,
  superseded_by BIGINT       NULL COMMENT 'ID of newer reasoning that contradicts this one',
  superseded_at TIMESTAMP    NULL,
  reasoning_vec VECTOR(384),
  INDEX idx_incident_reasoning (incident_ref, created_at DESC),
  INDEX idx_element_reasoning  (element_id, created_at DESC),
  INDEX idx_session            (session_id, created_at),
  INDEX idx_resolution         (resolution, created_at DESC),
  VECTOR INDEX idx_reasoning_vec ((VEC_COSINE_DISTANCE(reasoning_vec))),
  FULLTEXT INDEX ft_reasoning_obs (observation)
);

-- SEMANTIC — learned network patterns (← fleet_memory / fraud_memory)
CREATE TABLE IF NOT EXISTS incident_memory (
  id              BIGINT AUTO_RANDOM PRIMARY KEY,
  created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  category        ENUM('pattern','preference','hardware_note','site_context',
                       'vendor_bug','seasonal','operational_rule') NOT NULL,
  scope           VARCHAR(128) NOT NULL DEFAULT 'global',
  content         TEXT         NOT NULL,
  source_refs     JSON,
  confidence      DECIMAL(3,2) DEFAULT 0.70,
  evidence_count  INT          DEFAULT 1,
  access_count    INT          DEFAULT 0,
  last_reinforced_at TIMESTAMP NULL,
  status          ENUM('active','deprecated','superseded') DEFAULT 'active',
  superseded_by   BIGINT       NULL,
  memory_vec      VECTOR(384),
  INDEX idx_scope_status (scope, status),
  INDEX idx_category     (category, status),
  VECTOR INDEX idx_memory_vec ((VEC_COSINE_DISTANCE(memory_vec))),
  FULLTEXT INDEX ft_memory_content (content)
);

-- PROCEDURAL — remediation runbooks the Remediation Agent recalls + improves
CREATE TABLE IF NOT EXISTS runbook_memory (
  id            BIGINT AUTO_RANDOM PRIMARY KEY,
  title         VARCHAR(255) NOT NULL,
  scope         VARCHAR(128) NOT NULL DEFAULT 'global',
  trigger_condition TEXT     NOT NULL COMMENT 'when this runbook applies',
  steps         JSON         NOT NULL COMMENT 'ordered remediation steps',
  success_count INT          DEFAULT 0,
  fail_count    INT          DEFAULT 0,
  confidence    DECIMAL(3,2) DEFAULT 0.60,
  status        ENUM('active','deprecated','superseded') DEFAULT 'active',
  superseded_by BIGINT       NULL,
  last_used     TIMESTAMP    NULL,
  created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  runbook_vec   VECTOR(384),
  INDEX idx_scope_status (scope, status),
  VECTOR INDEX idx_runbook_vec ((VEC_COSINE_DISTANCE(runbook_vec))),
  FULLTEXT INDEX ft_runbook_trigger (trigger_condition)
);

-- Session working state with token budgeting (← session_state)
CREATE TABLE IF NOT EXISTS session_state (
  session_id      VARCHAR(64)  PRIMARY KEY,
  user_id         VARCHAR(64),
  started_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  last_active     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  focus_elements  JSON,
  focus_site      VARCHAR(64),
  focus_incident  VARCHAR(48),
  investigation_summary TEXT,
  token_budget    INT          DEFAULT 4000,
  tokens_used     INT          DEFAULT 0,
  last_context_hash VARCHAR(64),
  INDEX idx_user   (user_id, last_active DESC),
  INDEX idx_active (last_active)
);

-- Cached, token-counted context fragments (← context_snapshots)
CREATE TABLE IF NOT EXISTS context_snapshots (
  id            BIGINT AUTO_RANDOM PRIMARY KEY,
  entity_type   ENUM('element','site','region','fleet_summary') NOT NULL,
  entity_id     VARCHAR(64)  NOT NULL,
  snapshot_type ENUM('profile','recent_incidents','maintenance_history',
                     'performance_baseline','active_investigations') NOT NULL,
  content       TEXT         NOT NULL,
  token_count   INT          NOT NULL DEFAULT 0,
  created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  expires_at    TIMESTAMP    NOT NULL,
  is_stale      BOOLEAN      DEFAULT FALSE,
  snapshot_vec  VECTOR(384),
  INDEX idx_entity  (entity_type, entity_id, snapshot_type),
  INDEX idx_expires (expires_at),
  UNIQUE INDEX idx_unique_snap (entity_id, snapshot_type)
);


-- ============================================================================
-- HTAP: TiFlash replicas for analytics tables (deviation/volume queries)
-- TiDB Cloud Starter provisions TiFlash automatically; on bare-metal TiDB
-- Operator, ensure a TiFlash component is defined in the TiDBCluster CR.
-- ============================================================================
ALTER TABLE network_alarms   SET TIFLASH REPLICA 1;
ALTER TABLE incidents        SET TIFLASH REPLICA 1;
ALTER TABLE tickets          SET TIFLASH REPLICA 1;
ALTER TABLE incident_memory  SET TIFLASH REPLICA 1;


-- ============================================================================
-- TTL POLICIES (uncomment for production — keep off during demos)
-- ============================================================================
-- ALTER TABLE network_alarms TTL = `ts` + INTERVAL 14 DAY TTL_JOB_INTERVAL = '1h';
-- ALTER TABLE incidents TTL = `window_start` + INTERVAL 90 DAY TTL_JOB_INTERVAL = '6h';
-- ALTER TABLE session_state TTL = `last_active` + INTERVAL 1 DAY TTL_JOB_INTERVAL = '1h';
-- ALTER TABLE context_snapshots TTL = `expires_at` + INTERVAL 0 DAY TTL_JOB_INTERVAL = '30m';
