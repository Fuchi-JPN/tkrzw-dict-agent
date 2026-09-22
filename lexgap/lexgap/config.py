"""Configuration loading and path resolution for LexGap.

All configuration lives in TOML files rather than in code, per the project
principle "configuration is data, not code".  This module is the single place
that knows where those files are and how the repository is laid out.

Layout (the project is a subdirectory of the tkrzw-dict-agent checkout)::

    lexgap/
    ├── lexgap/            this package
    ├── configs/           runners.toml, models.toml, prompts/
    ├── specs/             sampling specs such as s1.toml
    ├── data/              lexgap.db and corpora (not tracked)
    ├── logs/              run logs (not tracked)
    └── ...
"""

import os
from pathlib import Path

try:
  import tomllib
except ModuleNotFoundError:  # Python < 3.11
  import tomli as tomllib  # type: ignore

__all__ = [
  "LEXGAP_ROOT",
  "REPO_ROOT",
  "CONFIGS_DIR",
  "PROMPTS_DIR",
  "SPECS_DIR",
  "DATA_DIR",
  "LOGS_DIR",
  "DEFAULT_DB_PATH",
  "ConfigError",
  "load_toml",
  "load_runners",
  "load_models",
  "load_spec",
  "dict_prefix",
  "resolve_db_path",
  "ensure_dirs",
]

LEXGAP_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LEXGAP_ROOT.parent

CONFIGS_DIR = LEXGAP_ROOT / "configs"
PROMPTS_DIR = CONFIGS_DIR / "prompts"
SPECS_DIR = LEXGAP_ROOT / "specs"
DATA_DIR = LEXGAP_ROOT / "data"
LOGS_DIR = LEXGAP_ROOT / "logs"

DEFAULT_DB_PATH = DATA_DIR / "lexgap.db"

# The dictionary shipped with the sibling tkrzw-dict-agent checkout.
DEFAULT_DICT_PREFIX = REPO_ROOT / "tkrzw-dict" / "union"


class ConfigError(RuntimeError):
  """Raised when a configuration file is missing or malformed."""


def load_toml(path):
  """Loads a TOML file into a dictionary.

  :param path: Path to the file.  Relative paths are resolved against the
      LexGap root.
  :raises ConfigError: if the file does not exist or cannot be parsed.
  """
  resolved = Path(path)
  if not resolved.is_absolute():
    resolved = LEXGAP_ROOT / resolved
  if not resolved.exists():
    raise ConfigError("configuration file not found: {}".format(resolved))
  try:
    with open(resolved, "rb") as handle:
      return tomllib.load(handle)
  except tomllib.TOMLDecodeError as exc:
    raise ConfigError("invalid TOML in {}: {}".format(resolved, exc))


def load_runners():
  """Loads ``configs/runners.toml``."""
  return load_toml(CONFIGS_DIR / "runners.toml")


def load_models():
  """Loads ``configs/models.toml``."""
  return load_toml(CONFIGS_DIR / "models.toml")


def load_spec(name="s1"):
  """Loads a sampling spec such as ``specs/s1.toml``.

  :param name: Spec name with or without the ``.toml`` suffix.  Matching is
      case-insensitive because the set id is written upper-case (``S1``) while
      the file is lower-case (``s1.toml``).
  """
  stem = name[:-5] if name.endswith(".toml") else name
  filename = "{}.toml".format(stem.lower())
  return load_toml(SPECS_DIR / filename)


def load_prompt(name):
  """Loads a prompt template from ``configs/prompts``.

  :param name: File name such as ``p1_v1.txt``.
  :returns: The template text.
  """
  path = PROMPTS_DIR / name
  if not path.exists():
    raise ConfigError("prompt template not found: {}".format(path))
  return path.read_text(encoding="utf-8")


def dict_prefix():
  """Resolves the dictionary data prefix.

  ``TKRZW_DICT_PREFIX`` wins, then ``TKRZW_DICT_DIR`` + ``/union``, then the
  sibling checkout.  This mirrors the resolution used by tkrzw-dict-agent so
  the two agree.
  """
  explicit = os.environ.get("TKRZW_DICT_PREFIX")
  if explicit:
    return Path(explicit)
  dict_dir = os.environ.get("TKRZW_DICT_DIR")
  if dict_dir:
    return Path(dict_dir) / "union"
  return DEFAULT_DICT_PREFIX


def resolve_db_path(path=None):
  """Resolves the SQLite database path, honouring ``--db`` and the default."""
  if path is None:
    return DEFAULT_DB_PATH
  resolved = Path(path)
  if not resolved.is_absolute():
    resolved = LEXGAP_ROOT / resolved
  return resolved


def ensure_dirs():
  """Creates the data and log directories if they are missing."""
  for directory in (DATA_DIR, LOGS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
