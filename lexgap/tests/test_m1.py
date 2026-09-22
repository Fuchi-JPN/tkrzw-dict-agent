"""Tests for the M1 pipeline: sampler, prompter, judge, reporter, audit.

These tests use no network.  Anything that needs the dictionary is skipped
when the data is absent, so the suite still runs on a bare checkout.
"""

import json
import os
import tempfile
import unittest

from lexgap import audit, db as db_module, judge, prompter, reporter, sampler
from lexgap.dict_adapter import DictAdapter

_BODY = str(__import__("lexgap.config", fromlist=["config"]).DEFAULT_DICT_PREFIX) + "-body.tkh"
_HAS_DATA = os.path.exists(_BODY)


class TestQuantiles(unittest.TestCase):

  def test_even_split(self):
    mapping = sampler.plan_quantiles(list(range(100)), 10)
    counts = {}
    for quantile in mapping.values():
      counts[quantile] = counts.get(quantile, 0) + 1
    self.assertEqual(sorted(counts), list(range(10)))
    for count in counts.values():
      self.assertEqual(count, 10)

  def test_edges_are_contiguous(self):
    ranks = [5, 9, 100, 101, 500]
    mapping = sampler.plan_quantiles(ranks, 2)
    # The lower rank must not land in a higher quantile than a bigger rank.
    ordered = sorted(ranks, key=lambda rank: mapping[rank])
    self.assertEqual(ordered, ranks)

  def test_empty_input(self):
    self.assertEqual(sampler.plan_quantiles([], 20), {})

  def test_more_quantiles_than_items(self):
    mapping = sampler.plan_quantiles([1, 2, 3], 20)
    self.assertEqual(len(mapping), 3)
    self.assertTrue(all(0 <= q < 20 for q in mapping.values()))


class TestPrompter(unittest.TestCase):

  class _Record:
    def __init__(self, word, examples):
      self.word = word
      self.examples = examples

  def test_mark_target(self):
    marked = prompter.mark_target("The tightening policy hurt.", "tightening")
    self.assertIn("【tightening】", marked)

  def test_mark_target_is_case_insensitive(self):
    marked = prompter.mark_target("Tightening hurts.", "tightening")
    self.assertIn("【Tightening】", marked)

  def test_mark_target_allows_inflection(self):
    marked = prompter.mark_target("The Fed tightened policy.", "tighten")
    self.assertIn("【tightened】", marked)

  def test_mark_target_without_match_is_unchanged(self):
    self.assertEqual(prompter.mark_target("No match here.", "absent"),
                     "No match here.")

  def test_select_context_requires_the_target(self):
    record = self._Record("tightening", [
        {"e": "An unrelated sentence.", "j": "無関係。"},
        {"e": "The tightening was severe.", "j": "引き締めは厳しかった。"},
    ])
    self.assertEqual(prompter.select_context(record), "The tightening was severe.")

  def test_select_context_none_when_absent(self):
    record = self._Record("tightening", [{"e": "Nothing relevant.", "j": "。"}])
    self.assertIsNone(prompter.select_context(record))

  def test_select_context_none_without_examples(self):
    self.assertIsNone(prompter.select_context(self._Record("x", [])))

  def test_build_probe_falls_back_to_word_only(self):
    record = self._Record("tightening", [{"e": "Nothing relevant.", "j": "。"}])
    probe = prompter.build_probe(record)
    self.assertEqual(probe["task"], "W1")
    self.assertIsNone(probe["sentence"])
    self.assertIn("tightening", probe["prompt"])

  def test_build_probe_uses_p1_with_context(self):
    record = self._Record("tightening", [{"e": "The tightening hurt.", "j": "。"}])
    probe = prompter.build_probe(record)
    self.assertEqual(probe["task"], "P1")
    self.assertIn("【tightening】", probe["prompt"])
    self.assertIn("@", probe["prompt_ver"])

  def test_render_rejects_unknown_task(self):
    with self.assertRaises(ValueError):
      prompter.render("P9", "word")


