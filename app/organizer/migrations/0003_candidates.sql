-- Milestone 3: candidate generation, editable classification rules, review, and approval.
-- Additive only. Candidates are proposals kept apart from board tasks: a task (and its ticket
-- number) exists only after a person approves a candidate.

CREATE TABLE rule (
  id             TEXT PRIMARY KEY,
  project_id     TEXT REFERENCES project(id) ON DELETE CASCADE,   -- NULL = global rule
  name           TEXT NOT NULL CHECK (length(trim(name)) > 0),
  version        INTEGER NOT NULL DEFAULT 1,
  enabled        INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  match_scope    TEXT NOT NULL CHECK (match_scope IN ('title', 'text', 'heading_path', 'any')),
  operator       TEXT NOT NULL CHECK (operator IN ('contains', 'equals')),
  pattern        TEXT NOT NULL CHECK (length(trim(pattern)) > 0),  -- one phrase per line; a trailing * matches word endings
  case_sensitive INTEGER NOT NULL DEFAULT 0 CHECK (case_sensitive IN (0, 1)),
  output_field   TEXT NOT NULL CHECK (output_field IN ('discipline', 'category', 'impact', 'urgency')),
  proposed_value TEXT NOT NULL,
  precedence     INTEGER NOT NULL DEFAULT 0,
  origin         TEXT NOT NULL CHECK (origin IN ('builtin', 'user')),
  note           TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  CHECK (
    (output_field = 'discipline' AND proposed_value IN ('art', 'dev', 'both')) OR
    (output_field = 'category'   AND proposed_value IN ('core_gameplay', 'content', 'ui_ux', 'polish', 'bug_fix', 'technical', 'production')) OR
    (output_field = 'impact'     AND proposed_value IN ('critical', 'high', 'medium', 'low')) OR
    (output_field = 'urgency'    AND proposed_value IN ('high', 'medium', 'low'))
  )
);
CREATE INDEX rule_project ON rule(project_id);

-- Starting vocabulary: the wording in the offline-processing rule table, in English and in the
-- Vietnamese used by the sample. All of it is editable in the interface.
INSERT INTO rule(id, project_id, name, version, enabled, match_scope, operator, pattern, case_sensitive, output_field, proposed_value, precedence, origin, note, created_at, updated_at) VALUES
 ('builtin-dev-logic', NULL, 'Spawning, collision, and resource calculation', 1, 1, 'text', 'contains',
  'spawn*' || char(10) || 'collision*' || char(10) || 'collid*' || char(10) || 'resource calculation' || char(10) || 'va chạm' || char(10) || 'tính toán',
  0, 'discipline', 'dev', 0, 'builtin', 'Programming-side wording. The matched phrase is recorded as evidence.', '2026-11-01T00:00:00.000Z', '2026-11-01T00:00:00.000Z'),
 ('builtin-art-assets', NULL, 'Model, texture, and illustration', 1, 1, 'text', 'contains',
  'model*' || char(10) || 'texture*' || char(10) || 'illustration*' || char(10) || 'mô hình' || char(10) || 'minh họa',
  0, 'discipline', 'art', 0, 'builtin', 'Asset-creation wording. One nearby label never classifies a whole document.', '2026-11-01T00:00:00.000Z', '2026-11-01T00:00:00.000Z'),
 ('builtin-art-dev-animation', NULL, 'Animation or VFX integration', 1, 1, 'text', 'contains',
  'animation*' || char(10) || 'vfx' || char(10) || 'hoạt ảnh',
  0, 'discipline', 'both', 0, 'builtin', 'Animation needs art and implementation. A person can refine who owns what.', '2026-11-01T00:00:00.000Z', '2026-11-01T00:00:00.000Z'),
 ('builtin-core-gameplay-heading', NULL, 'Gameplay section', 1, 1, 'heading_path', 'contains',
  'core gameplay' || char(10) || 'gameplay',
  0, 'category', 'core_gameplay', 0, 'builtin', 'A heading about gameplay suggests Core gameplay. A more specific project rule can override it.', '2026-11-01T00:00:00.000Z', '2026-11-01T00:00:00.000Z'),
 ('builtin-bug-report', NULL, 'Explicit defect report', 1, 1, 'text', 'contains',
  'bug report' || char(10) || 'defect*' || char(10) || 'crash*' || char(10) || 'regression*',
  0, 'category', 'bug_fix', 0, 'builtin', 'Only explicit defect wording. Creative failure scenarios ("fail to defend") are not bugs.', '2026-11-01T00:00:00.000Z', '2026-11-01T00:00:00.000Z');

