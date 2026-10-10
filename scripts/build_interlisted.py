#!/usr/bin/env python3
"""build_interlisted — the interlisted master, src/taxjson/data/interlisted.toml.

The master lists the Canadian shares that also trade in the United States
under the SAME share class: a TSX / TSX Venture listing and its US
exchange listing (NYSE, Nasdaq, NYSE American, NYSE Arca, Cboe), and its
US over-the-counter listings (the "...F" ordinary-share symbols). Every
pair it ships is a fact checked against OpenFIGI: the Canadian line and
the US line carry the same share-class FIGI, and an exchange listing is
in the Nasdaq Trader symbol directory. A depositary receipt (a CDR in
Canada, an ADR / ADS in the US) has a share-class FIGI of its own, so it
is never paired; a CDR whose root is a US ticker is listed apart, as a
DISTINCT pair. The master holds no ISIN or CUSIP; an entry's `domicile`
is the country code of its share class's ISINs (OpenFIGI ISIN lookups in
the cache), when known. A TSX fund's US-dollar line `X.U` with the same
share-class FIGI as its CAD line `X` is the same units (an entry with no
US listing; a `.U` of another class is another series, left out). A
FINRA temporary OTC symbol (fifth letter D) is never shipped: its
permanent spelling (fifth letter F) when OpenFIGI gives it the same
class. Coverage: TSX and TSX Venture (not Cboe Canada, not the CSE).

Corrections (the maintainer's history file, load_history): a listing
shipped in error is retracted (`retracted`, never re-added); an ended US
ticker that names another security today refuses the build unless the
entry records `reused_by` (check_reused). Each entry keeps `first_seen`
and, for a listing added later, `added` (`since`): `taxjson
update-tobase-map` tells a new line from one the user deleted by them.

The TMX lists (interlisted companies, the TSX / TSXV issuer workbook) are
HINTS only: they say which roots to ask OpenFIGI about. Nothing from them
is shipped except what OpenFIGI confirms (the venue, sector and names in
the master are OpenFIGI's).

Two steps:

  --fetch   maintainer only, needs the network: download the hint lists
            and the Nasdaq Trader directories into the cache folder, then
            ask OpenFIGI (no API key, no identity sent) for every job the
            build needs that the cache lacks. The cache must be outside
            the repository (raw downloads are never committed).
  (build)   offline and reproducible: the cache plus the previous master
            give the new master. Append-only: an entry is never deleted;
            a listing that is gone gets `until` (the build date) and stays,
            so the books of the years it traded still pool it.

Usage:
  scripts/build_interlisted.py --cache DIR [--fetch] [--previous FILE]
                               [--out FILE] [--date YYYY-MM-DD]
                               [--history FILE] [--report FILE]

Cache folder (flat): interlisted-companies.txt, tsx-tsxv-listed-companies-
YYYY-MM-DD.xlsx, nasdaqlisted.txt, otherlisted.txt, openfigi_cache.json,
fetched.json ({"source": "as-of"}) and the maintainer's
interlisted_history.toml (ended pairs, load_history).
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "src" / "taxjson" / "data" / "interlisted.toml"
HISTORY = "interlisted_history.toml"
SCHEMA_VERSION = 1

TMX_TXT = "interlisted-companies.txt"
TMX_XLSX_PREFIX = "tsx-tsxv-listed-companies-"
NASDAQ = "nasdaqlisted.txt"
OTHER = "otherlisted.txt"
FIGI_CACHE = "openfigi_cache.json"
FETCHED = "fetched.json"

URLS = {
    TMX_TXT: "https://www.tsx.com/files/trading/interlisted-companies.txt",
    "tmx_xlsx": "https://www.tsx.com/en/resource/571",
    NASDAQ: "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    OTHER: "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
FIGI_URL = "https://api.openfigi.com/v3/mapping"
# No identity, no contact address: a generic agent string only.
USER_AGENT = "taxjson-build-interlisted"

# TMX "International Market" values that are US exchanges (hint).
US_MARKETS = ("NYSE", "NYSE Mkt", "NasdaqCM", "NasdaqGS", "NasdaqGM",
              "NASDAQ", "NYSE Arca")
# Nasdaq Trader otherlisted.txt exchange codes.
OTHER_EXCH = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca",
              "Z": "Cboe BZX", "V": "IEX"}
# OpenFIGI security types that are receipts, never the share itself.
RECEIPT_TYPES = ("ADR", "Canadian DR", "NY Reg Shrs", "GDR", "EDR")
FUND_TYPES = ("Closed-End Fund", "ETP", "Open-End Fund", "Mutual Fund")
UNIT_TYPES = ("REIT", "Unit", "Ltd Part", "MLP", "Royalty Trst")
# OpenFIGI CN spellings tried for a TMX workbook root that resolves to
# nothing: the workbook names the issuer's root, and an issuer whose
# shares trade only as a class or as trust units is listed under it
# (TECK -> TECK/B, HR -> HR-U for HR.UN, ACO -> ACO/X).
CLASS_SPELLINGS = ("-U", "/A", "/B", "/X", "/Y")


# ------------------------------------------------------------ spellings

def tsx_to_bbg(sym: str) -> str:
    """A TSX root as written (BEP.UN, TECK.B, CEF.U) -> OpenFIGI's CN
    ticker (BEP-U, TECK/B, CEF/U)."""
    if sym.endswith(".UN"):
        return sym[:-3] + "-U"
    return sym.replace(".", "/")


def bbg_to_tsx(t: str) -> str:
    if t.endswith("-U"):
        return t[:-2] + ".UN"
    return t.replace("/", ".")


def us_to_bbg(sym: str) -> str:
    return sym.replace(".", "/")


def bbg_to_us(t: str) -> str:
    return t.replace("/", ".")


def book_ca(root: str) -> str:
    """A Canadian listing as the books spell it: ROOT.TO (every parser
    books a TSX Venture line as .TO; taxjson treats X.V and X.TO as one
    listing in a Canadian project)."""
    return f"{root}.TO"


def book_us(root: str) -> str:
    return f"{root}.US"


# ------------------------------------------------------------ xlsx

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships"}
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_sheets(path: Path):
    """Yield (sheet name, rows as lists of str): a minimal stdlib reader."""
    z = zipfile.ZipFile(path)
    shared: List[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in
                                  si.iter("{%s}t" % _NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target")
              for r in rels.iter(_REL + "Relationship")}
    for sh in wb.find("m:sheets", _NS):
        t = target[sh.get("{%s}id" % _NS["r"])].lstrip("/")
        if not t.startswith("xl/"):
            t = "xl/" + t
        root = ET.fromstring(z.read(t))
        rows = []
        for row in root.iter("{%s}row" % _NS["m"]):
            cells = {}
            for c in row.findall("m:c", _NS):
                v = c.find("m:v", _NS)
                if c.get("t") == "s" and v is not None:
                    val = shared[int(v.text)]
                elif c.get("t") == "inlineStr":
                    val = "".join(x.text or "" for x in
                                  c.iter("{%s}t" % _NS["m"]))
                else:
                    val = v.text if v is not None else ""
                cells[_col(c.get("r"))] = (val or "").strip()
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        yield sh.get("name"), rows


# ------------------------------------------------------------ loaders

def load_tmx_txt(path: Path) -> Tuple[str, List[Dict[str, str]]]:
    """(as-of line, rows) of TMX's interlisted-companies.txt (a hint)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    asof = lines[0].replace("As of", "").strip() if lines else ""
    rows: List[Dict[str, str]] = []
    hdr: Optional[List[str]] = None
    for ln in lines[1:]:
        if not ln.strip():
            continue
        parts = [p.strip() for p in ln.split("\t")]
        if parts[0] == "Symbol":
            hdr = parts
            continue
        if hdr is None or ":" not in parts[0]:
            continue
        d = dict(zip(hdr, parts))
        sym, venue = d["Symbol"].rsplit(":", 1)
        d["ca_symbol"], d["venue"] = sym, venue
        rows.append(d)
    return asof, rows


