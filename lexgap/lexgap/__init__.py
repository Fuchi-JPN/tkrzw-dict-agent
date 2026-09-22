"""LexGap: a Japanese vocabulary-gap verification system for small LLMs.

The package measures which Japanese words a small local model fails to render
in context, models that failure as a probability, exports a gate that a
sidecar can apply, and evaluates whether gating degrades translation quality
while saving tokens.

The pipeline is deliberately staged so that every milestone leaves a usable
artefact behind::

    sampler -> probe -> judge -> features -> predictor -> exporter
                                                       -> intervention

See ``docs/spec/LexGap*実装計画書*.md`` in the parent repository for the plan
and ``README.md`` next to this package for the current state.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
