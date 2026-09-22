"""Tests for the SQLite layer."""

import os
import tempfile
import unittest

from lexgap import db


class TestSchema(unittest.TestCase):

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.path = os.path.join(self.tmp.name, "test.db")

  def tearDown(self):
    self.tmp.cleanup()

  def test_init_creates_expected_tables(self):
    conn = db.init_db(self.path)
    names = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in ("sample_sets", "samples", "models", "probes", "judgments",
                     "audit_tasks", "features", "predictions",
                     "intervention_runs", "meta"):
      self.assertIn(expected, names)
    conn.close()

  def test_init_is_idempotent(self):
    conn = db.init_db(self.path)
    conn.close()
    conn = db.init_db(self.path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    self.assertEqual(version, db.SCHEMA_VERSION)
    conn.close()

  def test_wal_mode_is_enabled(self):
    conn = db.init_db(self.path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    self.assertEqual(mode.lower(), "wal")
    conn.close()

  def test_foreign_keys_are_enforced(self):
    conn = db.get_conn(self.path)
    conn.execute("INSERT INTO sample_sets VALUES ('S1', '{}', 1, 'now', NULL)")
    with self.assertRaises(Exception):
      conn.execute(
          "INSERT INTO samples (set_id, headword, layer, created_at) "
          "VALUES ('MISSING', 'w', 'main', 'now')")
    conn.close()


class TestProbeCache(unittest.TestCase):
  """The UNIQUE constraint on probes is the probe cache."""

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.conn = db.get_conn(os.path.join(self.tmp.name, "test.db"))

  def tearDown(self):
    self.conn.close()
    self.tmp.cleanup()

  def _insert_probe(self, raw="out"):
    return self.conn.execute(
        "INSERT INTO probes (set_id, headword, model_id, task, prompt_ver, "
        "raw_output, created_at) VALUES (?,?,?,?,?,?,?)",
        ("S1", "bank", "m1", "P1", "p1_v1", raw, db.utcnow()))

  def test_duplicate_probe_is_rejected(self):
    self._insert_probe()
    self.conn.commit()
    with self.assertRaises(Exception):
      self._insert_probe("other")

  def test_same_word_different_prompt_version_is_allowed(self):
    self._insert_probe()
    self.conn.execute(
        "INSERT INTO probes (set_id, headword, model_id, task, prompt_ver, "
        "raw_output, created_at) VALUES (?,?,?,?,?,?,?)",
        ("S1", "bank", "m1", "P1", "p1_v2", "out", db.utcnow()))
    count = self.conn.execute("SELECT COUNT(*) FROM probes").fetchone()[0]
    self.assertEqual(count, 2)


class TestTransaction(unittest.TestCase):

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.conn = db.get_conn(os.path.join(self.tmp.name, "test.db"))

  def tearDown(self):
    self.conn.close()
    self.tmp.cleanup()

  def test_rollback_on_error(self):
    # A probe write is one transaction; a failure must not leave a partial row.
    try:
      with db.transaction(self.conn):
        self.conn.execute(
            "INSERT INTO probes (set_id, headword, model_id, task, prompt_ver, "
            "created_at) VALUES ('S1','bank','m1','P1','p1_v1','now')")
        raise RuntimeError("probe failed")
    except RuntimeError:
      pass
    count = self.conn.execute("SELECT COUNT(*) FROM probes").fetchone()[0]
    self.assertEqual(count, 0)

  def test_commit_on_success(self):
    with db.transaction(self.conn):
      self.conn.execute(
          "INSERT INTO probes (set_id, headword, model_id, task, prompt_ver, "
          "created_at) VALUES ('S1','bank','m1','P1','p1_v1','now')")
    count = self.conn.execute("SELECT COUNT(*) FROM probes").fetchone()[0]
    self.assertEqual(count, 1)


class TestMeta(unittest.TestCase):

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.conn = db.get_conn(os.path.join(self.tmp.name, "test.db"))

  def tearDown(self):
    self.conn.close()
    self.tmp.cleanup()

  def test_set_and_get(self):
    db.meta_set(self.conn, "answer", 42)
    self.assertEqual(db.meta_get(self.conn, "answer"), "42")

  def test_set_replaces(self):
    db.meta_set(self.conn, "k", "a")
    db.meta_set(self.conn, "k", "b")
    self.assertEqual(db.meta_get(self.conn, "k"), "b")

  def test_default(self):
    self.assertIsNone(db.meta_get(self.conn, "absent"))
    self.assertEqual(db.meta_get(self.conn, "absent", "fallback"), "fallback")

  def test_schema_version_recorded(self):
    self.assertEqual(db.meta_get(self.conn, "schema_version"),
                     str(db.SCHEMA_VERSION))


if __name__ == "__main__":
  unittest.main()
