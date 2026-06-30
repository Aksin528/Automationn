-- SOC Custom Tables — Init Script
-- Run once on a new system, or mount to PostgreSQL docker-entrypoint-initdb.d/
-- Safe to run multiple times (IF NOT EXISTS)

CREATE TABLE IF NOT EXISTS soc_cases (
    case_id                  TEXT PRIMARY KEY,
    title                    TEXT,
    verdict                  TEXT,
    confidence               INT,
    severity                 TEXT,
    status                   TEXT DEFAULT 'new',
    assignee                 TEXT,
    mitre_techniques         TEXT[],
    affected_assets          TEXT[],
    source                   TEXT DEFAULT 'splunk',
    workspace_id             TEXT,
    analyzed_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    resolution_time_minutes  INT
);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id                  SERIAL PRIMARY KEY,
    decision_id         TEXT UNIQUE DEFAULT gen_random_uuid()::TEXT,
    case_id             TEXT,
    agent               TEXT DEFAULT 'responder-agent',
    verdict             TEXT,
    confidence          INT,
    reasoning           TEXT,
    recommended_action  TEXT,
    human_decision      TEXT,
    feedback_comment    TEXT,
    time_saved_minutes  INT,
    analyzed_at         TIMESTAMPTZ DEFAULT NOW(),
    feedback_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ioc_history (
    id          SERIAL PRIMARY KEY,
    ioc_value   TEXT,
    ioc_type    TEXT,
    verdict     TEXT,
    confidence  INT,
    source      TEXT,
    first_seen  TIMESTAMPTZ DEFAULT NOW(),
    last_seen   TIMESTAMPTZ DEFAULT NOW(),
    seen_count  INT DEFAULT 1,
    tags        TEXT[],
    raw_data    JSONB
);

CREATE TABLE IF NOT EXISTS case_tasks (
    id           SERIAL PRIMARY KEY,
    case_id      TEXT,
    task_name    TEXT,
    assigned_to  TEXT,
    status       TEXT,
    priority     TEXT,
    due_date     TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS case_evidence (
    id               SERIAL PRIMARY KEY,
    case_id          TEXT,
    evidence_type    TEXT,
    file_name        TEXT,
    file_hash        TEXT,
    file_size_bytes  INT,
    uploaded_by      TEXT,
    upload_time      TIMESTAMPTZ DEFAULT NOW(),
    description      TEXT,
    storage_path     TEXT
);

CREATE TABLE IF NOT EXISTS case_sla (
    id                   SERIAL PRIMARY KEY,
    case_id              TEXT,
    sla_tier             TEXT,
    response_deadline    TIMESTAMPTZ,
    resolution_deadline  TIMESTAMPTZ,
    breached             BOOLEAN DEFAULT FALSE,
    breach_notified      BOOLEAN DEFAULT FALSE,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS case_metrics (
    id                    SERIAL PRIMARY KEY,
    case_id               TEXT,
    time_to_detect_min    INT,
    time_to_respond_min   INT,
    time_to_resolve_min   INT,
    analyst_id            TEXT,
    created_at            TIMESTAMPTZ DEFAULT NOW()
);
