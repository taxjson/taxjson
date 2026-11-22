"""One home for TOML parsing: every `.toml` the package reads (taxjson.toml,
holdings and positions reports, the maps, the slips, the package's own data
files) is parsed here, and nowhere else calls the parser (the refactoring
toward the 2026-11-02 architecture review, step 2e).

`parse_toml(text)` parses text the caller read and decoded, a leading BOM
dropped, so a file re-saved by an editor that writes one reads everywhere
(backlog TJ-1661); `read_toml(path)` is the common case, UTF-8, read as
bytes so the BOM policy is this one. The parser is the standard library's
`tomllib` (Python 3.12 is the floor). `TOML_ERRORS` is what a reader that
degrades on bad TOML catches: the parser's error, a ValueError (backlog
TJ-1653, TJ-1659, TJ-1660).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict

TOML_ERRORS = (tomllib.TOMLDecodeError,)


def parse_toml(text: str) -> Dict[str, Any]:
    """The document `text` holds (tomllib's reading, a leading BOM dropped;
    its TOMLDecodeError on bad text)."""
    return tomllib.loads(text.lstrip("\ufeff"))


def decode_toml_bytes(raw: bytes) -> str:
    """The text of a `.toml` file's bytes as every reader and editor sees
    it: UTF-8 (UnicodeDecodeError otherwise), a leading BOM dropped, CRLF
    and a bare CR read as a newline (what `read_text` did). The one
    encoding, BOM and newline policy, for the text that is parsed and the
    text that is edited and written back."""
    text = raw.decode("utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_toml_text(path: Path) -> str:
    """`decode_toml_bytes` of the file at `path`."""
    return decode_toml_bytes(Path(path).read_bytes())


def read_toml(path: Path) -> Dict[str, Any]:
    """The document at `path`: `read_toml_text`, parsed."""
    return parse_toml(read_toml_text(path))
