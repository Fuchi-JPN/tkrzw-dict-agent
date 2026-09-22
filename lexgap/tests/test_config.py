"""Tests for configuration loading and path resolution."""

import os
import tempfile
import unittest
from pathlib import Path

from lexgap import config


class TestPaths(unittest.TestCase):

  def test_root_is_the_lexgap_project(self):
    self.assertEqual(config.LEXGAP_ROOT.name, "lexgap")
    self.assertTrue((config.LEXGAP_ROOT / "lexgap" / "config.py").exists())
    self.assertEqual(config.SPECS_DIR, config.LEXGAP_ROOT / "specs")

  def test_default_db_is_under_data(self):
    self.assertEqual(config.DEFAULT_DB_PATH, config.DATA_DIR / "lexgap.db")

  def test_resolve_db_path_default(self):
    self.assertEqual(config.resolve_db_path(None), config.DEFAULT_DB_PATH)

  def test_resolve_db_path_relative_to_root(self):
    self.assertEqual(
        config.resolve_db_path("data/other.db"),
        config.LEXGAP_ROOT / "data" / "other.db")

  def test_resolve_db_path_absolute_is_kept(self):
    self.assertEqual(
        config.resolve_db_path("/tmp/lexgap.db"), Path("/tmp/lexgap.db"))


class TestDictPrefix(unittest.TestCase):

  def test_environment_prefix_wins(self):
    saved = os.environ.copy()
    try:
      os.environ["TKRZW_DICT_PREFIX"] = "/tmp/union"
      self.assertEqual(config.dict_prefix(), Path("/tmp/union"))
    finally:
      os.environ.clear()
      os.environ.update(saved)

  def test_environment_dir_falls_back_to_union(self):
    saved = os.environ.copy()
    try:
      os.environ.pop("TKRZW_DICT_PREFIX", None)
      os.environ["TKRZW_DICT_DIR"] = "/tmp/dict"
      self.assertEqual(config.dict_prefix(), Path("/tmp/dict") / "union")
    finally:
      os.environ.clear()
      os.environ.update(saved)

  def test_default_points_at_the_sibling_checkout(self):
    self.assertEqual(config.DEFAULT_DICT_PREFIX.name, "union")
    self.assertEqual(config.DEFAULT_DICT_PREFIX.parent.name, "tkrzw-dict")


class TestTomlLoading(unittest.TestCase):

  def test_missing_file_raises(self):
    with self.assertRaises(config.ConfigError):
      config.load_toml("configs/does-not-exist.toml")

  def test_malformed_file_raises(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "broken.toml"
      path.write_text("this is not = = toml", encoding="utf-8")
      with self.assertRaises(config.ConfigError):
        config.load_toml(path)

  def test_loads_a_valid_file(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "ok.toml"
      path.write_text("[a]\nb = 1\n", encoding="utf-8")
      self.assertEqual(config.load_toml(path)["a"]["b"], 1)

  def test_s1_spec_is_loadable_and_complete(self):
    spec = config.load_spec("s1")
    self.assertEqual(spec["set"]["id"], "S1")
    self.assertEqual(spec["main"]["n_quantiles"], 20)
    self.assertEqual(spec["main"]["per_quantile"], 100)
    self.assertTrue(spec["main"]["require_translation"])

  def test_runners_config_is_loadable(self):
    runners = config.load_runners()
    local = runners["runner"]["local"]
    self.assertEqual(local["backend"], "llama.cpp")
    self.assertTrue(local["capabilities"]["supports_logprobs"])

  def test_models_config_is_loadable(self):
    models = config.load_models()
    self.assertIn("models", models)
    roles = {entry.get("role") for entry in models["models"].values()}
    self.assertIn("primary", roles)


if __name__ == "__main__":
  unittest.main()
