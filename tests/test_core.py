"""Tests for the core agent layer: normalizer, scorer, dictionary, query."""

import os
import unittest

from tkrzw_dict_agent.core import db, normalizer, query, scorer

DICT_DIR = os.environ.get(
    "TKRZW_DICT_DIR",
    os.path.join(os.path.dirname(__file__), "..", "tkrzw-dict"))

_HAS_DATA = os.path.exists(os.path.join(DICT_DIR, "union-body.tkh"))


@unittest.skipUnless(_HAS_DATA, "dictionary data not present")
class DictionaryTestCase(unittest.TestCase):
  """Base class that opens one shared dictionary for the whole test class."""

  @classmethod
  def setUpClass(cls):
    if not _HAS_DATA:
      raise unittest.SkipTest("dictionary data not present")
    cls.dictionary = db.open_dictionary()
    cls.query = query.VocabularyQuery(cls.dictionary)

  @classmethod
  def tearDownClass(cls):
    cls.dictionary.close()


class TestNormalizer(unittest.TestCase):

  def test_normalize_word_delegates_to_engine(self):
    self.assertEqual(normalizer.normalize_word("Tightening"), "tightening")
    self.assertEqual(normalizer.normalize_word("  Hello  "), "hello")
    self.assertEqual(normalizer.normalize_word(""), "")

  def test_predict_language(self):
    self.assertEqual(normalizer.predict_language("tightening monetary policy"), "en")
    self.assertEqual(normalizer.predict_language("金融引き締め政策"), "ja")
    self.assertEqual(normalizer.predict_language(""), "other")

  def test_extract_words(self):
    words = normalizer.extract_words("The Fed's tightening policy.")
    surfaces = [surface for surface, _ in words]
    self.assertEqual(surfaces, ["The", "Fed's", "tightening", "policy"])
    self.assertEqual(words[0][1], 0)

  def test_extract_words_offsets(self):
    text = "A tightening policy"
    words = normalizer.extract_words(text)
    for surface, offset in words:
      self.assertEqual(text[offset:offset + len(surface)], surface)

  def test_split_sentences(self):
    sentences = normalizer.split_sentences("One. Two! Three?")
    self.assertEqual(len(sentences), 3)
    self.assertTrue(sentences[0].endswith("."))

  def test_stop_words_are_broader_than_engine(self):
    for word in ("the", "of", "and", "in", "to", "for", "with", "that"):
      self.assertTrue(normalizer.is_stop_word("en", word), word)
    self.assertFalse(normalizer.is_stop_word("en", "tightening"))
    self.assertFalse(normalizer.is_stop_word("en", "monetary"))

  def test_is_numeric_word(self):
    self.assertTrue(normalizer.is_numeric_word("123"))
    self.assertFalse(normalizer.is_numeric_word("abc"))

  def test_english_ratio(self):
    self.assertGreater(normalizer.english_ratio("hello world"), 0.9)
    self.assertLess(normalizer.english_ratio("こんにちは世界"), 0.3)


class TestScorer(DictionaryTestCase):

  def test_base_frequency_is_bounded(self):
    entry = self.dictionary.lookup("japan")[0]
    value = scorer.Scorer(self.dictionary).base_frequency(entry)
    self.assertGreaterEqual(value, 0.0)
    self.assertLessEqual(value, 1.0)

  def test_common_word_scores_above_rare_word(self):
    s = scorer.Scorer(self.dictionary)
    common = s.base_frequency(self.dictionary.lookup("japan")[0])
    rare = s.base_frequency(self.dictionary.lookup("tightening")[0])
    self.assertGreater(common, rare)

  def test_domain_match_zero_without_context(self):
    entry = self.dictionary.lookup("tightening")[0]
    self.assertEqual(scorer.Scorer(self.dictionary).domain_match(entry, []), 0.0)

  def test_domain_guess_economics(self):
    entry = self.dictionary.lookup("tightening")[0]
    self.assertEqual(scorer.guess_domain(entry), "経済")

  def test_domain_guess_from_definitions(self):
    # Labels come from the sense definitions, not the noisy related lists.
    self.assertEqual(scorer.guess_domain(self.dictionary.lookup("doctor")[0]), "医療")
    self.assertEqual(scorer.guess_domain(self.dictionary.lookup("server")[0]), "IT")
    self.assertEqual(scorer.guess_domain(self.dictionary.lookup("algorithm")[0]), "IT")

  def test_domain_guess_falls_back_to_general(self):
    self.assertEqual(scorer.guess_domain({"related": ["xyzzy"], "cooccurrence": []}), "一般")

  def test_entry_features_and_cosine(self):
    from tkrzw_dict_agent.core import scorer as scorer_module
    entry = self.dictionary.lookup("tightening")[0]
    features = scorer_module.build_entry_features(entry)
    self.assertIn("tightening", features)
    self.assertAlmostEqual(
        scorer_module._cosine_similarity(features, features), 1.0, places=6)

  def test_rank_entries_orders_by_score(self):
    entries = self.dictionary.lookup("japan")
    ranked = scorer.Scorer(self.dictionary).rank_entries(entries, {}, [])
    scores = [score for _, score in ranked]
    self.assertEqual(scores, sorted(scores, reverse=True))


