"""Pure-Python read-only replacement for the tkrzw C++ extension.

This module implements the exact subset of the ``tkrzw`` API that the
``tkrzw-dict`` search engine uses at run time:

- :class:`DBM`  : read-only access to Tkrzw ``HashDBM`` (``.tkh``) files.
- :class:`File` : plain-text line search over the ``*-keys.txt`` lists.
- :class:`Utility` : ``EditDistanceLev``, ``PrimaryHash``, ``GetMemoryUsage``.
- :class:`Status` : operation result with ``OrDie``.

The on-disk format is taken from the Tkrzw C++ sources (``tkrzw_dbm_hash.cc``,
``tkrzw_dbm_hash_impl.cc``, ``tkrzw_sys_config.h``).  Writing is not supported:
the project consumes pre-built dictionary data only.

Limitations: record compression (zstd/lz4/lzma/rc4/aes) and record CRC are not
implemented; an uncompressed, CRC-free database raises nothing and works, any
other configuration raises :class:`NotImplementedError` on ``Open``.
"""

import heapq
import mmap
import os
import re
import resource
import sys

__all__ = ["DBM", "File", "Status", "Utility"]

_MASK64 = 0xFFFFFFFFFFFFFFFF

# Status codes, mirroring tkrzw::Status::Code.
SUCCESS = "SUCCESS"
NOT_FOUND_ERROR = "NOT_FOUND_ERROR"
SYSTEM_ERROR = "SYSTEM_ERROR"
UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
BROKEN_DATA_ERROR = "BROKEN_DATA_ERROR"
DUPLICATION_ERROR = "DUPLICATION_ERROR"
INFEASIBLE_ERROR = "INFEASIBLE_ERROR"
PRECONDITION_ERROR = "PRECONDITION_ERROR"
NOT_IMPLEMENTED_ERROR = "NOT_IMPLEMENTED_ERROR"
INVALID_ARGUMENT_ERROR = "INVALID_ARGUMENT_ERROR"

# HashDBM metadata layout (tkrzw_dbm_hash.cc).
_META_SIZE = 128
_META_MAGIC = b"TkrzwHDB\n"
_META_OFFSET_STATIC_FLAGS = 12
_META_OFFSET_OFFSET_WIDTH = 13
_META_OFFSET_ALIGN_POW = 14
_META_OFFSET_NUM_BUCKETS = 16
_META_OFFSET_NUM_RECORDS = 24
_META_OFFSET_FILE_SIZE = 40
_FBP_SECTION_SIZE = 1008
_RECORD_BASE_HEADER_SIZE = 16
_RECORD_BASE_ALIGN = 4096

# Record operation types (upper two bits of the record magic byte).
_RECORD_MAGIC_VOID = 0xC0
_RECORD_MAGIC_SET = 0x80
_RECORD_MAGIC_REMOVE = 0x40
_RECORD_OP_SET = 1
_RECORD_OP_REMOVE = 2
_RECORD_OP_ADD = 3

_MURMUR_SEED = 19780211
_UINT32_MAX = 0xFFFFFFFF


class Status:
  """Result of an operation, compatible with tkrzw.Status."""

  def __init__(self, code=SUCCESS, message=""):
    self.code_ = code
    self.message_ = message

  def GetCode(self):
    return self.code_

  def GetMessage(self):
    return self.message_

  def IsOK(self):
    return self.code_ == SUCCESS

  def OrDie(self):
    if self.code_ != SUCCESS:
      raise RuntimeError("tkrzw status {}: {}".format(self.code_, self.message_))

  def __repr__(self):
    return "Status({}, {!r})".format(self.code_, self.message_)

  def __eq__(self, other):
    return isinstance(other, Status) and other.code_ == self.code_

  def __bool__(self):
    return self.code_ == SUCCESS


