"""SQLite storage for LexGap: connection handling, schema and migrations.

The database is the project's single source of truth.  Two properties matter:

* **Idempotence** -- ``init_db`` can be run at any time; it creates what is
  missing and touches nothing else.
* **Cache-by-constraint** -- ``probes`` carries a UNIQUE constraint over
  ``(set_id, headword, model_id, task, prompt_ver)``.  That constraint *is* the
  probe cache: a re-run skips work by looking the row up first, so an
  interrupted batch can be resumed without duplicating the model calls.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

__all__ = [
  "SCHEMA_VERSION",
  "init_db",
  "get_conn",
  "connect",
  "transaction",
  "meta_get",
  "meta_set",
  "utcnow",
  "PROBE_KEY",
]

# Bump when the DDL below changes in a way that needs a migration.
SCHEMA_VERSION = 1

# The columns that identify a cached probe.
PROBE_KEY = ("set_id", "headword", "model_id", "task", "prompt_ver")

SCHEMA = """
-- ---------------------------------------------------------------------------
-- Sampling
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sample_sets (
  set_id      TEXT PRIMARY KEY,
  spec_json   TEXT NOT NULL,
  seed        INTEGER NOT NULL,
  created_at  TEXT NOT NULL,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS samples (
  id            INTEGER PRIMARY KEY,
  set_id        TEXT NOT NULL REFERENCES sample_sets(set_id),
  headword      TEXT NOT NULL,
  layer         TEXT NOT NULL,
  quantile      INTEGER,
  log_freq      REAL,
  n_senses      INTEGER,
  domain        TEXT,
  is_duplicate  INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  UNIQUE (set_id, headword, layer)
);
CREATE INDEX IF NOT EXISTS idx_samples_set_layer ON samples (set_id, layer);
CREATE INDEX IF NOT EXISTS idx_samples_headword ON samples (headword);

-- ---------------------------------------------------------------------------
-- Models and probes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS models (
  model_id    TEXT PRIMARY KEY,
  family      TEXT,
  quant       TEXT,
  path        TEXT,
  sha256      TEXT,
  n_ctx       INTEGER,
  created_at  TEXT NOT NULL,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS probes (
  id               INTEGER PRIMARY KEY,
  set_id           TEXT NOT NULL,
  headword         TEXT NOT NULL,
  model_id         TEXT NOT NULL,
  task             TEXT NOT NULL,
  prompt_ver       TEXT NOT NULL,
  prompt_text      TEXT,
  raw_output       TEXT,
  parsed_json      TEXT,
  tokens_in        INTEGER,
  tokens_out       INTEGER,
  latency_ms       INTEGER,
  avg_logprob      REAL,
  self_consistency REAL,
  created_at       TEXT NOT NULL,
  UNIQUE (set_id, headword, model_id, task, prompt_ver)
);
CREATE INDEX IF NOT EXISTS idx_probes_lookup
  ON probes (set_id, model_id, task, prompt_ver);

-- ---------------------------------------------------------------------------
-- Judging and auditing
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS judgments (
  id             INTEGER PRIMARY KEY,
  probe_id       INTEGER NOT NULL REFERENCES probes(id),
  label          TEXT NOT NULL,
  rule           TEXT,
  matched        TEXT,
  normalizer_ver TEXT,
  judge_ver      TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  UNIQUE (probe_id, judge_ver)
);
CREATE INDEX IF NOT EXISTS idx_judgments_label ON judgments (label);

CREATE TABLE IF NOT EXISTS audit_tasks (
  id          INTEGER PRIMARY KEY,
  probe_id    INTEGER,
  auditor     TEXT,
  status      TEXT NOT NULL DEFAULT 'pending',
  result      TEXT,
  created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_tasks (status);

-- ---------------------------------------------------------------------------
-- Features, predictions and intervention runs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS features (
  set_id        TEXT,
  headword      TEXT,
  model_id      TEXT,
  feature_json  TEXT,
  created_at    TEXT,
  PRIMARY KEY (set_id, headword, model_id)
);

CREATE TABLE IF NOT EXISTS predictions (
  set_id        TEXT,
  model_id      TEXT,
  headword      TEXT,
  prob_missing  REAL,
  predictor_ver TEXT,
  PRIMARY KEY (set_id, model_id, headword)
);

CREATE TABLE IF NOT EXISTS intervention_runs (
  id          INTEGER PRIMARY KEY,
  condition   TEXT,
  corpus_id   TEXT,
  model_id    TEXT,
  sentence_id TEXT,
  raw_output  TEXT,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  latency_ms  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_condition ON intervention_runs (condition, model_id);

-- ---------------------------------------------------------------------------
-- Key/value metadata
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow():
  """Returns the current UTC time as an ISO-8601 string with a trailing Z."""
  return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path=None, readonly=False):
  """Opens a connection with the project's defaults applied.

  Row access is by name and foreign keys are enforced.  When ``readonly`` is
  set the database is opened in SQLite's read-only URI mode, which fails
  instead of silently creating an empty file.
  """
  resolved = config.resolve_db_path(path)
  resolved.parent.mkdir(parents=True, exist_ok=True)
  if readonly:
    uri = "file:{}?mode=ro".format(resolved)
    conn = sqlite3.connect(uri, uri=True)
  else:
    conn = sqlite3.connect(str(resolved))
  conn.row_factory = sqlite3.Row
  conn.execute("PRAGMA foreign_keys = ON")
  return conn


def get_conn(path=None):
  """Returns a connection with the schema guaranteed to exist.

  This is the accessor the rest of the code should use: it is safe to call
  from every entry point, because ``init_db`` is idempotent.
  """
  conn = connect(path)
  init_db(conn=conn)
  return conn


def init_db(path=None, conn=None):
  """Creates the schema if needed and records the schema version.

  Idempotent: safe to call on every process start.  Enables WAL so a reader
  (reporter, exporter) does not block a writer (probe runner).

  :returns: The connection that was initialised, left open for the caller to
      close.  ``get_conn`` is the usual entry point.
  """
  if conn is None:
    conn = connect(path)
  conn.execute("PRAGMA journal_mode = WAL")
  conn.execute("PRAGMA synchronous = NORMAL")
  conn.executescript(SCHEMA)
  current = conn.execute("PRAGMA user_version").fetchone()[0]
  if current != SCHEMA_VERSION:
    conn.execute("PRAGMA user_version = {}".format(SCHEMA_VERSION))
  conn.execute(
      "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
      (str(SCHEMA_VERSION),))
  conn.commit()
  return conn


@contextmanager
def transaction(conn):
  """Runs a block in a single transaction.

  The plan requires one probe per transaction so a failure cannot leave a
  half-written probe behind.  On exception the transaction is rolled back and
  the original exception is re-raised.
  """
  try:
    yield conn
    conn.commit()
  except BaseException:
    conn.rollback()
    raise


def meta_get(conn, key, default=None):
  """Reads a value from the ``meta`` table."""
  row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
  return row["value"] if row else default


def meta_set(conn, key, value):
  """Writes a value to the ``meta`` table, replacing any previous one."""
  conn.execute(
      "INSERT INTO meta (key, value) VALUES (?, ?) "
      "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      (key, str(value)))
  conn.commit()
