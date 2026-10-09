"""T5 / T3 slips downloaded from CRA My Account ("Tax information
slips"), one PDF per slip, read into slips.toml tables for `taxjson
slip-audit --import-cra` (tax-logic CA-SLIP-04).

CRA prints every issuer's slip in one layout (pdftotext -layout):

    2025 T5 slip (original) from SAMPLE BROKERAGE INC.
        Box
                     Box name                                 Box value
        number
        13           Interest from Canadian sources               12.34
                     Actual amount of dividends other than
        10                                                         0.00
                     eligible dividends
        27           Foreign currency                               CAD
      Other information
        Box number   Box name                                 Box value
        15           Foreign income                               40.00

Only three things are read: the slip line (year, T5 or T3, original,
amended or cancelled, the issuer — a company, on that line and at most
one more line that reads as a name), the box rows (a known amount box
of the slip's type and its amount; box 27's currency) and nothing else.
The page also carries the recipient's name and other personal details:
no line but those is kept, so they are never read into anything,
stored or printed. The CRA copy shows no account number. A PDF of
several slips (pages saved together) is cut at each slip line: each
slip is read from its own lines.

An imported slip is given to a project account by the books: the
issuer's name against the brokers' names (lib/brokerages/detect
DISPLAY_NAMES: the issuer carries the whole name) and the issuers the
shipped data file names for a broker (data/slip_issuers.toml: a
carrying dealer such as Webull Canada's, `issuer_aliases`), then the
slips of one broker are shared out among its broker accounts so that
the slips' dividends, withholding and interest are closest to each
account's (a broker account with no income rows in the books — an
export of trades only — when none of the broker's has any; several
such: not told apart); a
T3 goes to the fund (and broker account) whose distributions match it
by name and amount. What cannot be placed is listed, not guessed. The
broker account is written as a key salted with the project's own salt
(lib/slip_audit.broker_key, the salt in work/), never the books' hash
of the account number. Each slip is written once: a duplicate is
dropped and an amended slip replaces its original (plan_import).
"""
from __future__ import annotations

import itertools
import re
import os
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SLIP_TITLES = {"T5": "Statement of Investment Income",
               "T3": "Statement of Trust Income"}
# The amount boxes of each slip (the boxes CRA prints with money).
AMOUNT_BOXES = {
    "T5": ("10", "11", "12", "13", "14", "15", "16", "17", "18", "19",
           "20", "24", "25", "26", "30"),
    "T3": ("21", "22", "23", "24", "25", "26", "30", "31", "32", "33",
           "34", "35", "37", "38", "39", "40", "41", "42", "45", "46",
           "47", "48", "49", "50", "51"),
}
CURRENCY_BOX = "27"

_FROM_RE = re.compile(r"^\s*(\d{4})\s+(T5|T3)\s+slip\s+\(([A-Za-z ]+)\)\s+"
                      r"from\s+(\S.*?)\s*$", re.I)
# A line that names a slip but is not the from-line taxjson reads (a
# layout CRA changed, a status it does not print in English).
_SLIPLIKE_RE = re.compile(r"^\s*\d{4}\s+T[35]\s+slip\b", re.I)
# CRA's French page ("Feuillet T5 de 2025 (original) de ..."): not read.
_FRENCH_RE = re.compile(r"\bfeuillet\s+T[35]\b|\bÉtat\s+des\s+revenus\b|"
                        r"\bEtat\s+des\s+revenus\b", re.I)
_ROW_RE = re.compile(r"^\s*(\d{1,2})\s{2,}(?:\S.*?\s{2,})?(\S+)\s*$")
_HEAD_RE = re.compile(r"^\s*Box(?:\s+number)?(?:\s{2,}|$)")
_AMOUNT_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}$")
# Box 27 (foreign currency): a code, or the wording CRA may print.
_CURRENCY_ROW_RE = re.compile(r"^\s*27\s{2,}(?:\S.*?\s{2,})?(\S.*?)\s*$")
_CURRENCY_WORDS = {
    "CAD": "CAD", "CDN": "CAD", "CDN$": "CAD", "C$": "CAD", "CA$": "CAD",
    "CANADIAN DOLLARS": "CAD", "CANADIAN DOLLAR": "CAD",
    "USD": "USD", "US$": "USD", "U.S.$": "USD", "US DOLLARS": "USD",
    "U.S. DOLLARS": "USD", "US DOLLAR": "USD", "U.S. DOLLAR": "USD",
    "UNITED STATES DOLLARS": "USD",
}
# Statuses CRA prints in the from-line.
STATUSES = ("original", "amended", "cancelled")
# A line after the from-line that is not the issuer's name: the
# recipient's details (a digit, a postal code, a SIN), a label.
_NOT_ISSUER_RE = re.compile(
    r"\d|[A-Z]\d[A-Z]\s?\d[A-Z]\d|\b(?:recipient|signed\s+in|social\s+"
    r"insurance|sin|address|name|account|box)\b", re.I)
