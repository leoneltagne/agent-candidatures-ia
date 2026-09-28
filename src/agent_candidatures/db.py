"""Stockage SQLite : déduplication et suivi de statut de chaque offre."""
from __future__ import annotations

import sqlite3
from datetime import date

from .models import Job

# new -> scored -> drafted -> approved -> sent   (ou skipped / manual / low_score)
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, description TEXT,
  source TEXT, location TEXT, contact_email TEXT,
  score INTEGER, reasons TEXT, status TEXT DEFAULT 'new', folder TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, sent_at TEXT
);
"""


class DB:
    def __init__(self, path: str = "data/candidatures.db"):
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    def add_if_new(self, job: Job) -> bool:
        cur = self.con.execute(
            "INSERT OR IGNORE INTO jobs (id,title,company,url,description,source,location,contact_email)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (job.id, job.title, job.company, job.url, job.description, job.source,
             job.location, job.contact_email),
        )
        self.con.commit()
        return cur.rowcount == 1

    def by_status(self, status: str):
        return self.con.execute("SELECT * FROM jobs WHERE status=?", (status,)).fetchall()

    def get(self, job_id: str):
        return self.con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def update(self, job_id: str, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.con.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        self.con.commit()

    def sent_today(self) -> int:
        return self.con.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='sent' AND date(sent_at)=?",
            (date.today().isoformat(),),
        ).fetchone()[0]
