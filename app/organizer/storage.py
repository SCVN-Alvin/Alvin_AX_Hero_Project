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

    # -- managed source and media files (Milestone 2)
    def sources_dir(self, project_id, document_id):
        return os.path.join(self.project_dir(project_id), "sources", document_id)

    def incoming_dir(self, project_id):
        """Scratch space for files being copied; anything left here is garbage."""
        return os.path.join(self.project_dir(project_id), "assets", ".incoming")

    def asset_relative_path(self, content_hash, ext):
        ext = "".join(ch for ch in ext.lower() if ch.isalnum())[:8]
        return f"assets/{content_hash[:2]}/{content_hash}" + (f".{ext}" if ext else "")

    def ensure_project_dirs(self, project_id):
        base = self.project_dir(project_id)
        os.makedirs(os.path.join(base, "sources"), exist_ok=True)
        os.makedirs(os.path.join(base, "assets"), exist_ok=True)

    def sweep_incoming(self):
        """Remove partial copies left by an interrupted import (called at start-up)."""
        removed = 0
        if not os.path.isdir(self.projects_dir):
            return removed
        for pid in os.listdir(self.projects_dir):
            d = self.incoming_dir(pid)
            if os.path.isdir(d):
                for name in os.listdir(d):
                    try:
                        os.remove(os.path.join(d, name))
                        removed += 1
                    except OSError:
                        pass
        return removed

    def to_trash(self, path, label):
        """Move a file or folder inside the data folder to trash/ instead of erasing it."""
        if not os.path.exists(path):
            return None
        dest = os.path.join(self.trash_dir, f"{label}-{int(time.time())}-{os.path.basename(path)}")
        os.makedirs(self.trash_dir, exist_ok=True)
        shutil.move(path, dest)
        return dest

    def resolve_in_project(self, project_id, relative_path):
        """Resolve a path under project storage; refuse anything that escapes it."""
        base = os.path.realpath(self.project_dir(project_id))
        target = os.path.realpath(os.path.join(base, relative_path))
        if os.path.commonpath([base, target]) != base:
            raise ValueError("path escapes project storage")
        return target