ISSUER_MAX_LINES = 2          # the from-line's name and one more line
# Words of an issuer's or a broker's name that tell nothing apart.
_GENERIC = {"INC", "LTD", "LTEE", "CORP", "CORPORATION", "COMPANY", "THE",
            "AND", "OF", "DE", "DU", "LA", "LES", "ET", "EN", "CANADA",
            "CANADIAN", "SERVICES", "INVESTMENT", "INVESTMENTS", "FUND",
            "FUNDS", "ETF", "INDEX", "TRUST", "LIMITED", "FINANCIAL",
            "SECURITIES", "PLC", "INC.", "LTD.", "DIRECT", "INVESTING",
            "BROKERAGE", "BROKERS", "BROKER", "BANK", "ROYAL", "GLOBAL",
            "ASSET", "ASSETS", "MANAGEMENT", "WEALTH", "CAPITAL", "GROUP",
            "ONLINE", "TRADING", "PLACEMENTS", "COURTAGE", "PARTNERS",
            "LLC", "LP", "ULC", "CO"}
# Words that only say what legal form a company has: left out when a
# broker's display name is looked for in an issuer's name.
_LEGAL = {"INC", "LTD", "LTEE", "CORP", "CORPORATION", "COMPANY", "THE",
          "LIMITED", "LLC", "LP", "ULC", "CO", "PLC"}


class CraSlipError(ValueError):
    """A PDF that cannot be read as a CRA T5 / T3 slip."""


@dataclass
class CraSlip:
    shown: str                    # the file's shown name (ids masked)
    year: int
    type: str                     # T5 | T3
    status: str                   # original | amended | cancelled
    issuer: str
    currency: str = "CAD"
    boxes: Dict[str, float] = field(default_factory=dict)

    def total(self, boxes) -> float:
        return sum(self.boxes.get(b, 0.0) for b in boxes)


def parse_text(text: str, shown: str = "slip") -> CraSlip:
    """A CRA slip page's text (pdftotext -layout): exactly one slip.
    Raises CraSlipError when it is not a T5 or T3 slip, or holds
    several (parse_slips reads those)."""
    slips = parse_slips(text, shown)
    if len(slips) != 1:
        raise CraSlipError(f"{shown}: {len(slips)} slips in one text — "
                           f"read it with parse_slips")
    return slips[0]


def _sections(text: str) -> List[List[str]]:
    """The text cut at each from-line: one list of lines per slip, from
    its from-line to the next one. A page (form feed) that has its own
    from-line starts there: the lines above it on that page (the
    browser's header, the recipient's details) belong to no slip."""
    pages = text.split("\f")
    out: List[List[str]] = []
    for page in pages:
        lines = page.splitlines()
        heads = [i for i, ln in enumerate(lines) if _FROM_RE.match(ln)]
        if not heads:
            if out:
                out[-1] += lines          # a slip's next page
            continue
        for k, i in enumerate(heads):
            j = heads[k + 1] if k + 1 < len(heads) else len(lines)
            out.append(lines[i:j])
    return out


def _issuer(m: "re.Match", rest: List[str]) -> Tuple[str, int]:
    """(issuer, lines used after the from-line): the from-line's name
    and at most ISSUER_MAX_LINES - 1 more lines that read as a name —
    never a line with a digit, a postal code or a label (the
    recipient's details)."""
    parts = [m.group(4)]
    j = 0
    while (j < len(rest) and len(parts) < ISSUER_MAX_LINES
           and rest[j].strip() and not _HEAD_RE.match(rest[j])
           and not _ROW_RE.match(rest[j])
           and not _NOT_ISSUER_RE.search(rest[j])):
        parts.append(rest[j].strip())
        j += 1
    return " ".join(" ".join(parts).split()), j


def _currency(raw: str, where: str) -> str:
    v = " ".join(raw.upper().split())
    cur = _CURRENCY_WORDS.get(v)
    if cur is None and re.fullmatch(r"[A-Z]{3}", v):
        cur = v
    if cur is None:
        raise CraSlipError(f"{where}: box 27 (foreign currency) reads "
                           f"{raw.strip()!r} — not a currency taxjson "
                           f"knows; type the slip into slips.toml with "
                           f"its currency")
    return cur


def _one_slip(lines: List[str], where: str) -> CraSlip:
    m = _FROM_RE.match(lines[0])
    assert m is not None
    typ = m.group(2).upper()
    status = m.group(3).strip().lower()
    if status not in STATUSES:
        raise CraSlipError(f"{where}: a {typ} slip marked "
                           f"({m.group(3).strip()}) — taxjson reads "
                           f"original, amended and cancelled slips")
    issuer, used = _issuer(m, lines[1:])
    slip = CraSlip(shown=where, year=int(m.group(1)), type=typ,
                   status=status, issuer=issuer)
    for ln in lines[1 + used:]:
        if _FROM_RE.match(ln) or _SLIPLIKE_RE.match(ln):
            raise CraSlipError(f"{where}: a second slip line inside the "
                               f"slip — not read")
        c = _CURRENCY_ROW_RE.match(ln)
        if c and typ == "T5":
            slip.currency = _currency(c.group(1), where)
            continue
        r = _ROW_RE.match(ln)
        if not r:
            continue
        box = r.group(1).lstrip("0") or "0"
        val = r.group(2)
        if box in AMOUNT_BOXES[typ] and _AMOUNT_RE.match(val):
            if box in slip.boxes:
                raise CraSlipError(
                    f"{where}: box {box} twice in one {typ} slip — the "
                    f"page cannot be read cleanly; not imported")
            slip.boxes[box] = float(val.replace(",", ""))
    if not slip.boxes:
        raise CraSlipError(f"{where}: a {typ} slip with no box amounts")
    return slip


