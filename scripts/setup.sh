#!/bin/sh
# Sets up the development environment for tkrzw-dict-agent.
#
# Reproduces the two things this repository deliberately does not track:
#
#   1. the vendored tkrzw-dict checkout, pinned to the commit this project was
#      developed against, with patches/tkrzw-dict-use-pytkrzw.patch applied so
#      that the upstream scripts use the pure-Python reader instead of the C++
#      extension;
#   2. the union dictionary data, a separate download from dbmx.net.
#
# Usage:
#   scripts/setup.sh            # clone, download data, create .venv
#   scripts/setup.sh --no-data  # skip the ~900 MB dictionary download
#
# Run from the repository root (or anywhere; paths are resolved from the script).

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

# Upstream commit this project was developed against.
TKRZW_DICT_REPO="https://github.com/estraier/tkrzw-dict.git"
TKRZW_DICT_COMMIT="be2be5252e8a1ec28cc566ae64cd98cf85ff09fb"
TKRZW_DICT_DIR="$REPO_ROOT/tkrzw-dict"

# Dictionary data archive (contains union-*.tkh, union-*-keys.txt, union-examples.tsv).
DATA_URL="https://dbmx.net/dict/union-dict-data.tar.gz"
DATA_ARCHIVE="$TKRZW_DICT_DIR/union-dict-data.tar.gz"
DATA_MARKER="$TKRZW_DICT_DIR/union-body.tkh"

PATCH_FILE="$REPO_ROOT/patches/tkrzw-dict-use-pytkrzw.patch"
VENV_DIR="$REPO_ROOT/.venv"

DOWNLOAD_DATA=1
for arg in "$@"; do
  case "$arg" in
    --no-data) DOWNLOAD_DATA=0 ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

log() { printf '%s\n' "$*" >&2; }

# --- 1. tkrzw-dict checkout -------------------------------------------------
if [ -d "$TKRZW_DICT_DIR/.git" ]; then
  log "==> tkrzw-dict already cloned; leaving it in place"
else
  log "==> cloning tkrzw-dict"
  git clone "$TKRZW_DICT_REPO" "$TKRZW_DICT_DIR"
fi

log "==> checking out pinned commit $TKRZW_DICT_COMMIT"
git -C "$TKRZW_DICT_DIR" fetch --quiet origin "$TKRZW_DICT_COMMIT" 2>/dev/null || true
git -C "$TKRZW_DICT_DIR" checkout --quiet "$TKRZW_DICT_COMMIT"

# --- 2. apply the pure-Python patch ----------------------------------------
if git -C "$TKRZW_DICT_DIR" apply --reverse --check "$PATCH_FILE" 2>/dev/null; then
  log "==> patch already applied"
else
  log "==> applying patches/tkrzw-dict-use-pytkrzw.patch"
  git -C "$TKRZW_DICT_DIR" apply "$PATCH_FILE"
fi

# --- 3. dictionary data -----------------------------------------------------
if [ "$DOWNLOAD_DATA" -eq 1 ]; then
  if [ -f "$DATA_MARKER" ]; then
    log "==> dictionary data already present; skipping download"
  else
    log "==> downloading dictionary data (~313 MB archive, ~900 MB unpacked)"
    curl -fL --progress-bar -o "$DATA_ARCHIVE" "$DATA_URL"
    log "==> extracting"
    tar xzf "$DATA_ARCHIVE" -C "$TKRZW_DICT_DIR"
    log "==> keeping $DATA_ARCHIVE for future re-extraction"
  fi
else
  log "==> skipping dictionary data (--no-data); lookups will not work until it is present"
fi

# --- 4. virtual environment -------------------------------------------------
if [ -d "$VENV_DIR" ]; then
  log "==> .venv already exists; reinstalling the package in editable mode"
else
  log "==> creating .venv"
  python3 -m venv "$VENV_DIR"
fi
log "==> installing tkrzw-dict-agent (editable) with the api and dev extras"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT[api,dev]"

log ""
log "Setup complete."
log ""
log "  CLI:     $VENV_DIR/bin/dict lookup tightening"
log "  Server:  $VENV_DIR/bin/dict-server            # 0.0.0.0:8765"
log "  Tests:   $VENV_DIR/bin/python3 -m pytest tests/ -q"
log ""
log "Note: this project reads the Tkrzw HashDBM files directly, so the Tkrzw"
log "C++ library and its Python extension are not required."
