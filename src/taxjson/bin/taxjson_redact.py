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
    `transfer from|to [acct] <id>` phrase, `account = "<id>"` /
    `broker_account = "<id>"` / `"account_id": "<id>"` keys — each distinct id becomes a
    stable placeholder of the SAME shape (`U99900001`, `99900001`,
    `9990-0002`) and every occurrence in the file, descriptions
    included, is replaced. Ids shorter than five characters and
    date-like numbers are never treated as ids, so quantities, prices
    and dates stay;
  * holder identity — IB `Account Information` Name / Alias / address
    rows (CSV, and the label/value cells of IB's .html statements) and
    the name/address columns of a columnar or flat AccountInformation
    section,
    `AccountAlias` / `AcctAlias` columns, a `Name` column beside an
    account or alias column (IB Flex `Account` section), `Name:` /
    `Client:` / `Owner:` header lines and Coinbase's `User,<name>,<id>`
    line (quoted or not), names after `Initiated by`,
    `Payee:`, `Beneficiary:` and honorifics (`Mrs. …`), the name after
    an `Account: …,` header, name / street / city lines in a statement
    preamble (Webull), e-mail addresses, phone numbers, US city/state/ZIP
    lines, Canadian postal
    codes, street addresses, SIN-shaped (Luhn-valid) and SSN-shaped
    numbers;
  * crypto — wallet addresses (bc1…, legacy 1…/3… base58, 0x + 40 hex),
    on-chain transaction hashes and exchange transaction ids (Kraken
    txid/refid, Coinbase ids, UUIDs): each distinct value becomes a
    stable same-shape pseudonym, so rows that shared an id still share
    one and distinct rows stay distinct;
  * anything matching the private denylist (`~/.config/taxjson/
    pii-denylist`, or $TAXJSON_PII_DENYLIST — the same file
    scripts/check-pii.sh uses, matched the same way: case-insensitive,
    a 4+ digit run also matching with spaces or dashes between digits)
    or `--also`, in the text and in the output FILE NAME. An invalid
    pattern, or a TAXJSON_PII_DENYLIST naming a missing file, FAILS
    CLOSED on the command line (exit 2, nothing written).
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
# A config / JSON key naming an account: `broker_account = "..."` (the
# live-holdings TOML fetch/verify write), `account = "..."`,
# `"account_id": "..."`. `\baccount` cannot match inside
# broker_account and '=' was not a separator, so the number survived
# with 'account ids: none found' (S036-23). The value must still pass
# _is_id (5+ digits), so `account = "margin"` is left alone.
_KEY_NAME = r'(?:[A-Za-z]+_)*(?:account|acct)(?:_?(?:id|number|num|no))?'
_ACCOUNT_KEY = re.compile(
    r'(?:(?<![A-Za-z0-9_"])' + _KEY_NAME + r'[ \t]*='          # TOML key
    r'|"' + _KEY_NAME + r'"[ \t]*:)'                          # JSON key
    r'[ \t]*"?([A-Za-z0-9][A-Za-z0-9-]{3,16}[A-Za-z0-9])"?'
    r'(?![A-Za-z0-9]|\.\d)', re.IGNORECASE)
_COL_ID = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,}[A-Z0-9]$")     # 5+ chars, mostly digits
_DATE8 = re.compile(r"^(19|20)\d{6}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACCOUNT_COLS = ("account #", "account", "xfer account", "account number",
                 "account no", "account no.", "acct", "acct #", "account id",
                 "clientaccountid", "client account id", "acctid",
                 "accountid", "account_number", "accountnumber")
# Other id columns: client / plan / portfolio / customer numbers, and
# the bilingual "Numéro de compte" labels. A header cell (or a
# `Label:` cell) naming one is an account-id column.
_ACCT_COL_RE = re.compile(
    r"(?:account|acct|a/c|client|plan|portfolio|customer|member|contract"
    r"|policy)[ _.-]*(?:#|no\.?|num\.?|nbr|number|id|identifier)"
    r"|(?:num[ée]ro|no\.?|n°)\s*(?:de\s+|du\s+)?(?:compte|client|contrat"
    r"|r[ée]gime|portefeuille)|compte",
    re.IGNORECASE)