class TestJudge(unittest.TestCase):

  def test_exact_match_is_k(self):
    label, rule, _ = judge.classify("議会", {"議会", "国会"})
    self.assertEqual((label, rule), ("K", "exact"))

  def test_normalisation_applies(self):
    label, _, _ = judge.classify("ギカイ", {"議会"})  # different reading
    self.assertNotEqual(label, "K")
    label, _, _ = judge.classify("議会。", {"議会"})
    self.assertEqual(label, "K")

  def test_generalisation_is_p(self):
    # 引き締め is a generalisation of 金融引き締め (plan rule 3).
    label, rule, _ = judge.classify("引き締め", {"金融引き締め"})
    self.assertEqual((label, rule), ("P", "substring"))

  def test_short_overlap_is_not_p(self):
    label, _, _ = judge.classify("締", {"締め付け"})
    self.assertNotEqual(label, "P")

  def test_unmatched_japanese_is_x(self):
    label, rule, _ = judge.classify("全く別語", {"議会"})
    self.assertEqual((label, rule), ("X", "unmatched_japanese"))

  def test_non_japanese_is_u(self):
    label, rule, _ = judge.classify("parliament", {"議会"})
    self.assertEqual((label, rule), ("U", "unmatched_non_japanese"))

  def test_empty_output_is_u(self):
    self.assertEqual(judge.classify(None, {"議会"})[0], "U")
    self.assertEqual(judge.classify("", {"議会"})[0], "U")

  def test_prose_is_u(self):
    label, rule, _ = judge.classify(
        "この単語は文脈から判断すると複数の意味があり、ここでは特定できません。",
        {"議会"})
    self.assertEqual((label, rule), ("U", "too_long"))

  def test_prose_containing_the_gloss_is_x(self):
    # The right word is in there, but wrapped in prose; the length bound on the
    # substring rule rejects it, so it goes to audit rather than being scored.
    label, rule, _ = judge.classify(
        "この単語は議会という意味で文脈に合います。", {"議会"})
    self.assertEqual((label, rule), ("X", "unmatched_japanese"))

  def test_labels_are_the_four_of_the_plan(self):
    self.assertEqual(set(judge.LABELS), {"K", "P", "X", "U"})


class TestAuditParsing(unittest.TestCase):

  def test_accept(self):
    self.assertEqual(audit.parse_verdict("はい"), audit.ACCEPT)
    self.assertEqual(audit.parse_verdict("はい。"), audit.ACCEPT)
    self.assertEqual(audit.parse_verdict("Yes"), audit.ACCEPT)

  def test_reject(self):
    self.assertEqual(audit.parse_verdict("いいえ"), audit.REJECT)
    self.assertEqual(audit.parse_verdict("No"), audit.REJECT)

  def test_unknown(self):
    self.assertEqual(audit.parse_verdict("たぶん"), "unknown")
    self.assertEqual(audit.parse_verdict(None), "unknown")


