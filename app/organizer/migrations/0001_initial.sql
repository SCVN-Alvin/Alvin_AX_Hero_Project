-- Milestone 1 foundation: projects, people, tasks, and the task graph basics.
-- Every project-owned row carries project_id. Composite foreign keys
-- (id, project_id) make cross-project edges impossible at the database level.
-- Statements are split with sqlite3.complete_statement and run in one transaction.

CREATE TABLE project (
  id                 TEXT PRIMARY KEY,
  name               TEXT NOT NULL CHECK (length(trim(name)) > 0),
  ticket_prefix      TEXT NOT NULL UNIQUE
                       CHECK (length(ticket_prefix) BETWEEN 2 AND 10
                              AND ticket_prefix GLOB '[A-Z]*'
                              AND ticket_prefix NOT GLOB '*[^A-Z0-9]*'),
  description        TEXT NOT NULL DEFAULT '',
  next_ticket_number INTEGER NOT NULL DEFAULT 1 CHECK (next_ticket_number >= 1),
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

-- The prefix is part of every ticket key and must never change.
CREATE TRIGGER project_prefix_immutable
BEFORE UPDATE OF ticket_prefix ON project
WHEN NEW.ticket_prefix <> OLD.ticket_prefix
BEGIN
  SELECT RAISE(ABORT, 'ticket_prefix is immutable');
END;

-- Ticket numbers are allocated monotonically and never recycled.
CREATE TRIGGER project_ticket_counter_monotonic
BEFORE UPDATE OF next_ticket_number ON project
WHEN NEW.next_ticket_number < OLD.next_ticket_number
BEGIN
  SELECT RAISE(ABORT, 'next_ticket_number cannot decrease');
END;

-- A local project person (assignee). Not an account.
CREATE TABLE person (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name       TEXT NOT NULL CHECK (length(trim(name)) > 0),
  created_at TEXT NOT NULL,
  UNIQUE (id, project_id)
);
CREATE UNIQUE INDEX person_name_unique ON person(project_id, lower(name));

CREATE TABLE task (
  id                       TEXT PRIMARY KEY,
  project_id               TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  ticket_number            INTEGER NOT NULL CHECK (ticket_number >= 1),
  parent_id                TEXT,
  level                    TEXT NOT NULL CHECK (level IN ('epic', 'task', 'subtask')),
  title                    TEXT NOT NULL CHECK (length(trim(title)) > 0),
  description              TEXT NOT NULL DEFAULT '',
  category                 TEXT NOT NULL DEFAULT 'unassigned'
                             CHECK (category IN ('core_gameplay', 'content', 'ui_ux', 'polish',
                                                 'bug_fix', 'technical', 'production', 'unassigned')),
  impact                   TEXT NOT NULL DEFAULT 'unassessed'
                             CHECK (impact IN ('critical', 'high', 'medium', 'low', 'unassessed')),
  urgency                  TEXT NOT NULL DEFAULT 'unspecified'
                             CHECK (urgency IN ('high', 'medium', 'low', 'unspecified')),
  suggested_priority       TEXT CHECK (suggested_priority IS NULL OR suggested_priority IN ('p0', 'p1', 'p2', 'p3')),
  priority_reason          TEXT NOT NULL DEFAULT '',
  priority_override        TEXT CHECK (priority_override IS NULL OR priority_override IN ('p0', 'p1', 'p2', 'p3')),
  priority_override_reason TEXT NOT NULL DEFAULT '',
  status                   TEXT NOT NULL DEFAULT 'backlog'
                             CHECK (status IN ('backlog', 'to_do', 'in_progress', 'in_review', 'done')),
  is_blocked               INTEGER NOT NULL DEFAULT 0 CHECK (is_blocked IN (0, 1)),
  blocked_reason           TEXT NOT NULL DEFAULT '',
  review_state             TEXT NOT NULL DEFAULT 'needs_review'
                             CHECK (review_state IN ('needs_review', 'approved', 'dismissed')),
  assignee_id              TEXT,
  due_at                   TEXT,
  board_rank               INTEGER NOT NULL DEFAULT 0,
  provenance               TEXT NOT NULL CHECK (provenance IN ('manual', 'source_extraction', 'rule', 'template')),
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL,
  UNIQUE (project_id, ticket_number),
  UNIQUE (id, project_id),
  CHECK (parent_id IS NULL OR parent_id <> id),
  FOREIGN KEY (parent_id, project_id)   REFERENCES task(id, project_id),
  FOREIGN KEY (assignee_id, project_id) REFERENCES person(id, project_id)
);
CREATE INDEX task_board_order ON task(project_id, review_state, status, board_rank);
CREATE INDEX task_parent ON task(parent_id);

-- Hierarchy rule (defence in depth; the service validates first with friendlier errors):
-- a child's level must rank strictly below its parent's (epic < task < subtask),
-- and a subtask must have a parent. Strict ranking also makes hierarchy cycles impossible.
CREATE TRIGGER task_hierarchy_insert
BEFORE INSERT ON task
BEGIN
  SELECT RAISE(ABORT, 'invalid hierarchy: a subtask requires a parent')
   WHERE NEW.level = 'subtask' AND NEW.parent_id IS NULL;
  SELECT RAISE(ABORT, 'invalid hierarchy: parent level must be above child level')
   WHERE NEW.parent_id IS NOT NULL
     AND (SELECT CASE level WHEN 'epic' THEN 0 WHEN 'task' THEN 1 ELSE 2 END
            FROM task WHERE id = NEW.parent_id)
         >= CASE NEW.level WHEN 'epic' THEN 0 WHEN 'task' THEN 1 ELSE 2 END;
END;

CREATE TRIGGER task_hierarchy_update
BEFORE UPDATE OF parent_id, level ON task
BEGIN
  SELECT RAISE(ABORT, 'invalid hierarchy: a subtask requires a parent')
   WHERE NEW.level = 'subtask' AND NEW.parent_id IS NULL;
  SELECT RAISE(ABORT, 'invalid hierarchy: parent level must be above child level')
   WHERE NEW.parent_id IS NOT NULL
     AND (SELECT CASE level WHEN 'epic' THEN 0 WHEN 'task' THEN 1 ELSE 2 END
            FROM task WHERE id = NEW.parent_id)
         >= CASE NEW.level WHEN 'epic' THEN 0 WHEN 'task' THEN 1 ELSE 2 END;
  SELECT RAISE(ABORT, 'invalid hierarchy: child level must be below parent level')
   WHERE EXISTS (SELECT 1 FROM task c
                  WHERE c.parent_id = NEW.id
                    AND CASE c.level WHEN 'epic' THEN 0 WHEN 'task' THEN 1 ELSE 2 END
                        <= CASE NEW.level WHEN 'epic' THEN 0 WHEN 'task' THEN 1 ELSE 2 END);
END;

-- Disciplines are independent labels. No rows means unassigned.
CREATE TABLE task_discipline (
  task_id    TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  discipline TEXT NOT NULL CHECK (discipline IN ('art', 'dev')),
  PRIMARY KEY (task_id, discipline)
);

CREATE TABLE tag (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name       TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(name)) > 0),
  UNIQUE (id, project_id),
  UNIQUE (project_id, name)
);