def _is_account_col(h: str) -> bool:
    """A header / label naming an account (or client / plan) id."""
    h = h.strip().strip('"').strip().lower()
    if not h:
        return False
    if h in _ACCOUNT_COLS or _ACCT_COL_RE.fullmatch(h):
        return True
    # Bilingual labels: "Account Number / Numéro de compte".
    return "/" in h and h != "a/c" and any(
        p.strip() in _ACCOUNT_COLS or _ACCT_COL_RE.fullmatch(p.strip())
        for p in re.split(r"\s/\s|/(?!c\b)", h) if p.strip())


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
# Columns that always name a person (registered-plan parties, holders):
# RRSP/RRIF annuitant, RESP subscriber and beneficiary, joint holders.
_PERSON_COL_RE = re.compile(
    r".*\b(?:annuitant|subscriber|beneficiar(?:y|ies)|holder|owner"
    r"|contributor|spouse|successor|titulaire|b[ée]n[ée]ficiaire"
    r"|souscripteur|rentier|cotisant)s?\b.*"
    r"|(?:customer|client|member|first|last|full|given|family|middle|legal"
    r"|registered|primary|secondary|joint)[ _]*name",
    re.IGNORECASE)


def _is_person_col(h: str) -> bool:
    h = h.strip().strip('"').strip().lower()
    return bool(h) and (h in _ALIAS_COLS or bool(_PERSON_COL_RE.fullmatch(h)))


# `Label:` cells whose value (the next non-empty cell) is identity.
_IDENTITY_LABELS = ("name", "nom", "full name", "nom complet", "client",
                    "client name", "nom du client", "account holder",
                    "holder", "titulaire", "owner", "primary owner",
                    "customer", "customer name", "user", "user name",
                    "address", "adresse", "mailing address", "street",
                    "rue", "city", "ville", "postal code", "code postal",
                    "zip", "phone", "téléphone", "telephone", "email",
                    "e-mail", "courriel", "tel", "mobile", "cell", "fax",
                    "sin", "s.i.n.", "nas", "ssn", "tin", "tax id",
                    "social insurance number",
                    "numéro d'assurance sociale", "payee", "beneficiary",
                    "bénéficiaire", "remitter", "ordering customer")


def _label_kind(cell: str) -> Optional[str]:
    """'account' / 'identity' for a `Label:` cell (trailing colon), else
    None. Bilingual 'English / Français:' labels match on either half."""
    t = cell.strip().strip('"').strip()
    if not t.endswith(":"):
        return None
    t = t[:-1].strip().lower()
    if _is_account_col(t):
        return "account"
    parts = [t] + [p.strip() for p in t.split("/") if p.strip()]
    if any(p in _IDENTITY_LABELS for p in parts):
        return "identity"
    return None


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

# Letters, accented ones included (Canada-first: Josée, Hélène, Côté).
_UP = "A-ZÀ-ÖØ-Þ"
_AL = "A-Za-zÀ-ÖØ-öø-ÿ"
# A capitalised name: 1-4 words, Title-case or UPPER, or initials.
_NAME_WORD = rf"(?-i:[{_UP}][{_AL}'’-]+|[{_UP}]\.?)"
_NAME_WORDS = _NAME_WORD + r"(?:[ ]+" + _NAME_WORD + r"){0,3}"
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
    r"(" + _NAME_WORD + r"(?:[ ]+" + _NAME_WORD + r"){1,3})",
    re.IGNORECASE)

# Canada Post alphabet: no D F I O Q U anywhere, no W Z as first letter.
# Not inside a longer hyphenated / alphanumeric token.
_POSTAL = re.compile(
    r"(?<![A-Za-z0-9-])[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d"
    r"[ABCEGHJ-NPRSTV-Z]\d(?![A-Za-z0-9-])")
# A US "City, ST 12345" / "City, ST 12345-6789" line (USPS state and
# territory codes). A bare ZIP is only redacted after a state code: a
# lone 5-digit number is an amount or a quantity far more often.
_US_STATES = ("AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME"
              "|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA"
              "|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|AS|GU|MP|PR|VI|AA|AE"
              "|AP")
_US_ZIP_LINE = re.compile(
    r"(?<![\w-])(?:(?-i:[A-Z][A-Za-z.'’-]*)(?:[ ](?-i:[A-Z][A-Za-z.'’-]*))"
    r"{0,3},?\s+)?(?-i:(?:" + _US_STATES + r"))\s+\d{5}(?:-\d{4})?"
    r"(?![\w-])")