class TestReporter(unittest.TestCase):

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.conn = db_module.get_conn(os.path.join(self.tmp.name, "t.db"))
    self._seed()

  def tearDown(self):
    self.conn.close()
    self.tmp.cleanup()

  def _seed(self):
    conn = self.conn
    conn.execute("INSERT INTO sample_sets VALUES ('S1','{}',1,'now',NULL)")
    conn.execute("INSERT INTO samples (set_id,headword,layer,quantile,log_freq,"
                 "created_at) VALUES ('S1','alpha','main',0,-2.0,'now')")
    conn.execute("INSERT INTO samples (set_id,headword,layer,quantile,log_freq,"
                 "created_at) VALUES ('S1','beta','main',0,-2.5,'now')")
    conn.execute("INSERT INTO samples (set_id,headword,layer,quantile,log_freq,"
                 "created_at) VALUES ('S1','gamma','main',1,-6.0,'now')")
    for index, (word, probe_id) in enumerate(
        [("alpha", 1), ("beta", 2), ("gamma", 3)], start=1):
      conn.execute(
          "INSERT INTO probes (id,set_id,headword,model_id,task,prompt_ver,"
          "raw_output,created_at) VALUES (?,?,?,?,?,?,?,?)",
          (probe_id, "S1", word, "m1", "P1", "p1_v1", "out", "now"))
    labels = {"alpha": "K", "beta": "U", "gamma": "X"}
    for probe_id, word in [(1, "alpha"), (2, "beta"), (3, "gamma")]:
      conn.execute(
          "INSERT INTO judgments (probe_id,label,judge_ver,created_at) "
          "VALUES (?,?,?,?)", (probe_id, labels[word], judge.JUDGE_VERSION, "now"))
    conn.commit()

  def test_curve_groups_by_quantile(self):
    curve = reporter.frequency_curve(self.conn, "S1", "m1")
    self.assertEqual(len(curve), 2)
    first = curve[0]
    self.assertEqual(first["quantile"], 0)
    self.assertEqual(first["n"], 2)
    self.assertEqual(first["K"], 1)
    self.assertEqual(first["U"], 1)
    self.assertEqual(first["strict"], 0.5)

  def test_overall_counts(self):
    totals = reporter.overall(self.conn, "S1", "m1")
    self.assertEqual(totals["n"], 3)
    self.assertEqual(totals["strict"], round(1 / 3, 4))

  def test_markdown_contains_a_table(self):
    curve = reporter.frequency_curve(self.conn, "S1", "m1")
    totals = reporter.overall(self.conn, "S1", "m1")
    markdown = reporter.render_markdown(curve, totals)
    self.assertIn("| quantile |", markdown)
    self.assertIn("strict", markdown)

  def test_csv_round_trip(self):
    curve = reporter.frequency_curve(self.conn, "S1", "m1")
    path = os.path.join(self.tmp.name, "curve.csv")
    reporter.write_csv(curve, path)
    with open(path, encoding="utf-8") as handle:
      self.assertIn("quantile", handle.readline())


class TestAuditSummary(unittest.TestCase):

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.conn = db_module.get_conn(os.path.join(self.tmp.name, "t.db"))
    self.conn.execute("INSERT INTO probes (id,set_id,headword,model_id,task,"
                      "prompt_ver,raw_output,created_at) VALUES "
                      "(1,'S1','a','m1','P1','p','x','now')")
    self.conn.execute("INSERT INTO judgments (probe_id,label,judge_ver,created_at)"
                      " VALUES (1,'K',?,'now')", (judge.JUDGE_VERSION,))
    self.conn.execute("INSERT INTO audit_tasks (id,probe_id,status,result,created_at)"
                      " VALUES (1,1,'done',?, 'now')",
                      (json.dumps({"verdict": "accept"}),))
    self.conn.commit()

  def tearDown(self):
    self.conn.close()
    self.tmp.cleanup()

  def test_agreement_is_computed(self):
    result = audit.summary(self.conn)
    self.assertEqual(result["audited"], 1)
    self.assertEqual(result["agreement"], 1.0)
    self.assertEqual(result["audited_kp"], 1)


if __name__ == "__main__":
  unittest.main()


class TestJapaneseVariants(unittest.TestCase):
  """Morphological variants used by the judge's resolver."""

  def test_strips_inflection(self):
    from lexgap import normalizer

    self.assertIn("暴走", normalizer.japanese_variants("暴走した"))
    self.assertIn("普及", normalizer.japanese_variants("普及させた"))

  def test_strips_adjectival_suffix(self):
    from lexgap import normalizer

    self.assertIn("断片的", normalizer.japanese_variants("断片的な"))
    self.assertIn("慢性", normalizer.japanese_variants("慢性的な"))

  def test_keeps_the_original(self):
    from lexgap import normalizer

    self.assertIn("議会", normalizer.japanese_variants("議会"))

  def test_is_bounded(self):
    from lexgap import normalizer

    variants = normalizer.japanese_variants("あ" * 40)
    self.assertLess(len(variants), 200)

  def test_empty_input(self):
    from lexgap import normalizer

    self.assertEqual(normalizer.japanese_variants(""), set())


