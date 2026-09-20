"""Tests for the agent integration surface: tool schema, handler, pipelines."""

import json
import os
import unittest

from tkrzw_dict_agent import agent_api
from tkrzw_dict_agent.agent_api import handler
from tkrzw_dict_agent.pipelines import enrich as enrich_pipeline
from tkrzw_dict_agent.pipelines import extract_terms as extract_pipeline

DICT_DIR = os.environ.get(
    "TKRZW_DICT_DIR",
    os.path.join(os.path.dirname(__file__), "..", "tkrzw-dict"))
_HAS_DATA = os.path.exists(os.path.join(DICT_DIR, "union-body.tkh"))


class TestToolSchema(unittest.TestCase):

  def test_schema_shape(self):
    schema = handler.load_tool_schema()
    self.assertEqual(schema["name"], "vocabulary_support")
    self.assertIn("description", schema)
    parameters = schema["parameters"]
    self.assertEqual(parameters["type"], "object")
    self.assertIn("text", parameters["properties"])
    self.assertEqual(parameters["required"], ["text"])

  def test_schema_is_valid_json_on_disk(self):
    path = os.path.join(
        os.path.dirname(handler.__file__), "tool_schema.json")
    with open(path, encoding="utf-8") as schema_file:
      json.load(schema_file)

  def test_agent_api_exports_expected_names(self):
    self.assertEqual(agent_api.TOOL_NAME, "vocabulary_support")
    self.assertTrue(callable(agent_api.build_router))
    self.assertTrue(callable(agent_api.create_app))


@unittest.skipUnless(_HAS_DATA, "dictionary data not present")
class TestHandler(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.handler = handler.VocabularySupportHandler()

  @classmethod
  def tearDownClass(cls):
    cls.handler.close()

  def test_handle_requires_text(self):
    with self.assertRaises(ValueError):
      self.handler.handle({})
    with self.assertRaises(TypeError):
      self.handler.handle("not a dict")

  def test_handle_enriches_text(self):
    result = self.handler.handle(
        {"text": "The Fed's tightening monetary policy surprised the markets."})
    self.assertIn("terms", result)
    self.assertIn("hint", result)
    words = [term["word"] for term in result["terms"]]
    self.assertIn("tightening", words)
    self.assertIn("[参考語彙]", result["hint"])

  def test_handle_single_word_with_context(self):
    result = self.handler.handle({
        "text": "The Fed's tightening monetary policy",
        "word": "tightening",
    })
    self.assertEqual(len(result["terms"]), 1)
    self.assertEqual(result["terms"][0]["word"], "tightening")
    self.assertTrue(result["terms"][0]["candidates"])
    # The rendered hint carries the domain label of the specification.
    self.assertIn("（経済）", result["hint"])

  def test_handle_unknown_text(self):
    result = self.handler.handle({"text": "zzzznotawordqqqq"})
    self.assertEqual(result["terms"], [])
    self.assertEqual(result["hint"], "")

  def test_handle_max_terms_is_clamped(self):
    result = self.handler.handle(
        {"text": "The Fed's tightening monetary policy surprised the markets.",
         "max_terms": 2})
    self.assertLessEqual(len(result["terms"]), 2)

  def test_handle_json_round_trip(self):
    payload = json.dumps({"text": "monetary tightening policy", "word": "tightening"})
    result = self.handler.handle_json(payload)
    self.assertEqual(result["terms"][0]["word"], "tightening")


@unittest.skipUnless(_HAS_DATA, "dictionary data not present")
class TestPipelines(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    from tkrzw_dict_agent.core import db
    cls.dictionary = db.open_dictionary()

  @classmethod
  def tearDownClass(cls):
    cls.dictionary.close()

  def test_enrich_text_and_format(self):
    enrichment = enrich_pipeline.enrich_text(
        "The Fed's tightening monetary policy surprised the markets.",
        self.dictionary)
    self.assertIn("tightening", enrichment.words)
    hint = enrich_pipeline.format_vocabulary_hints(enrichment)
    self.assertTrue(hint.startswith("[参考語彙]"))
    self.assertIn("tightening:", hint)

  def test_extract_document_terms(self):
    terms = extract_pipeline.extract_document_terms(
        "The central bank announced a tightening of monetary policy. "
        "Inflation rose and interest rates climbed.",
        self.dictionary)
    self.assertTrue(terms)
    self.assertNotIn("the", terms)

  def test_unknown_word_ratio(self):
    ratio = extract_pipeline.unknown_word_ratio(
        "the monetary policy", self.dictionary)
    self.assertLess(ratio, 1.0)
    ratio = extract_pipeline.unknown_word_ratio(
        "zzzznotawordqqqq yyyanotherfake", self.dictionary)
    self.assertEqual(ratio, 1.0)

  def test_should_fire_on_english_text(self):
    self.assertTrue(extract_pipeline.should_fire(
        "The zzzznotawordqqqq phenomenon affected the yyyfake metric.",
        self.dictionary))
    self.assertFalse(extract_pipeline.should_fire(
        "金融引き締め政策", self.dictionary))

  def test_should_fire_on_domain_signal(self):
    # The words are all in the dictionary, but the finance domain is a signal.
    self.assertTrue(extract_pipeline.should_fire(
        "The Fed tightened monetary policy as inflation and interest rates rose.",
        self.dictionary))

  def test_detect_domains(self):
    self.assertIn("economics", extract_pipeline.detect_domains(
        "The central bank raised interest rates to control inflation."))
    self.assertIn("medicine", extract_pipeline.detect_domains(
        "The doctor diagnosed the infection and prescribed a drug for the patient."))
    self.assertEqual(extract_pipeline.detect_domains("hello there friend"), [])


if __name__ == "__main__":
  unittest.main()
