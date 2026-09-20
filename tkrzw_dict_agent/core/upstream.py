"""Access to the vendored ``tkrzw-dict`` search engine.

The upstream scripts live in the ``tkrzw-dict`` directory at the repository
root.  This module puts that directory on ``sys.path`` once, then re-exports
the two modules the agent layer needs.  Every other module imports upstream
functionality through here, so the coupling to the vendored code is confined
to a single place.

Set the ``TKRZW_DICT_DIR`` environment variable to point at a different
dictionary installation if needed.
"""

import os
import sys

__all__ = ["tkrzw_dict", "UnionSearcher", "is_available", "get_dict_dir"]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_DICT_DIR = os.path.join(_REPO_ROOT, "tkrzw-dict")

_DICT_DIR = os.environ.get("TKRZW_DICT_DIR", _DEFAULT_DICT_DIR)


def get_dict_dir():
  """Returns the directory containing the vendored tkrzw-dict scripts."""
  return _DICT_DIR


def is_available():
  """True when the vendored dictionary scripts can be imported."""
  return os.path.exists(os.path.join(_DICT_DIR, "tkrzw_union_searcher.py"))


if _DICT_DIR not in sys.path:
  sys.path.insert(0, _DICT_DIR)

import tkrzw_dict  # noqa: E402  (path set up above on purpose)
import tkrzw_union_searcher  # noqa: E402

UnionSearcher = tkrzw_union_searcher.UnionSearcher