CREATE TABLE generation_run (
  id               TEXT PRIMARY KEY,
  project_id       TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id      TEXT NOT NULL,
  generator_version TEXT NOT NULL,
  ruleset_version  TEXT NOT NULL,
  new_candidates   INTEGER NOT NULL DEFAULT 0,
  existing_candidates INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  UNIQUE (id, project_id),
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE
);

CREATE TABLE generation_section (
  run_id     TEXT NOT NULL REFERENCES generation_run(id) ON DELETE CASCADE,
  section_id TEXT NOT NULL REFERENCES source_section(id) ON DELETE CASCADE,
  PRIMARY KEY (run_id, section_id)
);

-- A proposal. Reviewed values live on the candidate (default: unassigned); what rules suggest lives
-- in rule_suggestion until a person accepts it.
CREATE TABLE candidate (
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
  original_text      TEXT NOT NULL DEFAULT '',
  values_json        TEXT NOT NULL DEFAULT '[]',
  category           TEXT NOT NULL DEFAULT 'unassigned'
                       CHECK (category IN ('core_gameplay', 'content', 'ui_ux', 'polish', 'bug_fix', 'technical', 'production', 'unassigned')),
  impact             TEXT NOT NULL DEFAULT 'unassessed' CHECK (impact IN ('critical', 'high', 'medium', 'low', 'unassessed')),
  urgency            TEXT NOT NULL DEFAULT 'unspecified' CHECK (urgency IN ('high', 'medium', 'low', 'unspecified')),
  priority_override  TEXT CHECK (priority_override IS NULL OR priority_override IN ('p0', 'p1', 'p2', 'p3')),
  priority_override_reason TEXT NOT NULL DEFAULT '',
  state              TEXT NOT NULL DEFAULT 'needs_review' CHECK (state IN ('needs_review', 'approved', 'dismissed', 'merged')),
  task_id            TEXT,
  requirement_id     TEXT,
  merged_into_id     TEXT REFERENCES candidate(id) ON DELETE SET NULL,
  origin             TEXT NOT NULL DEFAULT 'generated' CHECK (origin IN ('generated', 'user')),
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (id, project_id),
  UNIQUE (document_id, identity),
  CHECK (parent_id IS NULL OR parent_id <> id),
  CHECK (priority_override IS NOT NULL OR priority_override_reason = ''),
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE
);
CREATE INDEX candidate_document ON candidate(document_id, position);
CREATE INDEX candidate_parent ON candidate(parent_id);

CREATE TABLE candidate_discipline (
  candidate_id TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  discipline   TEXT NOT NULL CHECK (discipline IN ('art', 'dev')),
  PRIMARY KEY (candidate_id, discipline)
);

