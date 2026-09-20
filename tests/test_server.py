"""Tests for the HTTP server entry point (``dict-server``).

These tests do not start a server; they verify the configuration surface and
that the ASGI factory the server targets is importable and correct.
"""

import importlib
import os
import unittest

from tkrzw_dict_agent.agent_api import server


class TestServerParser(unittest.TestCase):

  def test_defaults_bind_all_interfaces(self):
    # Other hosts must be able to reach the endpoint out of the box.
    args = server.build_parser().parse_args([])
    self.assertEqual(args.host, server.DEFAULT_HOST)
    self.assertEqual(args.host, "0.0.0.0")
    self.assertEqual(args.port, server.DEFAULT_PORT)
    self.assertEqual(args.workers, 1)
    self.assertEqual(args.log_level, "info")

  def test_explicit_host_and_port(self):
    args = server.build_parser().parse_args(
        ["--host", "127.0.0.1", "--port", "9000", "--workers", "4"])
    self.assertEqual(args.host, "127.0.0.1")
    self.assertEqual(args.port, 9000)
    self.assertEqual(args.workers, 4)

  def test_environment_overrides(self):
    saved = os.environ.copy()
    try:
      os.environ["TKRZW_DICT_HOST"] = "10.0.0.5"
      os.environ["TKRZW_DICT_PORT"] = "8123"
      args = server.build_parser().parse_args([])
      self.assertEqual(args.host, "10.0.0.5")
      self.assertEqual(args.port, 8123)
    finally:
      os.environ.clear()
      os.environ.update(saved)

  def test_command_line_beats_environment(self):
    saved = os.environ.copy()
    try:
      os.environ["TKRZW_DICT_HOST"] = "10.0.0.5"
      args = server.build_parser().parse_args(["--host", "192.168.1.1"])
      self.assertEqual(args.host, "192.168.1.1")
    finally:
      os.environ.clear()
      os.environ.update(saved)

  def test_data_prefix_option(self):
    args = server.build_parser().parse_args(["--data-prefix", "/tmp/union"])
    self.assertEqual(args.data_prefix, "/tmp/union")


class TestServerTarget(unittest.TestCase):

  def test_app_import_string_points_at_a_factory(self):
    module_name, attribute = server.APP_IMPORT_STRING.split(":")
    self.assertEqual(module_name, "tkrzw_dict_agent.agent_api.router")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    self.assertTrue(callable(factory))

  def test_module_is_runnable(self):
    # ``python -m tkrzw_dict_agent.agent_api`` must find a main().
    package_dir = os.path.dirname(server.__file__)
    self.assertTrue(os.path.exists(os.path.join(package_dir, "__main__.py")))
    self.assertTrue(callable(server.main))


if __name__ == "__main__":
  unittest.main()
