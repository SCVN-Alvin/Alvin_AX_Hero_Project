-- Milestone 3 revision: task-first presentation and room for AI provenance.
--
-- * a role, friendly caption and optional playback range on every reference attached to a candidate or a task
-- * at most one primary demonstration per candidate and per task (enforced by the database)
-- * an explicit "no reference needed" decision
-- * open questions (clarifications) that stay visible until someone resolves them
-- * description history, so a replaced description can be recovered
-- * candidate.origin gains 'ai' so AI proposals are never labelled as manual or rule-generated work
--   (SQLite cannot alter a CHECK, so the candidate table is rebuilt; see db.migrate for the safeguards)
-- migrate: foreign_keys=off

CREATE TABLE candidate_new (
  id                 TEXT PRIMARY KEY,
  project_id         TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id        TEXT NOT NULL,
  generation_run_id  TEXT REFERENCES generation_run(id) ON DELETE SET NULL,
  identity           TEXT NOT NULL,
  kind               TEXT NOT NULL CHECK (kind IN ('task', 'requirement', 'exclusion', 'note', 'asset')),
  level              TEXT CHECK (level IS NULL OR level IN ('epic', 'task', 'subtask')),
  parent_id          TEXT REFERENCES candidate(id) ON DELETE SET NULL,
  suggested_parent_id TEXT REFERENCES candidate(id) ON DELETE SET NULL,
  suggestion_reason  TEXT NOT NULL DEFAULT '',
  position           INTEGER NOT NULL DEFAULT 0,
  title              TEXT NOT NULL CHECK (length(trim(title)) > 0),
  title_origin       TEXT NOT NULL DEFAULT 'source' CHECK (title_origin IN ('source', 'user')),
  description        TEXT NOT NULL DEFAULT '',
  description_origin TEXT NOT NULL DEFAULT 'user' CHECK (description_origin IN ('user', 'ai')),
  original_text      TEXT NOT NULL DEFAULT '',
  values_json        TEXT NOT NULL DEFAULT '[]',
  category           TEXT NOT NULL DEFAULT 'unassigned'
                       CHECK (category IN ('core_gameplay', 'content', 'ui_ux', 'polish', 'bug_fix', 'technical', 'production', 'unassigned')),
  impact             TEXT NOT NULL DEFAULT 'unassessed' CHECK (impact IN ('critical', 'high', 'medium', 'low', 'unassessed')),
  urgency            TEXT NOT NULL DEFAULT 'unspecified' CHECK (urgency IN ('high', 'medium', 'low', 'unspecified')),
  priority_override  TEXT CHECK (priority_override IS NULL OR priority_override IN ('p0', 'p1', 'p2', 'p3')),
  priority_override_reason TEXT NOT NULL DEFAULT '',
  reference_decision TEXT NOT NULL DEFAULT 'undecided' CHECK (reference_decision IN ('undecided', 'none')),
  state              TEXT NOT NULL DEFAULT 'needs_review' CHECK (state IN ('needs_review', 'approved', 'dismissed', 'merged')),
  task_id            TEXT,
  requirement_id     TEXT,
  merged_into_id     TEXT REFERENCES candidate(id) ON DELETE SET NULL,
  origin             TEXT NOT NULL DEFAULT 'generated' CHECK (origin IN ('generated', 'user', 'ai')),
  ai_run_id          TEXT,
  ai_draft_id        TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (id, project_id),
  UNIQUE (document_id, identity),
  CHECK (parent_id IS NULL OR parent_id <> id),
  CHECK (priority_override IS NOT NULL OR priority_override_reason = ''),
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE
);

INSERT INTO candidate_new(id, project_id, document_id, generation_run_id, identity, kind, level, parent_id, suggested_parent_id, suggestion_reason,
  position, title, title_origin, description, original_text, values_json, category, impact, urgency, priority_override, priority_override_reason,
  state, task_id, requirement_id, merged_into_id, origin, created_at, updated_at)
SELECT id, project_id, document_id, generation_run_id, identity, kind, level, parent_id, suggested_parent_id, suggestion_reason,
  position, title, title_origin, description, original_text, values_json, category, impact, urgency, priority_override, priority_override_reason,
  state, task_id, requirement_id, merged_into_id, origin, created_at, updated_at