def parse_slips(text: str, shown: str = "slip") -> List[CraSlip]:
    """Every slip in a CRA page's text (pdftotext -layout): one per
    from-line ("YYYY T5 slip (original) from ..."), each read from its
    own lines only — two slips in one PDF (pages joined, a merged file)
    are two slips, never one. Raises CraSlipError when it holds no T5
    or T3 slip, a slip-shaped line taxjson cannot read, a French page,
    or a slip that cannot be read cleanly (the whole file is refused:
    a part read is a wrong slip)."""
    if _FRENCH_RE.search(text) and not any(
            _FROM_RE.match(ln) for ln in text.splitlines()):
        raise CraSlipError(f"{shown}: a French-language slip page — "
                           f"taxjson reads CRA's English page (switch My "
                           f"Account to English and print it again), or "
                           f"type the slip into slips.toml")
    for ln in text.splitlines():
        if _SLIPLIKE_RE.match(ln) and not _FROM_RE.match(ln):
            raise CraSlipError(f"{shown}: a slip line taxjson cannot read "
                               f"(not 'YYYY T5 slip (original) from "
                               f"...') — type the slip into slips.toml")
    secs = _sections(text)
    if not secs:
        raise CraSlipError(f"{shown}: not a CRA T5 or T3 slip (no "
                           f"'YYYY T5 slip (original) from ...' line)")
    out = []
    for k, lines in enumerate(secs, 1):
        where = shown if len(secs) == 1 else f"{shown} #{k}"
        out.append(_one_slip(lines, where))
    return out