_PHONE = re.compile(
    r"(?<![\w.+-])(?:\+?1[ .-]?)?(?:\(\d{3}\)[ ]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w-])")
_PHONE_CTX = re.compile(
    r"(\b(?:phone|tel|telephone|mobile|cell|fax)\b\.?\s*(?:#|no\.?|number)?\s*[:#]?\s*)"
    r"(\+?[\d][\d ().-]{8,16}\d)", re.IGNORECASE)
_STREET_SUFFIX = (r"street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|court|crt"
                  r"|crescent|cres|lane|ln|way|place|terrace|terr|parkway|pkwy"
                  r"|highway|hwy|circle|cir|square|sq|trail|trl|sideroad|rue"
                  r"|chemin|route|rte|grove|gardens|gdns|heights|hts|mews")
# A unit prefix / suffix joins the street only through a hyphen or a
# comma FOLLOWED BY A SPACE ("<n> <Name> St, Unit <u>"): a bare comma is a
# CSV delimiter, and matching across it swallowed the next field and
# shifted every later column (S036-15).
_STREET = re.compile(
    r"(?<![\w.-])(?:(?:unit|apt|suite|ste)\.?[ \t]*#?[ \t]*\w+"
    r"(?:[ \t]*-[ \t]*|[ \t]*,[ \t]+|[ \t]+))?"
    r"\d{1,6}[A-Za-z]?(?:-\d{1,6})?\s+(?:(?-i:[A-Z][A-Za-z'’.-]*)\s+){1,4}"
    r"(?:" + _STREET_SUFFIX + r")\b\.?"
    r"(?:\s+(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\b\.?)?"
    r"(?:(?:[ \t]*,[ \t]+|[ \t]+)(?:unit|apt|suite|ste|#)\.?[ \t]*#?[ \t]*\w+"
    r"|[ \t]*#[ \t]*\w+)?", re.IGNORECASE)
# French order (Québec): number, the generic word first and lower-case,
# then the capitalised name — "1234 rue Saint-Denis", "55 boulevard de
# la Concorde", "10, chemin du Lac".
_STREET_FR = re.compile(
    r"(?<![\w.-])(?:(?:app|apt|bureau|unit|suite)\.?[ \t]*#?[ \t]*\w+"
    r"(?:[ \t]*-[ \t]*|[ \t]*,[ \t]+))?"
    r"\d{1,6}[A-Za-z]?(?:-\d{1,6})?\s*,?\s+"
    r"(?:rue|avenue|av|boulevard|boul|bd|chemin|ch|route|rte|rang|place"
    r"|mont[ée]e|c[ôo]te|all[ée]e|impasse|promenade|croissant|terrasse"
    r"|carr[ée]|autoroute|ruelle|quai|square)\.?\s+"
    r"(?:(?:de\s+la|de\s+l['’]|de|du|des|la|le|les|l['’]|d['’])\s*)?"
    rf"(?-i:[{_UP}][\w'’.-]*)(?:[ -](?-i:[{_UP}][\w'’.-]*)){{0,3}}"
    r"(?:(?:[ \t]*,[ \t]+|[ \t]+)(?:app|apt|bureau|unit|suite|#)\.?[ \t]*#?"
    r"[ \t]*\w+)?",
    re.IGNORECASE)
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
_REVIEW_NAME = re.compile(
    r"(?-i:(?<![\w])[" + _UP + r"][a-zß-öø-ÿ]+(?:\s+[" + _UP + r"]\.)?\s+["
    + _UP + r"][a-zß-öø-ÿ]+(?![\w]))")
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
        # Compiled denylist / --also patterns, also applied to the
        # output FILE NAME (check-pii scans names too).
        self.name_patterns: List[re.Pattern] = []
        # original -> placeholder for every id of the whole invocation
        # (content and file names), so two files never share a
        # pseudonym (S036-18). Per-file when redact_text runs alone.
        self.known_ids: Dict[str, str] = {}

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
    """The CSV cells of one line. A line the csv module refuses (a field
    over csv.field_size_limit()) used to return None, so its account
    column was never read and the id stayed in the copy while the
    report said every occurrence was replaced (R1-352): the limit is
    raised to the line's length, and anything else csv refuses is split
    on its unquoted commas."""
    try:
        rows = list(csv.reader(io.StringIO(line)))
    except csv.Error:
        if len(line) >= csv.field_size_limit():
            csv.field_size_limit(len(line) + 1)
            return _split(line)
        rows = [[_unquote(line[s:e]) for s, e in _field_spans(line)]]
    return rows[0] if rows else []


