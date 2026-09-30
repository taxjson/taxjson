"""`taxjson redact FILE...` — strip account numbers, names and contact
details it recognises from a broker export while keeping every row
shape, so a statement can be shared as a parser sample or attached to a
bug report. It is a best-effort pattern matcher, not a guarantee:
REVIEW THE OUTPUT BEFORE SHARING (the report lists the lines to read).

What it changes, and nothing else:
  * account identifiers — IB `U1234567` / `DU…` / `F…` ids, Questrade
    `Account #`, IB `Xfer Account` / `ClientAccountID`, RBC
    `"Account: 12345678 - Margin"` headers, Fidelity/Schwab-style
    `Z12345678` / `1234-5678` values in an account column, alphanumeric
    ids (`Account Number: 5MV07654`), any `account|acct|a/c … <id>` or
    `transfer from|to [acct] <id>` phrase — each distinct id becomes a
    stable placeholder of the SAME shape (`U99900001`, `99900001`,
    `9990-0002`) and every occurrence in the file, descriptions
    included, is replaced. Ids shorter than five characters and
    date-like numbers are never treated as ids, so quantities, prices
    and dates stay;
  * holder identity — IB `Account Information` Name / Alias / address
    rows (CSV, and the label/value cells of IB's .html statements),
    `AccountAlias` / `AcctAlias` columns, a `Name` column beside an
    account or alias column (IB Flex `Account` section), `Name:` /
    `Client:` / `Owner:` header lines and Coinbase's `User,<name>,<id>`
    line (quoted or not), names after `Initiated by`,
    `Payee:`, `Beneficiary:` and honorifics (`Mrs. …`), the name after
    an `Account: …,` header, name / street / city lines in a statement
    preamble (Webull), e-mail addresses, phone numbers, Canadian postal
    codes, street addresses, SIN-shaped (Luhn-valid) and SSN-shaped
    numbers;
  * crypto — wallet addresses (bc1…, legacy 1…/3… base58, 0x + 40 hex),
    on-chain transaction hashes and exchange transaction ids (Kraken
    txid/refid, Coinbase ids, UUIDs): each distinct value becomes a
    stable same-shape pseudonym, so rows that shared an id still share
    one and distinct rows stay distinct;
  * anything matching the private denylist (`~/.config/taxjson/
    pii-denylist`, the same file scripts/check-pii.sh uses) or `--also`
    — case-insensitive. An invalid pattern FAILS CLOSED on the command
    line (exit 2, nothing written).
Binary input (.xlsx — Questrade's default export — .xls, .pdf, .zip) is
REFUSED: export CSV (or run taxjson-xlsx-to-csv) and redact that.
Encoding: UTF-8 (BOM kept), UTF-16 (BOM or NUL-stuffed; re-written as
UTF-8 with a note), else cp1252 with a note. Line endings are preserved
byte for byte.

Output goes to `<name>.redacted<ext>` beside the input (or --out DIR),
with any id in the NAME itself replaced too; an existing copy is only
overwritten with --force, a symlink never. The input is never modified.
The report shows placeholders and id lengths, never the ids, checks
that no collected id is left in the copy (any left over is counted and
its lines listed for review), and lists
(by line number only) the free-text lines that still hold name-like
words or long digit runs it did not redact. `--check` writes nothing
and exits 1 when it finds anything to redact.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_IB_ID = re.compile(r"(?<![A-Za-z0-9])(?:DU|U|F|I)\d{7,8}(?![0-9])")
# Free text needs 7+ characters (5+ digits) — a 5-digit "id" is too
# often a round quantity elsewhere in the file. Explicit account
# COLUMNS accept 5+. Triggers: account / acct / a/c (+ #, number, no,
# id) and "transfer from|to [acct]" (RBC/Questrade journal lines).
_ACCOUNT_PHRASE = re.compile(
    r"((?:\b(?:account|acct|a/c)\.?(?:\s*(?:#|number|num|no\.?|id))?\s*[:#]?\s*)"
    r"|(?:\btransfer(?:red)?\s+(?:from|to)\s+(?:(?:account|acct|a/c)\.?\s*)?(?:#\s*)?))"
    r"([A-Za-z0-9][A-Za-z0-9-]{4,16}[A-Za-z0-9])(?![A-Za-z0-9]|\.\d)",
    re.IGNORECASE)
_COL_ID = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,}[A-Z0-9]$")     # 5+ chars, mostly digits
_DATE8 = re.compile(r"^(19|20)\d{6}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACCOUNT_COLS = ("account #", "account", "xfer account", "account number",
                 "account no", "account no.", "acct", "acct #", "account id",
                 "clientaccountid", "client account id", "acctid",
                 "accountid", "account_number", "accountnumber")
_ALIAS_COLS = ("accountalias", "account alias", "acctalias", "acct alias",
               "account name", "accountname", "account nickname",
               "nickname", "client name", "holder", "account holder",
               "owner")
# A plain `Name` column is a security name in most layouts; it is the
# HOLDER's name when the same header also carries an account id or an
# alias column (IB Flex `Account` section: AccountNumber, AccountAlias,
# Name).
_HOLDER_NAME_COLS = ("name", "full name", "holder name",
                     "account holder name", "customer name", "user",
                     "user name", "username")
_IDENTITY_ROWS = ("name", "account alias", "address", "street", "city",
                  "state", "postal code", "zip", "country", "phone",
                  "email", "customer id", "client name", "holder",
                  "owner", "primary owner", "customer", "province",
                  "address 1", "address 2", "mailing address")
_HEADER_LINE = re.compile(
    r'^(\s*"?)((?:name|client|client name|account holder|owner|primary owner|customer|user)\s*"?\s*[:,]\s*)',
    re.IGNORECASE)
# `"Account: 12345678 - Margin, Jane Sample"` (RBC): the text after the
# first comma of an Account: header line is the holder's name.
_ACCOUNT_HEADER_NAME = re.compile(
    r'^(\s*"?(?:account|acct)[^:\r\n"]{0,20}:[^,"\r\n]*,\s*)', re.IGNORECASE)

# A capitalised name: 1-4 words, Title-case or UPPER, or initials.
_NAME_WORDS = r"(?-i:[A-Z][A-Za-z'’-]+|[A-Z]\.?)(?:[ ]+(?-i:[A-Z][A-Za-z'’-]+|[A-Z]\.?)){0,3}"
_NAME_AFTER = re.compile(
    r"(\b(?:initiated|requested|authori[sz]ed|ordered)\s+by\s+"
    r"|\b(?:beneficiary|payee|remitter|ordering customer|in favou?r of)\s*[:-]?\s*"
    r"|(?<![A-Za-z])(?-i:Mr|Mrs|Ms|Miss|Mx|Dr|MR|MRS|MS|MISS)\.?\s+)"
    r"(" + _NAME_WORDS + r")", re.IGNORECASE)
# "Wire from Jane Sample" / "E-TRANSFER TO JANE SAMPLE": 2-4 words, none
# of them statement vocabulary or a currency code (checked in code).
_NAME_FROM_TO = re.compile(
    r"(\b(?:wire|e-?transfer|interac|payment|transfer|deposit|withdrawal"
    r"|cheque|check|paid|sent|received|funds)\s+(?:from|to)\s+)"
    r"((?-i:[A-Z][A-Za-z'’-]+|[A-Z]\.?)(?:[ ]+(?-i:[A-Z][A-Za-z'’-]+|[A-Z]\.?)){1,3})",
    re.IGNORECASE)

# Canada Post alphabet: no D F I O Q U anywhere, no W Z as first letter.
# Not inside a longer hyphenated / alphanumeric token.
_POSTAL = re.compile(
    r"(?<![A-Za-z0-9-])[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d"
    r"[ABCEGHJ-NPRSTV-Z]\d(?![A-Za-z0-9-])")
_PHONE = re.compile(
    r"(?<![\w.+-])(?:\+?1[ .-]?)?(?:\(\d{3}\)[ ]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w-])")
_PHONE_CTX = re.compile(
    r"(\b(?:phone|tel|telephone|mobile|cell|fax)\b\.?\s*(?:#|no\.?|number)?\s*[:#]?\s*)"
    r"(\+?[\d][\d ().-]{8,16}\d)", re.IGNORECASE)
_STREET_SUFFIX = (r"street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|court|crt"
                  r"|crescent|cres|lane|ln|way|place|terrace|terr|parkway|pkwy"
                  r"|highway|hwy|circle|cir|square|sq|trail|trl|sideroad|rue"
                  r"|chemin|route|rte|grove|gardens|gdns|heights|hts|mews")
_STREET = re.compile(
    r"(?<![\w.-])(?:(?:unit|apt|suite|ste)\.?\s*#?\s*\w+\s*[,-]?\s*)?"
    r"\d{1,6}[A-Za-z]?(?:-\d{1,6})?\s+(?:(?-i:[A-Z][A-Za-z'’.-]*)\s+){1,4}"
    r"(?:" + _STREET_SUFFIX + r")\b\.?"
    r"(?:\s+(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\b\.?)?"
    r"(?:\s*,?\s*(?:unit|apt|suite|ste|#)\.?\s*#?\s*\w+)?", re.IGNORECASE)
_PO_BOX = re.compile(r"\b(?:P\.?\s*O\.?\s*Box|Postal Box)\s*#?\s*\d+", re.IGNORECASE)
_SIN_SEP = re.compile(r"(?<![\d-])\d{3}([ -])\d{3}\1\d{3}(?![\d-])")
_SIN_CTX = re.compile(
    r"(\b(?:SIN|S\.I\.N\.?|social insurance(?: number)?|SSN|TIN|tax id)\b\s*[:#]?\s*)"
    r"(\d{9})(?!\d)", re.IGNORECASE)
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")

# Crypto wallets and transaction ids (pseudonymised, stable per value).
_WALLET = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?:bc1|tb1|ltc1)[ac-hj-np-z02-9]{11,87}"                    # bech32
    r"|0x[0-9a-fA-F]{40}(?![0-9a-fA-F])"                          # EVM
    r"|[13][a-km-zA-HJ-NP-Z1-9]{25,34}"                           # base58
    r")(?![A-Za-z0-9])")
_TXID = re.compile(
    r"(?<![A-Za-z0-9-])(?:"
    r"0x[0-9a-fA-F]{64}"                                          # EVM tx hash
    r"|[0-9a-fA-F]{64}"                                           # BTC txid
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
    r"|[A-Z0-9]{6,7}-[A-Z0-9]{5}-[A-Z0-9]{6}"                     # Kraken txid/refid
    r"|[0-9a-f]{24}"                                              # Coinbase id
    r")(?![A-Za-z0-9-])")
_TXID_COLS = ("txid", "refid", "ref id", "ordertxid", "postxid", "tx id",
              "txhash", "tx hash", "transaction hash", "hash",
              "transaction id", "transaction_id", "transactionid")

# Free text the review pass reads: these columns, and preamble lines.
_FREE_TEXT_COLS = ("description", "notes", "note", "memo", "comment",
                   "comments", "details", "remarks", "payee", "narrative",
                   "particulars", "security description",
                   "transaction description", "activity description")
_REVIEW_NAME = re.compile(r"(?-i:\b[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+\b)")
_REVIEW_DIGITS = re.compile(r"(?<![A-Za-z0-9.])\d{7,}(?![A-Za-z0-9])")
# Words that make a capitalised phrase NOT a person's name (preamble
# lines, the review pass). Lower-case; compared case-insensitively.
_VOCAB = frozenset("""
a account accounts accrual ach bill deposited disbursement eft initiated
interac payroll accruals acquisition action actions activity
adjustment adjustments all amount and annual as assigned at balance bank
base bond bonds borrow bought broker brokerage brokers buy by call canada
canadian capital card cash change client code commission company confirmation
contribution corp corporate credit currency data date debit deposit deposits
details direct distribution dividend dividends dollar dollars electronic etf
exchange exercise exercised expired export fee fees field for foreign from fund
funds gross group header history holdings in income inc index information
interactive interest investing investment investments journal lending lieu
limit limited loan long ltd margin market monthly net new of on online option
options order ordinary other paid payment per period portfolio price quarterly
realized received reinvestment report return rrsp resp rrif securities
security sell settle settlement share shares short sold split spinoff statement
statements stock stocks summary tax tfsa the to total trade trades trading
transaction transactions transfer transfers unrealized value webull wire
withdrawal withdrawals withholding year questrade rbc direct fidelity schwab
kraken coinbase wealthsimple td ameritrade vanguard""".split())


class InputRefused(Exception):
    """Raised for input the redactor cannot safely handle (binary)."""


class Report:
    def __init__(self) -> None:
        self.accounts: Dict[str, str] = {}      # original -> placeholder
        self.unreplaced: Dict[str, int] = {}    # original -> occurrences left
        self.identity_rows = 0
        self.emails = 0
        self.names = 0
        self.phones = 0
        self.postal_codes = 0
        self.addresses = 0
        self.sins = 0
        self.wallets: Dict[str, str] = {}       # original -> pseudonym
        self.txids: Dict[str, str] = {}
        self.patterns = 0
        self.description_columns: List[str] = []
        self.review: List[Tuple[int, str]] = []  # (line number, reason)
        self.encoding = "utf-8"
        self.notes: List[str] = []

    def found_anything(self) -> bool:
        return bool(self.accounts or self.identity_rows or self.emails
                    or self.names or self.phones or self.postal_codes
                    or self.addresses or self.sins or self.wallets
                    or self.txids or self.patterns)

    @staticmethod
    def masked(s: str) -> str:
        """Never the id: its shape and length only ('U + 8 digits')."""
        digits = sum(c.isdigit() for c in s)
        lead = re.match(r"[A-Za-z]*", s).group(0)
        return f"{lead + ' + ' if lead else ''}{digits} digits"


def _placeholder(orig: str, n: int) -> str:
    """Same shape as the original: letters and punctuation kept in place,
    the digit positions become a 9990-prefixed, zero-padded counter —
    the shape scripts/check-pii.sh recognises as synthetic."""
    digits = sum(c.isdigit() for c in orig)
    body = "9990" + str(n).zfill(max(digits - 4, 1))
    body = body[-digits:] if digits >= 5 else body
    it = iter(body)
    return "".join(next(it) if c.isdigit() else c for c in orig) if digits >= 5 \
        else orig.translate(str.maketrans("0123456789", "9999999999"))


def _pseudonym(orig: str, n: int) -> str:
    """Stable, same-shape stand-in for a wallet / transaction id: the
    type prefix (0x, bc1, the base58 version char) and every separator
    stay; each other letter or digit becomes a digit of a
    9990-prefixed counter — hex-valid, never a real address."""
    keep = 0
    low = orig.lower()
    for pre in ("0x", "bc1", "tb1", "ltc1"):
        if low.startswith(pre):
            keep = len(pre)
            break
    else:
        if _WALLET.fullmatch(orig) and orig[:1] in "13":
            keep = 1
    body_len = sum(c.isalnum() for c in orig[keep:])
    fill = ("9990" + str(n).zfill(max(body_len - 4, 1)))[-body_len:] if body_len else ""
    it = iter(fill)
    return orig[:keep] + "".join(next(it) if c.isalnum() else c
                                 for c in orig[keep:])


def _plausible_wallet(v: str) -> bool:
    """A base58 match must look like an address (mixed case plus a
    digit), not a long upper-case word that happens to start with 1/3."""
    if v[:1] not in "13":
        return True
    body = v[1:]
    return (any(c.islower() for c in body) and any(c.isupper() for c in body)
            and any(c.isdigit() for c in body))


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _split(line: str) -> Optional[List[str]]:
    try:
        rows = list(csv.reader(io.StringIO(line)))
    except csv.Error:
        return None
    return rows[0] if rows else []


def _is_date(v: str) -> bool:
    return bool(_DATE8.fullmatch(v) or _ISO_DATE.fullmatch(v))


def _is_id(v: str) -> bool:
    """An account-COLUMN value that is an id: 5+ characters, 5+ digits,
    upper-case letters/digits/hyphens, not a date."""
    v = v.strip()
    if _IB_ID.fullmatch(v):
        return True
    if _COL_ID.fullmatch(v) and sum(c.isdigit() for c in v) >= 5 and not _is_date(v):
        return True
    return False


def _is_phrase_id(v: str) -> bool:
    """An id found in free text after an account/transfer phrase: 7+
    characters with 5+ digits (a 5-digit quantity is not an id), not a
    date, and — when it has letters — upper-case (a word is not an id)."""
    if len(v) < 7 or sum(c.isdigit() for c in v) < 5 or _is_date(v):
        return False
    return not any(c.islower() for c in v)


def _collect_ids(lines: List[str]) -> List[str]:
    ids: List[str] = []
    seen = set()

    def add(v: str, check: Callable[[str], bool] = _is_id) -> None:
        v = v.strip()
        if v and v not in seen and check(v):
            seen.add(v); ids.append(v)

    col_idx: Dict[str, List[int]] = {}
    for line in lines:
        for m in _IB_ID.finditer(line):
            add(m.group(0))
        for m in _ACCOUNT_PHRASE.finditer(line):
            add(m.group(2), _is_phrase_id)
        cells = _split(line.rstrip("\r\n"))
        if not cells:
            continue
        low = [c.strip().lower() for c in cells]
        if len(low) > 1 and low[1] == "header":               # IB sections
            col_idx[low[0]] = [i for i, c in enumerate(low) if c in _ACCOUNT_COLS]
            continue
        if any(c in _ACCOUNT_COLS for c in low) and "data" not in low[:2]:
            col_idx["__flat__"] = [i for i, c in enumerate(low) if c in _ACCOUNT_COLS]
            continue
        if len(low) > 1 and low[1] == "data":
            for i in col_idx.get(low[0], []):
                if i < len(cells):
                    add(cells[i])
            if low[0] == "account information" and len(cells) > 3 \
                    and low[2] in ("account", "accounts included"):
                for tok in re.split(r"[,\s]+", cells[3]):
                    add(tok)
        else:
            for i in col_idx.get("__flat__", []):
                if i < len(cells):
                    add(cells[i])
    return ids


def _replace_field(line: str, cells: List[str], idx: int, value: str) -> str:
    """Replace one CSV field in the RAW line, leaving every other byte
    (quoting, spacing, line ending) as it was."""
    old = cells[idx]
    if not old:
        return line
    # The field may or may not be quoted in the raw text; try both.
    for form in (f'"{old}"', old):
        pos = line.find(form)
        if pos >= 0:
            rep = f'"{value}"' if form.startswith('"') else value
            return line[:pos] + rep + line[pos + len(form):]
    return line


def compile_patterns(pats: List[str]) -> Tuple[List[re.Pattern], List[str]]:
    good, bad = [], []
    for p in pats:
        try:
            good.append(re.compile(p, re.IGNORECASE))
        except re.error as e:
            bad.append(f"{p!r}: {e}")
    return good, bad


def _looks_like_name_line(text: str) -> bool:
    """A preamble line that is nothing but a person's name: 2-5
    capitalised alphabetic words, none of them statement vocabulary."""
    words = text.split()
    if not 2 <= len(words) <= 5 or ":" in text:
        return False
    for w in words:
        core = w.strip(".,'’-")
        if not core or not re.fullmatch(r"[A-Za-z][A-Za-z'’.-]*", w.strip(",")):
            return False
        if not core[0].isupper() or core.lower() in _VOCAB:
            return False
    return True


def _blank_cells(line: str) -> str:
    """Every non-empty field of the line becomes REDACTED; quoting,
    commas and the line ending stay."""
    cells = _split(line.rstrip("\r\n")) or []
    for i in range(len(cells)):
        if cells[i].strip():
            line = _replace_field(line, cells, i, "REDACTED")
            cells = _split(line.rstrip("\r\n")) or []
    return line


def _mapper(table: Dict[str, str],
            accept: Callable[[str], bool] = lambda v: True
            ) -> Callable[[re.Match], str]:
    def repl(m: re.Match) -> str:
        v = m.group(0)
        if not accept(v):
            return v
        if v not in table:
            table[v] = _pseudonym(v, len(table) + 1)
        return table[v]
    return repl


def _redact_contact(line: str, rep: Report) -> str:
    """Phones, postal codes, street addresses, SIN/SSN, honorific /
    initiated-by names — anywhere on the line."""
    line, k = _STREET.subn("REDACTED", line)
    rep.addresses += k
    line, k = _PO_BOX.subn("REDACTED", line)
    rep.addresses += k
    line, k = _PHONE_CTX.subn(lambda m: m.group(1) + "REDACTED", line)
    rep.phones += k
    line, k = _PHONE.subn("REDACTED", line)
    rep.phones += k
    line, k = _POSTAL.subn("REDACTED", line)
    rep.postal_codes += k

    def sin_sep(m: re.Match) -> str:
        d = re.sub(r"\D", "", m.group(0))
        if _luhn_ok(d):
            rep.sins += 1
            return "REDACTED"
        return m.group(0)
    line = _SIN_SEP.sub(sin_sep, line)
    line, k = _SIN_CTX.subn(lambda m: m.group(1) + "REDACTED", line)
    rep.sins += k
    line, k = _SSN.subn("REDACTED", line)
    rep.sins += k

    def name(m: re.Match) -> str:
        if any(w.strip(".").lower() in _VOCAB for w in m.group(2).split()):
            return m.group(0)
        rep.names += 1
        return m.group(1) + "REDACTED"
    line = _NAME_AFTER.sub(name, line)

    def name_from_to(m: re.Match) -> str:
        words = [w.strip(".") for w in m.group(2).split()]
        if any(w.lower() in _VOCAB or re.fullmatch(r"[A-Z]{3}", w) for w in words):
            return m.group(0)
        rep.names += 1
        return m.group(1) + "REDACTED"
    line = _NAME_FROM_TO.sub(name_from_to, line)
    return line


def _review(lineno: int, text: str, where: str, rep: Report) -> None:
    for ph in (*rep.accounts.values(), *rep.wallets.values(), *rep.txids.values()):
        if ph in text:
            text = text.replace(ph, " ")
    reasons = []
    for m in _REVIEW_NAME.finditer(text):
        words = [w.strip(".").lower() for w in m.group(0).split()]
        if not any(w in _VOCAB for w in words):
            reasons.append("name-like words")
            break
    for m in _REVIEW_DIGITS.finditer(text):
        tok = m.group(0)
        if "9990" not in tok[:5] and not _DATE8.fullmatch(tok):
            reasons.append("a 7+ digit number")
            break
    if reasons:
        rep.review.append((lineno, f"{' and '.join(reasons)} in {where}"))


def _identity_cols(low: List[str]) -> List[int]:
    """Header positions holding holder identity: alias columns always;
    a name column only beside an account or alias column."""
    cols = [i for i, c in enumerate(low) if c in _ALIAS_COLS]
    if cols or any(c in _ACCOUNT_COLS for c in low):
        cols += [i for i, c in enumerate(low) if c in _HOLDER_NAME_COLS]
    return sorted(cols)


# HTML statements (IB's .html reports): a label cell naming an identity
# field, then the value cell — on the same line or the next ones.
_HTML_IDENTITY = re.compile(
    r"(<t[dh][^>]*>\s*(?:" + "|".join(
        re.escape(r).replace(r"\ ", r"\s+") for r in sorted(
            set(_IDENTITY_ROWS) - {"country", "state", "province"},
            key=len, reverse=True)) +
    r")\s*:?\s*</t[dh]>\s*<td[^>]*>)([^<]*)(</td>)",
    re.IGNORECASE)


def _redact_html_identity(text: str, rep: "Report") -> str:
    if "<td" not in text.lower():
        return text

    def repl(m: re.Match) -> str:
        if not m.group(2).strip():
            return m.group(0)
        rep.identity_rows += 1
        return m.group(1) + "REDACTED" + m.group(3)
    return _HTML_IDENTITY.sub(repl, text)


def _id_pattern(orig: str) -> re.Pattern:
    """Where an account id is replaced — the SAME boundaries it was
    collected with. An IB id is found when it is followed by letters
    (`tblAccountInformation_U1234567Heading`), so it is replaced
    there too; other ids keep the strict boundary (not inside a longer
    token or a decimal)."""
    if _IB_ID.fullmatch(orig):
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(orig) + r"(?![0-9])")
    return re.compile(r"(?<![A-Za-z0-9.])" + re.escape(orig)
                      + r"(?![A-Za-z0-9]|\.\d)")


def redact_text(text: str, extra_patterns: Optional[List[str]] = None
                ) -> Tuple[str, Report]:
    rep = Report()
    compiled, bad = compile_patterns(extra_patterns or [])
    for b in bad:
        rep.notes.append(f"pattern skipped (not a valid regex): {b}")
    text = _redact_html_identity(text, rep)
    lines = text.splitlines(keepends=True)
    for n, orig in enumerate(_collect_ids(lines), start=1):
        rep.accounts[orig] = _placeholder(orig, n)
    # Longest first so a shorter id that is a substring of a longer one
    # cannot pre-empt it.
    ordered = sorted(rep.accounts.items(), key=lambda kv: -len(kv[0]))
    id_pats = {orig: _id_pattern(orig) for orig, _ in ordered}
    alias_cols: Dict[str, List[int]] = {}
    txid_cols: Dict[str, List[int]] = {}
    free_cols: Dict[str, List[Tuple[int, str]]] = {}
    in_preamble = True
    wallet_repl = _mapper(rep.wallets, _plausible_wallet)
    txid_repl = _mapper(rep.txids)
    out: List[str] = []
    for lineno, line in enumerate(lines, start=1):
        body = line.rstrip("\r\n")
        cells = _split(body)
        low = [c.strip().lower() for c in cells] if cells else []
        nonempty = [c for c in (cells or []) if c.strip()]
        is_ib_row = bool(low and len(low) > 1 and low[1] in ("header", "data"))
        header_row = False
        if low and len(low) > 1 and low[1] == "header":
            header_row = True
            if "description" in low[2:] and low[0] not in rep.description_columns:
                rep.description_columns.append(low[0])
            alias_cols[low[0]] = _identity_cols(low)
            txid_cols[low[0]] = [i for i, c in enumerate(low) if c in _TXID_COLS]
            free_cols[low[0]] = [(i, cells[i].strip()) for i, c in enumerate(low)
                                 if c in _FREE_TEXT_COLS]
        elif low and len(nonempty) >= 3 and not is_ib_row and (
                any(c in _ALIAS_COLS + _TXID_COLS + _FREE_TEXT_COLS + _ACCOUNT_COLS
                    for c in low)
                or all(not any(ch.isdigit() for ch in c) for c in nonempty)):
            # A flat CSV's column header row.
            header_row = True
            alias_cols["__flat__"] = _identity_cols(low)
            txid_cols["__flat__"] = [i for i, c in enumerate(low) if c in _TXID_COLS]
            free_cols["__flat__"] = [(i, cells[i].strip()) for i, c in enumerate(low)
                                     if c in _FREE_TEXT_COLS]
        if len(nonempty) >= 3:
            in_preamble = False
        sect = low[0] if is_ib_row else "__flat__"
        # Identity rows (IB Account Information) and alias columns.
        if (low and len(low) > 3 and low[0] == "account information"
                and low[1] == "data" and low[2] in _IDENTITY_ROWS and cells[3].strip()):
            line = _replace_field(line, cells, 3, "REDACTED")
            rep.identity_rows += 1
        elif low and not header_row:
            for i in alias_cols.get(sect, []):
                if i < len(cells) and cells[i].strip() and not _is_id(cells[i]):
                    line = _replace_field(line, cells, i, "REDACTED")
                    rep.identity_rows += 1
            for i in txid_cols.get(sect, []):
                v = cells[i].strip() if i < len(cells) else ""
                if len(v) >= 6 and any(c.isdigit() for c in v):
                    if v not in rep.txids:
                        rep.txids[v] = _pseudonym(v, len(rep.txids) + 1)
                    line = _replace_field(line, cells, i, rep.txids[v])
        m = _HEADER_LINE.match(line)
        if m and not is_ib_row:
            rest = line[m.end():]
            if rest.startswith('"'):
                # A quoted CSV field (`"User","Jane Sample",...`).
                new_rest = re.sub(r'^"[^"\r\n]*"', '"REDACTED"', rest, count=1)
            elif m.group(1).strip().endswith('"') and '"' not in m.group(2):
                new_rest = re.sub(r'^[^"\r\n]*', "REDACTED", rest, count=1)
            else:
                new_rest = re.sub(r'^[^,\r\n]*', "REDACTED", rest, count=1)
            line = line[:m.end()] + new_rest
            rep.identity_rows += 1
        m = _ACCOUNT_HEADER_NAME.match(line)
        if m and not is_ib_row and len(nonempty) <= 2:
            rest = line[m.end():]
            pat = r'^[^"\r\n]*' if m.group(1).lstrip().startswith('"') else r'^[^,\r\n]*'
            tail = re.match(pat, rest).group(0)
            if re.search(r"[A-Za-z]", tail):
                line = line[:m.end()] + "REDACTED" + rest[len(tail):]
                rep.names += 1
        # Statement preamble (before the first table): a line that is a
        # bare name, or that carries a street address / postal code, is
        # identity in its entirety (name, street, "City, PROV <postal code>").
        if in_preamble and not is_ib_row and nonempty:
            joined = " ".join(c.strip() for c in nonempty)
            if (_looks_like_name_line(joined) or _STREET.search(joined)
                    or _POSTAL.search(joined) or _PO_BOX.search(joined)):
                line = _blank_cells(line)
                rep.identity_rows += 1
        for orig, ph in ordered:
            if orig in line:
                line = id_pats[orig].sub(ph, line)
        if _EMAIL.search(line):
            line, k = _EMAIL.subn("redacted@example.com", line)
            rep.emails += k
        line = _WALLET.sub(wallet_repl, line)
        line = _TXID.sub(txid_repl, line)
        if not header_row:
            line = _redact_contact(line, rep)
        for pat in compiled:
            line, k = pat.subn("REDACTED", line)
            rep.patterns += k
        # Review pass: free text this line still carries.
        if not header_row:
            new_cells = _split(line.rstrip("\r\n")) or []
            if len([c for c in new_cells if c.strip()]) <= 2 and not is_ib_row:
                _review(lineno, " ".join(new_cells), "a free-text line", rep)
            else:
                for i, name in free_cols.get(sect, []):
                    if i < len(new_cells):
                        _review(lineno, new_cells[i], f"column {name!r}", rep)
        out.append(line)
    # Verify, never assume: any collected id still in the copy is
    # counted (and its lines listed for review) so the report cannot
    # claim a replacement it did not make.
    for orig in rep.accounts:
        hits = [n for n, ln in enumerate(out, start=1) if orig in ln]
        if hits:
            rep.unreplaced[orig] = sum(ln.count(orig) for ln in out)
            for n in hits:
                rep.review.append((n, "an account id the redactor "
                                      "could not replace"))
    return "".join(out), rep


_BINARY_MAGIC = (
    (b"PK\x03\x04", "a ZIP container (.xlsx / .zip)"),
    (b"PK\x05\x06", "a ZIP container (.xlsx / .zip)"),
    (b"\xd0\xcf\x11\xe0", "an OLE file (old Excel .xls)"),
    (b"%PDF", "a PDF"),
)
_BINARY_EXT = (".xlsx", ".xlsm", ".xls", ".ods", ".numbers", ".pdf", ".zip")


def sniff_binary(raw: bytes, name: str = "") -> Optional[str]:
    """Why this input is not a text export, or None. Magic bytes first;
    then NUL bytes that are not UTF-16's every-other-byte pattern."""
    for magic, what in _BINARY_MAGIC:
        if raw.startswith(magic):
            return what
    if name.lower().endswith(_BINARY_EXT):
        return f"a {Path(name).suffix} file"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    head = raw[:4096]
    if b"\x00" in head:
        half = max(len(head) // 2, 1)
        even = sum(1 for b in head[0::2] if b == 0) / half
        odd = sum(1 for b in head[1::2] if b == 0) / half
        if not ((odd > 0.3 and even < 0.05) or (even > 0.3 and odd < 0.05)):
            return "binary data (NUL bytes that are not UTF-16 text)"
    return None


def decode_export(raw: bytes) -> Tuple[str, str, bytes]:
    """(text, encoding-name, bom-to-restore). UTF-16 with or without a
    BOM, else strict UTF-8 (BOM kept), else cp1252."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16", b""
    head = raw[:4096]
    if b"\x00" in head:
        enc = "utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"
        return raw.decode(enc, errors="replace"), enc, b""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    try:
        return raw[len(bom):].decode("utf-8"), "utf-8", bom
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252", b""


def load_denylist(path: Optional[str] = None) -> List[str]:
    p = Path(path or os.environ.get("TAXJSON_PII_DENYLIST")
             or Path.home() / ".config" / "taxjson" / "pii-denylist")
    if not p.is_file():
        return []
    pats = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            pats.append(s)
    return pats


def redacted_name(src: Path, accounts: Dict[str, str]) -> str:
    stem = src.stem
    for orig, ph in sorted(accounts.items(), key=lambda kv: -len(kv[0])):
        stem = stem.replace(orig, ph)
    # Ids that appear only in the name (IB names downloads after the account).
    n = len(accounts)
    for m in list(_IB_ID.finditer(stem)) + list(re.finditer(r"(?<!\d)\d{8,}(?!\d)", stem)):
        tok = m.group(0)
        if _DATE8.fullmatch(tok) or tok in accounts.values():
            continue
        n += 1
        stem = stem.replace(tok, _placeholder(tok, n))
    return f"{stem}.redacted{src.suffix}"


def redact_file(src: Path, out_dir: Optional[Path], extra: List[str],
                check_only: bool, force: bool = False
                ) -> Tuple[Optional[Path], Report]:
    raw = src.read_bytes()
    why = sniff_binary(raw, src.name)
    if why:
        raise InputRefused(
            f"{src.name} is {why}, not a text export — nothing written. "
            f"Export CSV from the broker (or convert with "
            f"`taxjson-xlsx-to-csv`) and redact the CSV.")
    text, enc, bom = decode_export(raw)
    new, rep = redact_text(text, extra)
    rep.encoding = enc
    if enc != "utf-8":
        rep.notes.append(f"input was {enc}; the copy is written as UTF-8 "
                         f"(the strict UTF-8 parsers would refuse the original — say so in the report)")
    if check_only:
        return None, rep
    dst_dir = out_dir or src.parent
    if dst_dir.exists() and not dst_dir.is_dir():
        raise SystemExit(f"taxjson redact: --out {dst_dir} is not a directory")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / redacted_name(src, rep.accounts)
    if dst.resolve() == src.resolve():
        raise SystemExit(f"taxjson redact: refusing to overwrite {src}")
    if dst.is_symlink():
        raise SystemExit(f"taxjson redact: {dst} is a symlink — refusing to write through it")
    if dst.exists() and not force:
        raise SystemExit(f"taxjson redact: {dst} exists (use --force to overwrite)")
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        if bom:
            fh.write("﻿")
        fh.write(new)
    return dst, rep


_REVIEW_SHOWN = 25


def print_report(src: Path, dst: Optional[Path], rep: Report) -> None:
    shown_src = redacted_name(src, rep.accounts).replace(".redacted", "")
    where = f" -> {dst}" if dst else " (check only)"
    print(f"{shown_src}{where}")
    if rep.accounts and rep.unreplaced:
        left = sum(rep.unreplaced.values())
        print(f"  account ids: {len(rep.accounts)} distinct — {left} "
              f"occurrence(s) of {len(rep.unreplaced)} id(s) NOT replaced "
              f"(see REVIEW below; fix by hand or with --also)")
        for orig, ph in rep.accounts.items():
            print(f"    {Report.masked(orig)} -> {ph}")
    elif rep.accounts:
        print(f"  account ids: {len(rep.accounts)} distinct, every occurrence replaced")
        for orig, ph in rep.accounts.items():
            print(f"    {Report.masked(orig)} -> {ph}")
    else:
        print("  account ids: none found")
    print(f"  identity rows/cells (name/alias/address): {rep.identity_rows}")
    print(f"  e-mail addresses: {rep.emails}")
    for label, n in (("names in free text", rep.names),
                     ("phone numbers", rep.phones),
                     ("postal codes", rep.postal_codes),
                     ("street addresses", rep.addresses),
                     ("SIN/SSN-shaped numbers", rep.sins),
                     ("wallet addresses (distinct, pseudonymised)", len(rep.wallets)),
                     ("transaction ids (distinct, pseudonymised)", len(rep.txids)),
                     ("denylist / --also matches", rep.patterns)):
        if n:
            print(f"  {label}: {n}")
    for n in rep.notes:
        print(f"  NOTE: {n}")
    if rep.description_columns:
        print(f"  NOTE: free-text Description columns ({', '.join(rep.description_columns)}) "
              f"may still name people — read them once before sharing.")
    if rep.review:
        print(f"  REVIEW these lines of the copy before sharing — free text "
              f"the redactor could not classify ({len(rep.review)}):")
        for lineno, why in rep.review[:_REVIEW_SHOWN]:
            print(f"    line {lineno}: {why}")
        if len(rep.review) > _REVIEW_SHOWN:
            print(f"    … and {len(rep.review) - _REVIEW_SHOWN} more")


def main(argv: Optional[List[str]] = None) -> int:
    # Redacted copies are still private until reviewed: owner-only.
    os.umask(os.umask(0o077) | 0o077)
    ap = argparse.ArgumentParser(
        prog="taxjson redact",
        description="Strip account numbers, names and contact details it "
                    "recognises from broker exports, keeping row shapes. "
                    "Review the output before sharing.")
    ap.add_argument("files", nargs="+", metavar="FILE")
    ap.add_argument("--out", metavar="DIR", help="Write redacted copies here (default: beside each input)")
    ap.add_argument("--also", action="append", default=[], metavar="REGEX",
                    help="Extra pattern to replace with REDACTED (repeatable, case-insensitive)")
    ap.add_argument("--no-denylist", action="store_true",
                    help="Ignore ~/.config/taxjson/pii-denylist")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing redacted copy")
    ap.add_argument("--check", action="store_true",
                    help="Report only; write nothing; exit 1 if anything would be redacted")
    args = ap.parse_args(argv)
    # Fail CLOSED on a bad pattern: silently skipping it would publish
    # exactly the string the user asked to remove.
    _, bad_also = compile_patterns(list(args.also))
    deny = [] if args.no_denylist else load_denylist()
    bad_deny = [i for i, p in enumerate(deny, 1) if compile_patterns([p])[1]]
    if bad_also or bad_deny:
        for b in bad_also:
            print(f"taxjson redact: --also {b} is not a valid regular expression",
                  file=sys.stderr)
        if bad_deny:
            print(f"taxjson redact: denylist pattern(s) #{', #'.join(map(str, bad_deny))} "
                  f"are not valid regular expressions (fix "
                  f"~/.config/taxjson/pii-denylist or pass --no-denylist)",
                  file=sys.stderr)
        print("taxjson redact: nothing written.", file=sys.stderr)
        return 2
    extra = list(args.also) + deny
    rc = 0
    for f in args.files:
        src = Path(f)
        if not src.is_file():
            print(f"taxjson redact: {f}: not a file", file=sys.stderr); rc = 1; continue
        if src.stem.endswith(".redacted"):
            print(f"taxjson redact: {f}: already a redacted copy — skipped", file=sys.stderr); continue
        try:
            dst, rep = redact_file(src, Path(args.out) if args.out else None,
                                   extra, args.check, args.force)
        except InputRefused as e:
            print(f"taxjson redact: {e}", file=sys.stderr)
            rc = 1
            continue
        print_report(src, dst, rep)
        if args.check and rep.found_anything():
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