def pdf_text(path: Path) -> str:
    from taxjson.lib.brokerages.base import shown_name
    exe = shutil.which("pdftotext")
    if exe is None:
        raise CraSlipError("pdftotext is not installed (poppler-utils: "
                           "`sudo apt install poppler-utils`, `brew "
                           "install poppler`); it reads the CRA PDFs")
    # An absolute path after `--`: a file name starting with '-' is
    # never read as an option (2026-10 security review LOW d).
    arg = os.path.abspath(str(path))
    try:
        p = subprocess.Popen([exe, "-layout", "-enc", "UTF-8", "--", arg,
                              "-"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL)
    except OSError as e:
        # The OSError's text names the path: only its reason is said.
        raise CraSlipError(f"pdftotext failed on {shown_name(path)}: "
                           f"{e.strerror or type(e).__name__}") from None
    out, too_big = _read_capped(p, PDF_TEXT_MAX, 60)
    if out is None:
        raise CraSlipError(f"pdftotext took over 60 s on "
                           f"{shown_name(path)}")
    if too_big:
        raise CraSlipError(f"pdftotext wrote over {PDF_TEXT_MAX // 2**20} "
                           f"MB of text — not a CRA slip PDF")
    if p.returncode != 0:
        raise CraSlipError(f"pdftotext could not read it (exit "
                           f"{p.returncode})")
    return out.decode("utf-8", errors="replace")


# A CRA slip PDF is a few KB of text; a file that expands past this is
# refused instead of read into memory.
PDF_TEXT_MAX = 8 * 2**20


def _read_capped(p: "subprocess.Popen", cap: int, timeout: float
                 ) -> Tuple[Optional[bytes], bool]:
    """(stdout, over the cap) of `p`, reading at most `cap` + 1 bytes;
    the process is killed when it exceeds the cap or `timeout` seconds
    ((None, False) on a timeout)."""
    import threading
    buf = bytearray()
    over = [False]

    def pump() -> None:
        while True:
            chunk = p.stdout.read(65536)
            if not chunk:
                return
            buf.extend(chunk)
            if len(buf) > cap:
                over[0] = True
                p.kill()
                return
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        p.kill()
        t.join(5)
        p.wait()
        return None, False
    p.wait()
    p.stdout.close()
    return bytes(buf[:cap]), over[0]


def read_pdf(path: Path) -> List[CraSlip]:
    """The slips of one CRA PDF (usually one; a merged file several)."""
    from taxjson.lib.brokerages.base import shown_name
    shown = shown_name(path)
    try:
        text = pdf_text(Path(path))
    except CraSlipError as e:
        raise CraSlipError(f"{shown}: {e}") from None
    return parse_slips(text, shown)


def pdf_paths(args: List[str]) -> List[Path]:
    """The PDFs named, a folder's *.pdf (not recursive); a file named
    twice (itself and in its folder) once."""
    from taxjson.lib.brokerages.base import shown_name
    out: List[Path] = []
    seen = set()

    def add(q: Path) -> None:
        try:
            k = q.resolve()
        except OSError:
            k = q
        if k not in seen:
            seen.add(k)
            out.append(q)
    for a in args:
        p = Path(a)
        if p.is_dir():
            for q in sorted((q for q in p.iterdir()
                             if q.is_file() and q.suffix.lower() == ".pdf"),
                            key=lambda q: q.name.lower()):
                add(q)
        elif p.is_file():
            add(p)
        else:
            raise CraSlipError(f"{shown_name(a)}: no such file or folder")
    return out


# ------------------------------------------------------------ placing
def _words(text: str) -> set:
    return {w for w in re.findall(r"[A-Z0-9]+", str(text).upper())
            if len(w) >= 2 and w not in _GENERIC}


def _all_words(text: str) -> set:
    return {w for w in re.findall(r"[A-Z0-9]+", str(text).upper())
            if len(w) >= 2}


# The shipped issuer aliases (package data; read by issuer_aliases).
ISSUERS_FILE = (Path(__file__).resolve().parent.parent / "data"
                / "slip_issuers.toml")


@dataclass(frozen=True)
class IssuerAlias:
    """An issuer CRA prints on a broker's slips that does not carry the
    broker's name (its carrying dealer): data/slip_issuers.toml."""
    broker: str                    # a taxjson broker id
    names: Tuple[Tuple[str, ...], ...]   # each name's words (_phrase)
    source: str                    # where the name was seen


def _phrase(text: str) -> Tuple[str, ...]:
    """A name's words in order: upper case, accents and punctuation
    dropped (D'INVESTISSEMENT -> D, INVESTISSEMENT), legal forms (INC,
    LTEE, ...) left out."""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()
    return tuple(w for w in re.findall(r"[A-Z0-9]+", t) if w not in _LEGAL)


def parse_issuer_aliases(doc: Dict[str, Any], where: str
                         ) -> List[IssuerAlias]:
    """The [[alias]] tables of a slip_issuers.toml document. Raises
    CraSlipError on an unknown key, an unknown broker id, a name with no
    distinctive word (it would take another institution's slips) or no
    source."""
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    if set(doc) - {"alias"}:
        raise CraSlipError(f"{where}: unknown key(s) "
                           f"{', '.join(sorted(set(doc) - {'alias'}))} — "
                           f"the file holds [[alias]] tables")
    out = []
    for i, t in enumerate(doc.get("alias") or [], 1):
        at = f"{where} [[alias]] #{i}"
        if not isinstance(t, dict) or set(t) - {"broker", "issuer",
                                                "source"}:
            raise CraSlipError(f"{at}: keys are broker, issuer, source")
        broker = str(t.get("broker") or "")
        ids = [b for b in DISPLAY_NAMES if b != "generic"]
        if broker not in ids:
            raise CraSlipError(f"{at}: broker {broker!r} is not a taxjson "
                               f"broker id ({', '.join(ids)})")
        names = t.get("issuer")
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not names:
            raise CraSlipError(f"{at}: issuer is a name or a list of "
                               f"names")
        phrases = []
        for n in names:
            ph = _phrase(n)
            if not ({w for w in ph if len(w) >= 2} - _GENERIC):
                raise CraSlipError(
                    f"{at}: issuer {n!r} has no distinctive word (only "
                    f"words such as INVESTMENT or SERVICES) — it would "
                    f"take other institutions' slips")
            phrases.append(ph)
        source = str(t.get("source") or "").strip()
        if not source:
            raise CraSlipError(f"{at}: no source (where the issuer name "
                               f"was seen)")
        out.append(IssuerAlias(broker, tuple(phrases), source))
    return out


@lru_cache(maxsize=None)
def issuer_aliases() -> Tuple[IssuerAlias, ...]:
    """The shipped issuer aliases (cached). Raises CraSlipError when the
    file cannot be read (a broken install)."""
    from taxjson.lib.tomlcompat import tomllib
    try:
        doc = tomllib.loads(ISSUERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CraSlipError(f"taxjson's slip issuer file {ISSUERS_FILE} "
                           f"cannot be read ({e}) — reinstall "
                           f"taxjson") from None
    return tuple(parse_issuer_aliases(doc, ISSUERS_FILE.name))


def _holds(half: Tuple[str, ...], name: Tuple[str, ...]) -> bool:
    n = len(name)
    return any(half[i:i + n] == name for i in range(len(half) - n + 1))


def alias_of_issuer(issuer: str) -> Optional[IssuerAlias]:
    """The data file's alias whose name stands, word for word and in
    order, in one half of the issuer's bilingual name ("ENGLISH
    INC./FRENCH INC"); None when none or several brokers'."""
    halves = [_phrase(h) for h in str(issuer).split("/")]
    hits = [a for a in issuer_aliases()
            if any(_holds(h, nm) for h in halves for nm in a.names)]
    return hits[0] if len({a.broker for a in hits}) == 1 else None


def broker_of_issuer(issuer: str) -> Optional[str]:
    """The broker id whose display name (lib/brokerages/detect
    DISPLAY_NAMES) the issuer's name carries in full — every word of it
    but a legal form, at least one of them distinctive (INTERACTIVE
    BROKERS CANADA INC. -> ib; RBC DIRECT INVESTING INC. -> rbc_direct;
    TD DIRECT INVESTING, RBC ROYAL BANK -> None) — or whose issuer
    alias it carries (data/slip_issuers.toml: CI INVESTMENT SERVICES
    INC. -> webull; CI DIRECT INVESTING -> None); None when none or
    several."""
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    iw = _all_words(issuer)
    hits = set()
    for b, name in DISPLAY_NAMES.items():
        if b == "generic":
            continue
        need = _all_words(name) - _LEGAL
        if need and need - _GENERIC and need <= iw:
            hits.add(b)
    a = alias_of_issuer(issuer)
    if a is not None:
        hits.add(a.broker)
    return next(iter(hits)) if len(hits) == 1 else None


@dataclass
class Group:
    """One broker account's book rows in one project account."""
    account: str
    hashes: Tuple[str, ...]
    broker: Optional[str]
    sources: Tuple[str, ...]
    rows: List[Any] = field(default_factory=list)


def _row_broker(acct: str, base: dict) -> Dict[str, str]:
    """{input file: broker id} from a base book's metadata (the parse
    file `<account>_<broker>.json` lists its input files)."""
    out: Dict[str, str] = {}
    meta = base.get("metadata") if isinstance(base, dict) else None
    for e in (meta or {}).get("sources") or []:
        if not isinstance(e, dict):
            continue
        f = Path(str(e.get("file") or "")).name
        if not (f.startswith(f"{acct}_") and f.endswith(".json")):
            continue
        broker = f[len(acct) + 1:-len(".json")]
        om = e.get("original_metadata") or {}
        for src in (om.get("source_accounts") or {}):
            out[str(src)] = broker
    return out


def book_groups(root: Path, cfg: Dict[str, Any], year: int
                ) -> List[Group]:
    """The broker accounts in the books of every slip account: rows that
    share a broker-account key (an IB statement of two accounts joins
    them); then, with no rows, each broker account an export names that
    no income row carries (a Webull export books trades only: its
    slips have no payments to match, but the account is that broker's):
    a candidate only for a broker none of whose accounts has any."""
    import json
    from taxjson.lib import slip_audit as SA
    books, _p = SA.load_books(root, cfg, year, SA.slip_accounts(cfg))
    groups: List[Group] = []
    bare: List[Group] = []
    for acct in SA.slip_accounts(cfg):
        try:
            base = json.loads((Path(root) / "work" / f"{acct}_base.json")
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            base = {}
        brk = _row_broker(acct, base)
        parent: Dict[str, str] = {}

        def find(h: str) -> str:
            while parent.setdefault(h, h) != h:
                h = parent[h]
            return h
        rows = [r for r in books if r.account == acct and r.hashes]
        for r in rows:
            for h in r.hashes[1:]:
                parent[find(h)] = find(r.hashes[0])
        by: Dict[str, Group] = {}
        for r in rows:
            k = find(r.hashes[0])
            g = by.setdefault(k, Group(acct, (), None, ()))
            g.rows.append(r)
            g.hashes = tuple(sorted(set(g.hashes) | set(r.hashes)))
            g.sources = tuple(sorted(set(g.sources) | {r.source}))
            b = brk.get(r.source)
            if b and g.broker is None:
                g.broker = b
        groups += [by[k] for k in sorted(by)]
        bare += _bare_groups(acct, base, brk, by.values())
    return groups + bare


def _bare_groups(acct: str, base: dict, brk: Dict[str, str],
                 have) -> List[Group]:
    """The broker accounts the account's exports name (the parse
    metadata's source_accounts) that no income row carries: one group
    each, with no rows — the hashes one file names together joined."""
    from taxjson.lib import slip_audit as SA
    seen = {h for g in have for h in g.hashes}
    parent: Dict[str, str] = {}

    def find(h: str) -> str:
        while parent.setdefault(h, h) != h:
            h = parent[h]
        return h
    files = SA._file_hashes(base)
    for f, hs in files.items():
        hs = [h for h in hs if h not in seen]
        for h in hs:
            find(h)
        for h in hs[1:]:
            parent[find(h)] = find(hs[0])
    out: Dict[str, Group] = {}
    for f in sorted(files):
        b = brk.get(f)
        for h in files[f]:
            if h in seen or not b:
                continue
            g = out.setdefault(find(h), Group(acct, (), None, ()))
            g.hashes = tuple(sorted(set(g.hashes) | {h}))
            g.sources = tuple(sorted(set(g.sources) | {f}))
            if g.broker is None:
                g.broker = b
    return [out[k] for k in sorted(out)]


def _via(issuer: str) -> str:
    """How the issuer's name gave the broker when an alias did (empty
    when its own name does)."""
    a = alias_of_issuer(issuer)
    if a is None:
        return ""
    return (f" (issuer {' '.join(a.names[0])}: {a.broker}'s carrying "
            f"dealer, {ISSUERS_FILE.name})")


def _cad(amount: float, cur: str, rate: Dict[str, Optional[float]]
         ) -> Optional[float]:
    if cur == "CAD":
        return amount
    r = rate.get(cur)
    return None if r is None else amount * r


@dataclass
class Placement:
    slip: CraSlip
    account: Optional[str] = None
    group: Optional[Group] = None
    key: str = ""                  # the broker-account key written
    security: str = ""             # a T3's fund, as the books spell it
    how: str = ""                  # how it was placed, or why not


_T3_INCOME = ("21", "23", "24", "25", "26", "49")
_T5_DIVS = ("10", "15", "18", "24")


def place(slips: List[CraSlip], groups: List[Group], year: int,
          rate: Dict[str, Optional[float]], ib_reports: List[Any] = (),
          account: Optional[str] = None) -> List[Placement]:
    """Give each slip a project account (and broker account, and a T3
    its fund) from the books; `account` places the slips no broker
    name or match places."""
    ystr = str(year)
    out = [Placement(s) for s in slips]
    t3_roots: Dict[Tuple[str, Tuple[str, ...]], set] = {}
    # T3s: the fund whose distributions match by name and amount.
    for pl in out:
        s = pl.slip
        if s.type != "T3":
            continue
        want_all = s.total(_T3_INCOME + ("42",))
        want_inc = s.total(_T3_INCOME)
        iw = _words(s.issuer)
        cands = []
        for g in groups:
            if account and g.account != account:
                continue
            roots: Dict[str, List[Any]] = {}
            for r in g.rows:
                if r.category in ("ca_div", "foreign") and r.root \
                        and r.tax_date.startswith(ystr):
                    roots.setdefault(r.root, []).append(r)
            for rt, rs in roots.items():
                have = sum(r.cad for r in rs)
                d = min(abs(have - want_all), abs(have - want_inc)) \
                    / max(want_all, 1.0)
                desc = set()
                for r in rs:
                    desc |= _words(getattr(r, "description", "") or "")
                name = (len(iw & desc) / len(iw)) if iw else 0.0
                cands.append((d, -name, g, rs[0].symbol, rt))
        good = [c for c in cands if c[0] <= 0.02 or (-c[1] >= 0.6
                                                     and c[0] <= 0.25)]
        good.sort(key=lambda c: (c[0] > 0.02, c[1], c[0]))
        if not good:
            pl.how = ("no fund in the books whose distributions match "
                      "its amounts or name")
            continue
        if len(good) > 1 and good[1][0] <= 0.02 and good[0][0] <= 0.02 \
                and abs(good[0][0] - good[1][0]) < 0.002 \
                and good[0][1] == good[1][1]:
            pl.how = (f"ambiguous: {good[0][3]} and {good[1][3]} "
                      f"distributions match it equally")
            continue
        d, nm, g, sym, rt = good[0]
        pl.group, pl.account, pl.security = g, g.account, sym
        pl.how = (f"fund {sym}: its distributions in the books "
                  f"{'match the amounts' if d <= 0.02 else 'match the name'}")
        t3_roots.setdefault((g.account, g.hashes), set()).add(rt)
    # T5s: by broker, shared out among the broker's accounts.
    by_broker: Dict[Optional[str], List[Placement]] = {}
    for pl in out:
        if pl.slip.type != "T5":
            continue
        s = pl.slip
        if s.currency != "CAD" and rate.get(s.currency) is None:
            # Its amounts cannot be set against the books' CAD: never
            # placed by a guess.
            pl.how = (f"a {s.currency} slip and no {s.currency} rate for "
                      f"{year} in the FX cache — run `taxjson run` (it "
                      f"fills the cache), or type the slip into slips.toml")
            continue
        by_broker.setdefault(broker_of_issuer(s.issuer), []).append(pl)
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    for broker, pls in by_broker.items():
        gs = [g for g in groups if broker and g.broker == broker
              and (not account or g.account == account)]
        shown = DISPLAY_NAMES.get(broker or "", broker or "")
        # A broker account with no income rows in the books (_bare_groups)
        # is a candidate only when none of the broker's has any: its
        # slips go by the payments otherwise, as before. Two with none:
        # nothing tells them apart — the slip is not placed by a guess.
        paid = [g for g in gs if g.rows]
        gs = paid or gs
        blind = len(gs) > 1 and not paid
        if not gs or blind:
            for pl in pls:
                if account:
                    pl.account = account
                    pl.how = (f"account {account}, as asked ("
                              + (f"{len(gs)} {shown} broker accounts and "
                                 f"no payments in the books to tell them "
                                 f"apart: no broker account written"
                                 if blind else
                                 "no broker account of the issuer in the "
                                 "books") + ")")
                elif blind:
                    pl.how = (f"its {shown} broker account cannot be told: "
                              f"the books hold {len(gs)} and no {year} "
                              f"payments in them to match — import it "
                              f"again naming its account (`taxjson "
                              f"slip-audit ACCOUNT --import-cra FILE`), or "
                              f"type it into slips.toml with "
                              f"broker_account")
                elif broker:
                    pl.how = (f"the issuer is {shown}'s"
                              + _via(pl.slip.issuer)
                              + f" and the books hold no {shown} broker "
                                f"account — import it again naming its "
                                f"account (`taxjson slip-audit ACCOUNT "
                                f"--import-cra FILE`), or add the {shown} "
                                f"export to inputs/<account>/")
                else:
                    pl.how = ("no broker in the books by the issuer's "
                              "name — import it again naming its account "
                              "(`taxjson slip-audit ACCOUNT --import-cra "
                              "FILE`), or it is outside the books (a bank "
                              "account)")
            continue
        books = []
        for g in gs:
            skip = t3_roots.get((g.account, g.hashes), set())
            rs = [r for r in g.rows if r.tax_date.startswith(ystr)
                  and r.root not in skip]
            books.append((
                sum(r.cad for r in rs if r.category in
                    ("ca_div", "foreign")),
                sum(r.cad for r in rs if r.category == "foreign_tax"),
                sum(r.cad for r in rs if r.category == "interest")))
        cad = []
        for pl in pls:
            s = pl.slip
            vals = [_cad(s.total(_T5_DIVS), s.currency, rate),
                    _cad(s.boxes.get("16", 0.0), s.currency, rate),
                    _cad(s.boxes.get("13", 0.0), s.currency, rate)]
            cad.append([v if v is not None else 0.0 for v in vals])
        best = None
        if len(gs) ** len(pls) <= 20000:
            for combo in itertools.product(range(len(gs)), repeat=len(pls)):
                cost = 0.0
                for gi in set(combo):
                    tot = [sum(cad[k][j] for k, c in enumerate(combo)
                               if c == gi) for j in range(3)]
                    cost += sum(abs(tot[j] - books[gi][j]) for j in range(3))
                if best is None or cost < best[0] - 1e-6:
                    best = (cost, combo)
        if best is None:
            for pl in pls:
                pl.how = "too many broker accounts to share the slips out"
            continue
        for pl, gi in zip(pls, best[1]):
            g = gs[gi]
            pl.group, pl.account = g, g.account
            pl.how = (f"{broker} broker account by its payments"
                      if len(gs) > 1 else f"the {broker} broker account"
                      ) + _via(pl.slip.issuer)
    # The broker account written: the group's one hash; when the group
    # holds several (an IB statement of two accounts), the one IB
    # dividends report whose figures match the slip, else the one hash
    # with no report; neither: not placed (a wrong key would make the
    # other account's report "payments only").
    for pl in out:
        g = pl.group
        if g is None:
            continue
        pl.key = g.hashes[0] if g.hashes else ""
        if len(g.hashes) <= 1:
            continue
        reps = [rep for rep in ib_reports if rep.account_hash in g.hashes]
        hits = [rep.account_hash for rep in reps
                if _report_matches(rep, pl, rate)]
        if len(set(hits)) == 1:
            pl.key = hits[0]
            continue
        bare = [h for h in g.hashes
                if h not in {rep.account_hash for rep in reps}]
        if not hits and reps and len(bare) == 1:
            pl.key = bare[0]
            continue
        if not reps:
            continue
        pl.how = ("its broker account cannot be told: the statement "
                  "holds several IB accounts and "
                  + ("several dividends reports match it"
                     if hits else "no dividends report matches it")
                  + " — type it into slips.toml with broker_account")
        pl.group = None
        pl.account = None
        pl.key = ""
    return out


def _report_cad(rep: Any, p: Any, c: Any,
                rate: Dict[str, Optional[float]]) -> Optional[float]:
    """A report component in CAD: its own amount when paid in CAD, IB's
    base amount when the base is CAD, else at the year's average."""
    if p.currency == "CAD":
        return c.gross
    if rep.base_currency == "CAD":
        return c.gross_base
    r = rate.get(p.currency)
    return None if r is None else c.gross * r


def _report_matches(rep: Any, pl: "Placement",
                    rate: Dict[str, Optional[float]]) -> bool:
    """IB's dividends report shows the slip's figures: a T5's boxes 24
    + 10, 18 and 15 (each within 0.5%, at least 1.00), a T3's fund."""
    s = pl.slip
    if s.type == "T5":
        cats = {"ca": ("eligible", "non_eligible"), "cg": ("cg_div",),
                "fo": ("foreign",)}
        want = {"ca": s.total(("24", "10")), "cg": s.total(("18",)),
                "fo": s.total(("15",))}
        got = {k: 0.0 for k in cats}
        for p in rep.payments:
            for c in p.components:
                if c.slip != "T5":
                    continue
                for k, cs in cats.items():
                    if c.category in cs:
                        v = _report_cad(rep, p, c, rate)
                        if v is None:
                            return False
                        got[k] += v
        cur = _cad(1.0, s.currency, rate) or 1.0
        if not any(want.values()):
            return False
        return all(abs(got[k] - want[k] * cur)
                   <= max(1.0, 0.005 * abs(want[k] * cur)) for k in cats)
    from taxjson.lib.slip_audit import _root
    want = s.total(_T3_INCOME + ("42",))
    got = 0.0
    held = False
    for p in rep.payments:
        if _root(p.symbol) != _root(pl.security):
            continue
        for c in p.components:
            if c.slip == "T3":
                held = True
                v = _report_cad(rep, p, c, rate)
                got += v or 0.0
    return held and abs(got - want) <= max(1.0, 0.005 * abs(want))


# ------------------------------------------------------------ deduping
def _norm(text: str) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", str(text).upper()))


def _boxes_key(boxes: Dict[str, float]) -> Tuple:
    return tuple(sorted((str(b), round(v, 2)) for b, v in boxes.items()
                        if abs(v) >= 0.005))


def drop_duplicates(slips: List[CraSlip]) -> Tuple[List[CraSlip],
                                                    List[str]]:
    """The slips with each one read twice kept once (a re-download `t3
    (1).pdf`, a file named with its folder): the same year, type,
    issuer, status, currency and boxes. (kept, notes)."""
    kept: List[CraSlip] = []
    notes: List[str] = []
    seen: Dict[Tuple, CraSlip] = {}
    for s in slips:
        k = (s.year, s.type, _norm(s.issuer), s.status, s.currency,
             _boxes_key(s.boxes))
        if k in seen:
            notes.append(f"{s.shown}: the same slip as {seen[k].shown} — "
                         f"read once")
            continue
        seen[k] = s
        kept.append(s)
    return kept, notes


@dataclass
class Existing:
    """A [[slip]] table already in slips.toml, as the import compares
    it: its number, type, account, fund, issuer, status, currency,
    boxes and the broker_keys it may carry."""
    index: int                    # 1-based [[slip]] number
    type: str
    account: str
    security: str                 # root
    issuer: str
    status: str
    currency: str
    boxes: Dict[str, float]
    keys: frozenset
    imported: bool                # source = "cra:..."


@dataclass
class ImportPlan:
    add: List[Tuple[Placement, str]] = field(default_factory=list)
    replace: List[Tuple[Existing, Placement, str]] = field(
        default_factory=list)
    superseded: List[str] = field(default_factory=list)   # in the import
    notes: List[str] = field(default_factory=list)


def plan_import(placed: List[Tuple[Placement, str]],
                existing: List[Existing]) -> ImportPlan:
    """What --import-cra adds to slips.toml: each placed slip (with the
    broker_key it is written with) once. A slip slips.toml already holds
    (the same type, account, fund, broker account, currency and boxes)
    is not added again; an amended slip replaces the original of the
    same issuer, account, fund and broker account — in this import or
    already in slips.toml — and an original whose amended slip is there
    is not added. Where it cannot be told which original an amended
    slip replaces, neither is imported (listed)."""
    from taxjson.lib.slip_audit import _root
    plan = ImportPlan()

    def ident(pl: Placement, key: str) -> Tuple:
        return (pl.slip.type, pl.account or "", _root(pl.security)
                if pl.security else "", key)

    def same_slip(e: Existing, pl: Placement, key: str) -> bool:
        return (e.type == pl.slip.type and e.account == pl.account
                and e.security == (_root(pl.security) if pl.security
                                   else "")
                and ((key in e.keys) if key else not e.keys))

    def same_issuer(e: Existing, pl: Placement) -> bool:
        return not e.imported or _norm(e.issuer) == _norm(pl.slip.issuer)

    live = [(pl, k) for pl, k in placed if pl.account]
    # In this import: an amended slip and the original it amends.
    drop = set()
    hold = set()
    for i, (pl, k) in enumerate(live):
        if pl.slip.status != "amended":
            continue
        orig = [j for j, (q, kq) in enumerate(live)
                if q.slip.status != "amended" and ident(q, kq) == ident(pl, k)
                and _norm(q.slip.issuer) == _norm(pl.slip.issuer)]
        if len(orig) == 1:
            drop.add(orig[0])
            plan.superseded.append(
                f"{live[orig[0]][0].slip.shown} (original): replaced by "
                f"the amended slip {pl.slip.shown}")
        elif len(orig) > 1:
            hold.add(i)
            hold.update(orig)
            plan.notes.append(
                f"{pl.slip.shown} (amended): it may amend "
                + ", ".join(live[j][0].slip.shown for j in orig)
                + " — which cannot be told; none imported (import the "
                  "originals with --write, then the amended slip)")
    for i, (pl, k) in enumerate(live):
        if i in drop or i in hold:
            continue
        sb = _boxes_key(pl.slip.boxes)
        dup = [e for e in existing if same_slip(e, pl, k)
               and e.currency == pl.slip.currency
               and _boxes_key(e.boxes) == sb]
        if dup:
            plan.notes.append(
                f"{pl.slip.shown}: already in slips.toml ([[slip]] "
                f"#{dup[0].index}, the same boxes)")
            continue
        twins = [e for e in existing if same_slip(e, pl, k)
                 and same_issuer(e, pl)]
        if pl.slip.status == "amended":
            origs = [e for e in twins if e.status != "amended"]
            if len(origs) == 1:
                plan.replace.append((origs[0], pl, k))
                continue
            if len(origs) > 1:
                plan.notes.append(
                    f"{pl.slip.shown} (amended): slips.toml holds several "
                    f"slips it may amend ([[slip]] "
                    + ", ".join(f"#{e.index}" for e in origs)
                    + ") — delete the one it amends and import it again")
                continue
        else:
            amd = [e for e in twins if e.status == "amended"]
            if amd:
                plan.notes.append(
                    f"{pl.slip.shown} (original): slips.toml holds its "
                    f"amended slip ([[slip]] #{amd[0].index}) — not "
                    f"imported")
                continue
        plan.add.append((pl, k))
    return plan


# ------------------------------------------------------------ writing
def table(pl: Placement, key: str = "") -> str:
    """The slips.toml [[slip]] table of a placed slip; `key` is the
    broker_key written for its broker account (slip_audit.broker_key:
    salted with the project's work/ salt, never the books' own hash)."""
    s = pl.slip
    boxes = ", ".join(f"{b} = {v:.2f}" for b, v in
                      sorted(s.boxes.items(), key=lambda kv: int(kv[0]))
                      if abs(v) >= 0.005)
    lines = ["[[slip]]", f'type = "{s.type}"',
             f'issuer = "{_toml_str(s.issuer)}"',
             f'account = "{_toml_str(pl.account)}"',
             f'currency = "{s.currency}"', f'status = "{s.status}"']
    if key:
        lines.append(f'broker_key = "{key}"   # '
                     + _comment(", ".join(pl.group.sources
                                          if pl.group else ())))
    if pl.security:
        lines.append(f'security = "{_toml_str(pl.security)}"')
    lines.append(f'source = "cra:{_toml_str(s.shown)}"')
    lines.append(f"boxes = {{ {boxes} }}")
    return "\n".join(lines)


def _toml_str(s: str) -> str:
    """A TOML basic string's body: backslash, quote and every control
    character escaped (a name holding a newline or a NUL is one line)."""
    out = []
    for ch in str(s):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)


def _comment(s: str) -> str:
    """Text safe after a TOML '#': control characters dropped."""
    return "".join(ch if ord(ch) >= 0x20 and ord(ch) != 0x7F else "?"
                   for ch in str(s))