def tmx_xlsx_path(cache: Path) -> Optional[Path]:
    found = sorted(p for p in cache.glob(TMX_XLSX_PREFIX + "*.xlsx"))
    return found[-1] if found else None


def load_tmx_xlsx(path: Optional[Path]) -> Dict[Tuple[str, str], Dict[str, str]]:
    """{(root, TSX|TSXV): issuer row} of the TMX issuer workbook (a hint)."""
    out: Dict[Tuple[str, str], Dict[str, str]] = {}
    if path is None:
        return out
    for name, rows in xlsx_sheets(path):
        if "Issuers" not in name:
            continue
        hi = next((i for i, r in enumerate(rows) if r and r[0] == "Co_ID"),
                  None)
        if hi is None:
            continue
        hdr = [" ".join(h.split()) for h in rows[hi]]
        venue = "TSX" if name.startswith("TSX ") else "TSXV"
        for r in rows[hi + 1:]:
            if r and r[0]:
                d = dict(zip(hdr, r))
                if d.get("Root Ticker"):
                    out[(d["Root Ticker"], venue)] = d
    return out


def load_us_directory(cache: Path) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """({US symbol: {exchange, test}}, the files' creation stamp) of the
    Nasdaq Trader symbol directory (exchange-listed only)."""
    us: Dict[str, Dict[str, Any]] = {}
    stamp = ""
    for fname, is_nasdaq in ((NASDAQ, True), (OTHER, False)):
        p = cache / fname
        if not p.is_file():
            continue
        for ln in p.read_text(encoding="utf-8",
                              errors="replace").splitlines()[1:]:
            if ln.startswith("File Creation Time"):
                stamp = ln.split(":", 1)[1].split("|")[0].strip()
                continue
            q = ln.split("|")
            if len(q) < 7:
                continue
            if is_nasdaq:
                us[q[0]] = {"exchange": "Nasdaq", "test": q[3] == "Y"}
            else:
                us[q[0]] = {"exchange": OTHER_EXCH.get(q[2], q[2]),
                            "test": q[6] == "Y"}
    return us, stamp


def stamp_date(stamp: str) -> str:
    """Nasdaq Trader's 'MMDDYYYYHH:MM' creation stamp as YYYY-MM-DD."""
    m = re.match(r"(\d{2})(\d{2})(\d{4})", stamp or "")
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


# ------------------------------------------------------------ OpenFIGI