def _unquote(field: str) -> str:
    f = field.strip()
    if len(f) >= 2 and f[0] == f[-1] == '"':
        return f[1:-1].replace('""', '"')
    return field


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
        for m in _ACCOUNT_KEY.finditer(line):
            add(m.group(1))
        cells = _split(line.rstrip("\r\n"))
        if not cells:
            continue
        low = [c.strip().lower() for c in cells]
        # `Account Number:,,,,<id>,` (Webull preamble): the id is
        # the next non-empty cell after the label cell.
        labelled = False
        for i, c in enumerate(cells):
            if _label_kind(c) == "account":
                nxt = next((v for v in cells[i + 1:] if v.strip()), "")
                if nxt:
                    add(nxt)
                labelled = True
        if labelled:
            continue
        if len(low) > 1 and low[1] == "header":               # IB sections
            col_idx[low[0]] = [i for i, c in enumerate(low) if _is_account_col(c)]
            continue
        if any(_is_account_col(c) for c in low) and "data" not in low[:2]:
            col_idx["__flat__"] = [i for i, c in enumerate(low) if _is_account_col(c)]
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


def _field_spans(body: str) -> List[Tuple[int, int]]:
    """(start, end) of each comma-separated field of one raw CSV line."""
    spans, start, in_q = [], 0, False
    for i, ch in enumerate(body):
        if ch == '"':
            in_q = not in_q
        elif ch == "," and not in_q:
            spans.append((start, i))
            start = i + 1
    spans.append((start, len(body)))
    return spans


def _replace_field(line: str, cells: List[str], idx: int, value: str) -> str:
    """Replace one CSV field in the RAW line, leaving every other byte
    (quoting, spacing, line ending) as it was. The field is located by
    POSITION — the same text in an earlier cell (a holder's name in
    both a Subscriber and a Beneficiary column) must not be hit."""
    old = cells[idx]
    if not old:
        return line
    body = line.rstrip("\r\n")
    spans = _field_spans(body)
    if len(spans) == len(cells):
        s, e = spans[idx]
        raw = body[s:e]
        lead = raw[:len(raw) - len(raw.lstrip())]
        rep = f'"{value}"' if raw.strip().startswith('"') else value
        return line[:s] + lead + rep + line[e:]
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


def _name_line_kind(text: str) -> Optional[str]:
    """'name' for a line that is nothing but a person's name (2-5
    capitalised words, accented letters included, no statement
    vocabulary); 'mixed' when some — not all — of those words are
    statement vocabulary ("Bill Sample", "Jane Price": a name the
    redactor will not guess at, so the line goes to REVIEW); else None."""
    words = text.split()
    if not 2 <= len(words) <= 5 or ":" in text:
        return None
    vocab = 0
    for w in words:
        core = w.strip(".,'’-")
        if not core or not re.fullmatch(rf"[{_AL}][{_AL}'’.-]*", w.strip(",")):
            return None
        if not core[0].isupper():
            return None
        vocab += core.lower() in _VOCAB
    if not vocab:
        return "name"
    return "mixed" if vocab < len(words) else None


def _looks_like_name_line(text: str) -> bool:
    return _name_line_kind(text) == "name"


def _vocab_split(words: List[str]) -> Tuple[int, int]:
    """(statement-vocabulary words, words) of a candidate name."""
    n = sum(1 for w in words
            if w.lower() in _VOCAB or re.fullmatch(r"[A-Z]{3}", w))
    return n, len(words)


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


