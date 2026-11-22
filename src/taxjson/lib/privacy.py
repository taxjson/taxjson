"""How a message prints a broker's identifier (an account number, a
transaction id, a reference): its first two characters and `***`, the
privacy rule every message follows (audits A2-0756, A2-1381). The raw id
stays the work-JSON's data; only what a message shows is masked. One
function (backlog TJ-2063): the parsers read it through their toolbox
(`taxjson.parsers.toolbox.masking`), the rest of the code from here; each
caller names its own fallback for an empty value.

The form an account id takes in work/ files, where rows and files are
deduplicated by account, is its hash (hash_broker_account): the id itself
never leaves the parse."""
import hashlib
import re
from typing import Any

# An account-id-shaped token inside a file name or a message (the grammar
# `shown_name` has used since audits S027-02 / S059-10, one for every
# message since TJ-2352): an IB account id (U1234567; DU a paper account,
# F and I advisor ones; IB names its downloads after the account,
# U1234567_20250101_20251231.csv), or a digit run of 7 to 12 that is not a
# YYYYMMDD date (a bank or broker account number). A run of 5 or 6 digits
# is not an id a broker issues and stays readable.
_IB_ID_RE = re.compile(r'(?<![A-Za-z0-9])(?:DU|U|F|I)\d{5,8}(?![0-9])')
_DIGITS_RE = re.compile(r'(?<![0-9])\d{7,12}(?![0-9])')
_DATE_RE = re.compile(r'^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$')


def hash_broker_account(acct) -> str:
    """The form a broker account id takes in work/ files: sha256 of the
    id the export prints, first 10 hex (the id itself never leaves the
    parse). Dedup compares these per row and per file (R1-296, A2-0008)."""
    return hashlib.sha256(str(acct).strip().encode()).hexdigest()[:10]


def mask_id(raw: Any, *, empty: str = '') -> str:
    """`raw`'s first two characters and `***`, or `empty` when it is
    missing or blank (surrounding whitespace dropped first, as the
    parsers that read ids from a cell do; an account a parser already
    normalised has none)."""
    s = str(raw or '').strip()
    return f"{s[:2]}***" if s else empty


def mask_ids(text: Any) -> str:
    """`text` with every account-id-shaped token masked by `mask_id`
    (U1234567 -> U1***, 99900001 -> 99***, a date kept); for a file name or
    a message that carries ids among other words."""
    def _mask(m):
        tok = m.group(0)
        return tok if _DATE_RE.match(tok) else mask_id(tok)
    return _DIGITS_RE.sub(_mask, _IB_ID_RE.sub(_mask, str(text)))
