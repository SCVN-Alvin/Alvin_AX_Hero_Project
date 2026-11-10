-- Milestone 2: imported source documents, their ordered blocks, managed media,
-- references, scenarios, and import runs. Additive only: no Milestone 1 table is touched.
-- Every row carries project_id; composite foreign keys (id, project_id) keep rows of
-- different projects from ever referring to each other.

CREATE TABLE source_document (
  id                  TEXT PRIMARY KEY,
  project_id          TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  original_name       TEXT NOT NULL CHECK (length(original_name) > 0),
  title               TEXT NOT NULL DEFAULT '',
  local_relative_path TEXT NOT NULL
                        CHECK (substr(local_relative_path, 1, 1) <> '/' AND instr(local_relative_path, '..') = 0
                               AND instr(local_relative_path, char(92)) = 0),
  content_hash        TEXT NOT NULL CHECK (length(content_hash) = 64),
  size_bytes          INTEGER NOT NULL CHECK (size_bytes >= 0),
  version             INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  companion_name      TEXT NOT NULL DEFAULT '',
  manifest_hash       TEXT NOT NULL,
  parser_version      TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  UNIQUE (id, project_id)
);
CREATE INDEX source_document_project ON source_document(project_id, created_at);
CREATE INDEX source_document_hash ON source_document(project_id, content_hash);

-- One row per import attempt. A run exists from the moment an import starts, so an
-- interrupted or failed attempt stays visible. Content rows are only written by the
-- final transaction, which is why a failed run leaves no partial document behind.
CREATE TABLE import_run (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id     TEXT,
  kind            TEXT NOT NULL DEFAULT 'import' CHECK (kind IN ('import', 'asset_refresh')),
  parser_version  TEXT NOT NULL,
  ruleset_version TEXT NOT NULL DEFAULT '',
  state           TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'identical', 'failed', 'interrupted', 'cancelled')),
  stage           TEXT NOT NULL DEFAULT '',
  source_name     TEXT NOT NULL,
  error           TEXT NOT NULL DEFAULT '',
  note            TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  finished_at     TEXT,
  UNIQUE (id, project_id),
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE
);
CREATE INDEX import_run_project ON import_run(project_id, created_at);

-- Heading-delimited ranges of blocks. `role` records the interpretation (current or
-- historical); the person can change it, the parser's guess is kept in role_reason.
CREATE TABLE source_section (
  id                TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id       TEXT NOT NULL,
  parent_section_id TEXT,
  position          INTEGER NOT NULL,
  level             INTEGER NOT NULL CHECK (level BETWEEN 0 AND 6),
  title             TEXT NOT NULL,
  heading_path      TEXT NOT NULL DEFAULT '[]',
  heading_block_id  TEXT,
  first_sequence    INTEGER NOT NULL,
  last_sequence     INTEGER NOT NULL,
  role              TEXT NOT NULL CHECK (role IN ('current', 'historical', 'metadata')),
  role_origin       TEXT NOT NULL DEFAULT 'parser' CHECK (role_origin IN ('parser', 'user')),
  role_reason       TEXT NOT NULL DEFAULT '',
  UNIQUE (id, project_id),
  UNIQUE (document_id, position),
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (parent_section_id) REFERENCES source_section(id) ON DELETE CASCADE
);

