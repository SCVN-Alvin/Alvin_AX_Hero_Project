"""Managed on-disk layout. Everything lives under one data directory:

  <data>/organizer.db
  <data>/projects/<project_id>/sources/   original imported files (Milestone 2)
  <data>/projects/<project_id>/assets/    local media (Milestone 2)
  <data>/trash/                           deleted project folders, kept for recovery
"""

import os
import shutil
import time


class Storage:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.db_path = os.path.join(self.data_dir, "organizer.db")
        self.projects_dir = os.path.join(self.data_dir, "projects")
        self.trash_dir = os.path.join(self.data_dir, "trash")

    def init(self):
        for d in (self.data_dir, self.projects_dir, self.trash_dir):
            os.makedirs(d, exist_ok=True)

    def project_dir(self, project_id):
        return os.path.join(self.projects_dir, project_id)

    def create_project_dirs(self, project_id):
        base = self.project_dir(project_id)
        os.makedirs(os.path.join(base, "sources"), exist_ok=False)
        os.makedirs(os.path.join(base, "assets"), exist_ok=False)

    def remove_project_dirs(self, project_id):
        shutil.rmtree(self.project_dir(project_id), ignore_errors=True)

    def trash_project_dirs(self, project_id):
        src = self.project_dir(project_id)
        if not os.path.isdir(src):
            return None
        dest = os.path.join(self.trash_dir, f"{project_id}-{int(time.time())}")
        shutil.move(src, dest)
        return dest

    def resolve_in_project(self, project_id, relative_path):
        """Resolve a path under project storage; refuse anything that escapes it."""
        base = os.path.realpath(self.project_dir(project_id))
        target = os.path.realpath(os.path.join(base, relative_path))
        if os.path.commonpath([base, target]) != base:
            raise ValueError("path escapes project storage")
        return target