def _redact_contact(line: str, rep: Report, lineno: int = 0) -> str:
    """Phones, postal codes, street addresses, SIN/SSN, honorific /
    initiated-by names — anywhere on the line. A candidate name that
    mixes a statement word with other words ("FROM BILL SAMPLE") is
    kept but its line is listed for REVIEW."""
    line, k = _STREET.subn("REDACTED", line)
    rep.addresses += k
    line, k = _STREET_FR.subn("REDACTED", line)
    rep.addresses += k
    line, k = _PO_BOX.subn("REDACTED", line)
    rep.addresses += k
    line, k = _PHONE_CTX.subn(lambda m: m.group(1) + "REDACTED", line)
    rep.phones += k
    line, k = _PHONE.subn("REDACTED", line)
    rep.phones += k
    line, k = _POSTAL.subn("REDACTED", line)
    rep.postal_codes += k
    line, k = _US_ZIP_LINE.subn("REDACTED", line)
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

    mixed = []

    def decide(m: re.Match) -> str:
        v, n = _vocab_split([w.strip(".") for w in m.group(2).split()])
        if v == 0:
            rep.names += 1
            return m.group(1) + "REDACTED"
        if v < n:
            mixed.append(True)
        return m.group(0)
    line = _NAME_AFTER.sub(decide, line)
    line = _NAME_FROM_TO.sub(decide, line)
    if mixed and lineno:
        rep.review.append((lineno, "a name-like phrase containing a "
                                   "statement word (not redacted)"))
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


_ADDRESS_COLS = ("street", "street1", "street2", "street 1", "street 2",
                 "address", "address1", "address2", "address 1",
                 "address 2", "address line 1", "address line 2",
                 "addressline1", "addressline2", "city", "state",
                 "province", "country", "postalcode", "postal code",
                 "zip", "zipcode", "zip code", "county")
# Columns that make a header an address block (country/state alone do
# not: a trades file can carry an issuer country).
_ADDRESS_ANCHORS = ("street", "street1", "street 1", "address", "address1",
                    "address 1", "address line 1", "addressline1", "city",
                    "postalcode", "postal code", "zip", "zipcode",
                    "zip code")


def _identity_cols(low: List[str]) -> List[int]:
    """Header positions holding holder identity: alias columns always;
    a name column only beside an account or alias column; the address
    columns (Street2, City, State, Country ...) of a header that names
    an account and carries an address block — IB Flex
    AccountInformation, columnar or flat. Only the Field Name/Field
    Value layout was covered, so city, province and unit survived
    (S037-03)."""
    cols = [i for i, c in enumerate(low) if _is_person_col(c)]
    if cols or any(_is_account_col(c) for c in low):
        cols += [i for i, c in enumerate(low) if c in _HOLDER_NAME_COLS]
        if any(c in _ADDRESS_ANCHORS for c in low):
            cols += [i for i, c in enumerate(low) if c in _ADDRESS_COLS]
    return sorted(set(cols))


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