CREATE TABLE source_block (
  id                TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id       TEXT NOT NULL,
  import_run_id     TEXT NOT NULL,
  section_id        TEXT,
  source_element_id TEXT,
  locator           TEXT NOT NULL,
  heading_path      TEXT NOT NULL DEFAULT '[]',
  sequence          INTEGER NOT NULL CHECK (sequence >= 0),
  depth             INTEGER NOT NULL DEFAULT 0 CHECK (depth >= 0),
  parent_block_id   TEXT,
  block_type        TEXT NOT NULL CHECK (block_type IN
                      ('title', 'heading', 'paragraph', 'list_item', 'table', 'image', 'video', 'audio',
                       'attachment', 'divider', 'quote', 'code', 'callout', 'toggle')),
  variant           TEXT NOT NULL DEFAULT '',
  heading_level     INTEGER CHECK (heading_level IS NULL OR heading_level BETWEEN 1 AND 6),
  list_kind         TEXT CHECK (list_kind IS NULL OR list_kind IN ('bulleted', 'numbered', 'todo', 'toggle')),
  list_index        INTEGER,
  checked           INTEGER CHECK (checked IS NULL OR checked IN (0, 1)),
  table_rows        INTEGER,
  table_cols        INTEGER,
  table_header_rows INTEGER,
  column_group      TEXT,
  column_index      INTEGER,
  original_text     TEXT NOT NULL,
  text_hash         TEXT NOT NULL CHECK (length(text_hash) = 64),
  inline_json       TEXT NOT NULL DEFAULT '[]',
  UNIQUE (id, project_id),
  UNIQUE (document_id, sequence),
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (import_run_id, project_id) REFERENCES import_run(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (section_id) REFERENCES source_section(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (parent_block_id) REFERENCES source_block(id) ON DELETE CASCADE
);
CREATE INDEX source_block_section ON source_block(section_id, sequence);
CREATE INDEX source_block_element ON source_block(document_id, source_element_id);

-- Original wording is evidence: it cannot be rewritten after import. Interpretations
-- (section roles, tasks, simplified descriptions) live in other tables.
CREATE TRIGGER source_block_evidence_immutable
BEFORE UPDATE OF original_text, text_hash, sequence, locator, heading_path, source_element_id, document_id, block_type ON source_block
BEGIN
  SELECT RAISE(ABORT, 'imported source text is immutable');
END;

-- Table cells keep their own text so rows and columns stay structured.
CREATE TABLE source_cell (
  id                 TEXT PRIMARY KEY,
  project_id         TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  block_id           TEXT NOT NULL REFERENCES source_block(id) ON DELETE CASCADE,
  row_index          INTEGER NOT NULL CHECK (row_index >= 0),
  col_index          INTEGER NOT NULL CHECK (col_index >= 0),
  is_header          INTEGER NOT NULL DEFAULT 0 CHECK (is_header IN (0, 1)),
  row_element_id     TEXT,
  source_element_id  TEXT,
  original_text      TEXT NOT NULL,
  inline_json        TEXT NOT NULL DEFAULT '[]',
  UNIQUE (block_id, row_index, col_index)
);

CREATE TRIGGER source_cell_evidence_immutable
BEFORE UPDATE OF original_text, row_index, col_index, block_id ON source_cell
BEGIN
  SELECT RAISE(ABORT, 'imported source text is immutable');
END;

-- A file the document points at. availability explains what happened to it; the
-- reference is kept even when the file is missing, blocked, or unreadable.
CREATE TABLE asset (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id   TEXT NOT NULL,
  source_path   TEXT NOT NULL,
  original_name TEXT NOT NULL,
  relative_path TEXT
                  CHECK (relative_path IS NULL OR
                         (substr(relative_path, 1, 1) <> '/' AND instr(relative_path, '..') = 0
                          AND instr(relative_path, char(92)) = 0)),
  media_type    TEXT NOT NULL DEFAULT '',
  size_bytes    INTEGER,
  content_hash  TEXT,
  availability  TEXT NOT NULL CHECK (availability IN ('available', 'missing', 'blocked', 'unreadable')),
  problem       TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE (id, project_id),
  UNIQUE (document_id, source_path),
  CHECK (availability <> 'available' OR (relative_path IS NOT NULL AND content_hash IS NOT NULL AND size_bytes IS NOT NULL)),
  FOREIGN KEY (document_id, project_id) REFERENCES source_document(id, project_id) ON DELETE CASCADE
);
CREATE INDEX asset_document ON asset(document_id, availability);
CREATE INDEX asset_hash ON asset(project_id, content_hash);

-- A pointer from a source block to an asset, a web address, or another page.
CREATE TABLE reference (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id   TEXT NOT NULL,
  source_block_id TEXT NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN
                  ('image', 'video', 'audio', 'attachment', 'external_link', 'external_image', 'page_link')),
  placement     TEXT NOT NULL DEFAULT 'figure' CHECK (placement IN ('figure', 'inline', 'link', 'cell')),
  position      INTEGER NOT NULL DEFAULT 0,
  asset_id      TEXT,
  external_url  TEXT,
  raw_target    TEXT NOT NULL DEFAULT '',
  link_text     TEXT NOT NULL DEFAULT '',
  caption       TEXT NOT NULL DEFAULT '',
  start_ms      INTEGER CHECK (start_ms IS NULL OR start_ms >= 0),
  end_ms        INTEGER CHECK (end_ms IS NULL OR (start_ms IS NOT NULL AND end_ms > start_ms)),
  UNIQUE (id, project_id),
  -- A reference needs a target that fits its kind.
  CHECK (kind NOT IN ('image', 'video', 'audio', 'attachment') OR asset_id IS NOT NULL),
  CHECK (kind NOT IN ('external_link', 'external_image') OR (external_url IS NOT NULL AND length(external_url) > 0)),
  CHECK (kind <> 'page_link' OR length(raw_target) > 0),
  FOREIGN KEY (document_id, project_id)     REFERENCES source_document(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (source_block_id)             REFERENCES source_block(id) ON DELETE CASCADE,
  FOREIGN KEY (asset_id, project_id)        REFERENCES asset(id, project_id) ON DELETE CASCADE
);
CREATE INDEX reference_block ON reference(source_block_id, position);
CREATE INDEX reference_asset ON reference(asset_id);

-- Rows of a recognised scenario table. The identifier is the document's own label
-- (for example "001"); it is evidence, never the database key.
CREATE TABLE scenario (
  id                TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id       TEXT NOT NULL,
  import_run_id     TEXT NOT NULL,
  position          INTEGER NOT NULL,
  source_identifier TEXT NOT NULL,
  identifier_raw    TEXT NOT NULL,
  title             TEXT NOT NULL DEFAULT '',
  description       TEXT NOT NULL DEFAULT '',
  preamble          TEXT NOT NULL DEFAULT '',
  segmentation      TEXT NOT NULL CHECK (segmentation IN ('steps', 'plain', 'ambiguous')),
  source_block_id   TEXT NOT NULL,
  row_index         INTEGER NOT NULL,
  row_element_id    TEXT,
  UNIQUE (id, project_id),
  UNIQUE (source_block_id, row_index),
  FOREIGN KEY (document_id, project_id)  REFERENCES source_document(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (import_run_id, project_id) REFERENCES import_run(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (source_block_id)          REFERENCES source_block(id) ON DELETE CASCADE
);
CREATE INDEX scenario_document ON scenario(document_id, position);

-- Inline numbered steps. number_label is the number as written, which may repeat.
CREATE TABLE scenario_step (
  id           TEXT PRIMARY KEY,
  scenario_id  TEXT NOT NULL REFERENCES scenario(id) ON DELETE CASCADE,
  sequence     INTEGER NOT NULL,
  number_label TEXT NOT NULL,
  text         TEXT NOT NULL,
  raw_text     TEXT NOT NULL,
  UNIQUE (scenario_id, sequence)
);

-- Things the importer noticed that a person may want to know about.
CREATE TABLE import_diagnostic (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  document_id     TEXT NOT NULL,
  import_run_id   TEXT NOT NULL,
  severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
  code            TEXT NOT NULL,
  message         TEXT NOT NULL,
  source_block_id TEXT,
  position        INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (document_id, project_id)   REFERENCES source_document(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (import_run_id, project_id) REFERENCES import_run(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (source_block_id)           REFERENCES source_block(id) ON DELETE CASCADE
);
CREATE INDEX import_diagnostic_document ON import_diagnostic(document_id, position);