def _murmur(data, seed):
  """MurmurHash2 64-bit (variant A), as tkrzw::HashMurmur."""
  mul = 0xC6A4A7935BD1E995
  rtt = 47
  size = len(data)
  hash_ = (seed ^ ((size * mul) & _MASK64)) & _MASK64
  num_blocks = size >> 3
  i = 0
  for _ in range(num_blocks):
    num = int.from_bytes(data[i:i + 8], "little")
    num = (num * mul) & _MASK64
    num ^= num >> rtt
    num = (num * mul) & _MASK64
    hash_ = (hash_ * mul) & _MASK64
    hash_ ^= num
    i += 8
  rem = size - i
  if rem >= 7:
    hash_ ^= data[i + 6] << 48
  if rem >= 6:
    hash_ ^= data[i + 5] << 40
  if rem >= 5:
    hash_ ^= data[i + 4] << 32
  if rem >= 4:
    hash_ ^= data[i + 3] << 24
  if rem >= 3:
    hash_ ^= data[i + 2] << 16
  if rem >= 2:
    hash_ ^= data[i + 1] << 8
  if rem >= 1:
    hash_ ^= data[i]
    hash_ = (hash_ * mul) & _MASK64
  hash_ ^= hash_ >> rtt
  hash_ = (hash_ * mul) & _MASK64
  hash_ ^= hash_ >> rtt
  return hash_ & _MASK64


def _primary_hash(data, num_buckets):
  """tkrzw::PrimaryHash: MurmurHash2 with folding for small bucket counts."""
  hash_ = _murmur(data, _MURMUR_SEED)
  if num_buckets <= _UINT32_MAX:
    hash_ = ((((hash_ & 0xFFFF000000000000) >> 48) |
              ((hash_ & 0x0000FFFF00000000) >> 16)) ^
             (((hash_ & 0x000000000000FFFF) << 16) |
              ((hash_ & 0x00000000FFFF0000) >> 16)))
  return hash_ % num_buckets


def _read_varnum(buf, pos):
  """Read a byte-delta encoded variable length number.

  Returns a pair of the value and the next position.
  """
  num = 0
  while True:
    c = buf[pos]
    num = (num << 7) + (c & 0x7F)
    pos += 1
    if c < 0x80:
      return num, pos


def _crc_width_from_static_flags(static_flags):
  """tkrzw::HashDBM::GetCRCWidthFromStaticFlags."""
  mode = (static_flags >> 2) & 0x3
  return {0: 0, 1: 1, 2: 2, 3: 4}[mode]


def _compression_from_static_flags(static_flags):
  """Subset of tkkrzw::HashDBM::MakeCompressorFromStaticFlags."""
  return (static_flags >> 4) & 0x7