CREATE TABLE task_tag (
  project_id TEXT NOT NULL,
  task_id    TEXT NOT NULL,
  tag_id     TEXT NOT NULL,
  PRIMARY KEY (task_id, tag_id),
  FOREIGN KEY (task_id, project_id) REFERENCES task(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id, project_id)  REFERENCES tag(id, project_id)  ON DELETE CASCADE
);

CREATE TABLE acceptance_criterion (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  task_id    TEXT NOT NULL,
  text       TEXT NOT NULL CHECK (length(trim(text)) > 0),
  checked    INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0, 1)),
  position   INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (task_id, project_id) REFERENCES task(id, project_id) ON DELETE CASCADE
);
CREATE INDEX acceptance_criterion_task ON acceptance_criterion(task_id, position);

-- task_id depends on depends_on_task_id. Cycle checks live in the service.
CREATE TABLE task_dependency (
  project_id         TEXT NOT NULL,
  task_id            TEXT NOT NULL,
  depends_on_task_id TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  PRIMARY KEY (task_id, depends_on_task_id),
  CHECK (task_id <> depends_on_task_id),
  FOREIGN KEY (task_id, project_id)            REFERENCES task(id, project_id) ON DELETE CASCADE,
  FOREIGN KEY (depends_on_task_id, project_id) REFERENCES task(id, project_id) ON DELETE CASCADE
);
CREATE INDEX task_dependency_reverse ON task_dependency(depends_on_task_id);

-- Field changes with their origin, so later reprocessing can protect manual edits.
CREATE TABLE task_field_change (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  field      TEXT NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  origin     TEXT NOT NULL CHECK (origin IN ('manual', 'source_extraction', 'rule', 'template', 'system')),
  changed_at TEXT NOT NULL
);
CREATE INDEX task_field_change_task ON task_field_change(task_id, id);
