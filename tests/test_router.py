"""Tests for the optional FastAPI router.

Skipped when FastAPI is not installed, since it is an optional extra.
"""

import json
import os
import unittest

try:
  from fastapi.testclient import TestClient
  _HAS_FASTAPI = True
except ImportError:  # pragma: no cover - exercised only without the extra
  _HAS_FASTAPI = False

DICT_DIR = os.environ.get(
    "TKRZW_DICT_DIR",
    os.path.join(os.path.dirname(__file__), "..", "tkrzw-dict"))
_HAS_DATA = os.path.exists(os.path.join(DICT_DIR, "union-body.tkh"))


@unittest.skipUnless(_HAS_FASTAPI, "fastapi is not installed")
@unittest.skipUnless(_HAS_DATA, "dictionary data not present")
class TestRouter(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    from tkrzw_dict_agent.agent_api import router as router_module
    cls.app = router_module.create_app()
    cls.client = TestClient(cls.app)

  def test_health(self):
    response = self.client.get("/health")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["tool"], "vocabulary_support")

  def test_content_type_declares_utf8(self):
    # A client that reads raw bytes must be able to learn the encoding from
    # the header instead of guessing the console code page.
    response = self.client.post("/lookup", json={"word": "tightening"})
    self.assertEqual(response.status_code, 200)
    self.assertIn("charset=utf-8", response.headers["content-type"].lower())

  def test_japanese_round_trips_as_utf8(self):
    response = self.client.post(
        "/lookup", json={"word": "bank",
                         "context": "The river bank was eroded by the flood."})
    payload = json.loads(response.content.decode("utf-8"))
    self.assertTrue(payload["found"])
    meanings = [m["ja"] for m in payload["meanings"]]
    self.assertTrue(any("\u5ddd" in m or "\u571f\u624b" in m or "\u5cb8" in m
                        for m in meanings), meanings)

  def test_lookup(self):
    response = self.client.post(
        "/lookup", json={"word": "tightening", "context": "Fed monetary policy"})
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertTrue(payload["found"])
    self.assertTrue(payload["meanings"])

  def test_lookup_validation(self):
    self.assertEqual(self.client.post("/lookup", json={}).status_code, 400)

  def test_enrich(self):
    response = self.client.post(
        "/enrich", json={"text": "The Fed's tightening monetary policy surprised markets."})
    self.assertEqual(response.status_code, 200)
    words = [term["word"] for term in response.json()["terms"]]
    self.assertIn("tightening", words)

  def test_extract(self):
    response = self.client.post(
        "/extract", json={"text": "The central bank announced a tightening of monetary policy."})
    self.assertEqual(response.status_code, 200)
    self.assertIn("tightening", response.json()["terms"])

  def test_extract_validation(self):
    self.assertEqual(self.client.post("/extract", json={"text": ""}).status_code, 400)


if __name__ == "__main__":
  unittest.main()