class Figi:
    """OpenFIGI v3 mapping with an on-disk cache. Offline (online=False)
    a job the cache lacks maps to None ("not looked up"), counted in
    `missing`; online it is posted (no API key: 10 jobs a request, 25
    requests a minute)."""
    BATCH = 10
    PAUSE = 2.6

    def __init__(self, path: Path, online: bool = False):
        self.path = path
        self.online = online
        self.cache: Dict[str, Any] = {}
        if path.is_file():
            self.cache = json.loads(path.read_text(encoding="utf-8"))
        self.requests = 0
        self.missing = 0
        self.errors = 0

    @staticmethod
    def key(job: Dict[str, str]) -> str:
        return json.dumps(job, sort_keys=True)

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.cache, indent=0, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def _post(self, jobs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        body = json.dumps(jobs).encode()
        for attempt in range(6):
            req = urllib.request.Request(
                FIGI_URL, data=body,
                headers={"Content-Type": "application/json",
                         "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self.requests += 1
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(20 * (attempt + 1))
                    continue
                if e.code in (400, 413):
                    raise
                time.sleep(5)
        raise RuntimeError("OpenFIGI: too many retries")

    def map(self, jobs: List[Dict[str, str]]) -> List[Optional[List[Dict[str, Any]]]]:
        if self.online:
            todo: List[Dict[str, str]] = []
            seen = set()
            for j in jobs:
                k = self.key(j)
                if k not in self.cache and k not in seen:
                    seen.add(k)
                    todo.append(j)
            for i in range(0, len(todo), self.BATCH):
                chunk = todo[i:i + self.BATCH]
                for j, r in zip(chunk, self._post(chunk)):
                    if "error" in r:
                        # Not "not found": asked again by the next fetch.
                        self.errors += 1
                        continue
                    self.cache[self.key(j)] = r.get("data", []) or []
                self._save()
                time.sleep(self.PAUSE)
        out: List[Optional[List[Dict[str, Any]]]] = []
        for j in jobs:
            v = self.cache.get(self.key(j))
            if v is None:
                self.missing += 1
            out.append(v)
        return out


def equity(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [d for d in rows or [] if d.get("marketSector") == "Equity"]


def share_classes(rows) -> set:
    return {d["shareClassFIGI"] for d in equity(rows)
            if d.get("shareClassFIGI")}


def ticker_job(t: str, exch: str) -> Dict[str, str]:
    return {"idType": "TICKER", "idValue": t, "exchCode": exch}


def class_job(sc: str, exch: str) -> Dict[str, str]:
    return {"idType": "ID_BB_GLOBAL_SHARE_CLASS_LEVEL", "idValue": sc,
            "exchCode": exch}


# ------------------------------------------------------------ history

def load_toml(path: Path) -> Dict[str, Any]:
    sys.path.insert(0, str(REPO / "src"))
    from taxjson.lib.tomlcompat import tomllib      # noqa: E402
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_history(path: Optional[Path]) -> List[Dict[str, Any]]:
    """The maintainer's ended interlistings and corrections (CACHE/
    interlisted_history.toml).

    `[[ended]]`: name, kind, share_class_figi, ca, us (exchange) and/or
    us_otc listings, until, and optionally `reused_by` (the security the
    ended US ticker names today: required when it names another one —
    check_reused — and then taxjson warns on every row of it) and
    `retracted`. Pairs that no longer trade, so OpenFIGI cannot pair
    them now (or a stale ticker OpenFIGI still names: the maintainer's
    end wins). Each names its share-class FIGI (looked up on OpenFIGI);
    one without is skipped and reported. Once built in, the previous
    master carries them (append-only).

    `[[correction]]`: share_class_figi and `retracted = [{listing,
    reason, since}]`: a listing the master shipped wrongly leaves the
    entry for good (merge_previous)."""
    if path is None or not Path(path).is_file():
        return []
    doc = load_toml(Path(path))
    out = [e for e in doc.get("ended") or [] if isinstance(e, dict)]
    # `[[correction]]`: a current entry's retracted listings only (no
    # ended pair): `share_class_figi` and `retracted`.
    out += [dict(e, _correction=True) for e in doc.get("correction") or []
            if isinstance(e, dict)]
    return out


def retractions(h: Dict[str, Any], today: str) -> List[Dict[str, str]]:
    """A history table's `retracted = [{listing, reason, since}]`: the
    listings a correction takes out of the master (never re-added, and
    `taxjson update-tobase-map` removes an unedited line naming them)."""
    out = []
    for r in h.get("retracted") or []:
        if isinstance(r, dict) and r.get("listing"):
            out.append({"listing": str(r["listing"]).upper(),
                        "reason": str(r.get("reason") or "a correction"),
                        "since": str(r.get("since") or today)})
    return out


# FINRA's fifth-letter identifier D: a new issue or a reverse split, a
# temporary symbol (about 20 business days) — never the listing to book.
_TEMP_OTC = re.compile(r"[A-Z]{4}D")
TEMP_OTC_REASON = ("a FINRA temporary symbol (fifth letter D: a new issue "
                   "or a reverse split), not the permanent OTC symbol")


# ------------------------------------------------------------ build

def build(cache: Path, previous: Optional[Dict[str, Any]], today: str,
          figi: Figi, history: List[Dict[str, Any]],
          stamp: Optional[str] = None
          ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(master document, report). Offline unless `figi.online`. `stamp`:
    the master's `generated` value (next_stamp; default `today`), also
    each new entry's `first_seen` and each new listing's `since`."""
    stamp = stamp or today
    report: Dict[str, Any] = collections.OrderedDict()
    usdir, us_stamp = load_us_directory(cache)
    tmx_txt = cache / TMX_TXT
    asof, txt = load_tmx_txt(tmx_txt) if tmx_txt.is_file() else ("", [])
    xlsx = tmx_xlsx_path(cache)
    issuers = load_tmx_xlsx(xlsx)
    report["hint_rows"] = len(txt)
    report["hint_issuers"] = len(issuers)

    def listed(t: str) -> bool:
        return t in usdir and not usdir[t]["test"]

    # 1. Candidate Canadian roots (hints): TMX's interlisted rows with a
    #    US market, and every TSX / TSXV operating issuer's root.
    cand: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, str]] = []
    for r in txt:
        if r.get("International Market") not in US_MARKETS:
            continue
        ca = r["ca_symbol"]
        if re.search(r"\.WT(\.|$)", ca):
            excluded.append({"ca": ca, "why": "warrant"})
            continue
        if ".PR." in ca:
            excluded.append({"ca": ca, "why": "preferred share"})
            continue
        cand.setdefault(ca, {"us_hint": set()})["us_hint"].add(
            r.get("US Symbol") or "")
    cdr_roots = []
    for (root, _venue), d in sorted(issuers.items()):
        typ = d.get("SP_Type", "")
        if typ == "CDR":
            cdr_roots.append(root)
            continue
        if typ == "Exchange Traded Funds":
            continue
        cand.setdefault(root, {"us_hint": set()})
    for h in history:
        if h.get("_correction"):
            continue
        for c in h.get("ca") or []:
            if c.endswith(".TO"):
                cand.setdefault(c[:-3], {"us_hint": set()})
    etf_roots = sorted({root for (root, _v), d in issuers.items()
                        if d.get("SP_Type", "") == "Exchange Traded Funds"})

    # 2. Canadian root -> one share-class FIGI.
    roots = sorted(cand)
    res = figi.map([ticker_job(tsx_to_bbg(c), "CN") for c in roots])
    root_sc: Dict[str, str] = {}
    root_rows: Dict[str, List[Dict[str, Any]]] = {}
    unresolved = 0
    for c, rows in zip(roots, res):
        scs = share_classes(rows)
        if len(scs) == 1:
            root_sc[c] = scs.pop()
            root_rows[c] = equity(rows)
        else:
            unresolved += 1
    # A bare issuer root with no share class: its class and unit lines.
    alt = [(c, c + sfx) for c, rows in zip(roots, res)
           if not share_classes(rows) and re.fullmatch(r"[A-Z0-9]+", c)
           for sfx in CLASS_SPELLINGS]
    by_class_spelling = []
    for (c, t), rows in zip(alt, figi.map([ticker_job(t, "CN")
                                            for _c, t in alt])):
        scs1 = share_classes(rows)
        ca = bbg_to_tsx(t)
        if len(scs1) == 1 and ca not in root_sc:
            cand.setdefault(ca, {"us_hint": set()})
            root_sc[ca] = scs1.pop()
            root_rows[ca] = equity(rows)
            by_class_spelling.append(ca)
    roots = sorted(cand)
    report["roots_asked"] = len(roots)
    report["roots_resolved"] = len(root_sc)
    report["roots_resolved_by_class_spelling"] = sorted(by_class_spelling)
    # The hint's US symbol, resolved on its own (it must be the same class).
    hint_syms = sorted({u for c in cand.values() for u in c["us_hint"] if u})
    hres = dict(zip(hint_syms, figi.map([ticker_job(us_to_bbg(u), "US")
                                         for u in hint_syms])))

    # 2b. TSX ETFs: a US-dollar line X.U of the CAD line X with the same
    #     share-class FIGI is the same units (one security, booked
    #     under the CAD line); a .U of another class is another series.
    currency_lines: set = set()
    etf_pairs: Dict[str, Tuple[str, List[Dict[str, Any]]]] = {}
    report["etf_usd_lines_other_class"] = []
    ejobs = [ticker_job(tsx_to_bbg(r), "CN") for r in etf_roots] + \
        [ticker_job(tsx_to_bbg(r) + "/U", "CN") for r in etf_roots]
    eres = figi.map(ejobs)
    n = len(etf_roots)
    for r, a, b in zip(etf_roots, eres[:n], eres[n:]):
        sa, sb = share_classes(a), share_classes(b)
        if not sb:
            continue
        if len(sa) == 1 and sa == sb:
            etf_pairs[next(iter(sa))] = (r, equity(a))
        else:
            report["etf_usd_lines_other_class"].append(f"{r}.U")
    report["etf_usd_lines"] = len(etf_pairs)

    # 3. Share class -> every US ticker and every CN ticker.
    scs = sorted(set(root_sc.values()))
    us_res = dict(zip(scs, figi.map([class_job(s, "US") for s in scs])))
    cn_res = dict(zip(scs, figi.map([class_job(s, "CN") for s in scs])))

    # A temporary OTC symbol (FINRA's fifth letter D): the permanent
    # spelling (the fifth letter F) when OpenFIGI gives it the same class.
    temp = sorted({(sc, bbg_to_us(d.get("ticker") or ""))
                   for sc in scs for d in equity(us_res.get(sc))
                   if _TEMP_OTC.fullmatch(bbg_to_us(d.get("ticker") or ""))
                   and not listed(bbg_to_us(d.get("ticker") or ""))})
    perm = dict(zip(temp, figi.map([ticker_job(t[:-1] + "F", "US")
                                    for _sc, t in temp])))
    temp_otc: Dict[str, List[str]] = collections.defaultdict(list)
    report["otc_temporary_symbols"] = []
    by_sc: Dict[str, Dict[str, Any]] = {}
    adr_like = 0
    for c in roots:
        sc = root_sc.get(c)
        if sc is None:
            continue
        rows = root_rows[c]
        types = {d.get("securityType") for d in rows}
        if types & set(RECEIPT_TYPES):
            excluded.append({"ca": c, "why": "depositary receipt"})
            continue
        us_ex, us_otc = set(), set()
        for d in equity(us_res.get(sc)):
            if d.get("securityType") in RECEIPT_TYPES:
                adr_like += 1
                continue
            t = bbg_to_us(d.get("ticker") or "")
            if not t:
                continue
            if (sc, t) in perm:
                f = t[:-1] + "F"
                if sc in share_classes(perm[(sc, t)]):
                    t = f
                else:
                    if book_us(t) not in temp_otc[sc]:
                        temp_otc[sc].append(book_us(t))
                        report["otc_temporary_symbols"].append(
                            {"listing": book_us(t), "share_class_figi": sc,
                             "permanent": None})
                    continue
            (us_ex if listed(t) else us_otc).add(t)
        for u in cand[c]["us_hint"]:
            if u and listed(u) and sc in share_classes(hres.get(u)):
                us_ex.add(u)
        ca_all = {c}
        for d in equity(cn_res.get(sc)):
            t = bbg_to_tsx(d.get("ticker") or "")
            if t and not re.search(r"\.WT(\.|$)|\.PR\.", t):
                ca_all.add(t)
        if not us_ex and not us_otc:
            # No US listing: kept only for a US-dollar line of the same
            # units on the TSX (X.U beside X: one security).
            if c + ".U" not in ca_all:
                continue
            ca_all = {c, c + ".U"}
            currency_lines.add(sc)
        e = by_sc.setdefault(sc, {"ca": set(), "us": set(), "us_otc": set(),
                                  "rows": rows})
        e["ca"] |= ca_all
        e["us"] |= us_ex
        e["us_otc"] |= us_otc
    report["receipts_skipped_under_share_class"] = adr_like

    # 4. CDRs whose root is a US exchange ticker: two securities when
    #    OpenFIGI gives them different share classes (both resolved).
    cdr_jobs = [r for r in cdr_roots if listed(r)]
    cres = figi.map([ticker_job(r, "CN") for r in cdr_jobs])
    ures = figi.map([ticker_job(r, "US") for r in cdr_jobs])
    distinct: Dict[str, Dict[str, Any]] = {}
    for r, cn, us in zip(cdr_jobs, cres, ures):
        a, b = share_classes(cn), share_classes(us)
        if len(a) == 1 and b and not (a & b):
            sc = next(iter(a))
            # us_name: the US ticker's own company, which may be another
            # than the receipt's (a look-alike root: still two securities).
            distinct[sc] = {"name": (equity(cn)[0].get("name") or "").strip(),
                            "kind": "cdr", "ca": book_ca(r),
                            "us": book_us(r), "us_figi": sorted(b)[0],
                            "us_name": (equity(us)[0].get("name")
                                        or "").strip()}
    report["cdr_hints"] = len(cdr_roots)
    report["cdr_verified"] = len(distinct)

    # 5. The issuer's country: the country code of the share class's
    #    ISINs, from the OpenFIGI ISIN lookups in the cache (one country
    #    only; the ISIN itself is never shipped). It decides which
    #    listing a pair is booked under (lib/tobase_map: the home
    #    listing, so a US company's dividends stay foreign).
    dom = issuer_countries(figi)
    report["domicile_known"] = 0
    # 6. Entries.
    entries: Dict[str, Dict[str, Any]] = {}
    for sc, e in sorted(by_sc.items()):
        rows = e["rows"]
        typ = rows[0].get("securityType") or ""
        ca = sorted(e["ca"], key=lambda s: (bool(re.search(r"\.U$", s)), s))
        kind = ("fund" if typ in FUND_TYPES else
                "unit" if typ in UNIT_TYPES or ca[0].endswith(".UN")
                else "share")
        rec = {
            "name": (rows[0].get("name") or "").strip(),
            "kind": kind,
            "share_class_figi": sc,
            "ca": [book_ca(x) for x in ca],
            "us": sorted(book_us(x) for x in e["us"]),
            "us_exchange": sorted({usdir[x]["exchange"] for x in e["us"]}),
            "us_otc": sorted(book_us(x) for x in e["us_otc"]),
        }
        if sc in dom:
            rec["domicile"] = dom[sc]
            report["domicile_known"] += 1
        entries[sc] = rec
    # TSX ETFs' US-dollar lines (2b).
    for sc, (r, rows) in sorted(etf_pairs.items()):
        if sc in entries:
            for x in (book_ca(r), book_ca(r + ".U")):
                if x not in entries[sc]["ca"]:
                    entries[sc]["ca"].append(x)
            continue
        entries[sc] = {"name": (rows[0].get("name") or "").strip(),
                       "kind": "fund", "share_class_figi": sc,
                       "ca": [book_ca(r), book_ca(r + ".U")],
                       "us": [], "us_exchange": [], "us_otc": []}
        if sc in dom:
            entries[sc]["domicile"] = dom[sc]
    report["currency_line_entries"] = len(currency_lines) + len(etf_pairs)
    # Maintainer-entered ended pairs and corrections.
    hist_skipped = []
    retract: Dict[str, List[Dict[str, str]]] = collections.defaultdict(list)
    for sc, lst in temp_otc.items():
        for u in lst:
            retract[sc].append({"listing": u, "reason": TEMP_OTC_REASON,
                                "since": today, "_auto": "1"})
    for h in history:
        sc = str(h.get("share_class_figi") or "")
        if not re.fullmatch(r"BBG[0-9A-Z]{9}", sc):
            hist_skipped.append(", ".join(h.get("ca") or []) + " / "
                                + ", ".join(h.get("us") or []))
            continue
        retract[sc] += retractions(h, today)
        if h.get("_correction"):
            continue
        rec = entries.setdefault(sc, {
            "name": str(h.get("name") or ""), "kind": str(h.get("kind")
                                                          or "share"),
            "share_class_figi": sc, "ca": list(h.get("ca") or []),
            "us": [], "us_exchange": [], "us_otc": []})
        until = str(h.get("until") or "unknown")
        reused = str(h.get("reused_by") or "")
        for field in ("us", "us_otc"):
            for u in h.get(field) or []:
                u = str(u).upper()
                # The maintainer's end wins over a listing OpenFIGI still
                # names (a stale ticker of a former name).
                if u in rec[field]:
                    rec[field] = [x for x in rec[field] if x != u]
                    if field == "us":
                        rec["us_exchange"] = sorted(
                            {usdir[x[:-3]]["exchange"] for x in rec["us"]
                             if x[:-3] in usdir})
                e = {"listing": u, "kind": field, "until": until}
                if reused:
                    e["reused_by"] = reused
                rec.setdefault("_ended", []).append(e)
        if not rec["us"] and not rec["us_otc"]:
            rec["_until"] = until
    report["history_skipped_no_figi"] = hist_skipped

    # 7. Merge with the previous master: append-only.
    doc = merge_previous(entries, distinct, previous, today, retract, stamp)
    # 8. An ended US listing whose ticker names another security today
    #    (a reused ticker): refused unless the maintainer recorded it.
    report["reused_tickers"] = check_reused(doc, figi, usdir)
    counts = collections.Counter()
    for e in doc["security"].values():
        counts["securities"] += 1
        if e.get("us"):
            counts["with_us_exchange"] += 1
        if e.get("us_otc"):
            counts["with_us_otc"] += 1
        if e.get("us_otc") and not e.get("us"):
            counts["otc_only"] += 1
        if e.get("until"):
            counts["ended"] += 1
    report["counts"] = dict(counts)
    report["excluded"] = excluded
    report["figi_requests"] = figi.requests
    report["figi_errors_not_cached"] = figi.errors
    report["figi_not_in_cache"] = figi.missing
    report["unresolved_roots"] = unresolved
    fetched = {}
    fp = cache / FETCHED
    if fp.is_file():
        fetched = json.loads(fp.read_text(encoding="utf-8"))
    doc["meta"] = {
        "schema_version": SCHEMA_VERSION,
        "generated": stamp,
        "sources": [
            {"name": "OpenFIGI v3 mapping API (share-class FIGIs)",
             "as_of": str(fetched.get("openfigi") or today)},
            {"name": "Nasdaq Trader symbol directory (US exchange listings)",
             "as_of": stamp_date(us_stamp) or str(fetched.get("nasdaq")
                                                  or "")},
            {"name": "TMX interlisted companies and listed issuers (hints "
                     "only, nothing shipped from them)",
             "as_of": asof or str(fetched.get("tmx") or "")},
        ],
    }
    return doc, report


def issuer_countries(figi: Figi) -> Dict[str, str]:
    """{share-class FIGI: the two-letter country code of its ISINs} from
    the cache's ISIN lookups, when every ISIN of the class names one
    country."""
    out: Dict[str, set] = collections.defaultdict(set)
    for k, rows in figi.cache.items():
        try:
            job = json.loads(k)
        except ValueError:
            continue
        if job.get("idType") != "ID_ISIN":
            continue
        scs = share_classes(rows)
        if len(scs) == 1:
            out[scs.pop()].add(str(job.get("idValue") or "")[:2].upper())
    return {sc: next(iter(c)) for sc, c in out.items()
            if len(c) == 1 and re.fullmatch(r"[A-Z]{2}", next(iter(c)))}


def _listings(rec: Dict[str, Any]) -> set:
    out = {str(x).upper() for k in ("ca", "us", "us_otc")
           for x in rec.get(k) or []}
    out |= {str(h.get("listing") or "").upper()
            for h in rec.get("history") or [] if isinstance(h, dict)}
    out.discard("")
    return out


def _retract(rec: Dict[str, Any], gone: set) -> None:
    """Take the retracted listings out of every field of `rec`."""
    for k in ("ca", "us", "us_otc"):
        if k in rec:
            rec[k] = [x for x in rec[k] if str(x).upper() not in gone]
    if "history" in rec:
        rec["history"] = [h for h in rec["history"]
                          if str(h.get("listing") or "").upper() not in gone]
    if "added" in rec:
        rec["added"] = [a for a in rec["added"]
                        if str(a.get("listing") or "").upper() not in gone]


def next_stamp(previous: Optional[Dict[str, Any]], today: str) -> str:
    """The new master's `generated` value: the build date, or on a
    second changed build the same day `DATE.N` (N = 2, 3, ...): a
    project's tobase.map stamp then tells the two apart (a stamp is
    compared as text: "2026-01-02" < "2026-01-02.2" < "2026-01-03")."""
    prev = str(((previous or {}).get("meta") or {}).get("generated") or "")
    day, _, n = prev.partition(".")
    if day != today:
        return today
    return f"{today}.{int(n) + 1 if n.isdigit() else 2}"


def merge_previous(entries: Dict[str, Dict[str, Any]],
                   distinct: Dict[str, Dict[str, Any]],
                   previous: Optional[Dict[str, Any]], today: str,
                   retract: Optional[Dict[str, List[Dict[str, str]]]] = None,
                   stamp: Optional[str] = None) -> Dict[str, Any]:
    """The new master from this build's entries and the previous master:
    first_seen kept; a listing gone since gets `until` in `history`; a
    whole entry gone keeps every listing and gets `until`; an entry or
    listing that returns is current again (its history kept). A listing
    new to an entry the previous master had is recorded in `added` with
    the build date (`taxjson update-tobase-map` tells a new line from
    one the user deleted by it). `retract` ({FIGI: [{listing, reason,
    since}]}, the maintainer's corrections and the temporary OTC
    symbols): each listing leaves the entry for good and is recorded in
    its `retracted` list (carried by every later build, so never
    re-added); an automatic one (`_auto`) only when the previous master
    shipped it."""
    prev = (previous or {}).get("security") or {}
    retract = retract or {}
    stamp = stamp or today
    out: Dict[str, Dict[str, Any]] = {}
    for sc in sorted(set(prev) | set(entries)):
        old = prev.get(sc) or {}
        new = entries.get(sc)
        hist = [dict(h) for h in (old.get("history") or [])]
        rlist = [dict(r) for r in (old.get("retracted") or [])]
        had = _listings(old) | {str(a.get("listing") or "").upper()
                                for a in old.get("added") or []}
        for r in retract.get(sc) or []:
            if r.get("_auto") and r["listing"] not in had:
                continue
            if not any(x.get("listing") == r["listing"] for x in rlist):
                rlist.append({k: v for k, v in r.items()
                              if not k.startswith("_")})
        gone = {str(r.get("listing") or "").upper() for r in rlist}
        if new is None:
            rec = {k: v for k, v in old.items()}
            rec["history"] = hist
            _retract(rec, gone)
            if _listings(rec) - set(rec.get("ca") or []) or not gone:
                rec.setdefault("until", today)
            rec["retracted"] = rlist
            out[sc] = _clean(rec)
            continue
        rec = {k: v for k, v in new.items() if not k.startswith("_")}
        if not rec.get("domicile") and old.get("domicile"):
            rec["domicile"] = old["domicile"]
        rec["first_seen"] = str(old.get("first_seen") or stamp)
        _retract(rec, gone)
        cur = set(rec["us"]) | set(rec["us_otc"]) | set(rec["ca"])
        for h in new.get("_ended") or []:
            if h["listing"] in gone:
                continue
            old_h = [x for x in hist if x.get("listing") == h["listing"]]
            if not old_h:
                e = {"listing": h["listing"], "kind": h.get("kind", "us"),
                     "until": h["until"]}
                if h.get("reused_by"):
                    e["reused_by"] = h["reused_by"]
                hist.append(e)
                continue
            for x in old_h:
                if h["until"] != "unknown" and \
                        str(x.get("until") or "unknown") == "unknown":
                    # The maintainer's date replaces an earlier "unknown".
                    x["until"] = h["until"]
                if h.get("reused_by"):
                    x["reused_by"] = h["reused_by"]
        for field in ("us", "us_otc", "ca"):
            for lst in old.get(field) or []:
                if lst not in cur and lst not in gone and not any(
                        h.get("listing") == lst for h in hist):
                    hist.append({"listing": lst, "kind": field,
                                 "until": today})
        # A listing back again is current: its history line goes.
        hist = [h for h in hist if h.get("listing") not in cur
                and h.get("listing") not in gone]
        rec["history"] = hist
        if new.get("_until"):
            rec["until"] = new["_until"]
            rec["first_seen"] = str(old.get("first_seen") or stamp)
        added = [dict(a) for a in old.get("added") or []
                 if str(a.get("listing") or "").upper() not in gone]
        if old:
            for lst in sorted(_listings(rec) - had):
                added.append({"listing": lst, "since": stamp})
        rec["added"] = added
        rec["retracted"] = rlist
        out[sc] = _clean(rec)
    pd = (previous or {}).get("distinct") or {}
    dout = {}
    for sc in sorted(set(pd) | set(distinct)):
        rec = dict(distinct.get(sc) or pd[sc])
        rec["first_seen"] = str((pd.get(sc) or {}).get("first_seen")
                                or stamp)
        if sc not in distinct:
            rec.setdefault("until", today)
        dout[sc] = rec
    return {"security": out, "distinct": dout}


def _clean(rec: Dict[str, Any]) -> Dict[str, Any]:
    order = ("name", "kind", "share_class_figi", "domicile", "ca", "us",
             "us_exchange", "us_otc", "first_seen", "until", "history",
             "added", "retracted")
    out = {k: rec[k] for k in order if k in rec and rec[k] not in (None,)}
    for k in ("us_otc", "history", "added", "retracted"):
        if not out.get(k):
            out.pop(k, None)
    return out


def check_reused(doc: Dict[str, Any], figi: Figi,
                 usdir: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every ended US listing of the master (an entry's `history`) whose
    ticker today names another security: OpenFIGI's US ticker lookup
    gives other share classes only, or the Nasdaq Trader directory lists
    it and OpenFIGI does not give the entry's class. Each record says
    whether the maintainer recorded it (`reused_by`); an unrecorded one
    refuses the build (main). A lookup the cache lacks is not verified
    (counted in figi_not_in_cache: `--fetch` asks for it)."""
    items = []
    for sc, e in doc["security"].items():
        for h in e.get("history") or []:
            lst = str(h.get("listing") or "").upper()
            if h.get("kind") in ("us", "us_otc") and lst.endswith(".US"):
                items.append((sc, e, h, lst[:-3]))
    res = figi.map([ticker_job(us_to_bbg(t), "US") for _sc, _e, _h, t
                    in items])
    out = []
    for (sc, e, h, t), rows in zip(items, res):
        scs = share_classes(rows)
        other = ""
        if scs and sc not in scs:
            other = "; ".join(sorted({(d.get("name") or "").strip()
                                      for d in equity(rows)}))
        elif t in usdir and not usdir[t]["test"] and sc not in scs:
            other = f"the Nasdaq Trader directory lists {t}"
        if other:
            out.append({"listing": h["listing"], "share_class_figi": sc,
                        "name": e.get("name"), "today": other,
                        "recorded": bool(h.get("reused_by"))})
    return out


# ------------------------------------------------------------ writing

def _v(x: Any) -> str:
    if isinstance(x, list):
        return "[" + ", ".join(_v(i) for i in x) + "]"
    if isinstance(x, dict):
        return "{ " + ", ".join(f"{k} = {_v(v)}" for k, v in x.items()) + " }"
    if isinstance(x, int) and not isinstance(x, bool):
        return str(x)
    return json.dumps(str(x), ensure_ascii=False)


def render(doc: Dict[str, Any]) -> str:
    m = doc["meta"]
    out = [
        "# interlisted.toml: Canadian shares that also trade in the United",
        "# States under the same share class. GENERATED by",
        "# scripts/build_interlisted.py: do not edit (a change is lost at the",
        "# next build; a project overrides a pair in its ticker.map).",
        "# Keyed by OpenFIGI share-class FIGI; every pair is the same share",
        "# class on both sides (OpenFIGI) and an exchange listing is in the",
        "# Nasdaq Trader symbol directory. Entries are never deleted: an",
        "# ended one keeps `until`.",
        "",
        "[meta]",
        f"schema_version = {m['schema_version']}",
        f"generated = {_v(m['generated'])}",
        "sources = [",
    ]
    for s in m["sources"]:
        out.append(f"  {_v(s)},")
    out += ["]", ""]
    for sc, e in doc["security"].items():
        out.append(f"[security.{sc}]")
        for k, v in e.items():
            out.append(f"{k} = {_v(v)}")
        out.append("")
    for sc, e in doc["distinct"].items():
        out.append(f"[distinct.{sc}]")
        for k, v in e.items():
            out.append(f"{k} = {_v(v)}")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# ------------------------------------------------------------ fetch

def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def fetch_lists(cache: Path, today: str) -> None:
    """Download the hint lists and the US directories into `cache`."""
    for name in (TMX_TXT, NASDAQ, OTHER):
        print(f"fetch {URLS[name]}", file=sys.stderr)
        _download(URLS[name], cache / name)
    print(f"fetch {URLS['tmx_xlsx']}", file=sys.stderr)
    _download(URLS["tmx_xlsx"], cache / f"{TMX_XLSX_PREFIX}{today}.xlsx")
    fp = cache / FETCHED
    meta = json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else {}
    meta.update({"tmx": today, "nasdaq": today, "openfigi": today})
    fp.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n",
                  encoding="utf-8")


def inside_repo(p: Path) -> bool:
    try:
        p.resolve().relative_to(REPO)
        return True
    except ValueError:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", required=True, type=Path,
                    help="The download / OpenFIGI cache folder (outside "
                         "the repository)")
    ap.add_argument("--fetch", action="store_true",
                    help="Maintainer only: download the lists and ask "
                         "OpenFIGI for what the cache lacks (network)")
    ap.add_argument("--previous", type=Path, default=None,
                    help=f"The previous master (default: --out, "
                         f"{DEFAULT_OUT.relative_to(REPO)})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--history", type=Path, default=None,
                    help="The maintainer's ended interlistings (default: "
                         "CACHE/interlisted_history.toml; kept outside "
                         "the repository with the cache)")
    ap.add_argument("--date", default=None,
                    help="The build date (default: today)")
    ap.add_argument("--report", type=Path, default=None,
                    help="Write the build report (JSON) here")
    a = ap.parse_args(argv)
    today = a.date or _dt.date.today().isoformat()
    if inside_repo(a.cache):
        print("build_interlisted: --cache must be outside the repository "
              "(raw downloads are never committed)", file=sys.stderr)
        return 2
    a.cache.mkdir(parents=True, exist_ok=True)
    if a.fetch:
        fetch_lists(a.cache, today)
    prev_path = a.previous or a.out
    previous = load_toml(prev_path) if prev_path.is_file() else None
    figi = Figi(a.cache / FIGI_CACHE, online=a.fetch)
    doc, report = build(a.cache, previous, today, figi,
                        load_history(a.history or a.cache / HISTORY),
                        stamp=next_stamp(previous, today))
    unrec = [r for r in report["reused_tickers"] if not r["recorded"]]
    if unrec:
        for r in unrec:
            print(f"build_interlisted: {r['listing']} (an ended listing of "
                  f"{r['name']}, {r['share_class_figi']}) names another "
                  f"security today: {r['today']} — fix the pair in "
                  f"interlisted_history.toml (`retracted` if it was "
                  f"wrong) or record the reuse (`reused_by = \"<name>\"`)",
                  file=sys.stderr)
        print("build_interlisted: refused: the master is not written",
              file=sys.stderr)
        if a.report:
            a.report.write_text(json.dumps(report, indent=1, default=list)
                                + "\n", encoding="utf-8")
        return 3
    if previous and previous.get("security") == doc["security"] and \
            (previous.get("distinct") or {}) == doc["distinct"]:
        # Nothing changed: the master keeps its date and sources (a
        # project's tobase.map made from it stays current).
        doc["meta"] = dict(previous.get("meta") or doc["meta"])
    text = render(doc)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_name(a.out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, a.out)
    if a.report:
        a.report.write_text(json.dumps(report, indent=1, default=list)
                            + "\n", encoding="utf-8")
    summary = {k: report[k] for k in ("counts", "domicile_known",
                                      "figi_requests",
                                      "figi_not_in_cache", "roots_asked",
                                      "roots_resolved", "cdr_hints",
                                      "cdr_verified", "etf_usd_lines",
                                      "currency_line_entries")}
    summary["otc_temporary_symbols"] = len(report["otc_temporary_symbols"])
    summary["reused_tickers_recorded"] = len(report["reused_tickers"])
    print(json.dumps(summary, indent=1), file=sys.stderr)
    if report["figi_not_in_cache"] and not a.fetch:
        print(f"build_interlisted: {report['figi_not_in_cache']} OpenFIGI "
              f"job(s) are not in the cache (treated as not found); "
              f"`--fetch` asks for them", file=sys.stderr)
    if report["history_skipped_no_figi"]:
        print("build_interlisted: ended pair(s) without a share-class FIGI "
              "skipped: " + "; ".join(report["history_skipped_no_figi"]),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