class TestClassifyWithResolver(unittest.TestCase):
  """The resolver is additive: it can only turn a rejection into an accept."""

  class _FakeResolver:
    def __init__(self, rule):
      self._rule = rule

    def match(self, word, answer):
      return self._rule

  def test_no_resolver_keeps_the_old_behaviour(self):
    label, rule, _ = judge.classify("全く別語", {"議会"})
    self.assertEqual((label, rule), ("X", "unmatched_japanese"))

  def test_resolver_converts_x_to_p(self):
    label, rule, _ = judge.classify(
        "全く別語", {"議会"}, headword="test", resolver=self._FakeResolver("synonym_gloss"))
    self.assertEqual((label, rule), ("P", "synonym_gloss"))

  def test_resolver_does_not_override_exact_match(self):
    label, rule, _ = judge.classify(
        "議会", {"議会"}, headword="test", resolver=self._FakeResolver("synonym_gloss"))
    self.assertEqual((label, rule), ("K", "exact"))

  def test_resolver_does_not_override_u(self):
    label, rule, _ = judge.classify(
        "parliament", {"議会"}, headword="test", resolver=self._FakeResolver("synonym_gloss"))
    self.assertEqual((label, rule), ("U", "unmatched_non_japanese"))

  def test_resolver_without_headword_is_ignored(self):
    label, _, _ = judge.classify("全く別語", {"議会"}, resolver=self._FakeResolver("x"))
    self.assertEqual(label, "X")

  def test_null_resolver_match_keeps_x(self):
    label, _, _ = judge.classify(
        "全く別語", {"議会"}, headword="test", resolver=self._FakeResolver(None))
    self.assertEqual(label, "X")


@unittest.skipUnless(_HAS_DATA, "dictionary data not present")
class TestGlossResolver(unittest.TestCase):
  """Resolver behaviour against the real dictionary."""

  @classmethod
  def setUpClass(cls):
    from tkrzw_dict_agent.core import db as parent_db

    cls.parent = parent_db.open_dictionary()
    cls.resolver = judge.GlossResolver(cls.parent)

  @classmethod
  def tearDownClass(cls):
    cls.parent.close()

  def test_expand_is_a_superset_of_gloss_set(self):
    from lexgap import normalizer as jnorm

    with DictAdapter() as adapter:
      base = {jnorm.normalize(g) for g in adapter.gloss_set("bank") if g}
    expanded = self.resolver.expand("bank")
    self.assertTrue(base.issubset(expanded))
    self.assertGreater(len(expanded), len(base))

  def test_synonyms_are_exposed(self):
    self.assertTrue(self.resolver.synonyms("rogue"))

  def test_match_rejects_an_unrelated_answer(self):
    self.assertIsNone(self.resolver.match("bank", "全く無関係な語"))

  def test_match_finds_a_synonym_gloss(self):
    # 断片的 is a gloss of the synonyms of "disconnected" but not of the entry.
    with DictAdapter() as adapter:
      base = {jnorm_normalize(g) for g in adapter.gloss_set("disconnected") if g}
    self.assertNotIn("断片的", base)
    rule = self.resolver.match("disconnected", "断片的な")
    self.assertIsNotNone(rule)

  def test_judge_version_is_bumped(self):
    self.assertEqual(judge.JUDGE_VERSION, "j2")


def jnorm_normalize(value):
  from lexgap import normalizer as jnorm

  return jnorm.normalize(value)
