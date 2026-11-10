-- Optional AI drafting. Nothing here is needed for the offline workflow; the tables stay empty unless a
-- person turns AI on and starts a draft. An AI run belongs to one project and one source document for
-- its whole life, whatever the interface is showing when it finishes.

CREATE TABLE app_setting (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE ai_run (
  id               TEXT PRIMARY KEY,
  project_id       TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id      TEXT NOT NULL,
  provider         TEXT NOT NULL CHECK (provider IN ('codex_cli', 'fake')),
  requested_model  TEXT NOT NULL DEFAULT '',
  reported_model   TEXT NOT NULL DEFAULT '',
  adapter_version  TEXT NOT NULL DEFAULT '',
  request_format   INTEGER NOT NULL,
  prompt_version   TEXT NOT NULL,
  schema_version   TEXT NOT NULL,
  request_id       TEXT NOT NULL UNIQUE,
  parent_run_id    TEXT REFERENCES ai_run(id) ON DELETE SET NULL,
  state            TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  error_code       TEXT NOT NULL DEFAULT '',
  error_message    TEXT NOT NULL DEFAULT '',
  selection_json   TEXT NOT NULL,
  input_hash       TEXT NOT NULL,
  input_json       TEXT NOT NULL,
  alias_json       TEXT NOT NULL,
  output_text      TEXT NOT NULL DEFAULT '',
  issues_json      TEXT NOT NULL DEFAULT '[]',
  questions_json   TEXT NOT NULL DEFAULT '[]',
  created_at       TEXT NOT NULL,
  started_at       TEXT,
  finished_at      TEXT,
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE
);
CREATE INDEX ai_run_document ON ai_run(document_id, created_at);

-- One row per task or subtask the AI proposed, in the validated form. 'imported' means it was turned into candidates.
CREATE TABLE ai_draft (
  id                    TEXT PRIMARY KEY,
  run_id                TEXT NOT NULL REFERENCES ai_run(id) ON DELETE CASCADE,
  draft_id              TEXT NOT NULL,
  parent_draft_id       TEXT,
  position              INTEGER NOT NULL DEFAULT 0,
  title                 TEXT NOT NULL,
  description           TEXT NOT NULL DEFAULT '',
  payload_json          TEXT NOT NULL,
  issues_json           TEXT NOT NULL DEFAULT '[]',
  state                 TEXT NOT NULL DEFAULT 'proposed' CHECK (state IN ('proposed', 'imported')),
  imported_candidate_id TEXT,
  imported_at           TEXT,
  UNIQUE (run_id, draft_id)
);

-- What the AI proposed for a field a person decides: classification, primary reference. Always proposed until reviewed.
CREATE TABLE ai_proposal (
  id           TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  run_id       TEXT NOT NULL REFERENCES ai_run(id) ON DELETE CASCADE,
  field        TEXT NOT NULL CHECK (field IN ('discipline', 'category', 'impact', 'primary_reference')),
  value        TEXT NOT NULL,
  reason       TEXT NOT NULL DEFAULT '',
  state        TEXT NOT NULL DEFAULT 'suggested' CHECK (state IN ('suggested', 'accepted', 'rejected')),
  created_at   TEXT NOT NULL,
  UNIQUE (candidate_id, field, value)
);
CREATE INDEX ai_proposal_candidate ON ai_proposal(candidate_id);
