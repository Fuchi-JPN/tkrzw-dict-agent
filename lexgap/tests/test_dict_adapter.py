"""Tests for the dictionary adapter.

Dictionary-dependent tests are skipped when the data is not present, so the
suite still runs on a machine without the ~900 MB download.
"""

import os
import unittest

from lexgap import config
from lexgap.dict_adapter import PROB_FLOOR, DictAdapter

_BODY = str(config.DEFAULT_DICT_PREFIX) + "-body.tkh"
_KEYS = str(config.DEFAULT_DICT_PREFIX) + "-keys.txt"
_HAS_DATA = os.path.exists(_BODY) and os.path.exists(_KEYS)


class TestNormalizer(unittest.TestCase):

  def test_nfkc_and_width(self):
    from lexgap import normalizer

    self.assertEqual(normalizer.normalize("ＡＢＣ"), "abc")
    self.assertEqual(normalizer.normalize("１２３"), "123")

  def test_kana_unification(self):
    from lexgap import normalizer

    # Katakana and hiragana spellings of the same term normalise together.
    self.assertEqual(
        normalizer.normalize("カタカナ"), normalizer.normalize("かたかな"))

  def test_long_vowel_and_middle_dot_removed(self):
    from lexgap import normalizer

    self.assertEqual(normalizer.normalize("コーヒー"), "こひ")
    self.assertEqual(normalizer.normalize("インター・チェンジ"), "いんたちぇんじ")
    self.assertEqual(normalizer.normalize("インター・チェンジ").count("・"), 0)

  def test_latin_is_case_folded(self):
    from lexgap import normalizer

    self.assertEqual(normalizer.normalize("Tightening"), normalizer.normalize("tightening"))

  def test_punctuation_dropped(self):
    from lexgap import normalizer

    self.assertEqual(normalizer.normalize("引き締め。"), normalizer.normalize("引き締め"))

  def test_normalize_strict_strips_trailing_particle(self):
    from lexgap import normalizer

    self.assertEqual(
        normalizer.normalize_strict("引き締めの"), normalizer.normalize("引き締め"))

  def test_normalize_is_pure(self):
    from lexgap import normalizer

    self.assertEqual(normalizer.normalize("政策"), normalizer.normalize("政策"))
    self.assertEqual(normalizer.normalize(""), "")

  def test_kana_variants_include_both_scripts(self):
    from lexgap import normalizer

    variants = normalizer.kana_variants("カタカナ")
    self.assertTrue(any("\u3042" <= ch <= "\u309f" for v in variants for ch in v))


@unittest.skipUnless(_HAS_DATA, "dictionary data not present")
class TestAdapter(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.adapter = DictAdapter()

  @classmethod
  def tearDownClass(cls):
    cls.adapter.close()

  def test_stats(self):
    stats = self.adapter.stats()
    self.assertEqual(stats["keys"], 499750)
    self.assertEqual(stats["entries"], 499750)

  def test_lookup_found(self):
    records = self.adapter.lookup("tightening")
    self.assertTrue(records)
    record = records[0]
    self.assertTrue(record.word)
    self.assertTrue(record.translations)

  def test_lookup_missing_is_empty(self):
    self.assertEqual(self.adapter.lookup("zzzznotawordqqq"), [])

  def test_lookup_one_returns_none(self):
    self.assertIsNone(self.adapter.lookup_one("zzzznotawordqqq"))

  def test_multi_entry_key(self):
    # "japan" holds both the country and the lacquer.
    records = self.adapter.lookup("japan")
    self.assertGreaterEqual(len(records), 2)
    words = {record.word for record in records}
    self.assertIn("Japan", words)

  def test_gloss_set_spans_senses(self):
    glosses = self.adapter.gloss_set("tightening")
    self.assertTrue(glosses)
    self.assertTrue(all(isinstance(value, str) for value in glosses))

  def test_q01_probability_is_left_censored(self):
    # Q-01: probability has a floor of 1e-07; censored entries expose rank.
    floor_seen = False
    for record in self.adapter.iter_headwords(ascii_only=True):
      if record.probability_floor:
        self.assertEqual(record.prob, 0.0)
        self.assertAlmostEqual(record.log_freq, -7.0, places=6)
        self.assertGreaterEqual(record.freq_rank, 0)
        floor_seen = True
        break
    self.assertTrue(floor_seen, "expected at least one censored entry")

  def test_q01_freq_rank_is_ordered(self):
    # The key list is frequency-ordered, which is why rank is stratified on.
    ranks = []
    for record in self.adapter.iter_headwords(ascii_only=True):
      ranks.append(record.freq_rank)
      if len(ranks) >= 50:
        break
    self.assertEqual(ranks, sorted(ranks))

  def test_q02_examples_are_entry_level(self):
    # Examples carry no sense marker, so they belong to the entry, not a sense.
    checked = 0
    for record in self.adapter.iter_headwords(require_translation=True):
      if record.examples:
        for example in record.examples:
          self.assertEqual(set(example), {"e", "j"})
        checked += 1
      if checked >= 5:
        break
    self.assertGreaterEqual(checked, 1)

  def test_iter_filters(self):
    ascii_only = list(_take(self.adapter.iter_headwords(ascii_only=True), 20))
    for record in ascii_only:
      self.assertTrue(record.word.replace("-", "").isalpha())

    with_translation = list(_take(
        self.adapter.iter_headwords(require_translation=True), 20))
    for record in with_translation:
      self.assertTrue(record.translations)

  def test_senses_are_parsed(self):
    record = self.adapter.lookup_one("bank")
    self.assertGreater(record.n_senses, 1)
    self.assertTrue(any(sense.translations for sense in record.senses))
    self.assertIn(record.pos, record.pos_list)

  def test_record_is_json_serialisable(self):
    import json

    record = self.adapter.lookup_one("bank")
    payload = json.dumps(record.to_dict(), ensure_ascii=False)
    self.assertIn("bank", payload)

  def test_prob_floor_constant(self):
    self.assertEqual(PROB_FLOOR, 1e-07)


def _take(iterator, limit):
  for index, value in enumerate(iterator):
    if index >= limit:
      return
    yield value


if __name__ == "__main__":
  unittest.main()
