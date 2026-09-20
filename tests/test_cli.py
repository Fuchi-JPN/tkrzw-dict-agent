"""Tests for the command line interface."""

import contextlib
import io
import json
import os
import unittest

from tkrzw_dict_agent.cli import main as cli_main

DICT_DIR = os.environ.get(
    "TKRZW_DICT_DIR",
    os.path.join(os.path.dirname(__file__), "..", "tkrzw-dict"))
_HAS_DATA = os.path.exists(os.path.join(DICT_DIR, "union-body.tkh"))


def run_cli(argv):
  """Runs the CLI and returns (exit_code, stdout, stderr)."""
  stdout, stderr = io.StringIO(), io.StringIO()
  with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    try:
      code = cli_main.main(argv)
    except SystemExit as exc:  # argparse errors
      code = exc.code if isinstance(exc.code, int) else 1
  return code, stdout.getvalue(), stderr.getvalue()


class TestParser(unittest.TestCase):

  def test_parser_accepts_commands(self):
    parser = cli_main.build_parser()
    for argv in (["lookup", "word"], ["enrich", "text"], ["extract", "text"]):
      args = parser.parse_args(argv)
      self.assertEqual(args.command, argv[0])

  def test_parser_requires_command(self):
    parser = cli_main.build_parser()
    with self.assertRaises(SystemExit):
      parser.parse_args([])


class TestOutputEncoding(unittest.TestCase):
  """Redirected output must be UTF-8 so agent harnesses can decode it.

  On Windows a piped stdout would otherwise use the ANSI code page and the
  harness would see mojibake.  The contract is verified on the real bytes.
  """

  def test_configure_stdio_encoding_does_not_raise(self):
    # pytest already redirects stdio; the call must be safe on any platform.
    cli_main.configure_stdio_encoding()

  def test_respects_explicit_pythonioencoding(self):
    saved = os.environ.get("PYTHONIOENCODING")
    try:
      os.environ["PYTHONIOENCODING"] = "cp1252"
      cli_main.configure_stdio_encoding()  # must not override the request
      self.assertEqual(os.environ["PYTHONIOENCODING"], "cp1252")
    finally:
      if saved is None:
        os.environ.pop("PYTHONIOENCODING", None)
      else:
        os.environ["PYTHONIOENCODING"] = saved

  @unittest.skipUnless(_HAS_DATA, "dictionary data not present")
  def test_redirected_output_is_utf8_bytes(self):
    import subprocess
    import sys
    process = subprocess.run(
        [sys.executable, "-m", "tkrzw_dict_agent.cli", "lookup", "tightening"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        timeout=120)
    self.assertEqual(process.returncode, 0)
    text = process.stdout.decode("utf-8")   # raises if not valid UTF-8
    self.assertIn("tightening", text)
    self.assertTrue(any("\u3042" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff"
                        for ch in text), text)


@unittest.skipUnless(_HAS_DATA, "dictionary data not present")
class TestCliCommands(unittest.TestCase):

  def test_lookup(self):
    code, out, _ = run_cli(["lookup", "tightening"])
    self.assertEqual(code, 0)
    self.assertIn("tightening", out)

  def test_lookup_json(self):
    code, out, _ = run_cli(["lookup", "tightening", "--json"])
    self.assertEqual(code, 0)
    payload = json.loads(out)
    self.assertTrue(payload["found"])
    self.assertTrue(payload["meanings"])

  def test_lookup_missing_word(self):
    code, _, err = run_cli(["lookup", "zzzznotaword"])
    self.assertEqual(code, 1)
    self.assertIn("not found", err)

  def test_lookup_with_context_shows_domain(self):
    code, out, _ = run_cli(
        ["lookup", "tightening", "--context", "monetary policy inflation"])
    self.assertEqual(code, 0)
    self.assertIn("経済", out)

  def test_enrich(self):
    code, out, _ = run_cli(
        ["enrich", "The Fed's tightening monetary policy surprised the markets."])
    self.assertEqual(code, 0)
    self.assertIn("[参考語彙]", out)
    self.assertIn("tightening:", out)

  def test_enrich_json(self):
    code, out, _ = run_cli(["enrich", "monetary tightening policy", "--json"])
    self.assertEqual(code, 0)
    payload = json.loads(out)
    words = [term["word"] for term in payload["terms"]]
    self.assertIn("tightening", words)

  def test_extract(self):
    code, out, _ = run_cli(
        ["extract", "The central bank announced a tightening of monetary policy."])
    self.assertEqual(code, 0)
    self.assertIn("tightening", out)
    self.assertNotIn("the\n", out)

  def test_extract_json(self):
    code, out, _ = run_cli(
        ["extract", "The central bank announced a tightening of monetary policy.",
         "--json"])
    self.assertEqual(code, 0)
    self.assertIn("tightening", json.loads(out)["terms"])


if __name__ == "__main__":
  unittest.main()