-- The exact source blocks a candidate came from. Original wording is read from these, never copied.
CREATE TABLE candidate_source (
  candidate_id    TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  source_block_id TEXT NOT NULL REFERENCES source_block(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK (role IN ('label', 'evidence', 'scope', 'context', 'media')),
  position        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (candidate_id, source_block_id)
);

-- Scenario restriction stated by the source ("Scenario 001 + 002"). scenario_id is empty when the
-- document defines no such scenario; that is reported, not guessed.
CREATE TABLE candidate_scenario (
  candidate_id    TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  identifier      TEXT NOT NULL,
  scenario_id     TEXT REFERENCES scenario(id) ON DELETE SET NULL,
  source_block_id TEXT REFERENCES source_block(id) ON DELETE SET NULL,
  PRIMARY KEY (candidate_id, identifier)
);

-- Media shown near a candidate. 'suggested' is a guess from proximity or shared wording;
-- 'attached' is a person's decision; 'removed' keeps a rejected guess from coming back.
CREATE TABLE candidate_reference (
  candidate_id TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  reference_id TEXT NOT NULL REFERENCES reference(id) ON DELETE CASCADE,
  state        TEXT NOT NULL DEFAULT 'suggested' CHECK (state IN ('suggested', 'attached', 'removed')),
  reason       TEXT NOT NULL DEFAULT '',
  position     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (candidate_id, reference_id)
);

-- What a rule proposed, with the evidence that triggered it. States: suggested, conflicting
-- (equal-precedence rules disagree), superseded (a stronger rule or a set value wins), accepted, rejected.
CREATE TABLE rule_suggestion (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  candidate_id    TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  rule_ref        TEXT NOT NULL,
  rule_name       TEXT NOT NULL,
  rule_version    INTEGER NOT NULL,
  rule_scope      TEXT NOT NULL CHECK (rule_scope IN ('project', 'global')),
  field           TEXT NOT NULL CHECK (field IN ('discipline', 'category', 'impact', 'urgency')),
  proposed_value  TEXT NOT NULL,
  matched_text    TEXT NOT NULL,
  match_in        TEXT NOT NULL DEFAULT '',
  source_block_id TEXT REFERENCES source_block(id) ON DELETE SET NULL,
  state           TEXT NOT NULL CHECK (state IN ('suggested', 'conflicting', 'superseded', 'accepted', 'rejected')),
  created_at      TEXT NOT NULL,
  UNIQUE (candidate_id, rule_ref, field, proposed_value)
);
CREATE INDEX rule_suggestion_candidate ON rule_suggestion(candidate_id);

-- Approved evidence on the board side. A requirement keeps the source's own words; a task links to
-- the exact blocks, requirements, scenarios, and media it came from.
CREATE TABLE requirement (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('requirement', 'exclusion')),
  original_text   TEXT NOT NULL,
  interpretation  TEXT NOT NULL DEFAULT '',
  values_json     TEXT NOT NULL DEFAULT '[]',
  review_state    TEXT NOT NULL DEFAULT 'approved' CHECK (review_state IN ('approved')),
  provenance      TEXT NOT NULL CHECK (provenance IN ('manual', 'source_extraction', 'rule', 'template')),
  candidate_id    TEXT,
  created_at      TEXT NOT NULL,
  UNIQUE (id, project_id)
);

CREATE TABLE requirement_source (
  requirement_id  TEXT NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
  source_block_id TEXT NOT NULL REFERENCES source_block(id),
  role            TEXT NOT NULL CHECK (role IN ('evidence', 'scope', 'context')),
  PRIMARY KEY (requirement_id, source_block_id)
);

CREATE TABLE task_requirement (
  project_id     TEXT NOT NULL,
  task_id        TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  position       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (task_id, requirement_id),
  FOREIGN KEY (task_id, project_id)        REFERENCES task(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (requirement_id, project_id) REFERENCES requirement(id, project_id) ON DELETE CASCADE
);

CREATE TABLE scenario_requirement (
  project_id     TEXT NOT NULL,
  scenario_id    TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  review_state   TEXT NOT NULL DEFAULT 'confirmed' CHECK (review_state IN ('confirmed', 'proposed')),
  PRIMARY KEY (scenario_id, requirement_id),
  FOREIGN KEY (scenario_id, project_id)    REFERENCES scenario(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (requirement_id, project_id) REFERENCES requirement(id, project_id) ON DELETE CASCADE
);

CREATE TABLE task_source (
  task_id         TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  source_block_id TEXT NOT NULL REFERENCES source_block(id),
  role            TEXT NOT NULL CHECK (role IN ('label', 'evidence', 'scope', 'context', 'media')),
  position        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (task_id, source_block_id)
);

CREATE TABLE task_reference (
  project_id   TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  reference_id TEXT NOT NULL REFERENCES reference(id),
  position     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (task_id, reference_id),
  FOREIGN KEY (task_id, project_id) REFERENCES task(id, project_id) ON DELETE CASCADE
);
