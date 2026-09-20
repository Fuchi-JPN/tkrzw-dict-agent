"""Tests for the pure-Python tkrzw replacement.

The database tests run against the real union dictionary data, which is the
only configuration the project ships: pure Python, no C++ extension.
"""

import os
import unittest

from tkrzw_dict_agent.core import pytkrzw

DICT_DIR = os.environ.get(
    "TKRZW_DICT_DIR", os.path.join(os.path.dirname(__file__), "..", "tkrzw-dict"))


class TestMurmurHash(unittest.TestCase):

  def test_known_values(self):
    # Cross-checked against tkrzw.Utility.PrimaryHash in the C++ binding.
    cases = [
        (b"", 0x55B11E5415198C8D),
        (b"a", 0x9244A1E6E9FAA9BF),
        (b"hello", 0x38C5E7D713FD6BD6),
        (b"tightening", 0xCEA9B7026C7E5B82),
        (b"0123456789abcdef", 0x851C8A4996F181DC),
    ]
    for data, expected in cases:
      self.assertEqual(pytkrzw._murmur(data, 19780211), expected)

  def test_primary_hash_folding(self):
    # A bucket count under 2^32 exercises the folding path.
    h1 = pytkrzw.Utility.PrimaryHash(b"tightening", 1000545)
    h2 = pytkrzw.Utility.PrimaryHash(b"tightening", 1000545)
    self.assertEqual(h1, h2)
    self.assertLess(h1, 1000545)
    self.assertEqual(pytkrzw.Utility.PrimaryHash(b"abc", 1), 0)


class TestEditDistance(unittest.TestCase):

  def test_basic(self):
    self.assertEqual(pytkrzw.Utility.EditDistanceLev("", ""), 0)
    self.assertEqual(pytkrzw.Utility.EditDistanceLev("abc", "abc"), 0)
    self.assertEqual(pytkrzw.Utility.EditDistanceLev("abc", "abd"), 1)
    self.assertEqual(pytkrzw.Utility.EditDistanceLev("kitten", "sitting"), 3)
    self.assertEqual(pytkrzw.Utility.EditDistanceLev("run", "ran"), 1)

  def test_symmetric(self):
    for a, b in [("tightening", "tighten"), ("政策", "市場"), ("abc", "xyz")]:
      self.assertEqual(
          pytkrzw.Utility.EditDistanceLev(a, b),
          pytkrzw.Utility.EditDistanceLev(b, a))


class TestHashDBM(unittest.TestCase):

  def setUp(self):
    self.path = os.path.join(DICT_DIR, "union-body.tkh")
    if not os.path.exists(self.path):
      self.skipTest("dictionary data not present: " + self.path)
    self.dbm = pytkrzw.DBM()
    self.dbm.Open(self.path, False, dbm="HashDBM").OrDie()

  def tearDown(self):
    self.dbm.Close()

  def test_header_fields(self):
    self.assertEqual(self.dbm.Count(), 499750)
    self.assertGreater(self.dbm.GetFileSize(), 600 * 1000 * 1000)
    self.assertEqual(self.dbm.GetPath(), self.path)

  def test_exact_lookup(self):
    value = self.dbm.GetStr("tightening")
    self.assertIsNotNone(value)
    self.assertIn("締め付け", value)

  def test_missing_key_returns_none(self):
    self.assertIsNone(self.dbm.GetStr("zzzznotpresent"))
    status = pytkrzw.Status()
    self.dbm.Get("zzzznotpresent", status)
    self.assertEqual(status.GetCode(), pytkrzw.NOT_FOUND_ERROR)

  def test_uncompressed_header_is_rejected_when_compressed(self):
    # The shipped databases are uncompressed; a bogus static-flags byte that
    # advertises compression must fail cleanly instead of returning garbage.
    dbm = pytkrzw.DBM()
    status = dbm.Open(self.path, False, dbm="HashDBM")
    self.assertTrue(status.IsOK())
    dbm.Close()


class TestFileSearch(unittest.TestCase):

  def setUp(self):
    self.path = os.path.join(DICT_DIR, "union-keys.txt")
    if not os.path.exists(self.path):
      self.skipTest("dictionary data not present: " + self.path)
    self.file = pytkrzw.File()
    self.file.Open(self.path, False).OrDie()

  def tearDown(self):
    self.file.Close()

  def test_begin(self):
    lines = self.file.Search("begin", "tighten", 10)
    self.assertTrue(lines)
    self.assertTrue(all(l.startswith("tighten") for l in lines))

  def test_contain(self):
    lines = self.file.Search("contain", "policy", 10)
    self.assertTrue(all("policy" in l for l in lines))

  def test_end(self):
    lines = self.file.Search("end", "ing", 10)
    self.assertTrue(all(l.endswith("ing") for l in lines))

  def test_regex(self):
    lines = self.file.Search("regex", r"^tighten", 10)
    self.assertTrue(all(l.startswith("tighten") for l in lines))

  def test_edit_returns_closest(self):
    lines = self.file.Search("edit", "tightening", 5)
    self.assertEqual(lines[0], "tightening")

  def test_capacity_is_respected(self):
    self.assertEqual(len(self.file.Search("begin", "t", 7)), 7)

  def test_batch_modes(self):
    lines = self.file.Search("containcaseword*", "tightening\ntightenings", 20)
    self.assertTrue(lines)
    self.assertTrue(
        any("tightening" in l.lower() for l in lines))

  def test_unknown_mode(self):
    with self.assertRaises(RuntimeError):
      self.file.Search("nosuchmode", "x", 10)


class TestStatus(unittest.TestCase):

  def test_ordie_success(self):
    self.assertIsNone(pytkrzw.Status().OrDie())

  def test_ordie_failure(self):
    with self.assertRaises(RuntimeError):
      pytkrzw.Status(pytkrzw.NOT_FOUND_ERROR, "missing").OrDie()


if __name__ == "__main__":
  unittest.main()