class DBM:
  """Read-only access to a Tkrzw HashDBM (``.tkh``) file.

  This mirrors ``tkrzw.DBM`` for the operations the dictionary reader needs:
  ``Open``, ``Close``, ``Get``, ``GetStr``, ``Count`` and ``GetFileSize``.
  """

  def __init__(self):
    self._open = False
    self._path = None
    self._file = None
    self._mm = None
    self._offset_width = 4
    self._align_pow = 0
    self._num_buckets = 0
    self._num_records = 0
    self._file_size = 0
    self._record_base = 0
    self._crc_width = 0
    self._bucket_read_size = 4

  def Open(self, path, writable=False, dbm="HashDBM", **kwargs):
    """Opens the database.  Only read-only HashDBM is supported."""
    if self._open:
      raise RuntimeError("already opened database")
    if writable:
      raise NotImplementedError("pytkrzw supports read-only databases only")
    if dbm != "HashDBM":
      raise NotImplementedError(
          "pytkrzw supports HashDBM only, requested: {}".format(dbm))
    self._path = str(path)
    self._file = open(self._path, "rb")
    status = self._open_hash_dbm()
    if not status.IsOK():
      self._file.close()
      self._file = None
      self._mm = None
    else:
      self._open = True
    return status

  def _open_hash_dbm(self):
    file_size = os.fstat(self._file.fileno()).st_size
    if file_size < _META_SIZE:
      return Status(BROKEN_DATA_ERROR, "invalid file size")
    self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
    meta = self._mm[:_META_SIZE]
    if meta[:len(_META_MAGIC)] != _META_MAGIC:
      return Status(BROKEN_DATA_ERROR, "invalid magic data")
    static_flags = meta[_META_OFFSET_STATIC_FLAGS]
    if not (static_flags & 0x1) and not (static_flags & 0x2):
      return Status(BROKEN_DATA_ERROR, "invalid static flags")
    if _compression_from_static_flags(static_flags) != 0:
      return Status(NOT_IMPLEMENTED_ERROR, "record compression is unsupported")
    self._crc_width = _crc_width_from_static_flags(static_flags)
    self._offset_width = meta[_META_OFFSET_OFFSET_WIDTH]
    self._align_pow = meta[_META_OFFSET_ALIGN_POW]
    self._num_buckets = int.from_bytes(
        meta[_META_OFFSET_NUM_BUCKETS:_META_OFFSET_NUM_BUCKETS + 8], "big")
    self._num_records = int.from_bytes(
        meta[_META_OFFSET_NUM_RECORDS:_META_OFFSET_NUM_RECORDS + 8], "big")
    stored_size = int.from_bytes(
        meta[_META_OFFSET_FILE_SIZE:_META_OFFSET_FILE_SIZE + 8], "big")
    self._file_size = min(stored_size, file_size)
    if self._offset_width < 3 or self._offset_width > 6:
      return Status(BROKEN_DATA_ERROR, "the offset width is invalid")
    if self._num_buckets < 1:
      return Status(BROKEN_DATA_ERROR, "the bucket size is invalid")
    align = max(_RECORD_BASE_ALIGN, 1 << self._align_pow)
    record_base = (_META_SIZE + self._num_buckets * self._offset_width +
                   _FBP_SECTION_SIZE + _RECORD_BASE_HEADER_SIZE)
    self._record_base = ((record_base + align - 1) // align) * align
    self._bucket_read_size = self._offset_width
    return Status()

  def Close(self):
    """Closes the database."""
    if not self._open:
      return Status(PRECONDITION_ERROR, "not opened database")
    self._mm.close()
    self._mm = None
    self._file.close()
    self._file = None
    self._open = False
    return Status()

  def Get(self, key, status=None):
    """Retrieves the value of a record as bytes, or None if not found."""
    if not self._open:
      raise RuntimeError("not opened database")
    if isinstance(key, str):
      key = key.encode("utf-8")
    result = self._get_bytes(key)
    if status is not None:
      status.code_ = SUCCESS if result is not None else NOT_FOUND_ERROR
      status.message_ = ""
    return result

  def GetStr(self, key, status=None):
    """Retrieves the value of a record as a string, or None if not found."""
    value = self.Get(key, status)
    if value is None:
      return None
    return value.decode("utf-8")

  def _get_bytes(self, key):
    bucket_index = _primary_hash(key, self._num_buckets)
    offset = _META_SIZE + bucket_index * self._bucket_read_size
    current = int.from_bytes(
        self._mm[offset:offset + self._bucket_read_size], "big") << self._align_pow
    while current > 0:
      value, current = self._read_record(current, key)
      if value is not _MISSING:
        return value
    return None

  def _read_record(self, offset, key):
    """Reads the record at offset and matches it against key.

    Returns (value_or_None_if_removed, next_offset).  ``_MISSING`` means the
    key does not match and the bucket chain should be followed.
    """
    mm = self._mm
    head_size = min(_META_SIZE, self._file_size - offset)
    head = mm[offset:offset + head_size]
    magic = head[0]
    if magic & 0x3F < 3:
      raise RuntimeError("broken record at offset {}".format(offset))
    op_type = magic & 0xC0
    pos = 1
    child_offset = (int.from_bytes(
        head[pos:pos + self._offset_width], "big") << self._align_pow)
    pos += self._offset_width
    key_size, pos = _read_varnum(head, pos)
    value_size, pos = _read_varnum(head, pos)
    padding_size, pos = _read_varnum(head, pos)
    if self._crc_width:
      pos += self._crc_width
    if op_type == _RECORD_MAGIC_VOID:
      op = 0
    elif op_type == _RECORD_MAGIC_SET:
      op = _RECORD_OP_SET
    elif op_type == _RECORD_MAGIC_REMOVE:
      op = _RECORD_OP_REMOVE
    else:
      op = _RECORD_OP_ADD
    body_offset = offset + pos
    if head_size - pos >= key_size + value_size:
      rec_key = head[pos:pos + key_size]
      rec_value = head[pos + key_size:pos + key_size + value_size]
    else:
      rec_key = mm[body_offset:body_offset + key_size]
      rec_value = mm[body_offset + key_size:body_offset + key_size + value_size]
    if rec_key != key:
      return _MISSING, child_offset
    if op == _RECORD_OP_SET or op == _RECORD_OP_ADD:
      return rec_value, child_offset
    return None, child_offset

  def Count(self):
    """Returns the number of records."""
    return self._num_records

  def __contains__(self, key):
    """Membership test, mirroring the tkrzw C++ binding."""
    if isinstance(key, str):
      key = key.encode("utf-8")
    return self._get_bytes(key) is not None

  def GetFileSize(self):
    """Returns the file size in bytes."""
    return self._file_size

  def GetPath(self):
    """Returns the path of the database file."""
    return self._path

  def __del__(self):
    if getattr(self, "_open", False):
      try:
        if self._mm is not None:
          self._mm.close()
        if self._file is not None:
          self._file.close()
      except Exception:
        pass


class _Missing:
  """Sentinel meaning "key does not match, keep walking the chain"."""

  def __repr__(self):
    return "<missing>"


_MISSING = _Missing()


class File:
  """Plain text file reader with pattern search, like tkrzw.File.

  ``Search`` implements the modes used by the dictionary engine:
  ``contain``, ``begin``, ``end``, ``regex`` and ``edit``.  Lines are returned
  without the trailing newline, matching the C++ implementation.
  """

  def __init__(self):
    self._open = False
    self._path = None

  def Open(self, path, writable=False, **kwargs):
    if self._open:
      raise RuntimeError("already opened file")
    if writable:
      raise NotImplementedError("pytkrzw supports read-only files only")
    self._path = str(path)
    self._open = True
    return Status()

  def Close(self):
    if not self._open:
      return Status(PRECONDITION_ERROR, "not opened file")
    self._open = False
    return Status()

  def GetPath(self):
    return self._path

  def Search(self, mode, pattern, capacity=0):
    """Searches the file and returns the matching lines.

    :param mode: "contain", "begin", "end", "regex", "edit", "editbin",
      "containcase", "containword", "containcaseword", and the "contain*"
      batch variants that match any newline-separated pattern.
    :param pattern: The pattern for matching.
    :param capacity: The maximum number of lines to obtain.  0 means unlimited.
    """
    if not self._open:
      raise RuntimeError("not opened file")
    if capacity == 0:
      capacity = sys.maxsize
    if mode == "edit":
      return self._search_edit(pattern, capacity)
    if mode == "editbin":
      return self._search_edit(pattern, capacity, binary=True)
    if mode == "regex":
      return self._search_regex(pattern, capacity)
    if mode in _BATCH_MATCHERS:
      patterns = [part for part in str(pattern).split("\n") if part]
      matcher = _BATCH_MATCHERS[mode](patterns)
    else:
      factory = _MATCHERS.get(mode)
      if factory is None:
        raise RuntimeError("invalid argument: unknown mode: " + str(mode))
      matcher = factory(pattern)
    matched = []
    with open(self._path, "r", encoding="utf-8") as input_file:
      for line in input_file:
        content = line[:-1] if line.endswith("\n") else line
        if matcher(content):
          matched.append(content)
          if len(matched) >= capacity:
            break
    return matched

  def _search_regex(self, pattern, capacity):
    try:
      regex = re.compile(pattern)
    except re.error as err:
      raise RuntimeError("invalid regex: {}".format(err))
    matched = []
    with open(self._path, "r", encoding="utf-8") as input_file:
      for line in input_file:
        content = line[:-1] if line.endswith("\n") else line
        if regex.search(content):
          matched.append(content)
          if len(matched) >= capacity:
            break
    return matched

  def _search_edit(self, pattern, capacity, binary=False):
    """Top-capacity lines by ascending (edit distance, line).

    Mirrors SearchTextFileEditDistance: the whole file is scanned, and the
    ``capacity`` lines with the smallest Levenshtein distance to the pattern
    are returned, ties broken by the line content.  A bounded max-heap keeps
    the candidates; lines whose length gap already exceeds the current worst
    distance are skipped because their distance cannot improve the result.
    """
    if binary:
      pattern_key = pattern.encode("utf-8") if isinstance(pattern, str) else pattern
    else:
      pattern_key = pattern
    heap = []
    full = False
    worst = None
    with open(self._path, "r", encoding="utf-8") as input_file:
      for line in input_file:
        content = line[:-1] if line.endswith("\n") else line
        if full:
          length_gap = abs(len(content) - len(pattern_key))
          if (length_gap > worst.cost or
              (length_gap == worst.cost and content >= worst.payload)):
            continue
        if binary:
          candidate = content.encode("utf-8")
        else:
          candidate = content
        dist = Utility.EditDistanceLev(pattern_key, candidate)
        if not full:
          heapq.heappush(heap, _HeapItem(dist, content))
          if len(heap) >= capacity:
            full = True
            worst = heap[0]
        elif dist < worst.cost or (dist == worst.cost and content < worst.payload):
          heapq.heapreplace(heap, _HeapItem(dist, content))
          worst = heap[0]
    result = sorted(heap, key=lambda item: (item.cost, item.payload))
    return [item.payload for item in result]


class _HeapItem:
  """Max-heap item ordered by (cost, payload), mirroring std::push_heap."""

  __slots__ = ("cost", "payload")

  def __init__(self, cost, payload):
    self.cost = cost
    self.payload = payload

  def __lt__(self, other):
    if self.cost != other.cost:
      return self.cost > other.cost
    return self.payload > other.payload


def _is_alnum(ch):
  """tkrzw_isalnum: C-locale alphanumeric test, byte oriented."""
  return "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z"


def _ascii_lower(text):
  """StrLowerCase: lowercases ASCII 'A'-'Z' only."""
  return text.translate(_ASCII_LOWER_TABLE)


_ASCII_LOWER_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _str_word_search(text, pattern):
  """tkrzw::StrWordSearch: substring match on word boundaries.

  A match is rejected when the character just before it is alphanumeric and
  the pattern starts with an alphanumeric character, or when the character
  just after it is alphanumeric and the pattern ends with one.
  """
  if len(pattern) > len(text):
    return -1
  if not pattern:
    return 0
  first = pattern[0]
  last = pattern[-1]
  pattern_len = len(pattern)
  text_len = len(text)
  start = 0
  while True:
    index = text.find(pattern, start)
    if index < 0:
      return -1
    if index > 0 and _is_alnum(text[index - 1]) and _is_alnum(first):
      start = index + 1
      continue
    end = index + pattern_len
    if end < text_len and _is_alnum(text[end]) and _is_alnum(last):
      start = index + 1
      continue
    return index


def _make_contains(pattern):
  return lambda text: pattern in text


def _make_begins_with(pattern):
  return lambda text: text.startswith(pattern)


def _make_ends_with(pattern):
  return lambda text: text.endswith(pattern)


def _make_case_contains(pattern):
  lowered = _ascii_lower(pattern)
  return lambda text: lowered in _ascii_lower(text)


def _make_word_contains(pattern):
  return lambda text: _str_word_search(text, pattern) >= 0


def _make_case_word_contains(pattern):
  lowered = _ascii_lower(pattern)
  return lambda text: _str_word_search(_ascii_lower(text), lowered) >= 0


_MATCHERS = {
    "contain": _make_contains,
    "begin": _make_begins_with,
    "end": _make_ends_with,
    "containcase": _make_case_contains,
    "containword": _make_word_contains,
    "containcaseword": _make_case_word_contains,
}

def _word_boundary_regex(patterns):
  """Builds a matcher for batch word modes.

  Every pattern is assumed to start and end with an alphanumeric character,
  which is true for the dictionary words the engine searches for.  Under that
  condition the regex is exactly equivalent to the C++ word-boundary rule.
  Returns the compiled expression; callers match against already-normalised
  (e.g. lower-cased) text.
  """
  parts = [re.escape(pattern) for pattern in patterns]
  body = "|".join(parts)
  return re.compile(r"(?:^|[^0-9A-Za-z])(?:{})(?:$|[^0-9A-Za-z])".format(body))


def _make_batch_contains(patterns):
  def matcher(text):
    for pattern in patterns:
      if pattern in text:
        return True
    return False

  return matcher


def _make_batch_case_contains(patterns):
  lowered = [_ascii_lower(pattern) for pattern in patterns]

  def matcher(text):
    low_text = _ascii_lower(text)
    for pattern in lowered:
      if pattern in low_text:
        return True
    return False

  return matcher


def _make_batch_word_contains(patterns):
  expression = _word_boundary_regex(patterns)
  return lambda text: expression.search(text) is not None


def _make_batch_case_word_contains(patterns):
  expression = _word_boundary_regex([_ascii_lower(pattern) for pattern in patterns])
  return lambda text: expression.search(_ascii_lower(text)) is not None


_BATCH_MATCHERS = {
    "contain*": _make_batch_contains,
    "containcase*": _make_batch_case_contains,
    "containword*": _make_batch_word_contains,
    "containcaseword*": _make_batch_case_word_contains,
}


class Utility:
  """Collection of utility functions, like tkrzw.Utility."""

  @staticmethod
  def EditDistanceLev(a, b):
    """Levenshtein distance between two strings (UCS-4 codepoint space)."""
    if a == b:
      return 0
    len_a = len(a)
    len_b = len(b)
    if len_a == 0:
      return len_b
    if len_b == 0:
      return len_a
    if len_a > len_b:
      a, b = b, a
      len_a, len_b = len_b, len_a
    prev = list(range(len_a + 1))
    for i in range(1, len_b + 1):
      current = [i]
      bch = b[i - 1]
      for j in range(1, len_a + 1):
        cost = 0 if bch == a[j - 1] else 1
        current.append(min(prev[j] + 1, current[j - 1] + 1, prev[j - 1] + cost))
      prev = current
    return prev[len_a]

  @staticmethod
  def PrimaryHash(data, num_buckets=0):
    """Calculates the Murmur hash value of data.

    :param data: The data to calculate the hash value for.
    :param num_buckets: The number of buckets of the hash table.  0 means the
      raw hash value without folding or modulo.
    """
    if num_buckets == 0:
      num_buckets = _MASK64
    if isinstance(data, str):
      data = data.encode("utf-8")
    return _primary_hash(data, num_buckets)

  @staticmethod
  def GetMemoryUsage():
    """Gets the memory usage of the current process in bytes."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
# END OF FILE