def redact_text(text: str, extra_patterns: Optional[List[str]] = None,
                known_ids: Optional[Dict[str, str]] = None
                ) -> Tuple[str, Report]:
    """Redact one export. `known_ids` (original -> placeholder) is
    shared by every file of one invocation: an id seen in an earlier
    file keeps its placeholder, and a new one gets the next number —
    two accounts' files used to both become U9990001 (S036-18)."""
    rep = Report()
    if known_ids is not None:
        rep.known_ids = known_ids
    compiled, bad = compile_patterns(extra_patterns or [])
    rep.name_patterns = compiled
    for b in bad:
        rep.notes.append(f"pattern skipped (not a valid regex): {b}")
    text = _redact_html_identity(text, rep)
    lines = text.splitlines(keepends=True)
    for orig in _collect_ids(lines):
        if orig not in rep.known_ids:
            rep.known_ids[orig] = _placeholder(orig,
                                               len(rep.known_ids) + 1)
        rep.accounts[orig] = rep.known_ids[orig]
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
                any(c in _TXID_COLS + _FREE_TEXT_COLS or _is_person_col(c)
                    or _is_account_col(c) for c in low)
                or all(not any(ch.isdigit() for ch in c) for c in nonempty)):
            # A flat CSV's column header row.
            header_row = True
            alias_cols["__flat__"] = _identity_cols(low)
            txid_cols["__flat__"] = [i for i, c in enumerate(low) if c in _TXID_COLS]
            free_cols["__flat__"] = [(i, cells[i].strip()) for i, c in enumerate(low)
                                     if c in _FREE_TEXT_COLS]
            for _i, _name in free_cols["__flat__"]:
                if _name not in rep.description_columns:
                    rep.description_columns.append(_name)
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
        label_done = False
        if cells and not is_ib_row and not header_row:
            for i, c in enumerate(cells):
                if c.strip() and _label_kind(c) == "identity":
                    j = next((k for k in range(i + 1, len(cells))
                              if cells[k].strip()), None)
                    # The value is blanked unless it is a collected
                    # account id (replaced by its placeholder below). An
                    # id-SHAPED phone or SIN after 'Phone:' / 'SIN:' was
                    # skipped as if it were one (S037-08).
                    if (j is not None
                            and cells[j].strip() not in rep.accounts):
                        line = _replace_field(line, cells, j, "REDACTED")
                        cells = _split(line.rstrip("\r\n")) or []
                        rep.identity_rows += 1
                    label_done = True
                    break
                if c.strip():
                    break
        m = None if label_done else _HEADER_LINE.match(line)
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
            kind = _name_line_kind(joined)
            if (kind == "name" or _STREET.search(joined)
                    or _STREET_FR.search(joined)
                    or _POSTAL.search(joined) or _PO_BOX.search(joined)
                    or _US_ZIP_LINE.search(joined)):
                line = _blank_cells(line)
                rep.identity_rows += 1
            elif kind == "mixed":
                rep.review.append((lineno, "name-like words (one of them a "
                                           "statement word) in a preamble line"))
        for orig, ph in ordered:
            if orig in line:
                line = id_pats[orig].sub(ph, line)
        if _EMAIL.search(line):
            line, k = _EMAIL.subn("redacted@example.com", line)
            rep.emails += k
        line = _WALLET.sub(wallet_repl, line)
        line = _TXID.sub(txid_repl, line)
        if not header_row:
            line = _redact_contact(line, rep, lineno)
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


class DenylistMissing(Exception):
    """TAXJSON_PII_DENYLIST names a file that does not exist."""


def loosen(pattern: str) -> str:
    """scripts/check-pii.sh's loosen(): every run of 4+ literal digits
    also matches with a space or dash between digits (1234 5678,
    1234-5678). Escapes, bracket expressions and {m,n} are copied
    verbatim, so the result is still a valid pattern."""
    out: List[str] = []
    run = ""
    i, n = 0, len(pattern)

    def flush() -> None:
        nonlocal run
        out.append(run[0] + "".join("[ -]?" + d for d in run[1:])
                   if len(run) >= 4 else run)
        run = ""
    while i < n:
        c = pattern[i]
        if c.isdigit() and c.isascii():
            run += c; i += 1; continue
        flush()
        if c == "\\":
            out.append(pattern[i:i + 2]); i += 2; continue
        if c == "[":
            j = i + 1
            if pattern[j:j + 1] == "^":
                j += 1
            if pattern[j:j + 1] == "]":
                j += 1
            while j < n:
                if pattern[j:j + 2] == "[:":
                    e = pattern.find(":]", j + 2)
                    if e >= 0:
                        j = e + 2; continue
                if pattern[j] == "]":
                    break
                j += 1
            out.append(pattern[i:j + 1]); i = j + 1; continue
        if c == "{":
            j = pattern.find("}", i)
            if j >= 0:
                out.append(pattern[i:j + 1]); i = j + 1; continue
        out.append(c); i += 1
    flush()
    return "".join(out)


def load_denylist(path: Optional[str] = None) -> List[str]:
    """The private denylist's patterns, digit runs loosened exactly as
    check-pii.sh loosens them. A TAXJSON_PII_DENYLIST (or `path`) that
    names a missing file raises DenylistMissing — a typo must not turn
    the denylist off silently. The default path may be absent."""
    explicit = path or os.environ.get("TAXJSON_PII_DENYLIST")
    p = Path(explicit or Path.home() / ".config" / "taxjson" / "pii-denylist")
    if not p.is_file():
        if explicit:
            raise DenylistMissing(str(p))
        return []
    pats = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            pats.append(loosen(s))
    return pats


