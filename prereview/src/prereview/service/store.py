"""Долговечное хранилище прогонов: sqlite. Intent сохраняется до работы, события по порядку."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  kind TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  request_json TEXT NOT NULL,
  artifact_path TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (run_id, attempt)
);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  event_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (run_id, attempt, sequence)
);
"""


@dataclass
class RunRow:
    run_id: str
    attempt: int
    kind: str
    fingerprint: str
    request: dict
    artifact_path: str | None
    status: str


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def get_run(self, run_id: str, attempt: int) -> RunRow | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=? AND attempt=?", (run_id, attempt)).fetchone()
        if not row:
            return None
        return RunRow(row["run_id"], row["attempt"], row["kind"], row["fingerprint"], json.loads(row["request_json"]),
                      row["artifact_path"], row["status"])

    def create_run(self, run_id: str, attempt: int, kind: str, fingerprint: str, request: dict) -> bool:
        """True, если intent создан; False, если уже был."""
        with self._lock, self._conn() as conn:
            try:
                conn.execute("INSERT INTO runs(run_id, attempt, kind, fingerprint, request_json, status) VALUES (?,?,?,?,?,?)",
                             (run_id, attempt, kind, fingerprint, json.dumps(request, ensure_ascii=False), "accepted"))
                return True
            except sqlite3.IntegrityError:
                return False

    def set_artifact(self, run_id: str, attempt: int, artifact_path: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE runs SET artifact_path=? WHERE run_id=? AND attempt=?", (artifact_path, run_id, attempt))

    def set_status(self, run_id: str, attempt: int, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE runs SET status=? WHERE run_id=? AND attempt=?", (status, run_id, attempt))

    def last_event(self, run_id: str, attempt: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT event_json FROM events WHERE run_id=? AND attempt=? ORDER BY sequence DESC LIMIT 1",
                               (run_id, attempt)).fetchone()
        return json.loads(row["event_json"]) if row else None

    def append_event(self, run_id: str, attempt: int, event: dict) -> dict:
        """Добавляет событие со следующим sequence. Финальное событие второй раз не пишется."""
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT event_json FROM events WHERE run_id=? AND attempt=? ORDER BY sequence DESC LIMIT 1",
                               (run_id, attempt)).fetchone()
            last = json.loads(row["event_json"]) if row else None
            if last and last["status"] in {"succeeded", "failed"}:
                return last
            seq = (last["sequence"] + 1) if last else 1
            event = dict(event, sequence=seq, event_id=str(uuid.uuid5(uuid.UUID(run_id), f"{attempt}:{seq}")))
            conn.execute("INSERT INTO events(run_id, attempt, sequence, event_json) VALUES (?,?,?,?)",
                         (run_id, attempt, seq, json.dumps(event, ensure_ascii=False)))
            return event

    def pending(self) -> list[RunRow]:
        """Прогоны, принятые, но не завершённые: восстановление после рестарта."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM runs WHERE status IN ('accepted','running')").fetchall()
        return [RunRow(r["run_id"], r["attempt"], r["kind"], r["fingerprint"], json.loads(r["request_json"]),
                       r["artifact_path"], r["status"]) for r in rows]