FROM candidate;

DROP TABLE candidate;
ALTER TABLE candidate_new RENAME TO candidate;
CREATE INDEX candidate_document ON candidate(document_id, position);
CREATE INDEX candidate_parent ON candidate(parent_id);

-- References: role, friendly caption, playback range.
ALTER TABLE candidate_reference ADD COLUMN role TEXT NOT NULL DEFAULT 'supporting_demo'
  CHECK (role IN ('primary_demo', 'supporting_demo', 'asset_reference', 'source_evidence'));
ALTER TABLE candidate_reference ADD COLUMN caption TEXT NOT NULL DEFAULT '';
ALTER TABLE candidate_reference ADD COLUMN start_ms INTEGER CHECK (start_ms IS NULL OR start_ms >= 0);
ALTER TABLE candidate_reference ADD COLUMN end_ms INTEGER CHECK (end_ms IS NULL OR (start_ms IS NOT NULL AND end_ms > start_ms));
CREATE UNIQUE INDEX candidate_one_primary ON candidate_reference(candidate_id) WHERE role = 'primary_demo';
-- a primary demonstration must be an attached reference
CREATE TRIGGER candidate_primary_is_attached_insert BEFORE INSERT ON candidate_reference
WHEN NEW.role = 'primary_demo' AND NEW.state <> 'attached'
BEGIN SELECT RAISE(ABORT, 'A primary reference must be attached'); END;
CREATE TRIGGER candidate_primary_is_attached_update BEFORE UPDATE ON candidate_reference
WHEN NEW.role = 'primary_demo' AND NEW.state <> 'attached'
BEGIN SELECT RAISE(ABORT, 'A primary reference must be attached'); END;

ALTER TABLE task_reference ADD COLUMN role TEXT NOT NULL DEFAULT 'supporting_demo'
  CHECK (role IN ('primary_demo', 'supporting_demo', 'asset_reference', 'source_evidence'));
ALTER TABLE task_reference ADD COLUMN caption TEXT NOT NULL DEFAULT '';
ALTER TABLE task_reference ADD COLUMN start_ms INTEGER CHECK (start_ms IS NULL OR start_ms >= 0);
ALTER TABLE task_reference ADD COLUMN end_ms INTEGER CHECK (end_ms IS NULL OR (start_ms IS NOT NULL AND end_ms > start_ms));
CREATE UNIQUE INDEX task_one_primary ON task_reference(task_id) WHERE role = 'primary_demo';
-- a primary reference must belong to the task's own project
CREATE TRIGGER task_reference_same_project_insert BEFORE INSERT ON task_reference
WHEN (SELECT project_id FROM reference WHERE id = NEW.reference_id) IS NOT NEW.project_id
BEGIN SELECT RAISE(ABORT, 'A reference must belong to the same project as the task'); END;

ALTER TABLE task ADD COLUMN reference_decision TEXT NOT NULL DEFAULT 'undecided' CHECK (reference_decision IN ('undecided', 'none'));

-- Open questions: contradictions and gaps stay visible until somebody resolves them.
CREATE TABLE clarification (
  id           TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  candidate_id TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  text         TEXT NOT NULL CHECK (length(trim(text)) > 0),
  origin       TEXT NOT NULL CHECK (origin IN ('user', 'ai')),
  state        TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved')),
  identity     TEXT NOT NULL,
  ai_run_id    TEXT,
  resolution   TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL,
  resolved_at  TEXT,
  UNIQUE (candidate_id, identity)
);
CREATE INDEX clarification_candidate ON clarification(candidate_id);

CREATE TABLE clarification_source (
  clarification_id TEXT NOT NULL REFERENCES clarification(id) ON DELETE CASCADE,
  source_block_id  TEXT NOT NULL REFERENCES source_block(id) ON DELETE CASCADE,
  PRIMARY KEY (clarification_id, source_block_id)
);

-- Every description a candidate has had, newest last, so a replaced one can be restored.
CREATE TABLE candidate_description_version (
  id           TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  description  TEXT NOT NULL,
  origin       TEXT NOT NULL CHECK (origin IN ('user', 'ai')),
  ai_run_id    TEXT,
  note         TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL
);
CREATE INDEX candidate_description_version_candidate ON candidate_description_version(candidate_id, created_at);