def redacted_name(src: Path, accounts: Dict[str, str],
                  patterns: Optional[List[re.Pattern]] = None,
                  known_ids: Optional[Dict[str, str]] = None) -> str:
    """The copy's file name: the content's ids get the content's
    placeholders, and an id that appears only in the name (IB names
    downloads after the account) gets the next one. The name-only ids
    are found on the ORIGINAL stem and every id is substituted in one
    pass — scanning after the substitution re-numbered the digits of
    the placeholder itself (U99900001 -> U99900002, R1-352)."""
    stem = src.stem
    for pat in patterns or []:
        stem = pat.sub("REDACTED", stem)
    table = known_ids if known_ids is not None else dict(accounts)
    subs: Dict[str, str] = dict(accounts)
    for m in (list(_IB_ID.finditer(stem))
              + list(re.finditer(r"(?<!\d)\d{8,}(?!\d)", stem))):
        tok = m.group(0)
        if _DATE8.fullmatch(tok) or any(tok in o for o in subs):
            continue
        if tok not in table:
            table[tok] = _placeholder(tok, len(table) + 1)
        subs[tok] = table[tok]
    if subs:
        alt = re.compile("|".join(re.escape(o) for o in sorted(
            subs, key=len, reverse=True)))
        stem = alt.sub(lambda m: subs[m.group(0)], stem)
    return f"{stem}.redacted{src.suffix}"


def redact_file(src: Path, out_dir: Optional[Path], extra: List[str],
                check_only: bool, force: bool = False,
                known_ids: Optional[Dict[str, str]] = None,
                written: Optional[set] = None
                ) -> Tuple[Optional[Path], Report]:
    """`known_ids` and `written` are shared by the files of one
    invocation: one pseudonym table, and no output path written twice
    — `--force` let a second account's copy (or a second
    `activity.csv`) silently replace the first (S036-18)."""
    raw = src.read_bytes()
    why = sniff_binary(raw, src.name)
    if why:
        raise InputRefused(
            f"{src.name} is {why}, not a text export — nothing written. "
            f"Export CSV from the broker (or convert with "
            f"`taxjson-xlsx-to-csv`) and redact the CSV.")
    text, enc, bom = decode_export(raw)
    new, rep = redact_text(text, extra, known_ids)
    # A denylisted string in the file NAME counts as a finding too.
    rep.patterns += sum(len(p.findall(src.stem)) for p in rep.name_patterns)
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
    dst = dst_dir / redacted_name(src, rep.accounts, rep.name_patterns,
                                  rep.known_ids)
    if written is not None and dst.resolve() in written:
        raise InputRefused(
            f"{src}: its redacted copy {dst} was already written by this "
            f"run from another input — nothing written. Redact the two "
            f"files into different --out folders (or rename one).")
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
    if written is not None:
        written.add(dst.resolve())
    return dst, rep


_REVIEW_SHOWN = 25


def print_report(src: Path, dst: Optional[Path], rep: Report) -> None:
    shown_src = redacted_name(src, rep.accounts, rep.name_patterns,
                              rep.known_ids).replace(".redacted", "")
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
    try:
        deny = [] if args.no_denylist else load_denylist()
    except DenylistMissing as e:
        print(f"taxjson redact: TAXJSON_PII_DENYLIST names {e}, which does "
              f"not exist — fix the path (or pass --no-denylist). "
              f"Nothing written.", file=sys.stderr)
        return 2
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
    known_ids: Dict[str, str] = {}
    written: set = set()
    for f in args.files:
        src = Path(f)
        if not src.is_file():
            print(f"taxjson redact: {f}: not a file", file=sys.stderr); rc = 1; continue
        if src.stem.endswith(".redacted"):
            print(f"taxjson redact: {f}: already a redacted copy — skipped", file=sys.stderr); continue
        try:
            dst, rep = redact_file(src, Path(args.out) if args.out else None,
                                   extra, args.check, args.force,
                                   known_ids, written)
        except InputRefused as e:
            print(f"taxjson redact: {e}", file=sys.stderr)
            rc = 1
            continue
        except OSError as e:
            # An unreadable input or an unwritable --out: one line, and
            # the rest of the batch is still redacted (S036-24, S037-01).
            what = e.filename or f
            print(f"taxjson redact: {f}: {e.strerror or e} ({what}) — "
                  f"nothing written for it", file=sys.stderr)
            rc = 1
            continue
        print_report(src, dst, rep)
        if args.check and rep.found_anything():
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
