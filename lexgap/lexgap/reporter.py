"""Reporter: the frequency-stratified correctness curve (hypothesis H1).

H1 asks whether a model's failure rate tracks corpus frequency.  The curve is
the first real result M1 produces, so it is deliberately simple and expressed
as text tables plus CSV: no plotting dependency, and the numbers can be diffed
between runs.

Two correctness definitions are reported side by side because the K/P boundary
is provisional until the audit decides it:

* ``strict`` -- only ``K`` counts (exact match after normalisation).
* ``lenient`` -- ``K`` and ``P`` count (partial or generalising answers too).

``X`` is reported separately and never counted as correct: an ``X`` is exactly
the case the audit exists to resolve.
"""

import csv
import io

from . import judge

__all__ = ["frequency_curve", "overall", "render_markdown", "write_csv", "run_report"]


def frequency_curve(conn, set_id, model_id, task=None):
  """Aggregates judgements per frequency quantile.

  :returns: A list of rows ordered by quantile, each with counts, the share of
      each label, and the stable frequency range the quantile spans.
  """
  rows = judge.load_judgments(conn, set_id, model_id, task=task)
  buckets = {}
  for row in rows:
    quantile = row["quantile"]
    if quantile is None:
      continue
    bucket = buckets.setdefault(quantile, {
        "quantile": quantile, "n": 0, "K": 0, "P": 0, "X": 0, "U": 0,
        "log_freqs": [], "domains": {},
    })
    bucket["n"] += 1
    bucket[row["label"]] = bucket.get(row["label"], 0) + 1
    if row["log_freq"] is not None:
      bucket["log_freqs"].append(row["log_freq"])
    if row["domain"]:
      bucket["domains"][row["domain"]] = bucket["domains"].get(row["domain"], 0) + 1

  result = []
  for quantile in sorted(buckets):
    bucket = buckets[quantile]
    n = max(bucket["n"], 1)
    log_freqs = bucket.pop("log_freqs")
    bucket.pop("domains")
    bucket["log_freq_min"] = round(min(log_freqs), 2) if log_freqs else None
    bucket["log_freq_max"] = round(max(log_freqs), 2) if log_freqs else None
    bucket["strict"] = round(bucket["K"] / n, 4)
    bucket["lenient"] = round((bucket["K"] + bucket["P"]) / n, 4)
    bucket["x_rate"] = round(bucket["X"] / n, 4)
    bucket["u_rate"] = round(bucket["U"] / n, 4)
    result.append(bucket)
  return result


def overall(conn, set_id, model_id, task=None):
  """Overall counts and rates for one set and model."""
  rows = judge.load_judgments(conn, set_id, model_id, task=task)
  counts = {label: 0 for label in judge.LABELS}
  for row in rows:
    counts[row["label"]] = counts.get(row["label"], 0) + 1
  n = max(len(rows), 1)
  return {
      "n": len(rows),
      **counts,
      "strict": round(counts["K"] / n, 4),
      "lenient": round((counts["K"] + counts["P"]) / n, 4),
  }


def render_markdown(rows, overall_row=None, title="Frequency-stratified correctness"):
  """Renders the curve as a Markdown table (the plan asks for no plotting)."""
  lines = ["# {}".format(title), ""]
  if overall_row:
    lines.append("Overall: n={n}, strict={strict}, lenient={lenient}, "
                 "K={K}, P={P}, X={X}, U={U}".format(**overall_row))
    lines.append("")
  lines.append("| quantile | log_freq | n | K | P | X | U | strict | lenient |")
  lines.append("|---|---|---|---|---|---|---|---|---|")
  for row in rows:
    lines.append("| {quantile} | {lo}..{hi} | {n} | {K} | {P} | {X} | {U} "
                 "| {strict} | {lenient} |".format(
                     quantile=row["quantile"], lo=row["log_freq_min"],
                     hi=row["log_freq_max"], n=row["n"], K=row["K"], P=row["P"],
                     X=row["X"], U=row["U"], strict=row["strict"],
                     lenient=row["lenient"]))
  lines.append("")
  lines.append("strict = K/n (exact match only); lenient = (K+P)/n; "
               "X is excluded from both until the audit resolves it.")
  return "\n".join(lines)


def write_csv(rows, path):
  """Writes the curve to CSV, one row per quantile."""
  if not rows:
    return
  fieldnames = [key for key in rows[0] if key not in ("domains",)]
  with open(path, "w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
      writer.writerow({key: row.get(key) for key in fieldnames})


def run_report(conn, set_id, model_id, task=None, log=None, csv_path=None):
  """Builds the curve and returns it along with its Markdown rendering."""
  curve = frequency_curve(conn, set_id, model_id, task=task)
  totals = overall(conn, set_id, model_id, task=task)
  markdown = render_markdown(curve, totals)
  if csv_path:
    write_csv(curve, csv_path)
  if log:
    log.info("reported %d quantile(s), n=%d", len(curve), totals["n"])
  return curve, totals, markdown