class TestQuery(DictionaryTestCase):

  def test_lookup_found(self):
    result = self.query.lookup("tightening")
    self.assertTrue(result["found"])
    self.assertIn("tightening", result["word"])
    self.assertTrue(result["translations"])
    self.assertGreater(result["probability"], 0.0)

  def test_lookup_missing(self):
    result = self.query.lookup("zzzznotaword")
    self.assertFalse(result["found"])
    self.assertEqual(result["translations"], [])

  def test_lookup_resolves_inflection(self):
    result = self.query.lookup("ran")
    self.assertTrue(result["found"])
    self.assertEqual(result["word"].lower(), "run")

  def test_resolve_returns_ranked_meanings(self):
    meanings = self.query.resolve("tightening", "The Fed tightened monetary policy.")
    self.assertTrue(meanings)
    scores = [meaning["score"] for meaning in meanings]
    self.assertEqual(scores, sorted(scores, reverse=True))
    for meaning in meanings:
      self.assertIn("ja", meaning)
      self.assertIn("domain", meaning)
      self.assertGreaterEqual(meaning["score"], 0.0)
      self.assertLessEqual(meaning["score"], 1.0)

  def test_resolve_empty_for_unknown(self):
    self.assertEqual(self.query.resolve("zzzznotaword"), [])

  def test_context_raises_score(self):
    without = self.query.resolve("tightening")
    with_context = self.query.resolve(
        "tightening", "The Fed's tightening monetary policy slowed inflation.")
    self.assertGreater(with_context[0]["score"], without[0]["score"])

  def test_context_disambiguates_senses(self):
    # "bank" bundles the financial and the river sense in one record; the
    # surrounding context must decide which translations come first.
    river = [m["ja"] for m in self.query.resolve(
        "bank", "The river bank was eroded by the flood.", max_meanings=6)]
    money = [m["ja"] for m in self.query.resolve(
        "bank", "The central bank raised interest rates.", max_meanings=6)]
    river_river_sense = [t for t in river if t in ("土手", "岸辺", "堤防", "岸", "バンク")]
    money_money_sense = [t for t in money if t in ("銀行", "バンク")]
    self.assertTrue(river_river_sense, river)
    self.assertTrue(money_money_sense, money)
    # The financial translation must not lead the river reading.
    self.assertNotEqual(river[0], "銀行")
    # And the financial reading must lead the money context.
    self.assertEqual(money[0], "銀行")

  def test_enrich_structure(self):
    result = self.query.enrich("The Fed's tightening monetary policy surprised markets.")
    self.assertIn("terms", result)
    words = [term["word"] for term in result["terms"]]
    self.assertIn("tightening", words)
    for term in result["terms"]:
      self.assertTrue(term["candidates"])

  def test_enrich_skips_stop_words(self):
    result = self.query.enrich("The tightening of the policy and the market")
    words = [term["word"].lower() for term in result["terms"]]
    self.assertNotIn("the", words)
    self.assertNotIn("and", words)
    self.assertNotIn("of", words)

  def test_enrich_empty_text(self):
    self.assertEqual(self.query.enrich(""), {"terms": []})

  def test_extract_terms_ranks_rare_content_words(self):
    terms = self.query.extract_terms(
        "The central bank announced a tightening of monetary policy.")
    self.assertTrue(terms)
    self.assertNotIn("the", terms)
    self.assertNotIn("of", terms)

  def test_build_context_features_uses_known_words(self):
    features = query.build_context_features(
        self.dictionary, "monetary policy tightening")
    self.assertIn("monetary", features)
    self.assertNotIn("zzzznotaword", features)


if __name__ == "__main__":
  unittest.main()


class TestSidecarFacade(unittest.TestCase):
  """The public facade opens the shipped dictionary itself."""

  def test_facade_operations(self):
    from tkrzw_dict_agent import VocabularySidecar
    with VocabularySidecar() as sidecar:
      self.assertTrue(sidecar.lookup("tightening")["found"])
      self.assertTrue(sidecar.resolve("tightening", "Fed policy"))
      self.assertIn(
          "tightening",
          [t["word"] for t in sidecar.enrich("Fed tightening policy.")["terms"]])
      self.assertTrue(sidecar.extract_terms("The central bank tightened policy."))


class TestGlossExpansion(DictionaryTestCase):
  """Synonym-linked gloss expansion (used by LexGap's judge)."""

  def test_gloss_set_covers_entry_and_senses(self):
    glosses = self.dictionary.gloss_set("tightening")
    self.assertTrue(glosses)
    self.assertTrue(all(isinstance(g, str) for g in glosses))

  def test_gloss_set_respects_limit(self):
    self.assertLessEqual(len(self.dictionary.gloss_set("bank", limit=3)), 3)

  def test_synonyms_are_headwords(self):
    synonyms = self.dictionary.synonyms("rogue")
    self.assertTrue(synonyms)
    self.assertNotIn("rogue", [s.lower() for s in synonyms])

  def test_synonyms_respect_limit(self):
    self.assertLessEqual(len(self.dictionary.synonyms("bank", limit=4)), 4)

  def test_expand_glosses_is_a_superset(self):
    base = set(self.dictionary.gloss_set("bank"))
    expanded = set(self.dictionary.expand_glosses("bank"))
    self.assertTrue(base.issubset(expanded))
    self.assertGreater(len(expanded), len(base))

  def test_expand_glosses_respects_extra_limit(self):
    # extra_limit bounds the synonym-derived additions, not the base glosses.
    base = len(self.dictionary.gloss_set("bank"))
    expanded = self.dictionary.expand_glosses("bank", extra_limit=10)
    self.assertLessEqual(len(expanded), base + 10)
    self.assertGreaterEqual(len(expanded), base)

  def test_extract_sense_translations(self):
    from tkrzw_dict_agent.core import db as db_module

    text = "a sense [-] [translation]: 締め付け, 引き締め [-] [synonym]: tight"
    self.assertEqual(
        db_module.extract_sense_translations(text), ["締め付け", "引き締め"])
    self.assertEqual(db_module.extract_sense_translations("no markers"), [])
    self.assertEqual(db_module.extract_sense_translations(None), [])
