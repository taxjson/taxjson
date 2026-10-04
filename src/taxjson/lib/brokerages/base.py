"""Base class for brokerage parsers.

Every brokerage parser subclasses BaseBrokerage and implements parse_file.
Shared logic — OCC option symbol reconstruction, currency suffix mapping,
fee back-compute, T+1 settlement, robust date parsing — lives here as
helpers so each parser file stays small and focused on its CSV's quirks.

Designed so an AI-generated parser for a new brokerage can produce a short
subclass that delegates to these helpers instead of re-implementing them.
"""

from datetime import datetime, timedelta
from decimal import Decimal
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple


def encode_occ_strike(strike) -> str:
    """Encode an option strike as the 8-digit OCC thousandths field.

    Uses Decimal, NOT float: `int(float("4.02") * 1000)` evaluates to
    4019 (the float 4.02 is actually 4.01999999…), not 4020 — mis-encoding
    roughly half of all sub-$200 cent-precision strikes and fragmenting an
    option's identity / cost basis across two symbols. `ticker_map.py`
    already fixed this on the ticker.map side; this is the same fix for the
    broker parsers. `strike` may be a float-as-string or a number."""
    return f"{int(Decimal(str(strike).strip()) * 1000):08d}"


# An option strike in a broker description. A strike of 1,000 or more
# may carry a thousands separator ('CALL SPX 12/19/25 5,000.00'); the
# old `[\d\.]+` stopped at the comma, so 5,000 and 5,025 both became
# strike 5 -- two contracts in one OCC symbol and one ACB pool (audit
# R1-170). The grouped form is tried first; option_strike_text drops
# the separators.
#
# A comma that is NOT a valid thousands group ('2,50', '12,5',
# '1,0000') is captured whole (the second alternative), so
# option_strike_text refuses it instead of the regex stopping at the
# comma and booking strike 2 / 12 / 1000 (audit A2-0632).
OPTION_STRIKE_RE = (r'([1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?(?![\d,])'
                    r'|[\d\.]+(?:,[\d\.]+)*)')
_VALID_GROUPED_STRIKE_RE = re.compile(r'[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?')


# A number inside broker DESCRIPTION text ("ON 1,000 SHS", "BOOK COST
# $5,293.06", "REINV@C$1,234.56"): digits with any commas, so a decimal
# comma is captured whole and judged by `desc_number` instead of being
# stripped (1,16 read as 116) or cut at the comma (1,234.56 read as 1).
DESC_NUMBER_RE = r'(\d+(?:,\d+)*(?:\.\d+)?|\.\d+)'


def option_strike_text(raw: str) -> str:
    """A matched strike with its thousands separators removed. A comma
    that is not a thousands group (a decimal comma '2,50', '1,0000') is
    refused rather than guessed (audit A2-0632)."""
    raw = raw or ''
    if ',' in raw and not _VALID_GROUPED_STRIKE_RE.fullmatch(raw):
        raise BrokerageParseError(
            f"option strike {raw!r} has a comma that is not a thousands "
            f"separator (a decimal comma?) — refusing to guess the strike")
    return raw.replace(',', '')


# Return-of-capital marker, shared by every parser so the classification
# can't drift per broker. ROC is NOT dividend income: it reduces the
# position's ACB (emitted as an ADJUST row with negative net_amount via
# `BaseBrokerage.tx_roc_adjust`). Word-boundary so e.g. "RETURNED CAPITAL
# GAINS DISTRIBUTION" phrasing doesn't false-positive; "RETURN OF CAPITAL
# GAINS" and a fund NAME ("... RET OF CAPITAL ETF CASH DIV") are not a
# return of capital, nor is one the text negates ("NOT A RETURN OF
# CAPITAL") — each became an ACB reduction instead of a dividend (audit
# R1-76).
ROC_DESC_RE = re.compile(
    r'\bRET(?:URN)?\s+OF\s+CAPITAL\b(?!\s+(?:GAINS?|ETF|FUND|INCOME)\b)',
    re.IGNORECASE)
_ROC_NEGATED_RE = re.compile(r'\bNOT\s+(?:AN?\s+)?$', re.IGNORECASE)


def is_roc_description(desc: Optional[str]) -> bool:
    for m in ROC_DESC_RE.finditer(desc or ''):
        if not _ROC_NEGATED_RE.search((desc or '')[:m.start()]):
            return True
    return False


# The record date Questrade and RBC print in an income row's description
# ("... DIST ON 100 SHS REC 12/31/24 PAY 01/15/25"), and their word for a
# distribution ("DIST ON"). Neutral facts: which tax year they decide is
# a per-country rule (lib/income_dating), never the parser's.
_REC_DATE_RE = re.compile(r'\bREC\s+(\d{1,2})/(\d{1,2})/(\d{2}(?:\d{2})?)\b',
                          re.IGNORECASE)
_DIST_LABEL_RE = re.compile(r'\bDIST\s+ON\b', re.IGNORECASE)


def income_facts_from_description(desc: Optional[str],
                                  activity: str = '') -> Dict[str, str]:
    """{record_date, income_label} read from an income row's description
    (and the broker's activity label): only the keys it finds. An
    impossible REC date is left out (the row keeps its pay date)."""
    out: Dict[str, str] = {}
    m = _REC_DATE_RE.search(desc or '')
    if m:
        mm, dd, yy = (int(g) for g in m.groups())
        if yy < 100:
            yy += 2000
        try:
            out['record_date'] = datetime(yy, mm, dd).strftime('%Y-%m-%d')
        except ValueError:
            pass
    if (_DIST_LABEL_RE.search(desc or '')
            or 'distribution' in (activity or '').lower()):
        out['income_label'] = 'distribution'
    return out


# Shared dividend-description parsers. Real broker dividend rows include
# the share count and/or per-share rate as plain English inside the
# Description column; extracting them lets us populate `quantity` and
# `price` on the emitted DIVIDEND record so it self-describes (qty held
# at record date × per-share rate ≈ amount). Falls back to None when
# the pattern doesn't match so the caller can default to zeros.
_DIV_QTY_ON_SHS_RE = re.compile(
    r'\bON\s+' + DESC_NUMBER_RE + r'\s+SH(?:S|RS|ARES)?\b',
    re.IGNORECASE,
)
_DIV_PER_SHARE_RE = re.compile(
    # "USD 0.24 per Share" / "$0.50 PER SHR" / "0.09 per share" — the
    # currency prefix is optional because we don't actually need it
    # here (the row already carries currency).
    # A thousands comma is read whole ('$1,250.00 PER SHARE' was 250)
    # and a decimal comma leaves the rate unset (audit S055-23).
    r'(?:[A-Z]{3}\s+|\$)?(?<![\d.,])' + DESC_NUMBER_RE
    + r'\s*PER\s*SH(?:R|ARE)?\b',
    re.IGNORECASE,
)
_DIV_CASH_DIVIDEND_RE = re.compile(
    # IB also ships dividend rows whose rate has no "per Share" suffix:
    # "Cash Dividend CAD 0.97 (Ordinary Dividend)". The per-share figure
    # always sits right after "Cash Dividend <CCY>", so anchor on that
    # phrase — matching a bare "<CCY> <number>" anywhere would catch the
    # ISIN or other stray digits.
    r'\bCash\s+Dividend\s+(?:[A-Z]{3}\s+|\$)' + DESC_NUMBER_RE,
    re.IGNORECASE,
)


def _parse_div_qty_rate(description: str, amount: float):
    """Return `(quantity, price)` for a dividend row.

    quantity: shares held at the record date (from "ON N SHS" pattern).
    price:    per-share dividend rate (from "X per Share" pattern, or
              derived from amount/quantity when only the count appears).

    Either or both default to 0.0 when the description is silent, in
    which case the emitting record falls back to the historical
    "qty=0, price=0" shape and downstream consumers unchanged.
    """
    qty = 0.0
    rate = 0.0
    m_qty = _DIV_QTY_ON_SHS_RE.search(description or '')
    if m_qty:
        # A decimal comma ('ON 1,5 SHS') is not a share count: left
        # unset (informational), never read as 15 (audit S062-13).
        qty = desc_number(m_qty.group(1), strict=False) or 0.0
    m_rate = _DIV_PER_SHARE_RE.search(description or '')
    if m_rate:
        rate = desc_number(m_rate.group(1), strict=False) or 0.0
    if rate == 0.0:
        # IB "Cash Dividend CAD 0.97" form — a rate without "per Share".
        m_cash = _DIV_CASH_DIVIDEND_RE.search(description or '')
        if m_cash:
            rate = desc_number(m_cash.group(1), strict=False) or 0.0
    # Derive missing field from the other when we have one + amount.
    if qty > 0 and rate == 0 and amount:
        rate = round(amount / qty, 8)
        # The paid amount is rounded to CENTS, so back-computing
        # manufactures spurious precision: 7 sh paid $2.33 yields
        # 0.33285714 for a dividend actually declared at 0.333 — and
        # the SAME payment in another account, from a broker whose
        # statement states the rate, showed a clean 0.333. Snap to
        # the FEWEST decimals that still explain the paid amount
        # within the half-cent tolerance the quantity snap below
        # uses. A genuinely fine-grained rate (0.0375 on a big
        # position) survives because no shorter form reproduces the
        # cash.
        # Only snap when the shorter form is UNAMBIGUOUS: at small
        # share counts the half-cent cash tolerance admits several
        # candidates, and blindly taking round(quotient, d) picked a
        # NEIGHBOUR of the declared rate (3 sh of a 0.555 dividend
        # gave 0.557) — and could even replace an exact quotient with
        # a worse one (4 sh at 0.0375 -> 0.037). Requiring the
        # neighbours at that precision to fail keeps the raw quotient
        # whenever the data genuinely cannot resolve the rate. At small
        # share counts the shortest consistent form can still be
        # SHORTER than the declared rate (7 sh paying $0.26 fits both
        # 0.037 and a declared 0.0375) — nothing in the data
        # distinguishes them, and the shorter form at least claims no
        # precision the cash cannot back.
        tol = 0.005 + 1e-9
        for _places in range(0, 9):
            cand = round(rate, _places)
            if abs(amount - cand * qty) > tol:
                continue
            step = 10.0 ** -_places
            if any(abs(amount - (cand + d * step) * qty) <= tol
                   for d in (-1, 1)):
                continue                       # ambiguous at this width
            rate = cand
            break
    elif rate > 0 and qty == 0 and amount:
        qty = round(amount / rate, 8)
        # The statement's amount is rounded to CENTS, so the division
        # lands NEAR the true share count, not on it (23 sh x 0.417 =
        # 9.591 -> paid 9.59 -> derived 22.99760192). When the nearest
        # integer count explains the paid amount to within the
        # half-cent rounding IB applies, snap to it. A genuinely
        # fractional DRIP position differs by more than the tolerance
        # unless it's within half a cent of the whole-share payout —
        # in which case the integer is the better estimate anyway.
        nearest = round(qty)
        if nearest > 0 and abs(amount - nearest * rate) <= 0.005 + 1e-9:
            qty = float(nearest)
    return qty, rate


# A broker-printed settle date more than this many calendar days after
# the trade is flagged ATTENTION (CA-DATE-03 / US-DATE-04, audit A2-0104).
SETTLE_LAG_FLAG_DAYS = 7


class BrokerageParseError(ValueError):
    """A broker export the parser refuses to read rather than guess at:
    a required column is missing, a required money/quantity/date cell
    is unparseable, a row's money does not add up (|proceeds| far from
    |qty| x price x multiplier), the file is a different report than the
    parser reads, or the parsed rows disagree with the broker's own
    totals. Reading a missing column as 0 can inflate a return by
    thousands — failing closed is the point. `taxjson-brokerage` turns it
    into a one-line error and a nonzero exit."""


# The project's ticker.map as `taxjson-brokerage --ticker-map` loaded it
# (run passes it): (fixed-point renames incl. TOBASE/JOURNAL, the dated
# RENAME pairs). None = no map given — every identity hint prints.
_TICKER_JOINS: Optional[Tuple[Dict[str, str], frozenset]] = None


def set_ticker_map(path) -> None:
    """Load the ticker.map whose joins make a parser's identity hint
    moot (re-audit A2-1056): a ticker-change or listing hint for a pair
    the map already pools is not printed. None clears it. A map that
    does not parse is ignored here (`taxjson run` refuses it up front)."""
    global _TICKER_JOINS
    if path is None:
        _TICKER_JOINS = None
        return
    from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                merge_renames)
    tmap = _parse_map_file(Path(path))[0]
    try:
        ren = merge_renames(tmap, True)
    except ValueError:
        ren = {}
    _TICKER_JOINS = (ren, frozenset((d.old, d.new) for d in tmap.dated))


def ticker_map_loaded() -> bool:
    return _TICKER_JOINS is not None


def ticker_map_renames(sym: str) -> bool:
    """True when the loaded ticker.map renames listing `sym` (an
    undated rule or chain, or a dated RENAME of it)."""
    if _TICKER_JOINS is None or not sym:
        return False
    from taxjson.bin.taxjson_ticker_map import map_symbol
    ren, dated = _TICKER_JOINS
    s = sym.upper()
    return map_symbol(s, ren) != s or any(old == s for old, _n in dated)


def ticker_map_joins(a: str, b: str) -> bool:
    """True when the loaded ticker.map already treats listings `a` and
    `b` as one security: both rename to the same symbol (GLOBAL, TOBASE,
    JOURNAL, undated RENAME, chains included), or a dated RENAME joins
    them (either direction, after the undated renames)."""
    if _TICKER_JOINS is None or not a or not b:
        return False
    from taxjson.bin.taxjson_ticker_map import map_symbol
    ren, dated = _TICKER_JOINS
    ma, mb = map_symbol(a.upper(), ren), map_symbol(b.upper(), ren)
    if ma == mb:
        return True
    for old, new in dated:
        mo, mn = map_symbol(old, ren), map_symbol(new, ren)
        if {mo, mn} == {ma, mb}:
            return True
    return False


def combined_accounts_note(where: str, broker: str, masked) -> str:
    """The one-line NOTE that replaces the 'statement spans N accounts'
    ATTENTION when the label declares combined_broker_accounts = true
    (`masked`: the ids, already masked to their first 2 chars + ***)."""
    masked = list(masked)
    return (f"note: {where}: {len(masked)} {broker} accounts "
            f"({', '.join(masked)}) booked together under this account "
            f"label (combined_broker_accounts = true).")


def combined_accounts_refusal(where: str, broker: str, masked,
                              why: str) -> 'BrokerageParseError':
    """combined_broker_accounts = true on a SHELTERED label whose
    statement spans several broker accounts that are not provably one
    plan: refused — one registered plan's rows booked in another's
    account would mis-state both (and a taxable account's rows would
    vanish from the return)."""
    masked = list(masked)
    return BrokerageParseError(
        f"{where}: the statement spans {len(masked)} {broker} accounts "
        f"({', '.join(masked)}) and this account is sheltered — "
        f"combined_broker_accounts = true is honoured on a sheltered "
        f"account only when every account is the same registered plan, "
        f"and {why}. Export each plan into its own inputs/<account>/ "
        f"folder (or drop the setting).")


# An account id inside a file NAME: IB names its downloads after the
# account (U1234567_20250101_20251231.csv). A digit run of 7+ that is
# not a YYYYMMDD date counts too (a bank account number).
_NAME_IB_ID_RE = re.compile(r'(?<![A-Za-z0-9])(?:DU|U|F|I)\d{5,8}(?![0-9])')
_NAME_DIGITS_RE = re.compile(r'(?<![0-9])\d{7,12}(?![0-9])')
_NAME_DATE_RE = re.compile(r'^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$')


def shown_name(path) -> str:
    """A file's name as diagnostics print it: account-id-shaped tokens
    masked to their first 2 characters + *** (the parsers' rule for ids
    in the data), so a broker's default download name does not carry
    the id into reports/ (audit S027-02 / S059-10)."""
    name = Path(str(path)).name

    def _mask(m):
        tok = m.group(0)
        if _NAME_DATE_RE.match(tok):
            return tok
        return tok[:2] + '***'
    return _NAME_DIGITS_RE.sub(_mask, _NAME_IB_ID_RE.sub(_mask, name))


def source_key(path) -> str:
    """The dedup key beside a row's `source` (its shown_name): '' when
    the name has nothing masked, else sha256 of the real file name,
    first 10 hex — so two files whose names differ only in a masked
    account-number token (manual_55500001.tt / manual_55500002.tt)
    stay two sources, while no id is written out (audit A2-0159)."""
    name = Path(str(path)).name
    if shown_name(path) == name:
        return ''
    import hashlib
    return hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]


def source_identity(source, key) -> str:
    """One string per input file for dedup: the shown name, plus the
    key when the name was masked (the form of metadata.source_accounts
    keys)."""
    source = str(source or '')
    key = str(key or '')
    return f"{source}#{key}" if key and source else source


def decode_broker_text(raw: bytes, name: str = '') -> str:
    """The text of a broker export, decoded the way every parser and
    broker detection read it: a UTF-16 BOM is UTF-16, anything else
    UTF-8 with an optional BOM. Any other encoding (a cp1252 re-save)
    raises BrokerageParseError with a one-line remedy instead of a codec
    traceback (audit S059-17)."""
    try:
        if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
            return raw.decode('utf-16')
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError as e:
        where = f"{name}: " if name else ''
        raise BrokerageParseError(
            f"{where}not UTF-8 or UTF-16 text (byte 0x{raw[e.start]:02x} "
            f"at offset {e.start}) — a spreadsheet re-save in a legacy "
            f"encoding? Re-export the file, or save it as CSV UTF-8.")


def read_broker_text(path) -> str:
    """A broker export's text through `decode_broker_text` (UTF-16 with a
    BOM, else UTF-8 with an optional BOM), with line ends folded to
    '\\n' the way a text-mode open() reads them — the drop-in for
    `open(path, encoding='utf-8-sig').read()` in a parser, so a UTF-16
    re-save is read rather than refused as 'not UTF-16' (audit
    A2-0101 / A2-1451)."""
    text = decode_broker_text(Path(path).read_bytes(), shown_name(path))
    return text.replace('\r\n', '\n').replace('\r', '\n')


# Strict number grammar for REQUIRED money/quantity cells. A leading
# sign, then either a plain digit run or a comma-grouped integer part
# whose groups are exactly three digits after a lead that is not 0
# (1,234,567; "0,125" is refused), then an optional fraction. A decimal comma ("1234,56"), a space-grouped number
# ("1 000"), a stray letter, or an empty cell is not guessed at.
_STRICT_NUM_RE = re.compile(
    r'^(?P<sign>[+-]?)'
    r'(?P<int>[1-9]\d{0,2}(?:,\d{3})+|\d*)'
    r'(?P<frac>\.\d*)?'
    r'(?P<exp>[eE][+-]?\d+)?$', re.ASCII)   # ASCII digits only (S055-09)
# A comma is only ever a THOUSANDS separator: digit groups of exactly
# three after a 1-3 digit lead that does not start with 0 ("0,125" is a
# decimal comma, never 125 -- audit S055-08), optionally followed by a
# dot fraction.
# Anything else with a comma ("0,95", "1,5", "1.234,56", "12,3456") is a
# decimal-comma (French/European locale) number, and stripping the comma
# reads it 100x or 10x too large -- so it is refused, never scaled.
_THOUSANDS_COMMA_RE = re.compile(
    r'^[+-]?[1-9]\d{0,2}(?:,\d{3})+(?:\.\d*)?$')


def check_comma_grouping(num_text: str, raw=None, *, where: str = '',
                         field: str = 'value') -> None:
    """Raise BrokerageParseError when `num_text` (a number already
    stripped of currency signs and accounting parentheses) contains a
    comma that is not a valid thousands separator. `raw` is the original
    cell for the message. '1,234.56' passes; '1234,56' and '1.234,56'
    raise -- a decimal comma is refused rather than read as 123456."""
    if ',' not in num_text:
        return
    if _THOUSANDS_COMMA_RE.match(num_text.strip()):
        return
    loc = f"{where}: " if where else ''
    shown = num_text if raw is None else raw
    raise BrokerageParseError(
        f"{loc}{field} {shown!r} uses a comma that is not a thousands "
        f"separator — a decimal comma (French/European locale)? It is "
        f"refused rather than read 10x-100x too large. Re-export with a "
        f"decimal POINT (e.g. 1234.56 or 1,234.56).")


def desc_number(text: str, *, where: str = '', field: str = 'value',
                strict: bool = True) -> Optional[float]:
    """A number captured from description text (audit S062-13 / S064-19 /
    S016-01): thousands commas only. A decimal comma raises
    BrokerageParseError when `strict` (money and share counts), else
    returns None (an informational field the caller leaves unset)."""
    s = (text or '').strip()
    try:
        check_comma_grouping(s, where=where, field=field)
        return float(s.replace(',', ''))
    except BrokerageParseError:
        if strict:
            raise
        return None


# Unicode minus signs and dashes spreadsheets substitute for '-'.
_MINUS_CHARS = ('−', '‒', '–', '—', '﹣', '－')
_CURRENCY_SIGNS = ('$', '€', '£', '¥')


def parse_strict_number(raw, *, field: str = 'value', where: str = '',
                        allow_blank: bool = False,
                        blank: Optional[float] = None) -> Optional[float]:
    """Parse a REQUIRED numeric cell, raising BrokerageParseError on
    anything ambiguous instead of returning 0.

    Accepted: `1234.5`, `-1,234.50`, `+3`, `.5`, `1e-05`, a currency
    sign (`$-12.00`, `-$12.00`), accounting parentheses as NEGATIVE
    (`(1,234.56)` == -1234.56), and the unicode minus (U+2212) or a
    dash as the sign. Rejected: a decimal comma (`1234,56`), thousands
    separators outside valid three-digit groups (`12,34`, `1,2345`),
    space-grouped digits (`1 000`), a trailing minus, and any other
    text. A blank cell is an error unless `allow_blank`, in which case
    `blank` is returned. `field`/`where` name the cell in the error."""
    loc = f"{where}: " if where else ''
    s = '' if raw is None else str(raw).strip()
    if not s:
        if allow_blank:
            return blank
        raise BrokerageParseError(f"{loc}required {field} is blank")
    t = s
    for ch in _MINUS_CHARS:
        t = t.replace(ch, '-')
    neg = False
    if t.startswith('(') and t.endswith(')'):
        neg = True
        t = t[1:-1].strip()
    # ONE currency sign on either side of the sign: $-12 / -$12. A
    # second one ('$€5', '$-€5') is text, refused below (audit A2-0633).
    for cs in _CURRENCY_SIGNS:
        if t.startswith(cs):
            t = t[len(cs):].lstrip()
            break
        if t[:1] in '+-' and t[1:].startswith(cs):
            t = t[0] + t[1 + len(cs):].lstrip()
            break
    m = _STRICT_NUM_RE.match(t)
    if (not m or not (m.group('int') or (m.group('frac') or '')[1:])
            or (m.group('exp') and ',' in m.group('int'))
            or (neg and m.group('sign'))):
        raise BrokerageParseError(
            f"{loc}{field} {s!r} is not a number this parser accepts "
            f"(decimal commas, space-grouped digits and text are refused "
            f"rather than guessed)")
    val = float(t.replace(',', ''))
    if not math.isfinite(val):
        # 1e400 or a 400-digit run overflows to inf (audit S055-09).
        raise BrokerageParseError(
            f"{loc}{field} {s!r} is out of range — refusing it")
    return -val if neg else val


# Canadian listing identity (audit S010-05 / S014-07). A security's
# symbol is its ACB pool and superficial-loss key, so every parser must
# spell one Canadian listing the same way. The canonical form is
# ROOT.TO for EVERY Canadian venue (TSX, TSX Venture, CSE, NEO):
#   * IB, RBC and Webull cannot (RBC/Webull) or do not (IB) put the venue
#     in the symbol -- they stamp every CAD listing .TO, and real books
#     (ticker.map TOBASE rules, QUOTE price aliases) are keyed on
#     that; Questrade alone named the venue (.VN/.CN/.NE), so a Venture
#     name bought at Questrade and sold at IB split into two pools and a
#     cross-account superficial loss was missed.
#   * TSX and TSX Venture share one symbol namespace (TMX), so ROOT.TO is
#     unambiguous for a Venture listing; price lookups that need the venue
#     go through a ticker.map QUOTE line (PNG.TO -> PNG.V).
# TSX preferred shares are dotted per series: Questrade's FTN.PRA.TO is
# the FTN.PR.A.TO every other parser emits.
_CA_VENUE_SUFFIX_RE = re.compile(r'\.(VN|CN|NE)$', re.IGNORECASE)
_CA_PREF_UNDOTTED_RE = re.compile(r'^([A-Z0-9]+)\.(PR|PF)([A-Z]{1,2})$',
                                  re.IGNORECASE)


def canonical_ca_root(root: str) -> str:
    """Dot a TSX preferred-share series (FTN.PRA -> FTN.PR.A,
    TD.PFB -> TD.PF.B); any other root is returned unchanged. `root` is
    the symbol WITHOUT its .TO suffix."""
    m = _CA_PREF_UNDOTTED_RE.match(root or '')
    if not m:
        return root
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"


def canonical_ca_listing(symbol: str, currency: str = 'CAD') -> Optional[str]:
    """The canonical spelling of a symbol that names a CANADIAN venue
    (.TO, .V in CAD, .VN, .CN, .NE): ROOT.TO with a dotted preferred
    series. None when the symbol carries no Canadian venue suffix (the
    caller then suffixes it from the currency). A bare `.V` counts as
    TSX Venture only in CAD -- in another currency it may be a class
    letter."""
    sym = (symbol or '').strip()
    up = sym.upper()
    if up.endswith('.TO') and len(up) > 3:
        return f"{canonical_ca_root(sym[:-3])}.TO"
    if (up.endswith('.V') and len(up) > 2
            and (currency or '').upper() == 'CAD'):
        return f"{canonical_ca_root(sym[:-2])}.TO"
    m = _CA_VENUE_SUFFIX_RE.search(sym)
    if m and m.start() > 0:
        return f"{canonical_ca_root(sym[:m.start()])}.TO"
    return None


class BaseBrokerage:
    # 100 for options (each contract = 100 shares); 1 for equities/crypto.
    OPTION_MULTIPLIER = 100

    # Currency → ticker-suffix mapping for the apply_currency_suffix helper.
    # Subclasses can override with a narrower or different mapping.
    CURRENCY_EXT_MAP: Dict[str, str] = {
        'CAD': 'TO',
        'USD': 'US',
        'AUD': 'AX',
        'GBP': 'L',
    }

    # Default fallback when CURRENCY_EXT_MAP doesn't contain the currency.
    # Use 'US' to match Webull's behavior; subclasses use this for unknown
    # currencies — most brokerages echo the currency code itself instead.
    CURRENCY_EXT_FALLBACK: Optional[str] = None

    # Default account label used when the source CSV doesn't name an account.
    # The taxjson-brokerage CLI overrides this with --account <name>.
    DEFAULT_ACCOUNT: str = "Unknown"

    # The project's country ("canada" | "usa"), set by taxjson-brokerage
    # from --country; None when the parser runs without one. Parsers
    # emit neutral FACTS whatever it is (partition rule: country gates
    # live in the engine/command layer). It only chooses which law a
    # user-facing message cites, so a US project never reads an ITA
    # section and a Canadian one never reads an IRC one (re-audit
    # A2-0723 / A2-1304 / A2-1308).
    country: Optional[str] = None

    # [accounts.<name>] combined_broker_accounts = true (taxjson-brokerage
    # --combined-broker-accounts): the user declares that every broker
    # account in this label's statements is theirs and taxable together,
    # so a statement spanning several broker accounts is a one-line NOTE,
    # not an ATTENTION. Refused on a sheltered label unless the statement
    # itself shows every account is the same plan (combined_accounts_*).
    combined_broker_accounts: bool = False
    # Whether the label is a taxable account (taxjson-brokerage
    # --account-type); None when the caller did not say.
    account_taxable: Optional[bool] = None

    def law(self, canada: str, usa: str, neutral: str = "") -> str:
        """The wording for the project's country: `canada` / `usa`, or
        `neutral` (no statute) when the country is unknown."""
        if self.country == "canada":
            return canada
        if self.country == "usa":
            return usa
        return neutral

    def __init__(self) -> None:
        # Skipped-row accounting. A row the parser cannot classify must be
        # COUNTED, never silently dropped — the 0-transactions safety net
        # only catches total parser failure, not partial drop (a new
        # broker action code losing rows with zero signal is exactly how
        # taxable events go missing). `_rows_seen`/`_rows_consumed` stay
        # None/0 for parsers that don't do row-level accounting; the ones
        # that do let `taxjson-brokerage --lint` reconcile
        # rows_seen == consumed + skipped exactly.
        self._skip_counts: Dict[str, int] = {}
        self._rows_seen: Optional[int] = None
        self._rows_consumed: int = 0

    def count_skip(self, category: str) -> None:
        self._skip_counts[category] = self._skip_counts.get(category, 0) + 1

    def note_row_consumed(self) -> None:
        self._rows_consumed += 1

    # Skip-category prefix for rows the parser RECOGNIZES as non-events
    # (a fiat deposit, an FX conversion, a statement-metadata section).
    # They still count toward the lint reconciliation, but the summary
    # lists them in their own quieter note instead of the "if any of
    # these are trades/income, the parser needs a new branch" call to
    # action — that wording is for rows the parser could NOT classify.
    KNOWN_NONEVENT_PREFIX = 'non-event '

    def count_nonevent(self, category: str) -> None:
        """count_skip for a row the parser RECOGNIZES as a non-event
        (subtotal, FX conversion, fiat deposit, metadata). Still part
        of the lint reconciliation; reported in the calmer note."""
        self.count_skip(self.KNOWN_NONEVENT_PREFIX + category)

    def emit_skip_summary(self, source_name: str) -> None:
        """One stderr note per parse listing what was dropped and why.
        Quiet when nothing was skipped. Recognized non-events (see
        KNOWN_NONEVENT_PREFIX) get a separate, calmer line."""
        if not self._skip_counts:
            return
        import sys
        pfx = self.KNOWN_NONEVENT_PREFIX
        known = {c: n for c, n in self._skip_counts.items()
                 if c.startswith(pfx)}
        unknown = {c: n for c, n in self._skip_counts.items()
                   if not c.startswith(pfx)}
        if known:
            total = sum(known.values())
            detail = ", ".join(f"{cat[len(pfx):]}: {n}" for cat, n in
                               sorted(known.items()))
            print(f"note: {source_name}: {total} recognized non-event "
                  f"row(s) not translated — {detail}.", file=sys.stderr)
        if unknown:
            total = sum(unknown.values())
            detail = ", ".join(f"{cat}: {n}" for cat, n in
                               sorted(unknown.items()))
            print(f"note: {source_name}: skipped {total} unclassified "
                  f"row(s) — {detail}. If any of these are trades/income, "
                  f"the parser needs a new branch (see CONTRIBUTING).",
                  file=sys.stderr)

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def statement_accounts(self) -> set:
        """The broker account ids the last parsed file covers, when the
        export names them (an IB statement's Account Information);
        empty when it does not. taxjson-brokerage records them HASHED
        per file, so cross-file dedup can tell statements of two
        different broker accounts apart (audit R1-296)."""
        return set()

    # ---------------------------------------------------------------- options

    # The two regex shapes the brokerages produce:
    #   "CALL AAPL 06/20/25 150.00"           — Questrade/Webull/RBC variant
    #   "ASN - CALL .QQZ 06/20/25 30 QQZ HOLDINGS"   — RBC option-leg notification
    #   "ASSIGNMENT OF OPTION ... CALL ..."   — RBC stock-leg notification
    # The base regex captures the common skeleton; subclasses can add their
    # own variants by overriding _option_description_patterns.
    _BASE_OPTION_PATTERNS = (
        re.compile(
            r'^(?:EXP\s*-\s*|ASN\s*-\s*)?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+(\d{1,2}/\d{1,2}/\d{2})\s+' + OPTION_STRIKE_RE,
            re.IGNORECASE,
        ),
        re.compile(
            r'ASSIGNMENT OF OPTION.*?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+(\d{1,2}/\d{1,2}/\d{2})\s+' + OPTION_STRIKE_RE,
            re.IGNORECASE,
        ),
    )

    def _option_description_patterns(self) -> Tuple[re.Pattern, ...]:
        return self._BASE_OPTION_PATTERNS

    def parse_option_from_description(self, desc: str) -> Optional[Dict[str, str]]:
        """Extract (right, base_ticker, expiry, strike) from a brokerage's
        free-text option description. Returns None if nothing looks like an
        option string."""
        if not desc:
            return None
        for pat in self._option_description_patterns():
            m = pat.search(desc)
            if m:
                right_str, base, expiry_raw, strike_raw = m.groups()
                return {
                    'right': 'P' if right_str.upper() == 'PUT' else 'C',
                    'base': base.strip().lstrip('.').rstrip('.').replace(' ', '.'),
                    'expiry': expiry_raw,
                    'strike': option_strike_text(strike_raw),
                }
        return None

    def format_occ_symbol(self, right: str, base: str, expiry: str, strike: str) -> str:
        """Build an OCC-style option symbol: BASE{YYMMDD}{C/P}{strike*1000:08d}.

        Accepts the expiry as "MM/DD/YY" (two-digit year). Strike may be a
        float-as-string."""
        m, d, y = expiry.split('/')
        expiry_date = f"{y}{int(m):02d}{int(d):02d}"
        return f"{base}{expiry_date}{right}{encode_occ_strike(strike)}"

    # ----------------------------------------------------------- currency ext

    _CURRENCY_SUFFIX_RE = re.compile(r'\.(TO|US|AX|L)$', re.IGNORECASE)

    def apply_currency_suffix(self, symbol: str, currency: str) -> str:
        """Normalize a symbol and append a currency-derived suffix.

        Strips any existing .TO/.US/.AX/.L, replaces spaces with dots, then
        appends the suffix from CURRENCY_EXT_MAP. Unknown currencies fall
        back to CURRENCY_EXT_FALLBACK if set, otherwise echo the currency
        code itself."""
        if not symbol:
            return symbol
        sym = symbol.replace(' ', '.')
        # A Canadian venue suffix (Questrade's .VN / .CN / .NE, a .V)
        # is one Canadian listing: canonical ROOT.TO, the spelling every
        # other parser emits (see canonical_ca_listing). It used to give
        # ABC.V, CCC.CN.TO and XYZ.NE.TO -- three identities IB, RBC and
        # Webull never produce, so pools split across brokers and a
        # cross-account superficial loss was missed (audit S010-05). A
        # .TO listing traded in USD (DLR.U.TO) keeps the currency rule
        # below.
        if not sym.upper().endswith('.TO'):
            ca = canonical_ca_listing(sym, currency)
            if ca is not None:
                return ca
        sym = self._CURRENCY_SUFFIX_RE.sub('', sym)
        # A currency code is case-blind: 'usd' made the suffix '.usd'
        # and split the pool from XYZ.US (audit S055-17).
        currency = (currency or '').strip().upper()
        ext = self.CURRENCY_EXT_MAP.get(currency, self.CURRENCY_EXT_FALLBACK or currency)
        if ext == 'TO':
            # FTN.PRA -> FTN.PR.A (audit S014-07).
            sym = canonical_ca_root(sym)
        return f"{sym}.{ext}"

    def tx_roc_adjust(self, *, symbol: str, currency: str, date: str,
                      desc: str, amount: float,
                      account: Optional[str] = None) -> Dict[str, Any]:
        """One shared shape for a return-of-capital row: an ADJUST that
        REDUCES ACB by the cash received (net_amount = -amount), tagged
        type='roc' so `taxjson roc`/`roc-sum` can report it. `amount` is
        signed cash received — positive for a normal ROC posting, negative
        for a broker reversal row (which then nets out as a positive
        ADJUST). ROC must NOT be booked as dividend income: that
        double-errs (income overstated now, ACB overstated → gains
        understated at sale).

        The row is dated by its posting (pay) date; a record date the
        description prints ("REC 12/30/24") rides along as the neutral
        `record_date` fact — lib/income_dating decides, per country,
        which date lowers the cost."""
        tx = {
            'action': 'ADJUST',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': symbol, 'quantity': 0.0, 'currency': currency,
            'net_amount': -amount, 'gross_amount': 0.0, 'type': 'roc',
            'account': account or getattr(self, 'DEFAULT_ACCOUNT', 'PORTFOLIO'),
            'description': desc,
        }
        rec = income_facts_from_description(desc).get('record_date')
        if rec:
            tx['record_date'] = rec
        return tx

    # ----------------------------------------------------------- sign / coerce

    @staticmethod
    def signed_quantity(qty: float, action_is_sell: bool) -> float:
        """Convention: buys positive, sells negative. The cost-basis tracker
        keys off this sign — flipping it silently corrupts gain/loss."""
        return -abs(qty) if action_is_sell else abs(qty)

    @staticmethod
    def clean_number(raw: str, default: float = 0.0) -> float:
        """LEGACY lenient CSV numeric, for OPTIONAL cells. Strips commas
        and currency symbols. Accounting parentheses are NEGATIVE
        (`(1,234.56)` == -1234.56) — the old magnitude reading silently
        flipped the sign of any parenthesized amount whose parser did
        not abs() it; a caller that wants a magnitude (Webull's buy
        proceeds) takes abs() itself. The unicode minus is a minus.

        Returns `default` for a blank cell and, with a stderr warning,
        for unparseable text. A DECIMAL comma (`0,95`, `1.234,56`) raises
        BrokerageParseError: stripping it read the value 100x too large
        (audit R1-93); a thousands comma (`1,234.56`) is fine.
        REQUIRED money/quantity cells must use
        `parse_strict_number` instead: a garbage-to-0 read of a
        required field is how a missing column can inflate a return
        by thousands."""
        if raw is None or raw == '':
            return default
        s = str(raw).strip()
        if not s:
            return default
        for ch in _MINUS_CHARS:
            s = s.replace(ch, '-')
        neg = s.startswith('(') and s.endswith(')')
        if neg:
            s = s[1:-1]
        s = s.replace('$', '').replace('€', '').replace('£', '').strip()
        check_comma_grouping(s, raw)
        s = s.replace(',', '')
        try:
            v = float(s)
        except ValueError:
            import sys
            print(f"warning: numeric cell {raw!r} is not a number — read "
                  f"as {default!r}", file=sys.stderr)
            return default
        return -v if neg else v

    # Strict counterpart for REQUIRED cells (see module-level helper).
    parse_strict_number = staticmethod(parse_strict_number)

    @staticmethod
    def require_columns(header_map: Dict[str, int], required, *,
                        section: str, where: str = '') -> None:
        """Raise BrokerageParseError naming the section and every
        missing column when a required one is absent — the parser must
        not fall back to 0 / a default for money, quantity, price, date
        or currency columns. `required` items may be a tuple of
        alternatives (any one present satisfies it)."""
        missing = []
        for col in required:
            alts = col if isinstance(col, tuple) else (col,)
            if not any(a in header_map for a in alts):
                missing.append(' or '.join(repr(a) for a in alts))
        if missing:
            loc = f"{where}: " if where else ''
            raise BrokerageParseError(
                f"{loc}section {section!r} is missing required "
                f"column(s) {', '.join(missing)} — refusing to guess "
                f"(a missing money column read as 0 corrupts the "
                f"return). Re-export the statement in the standard "
                f"English layout.")

    # ------------------------------------------------------- fee back-compute

    def back_compute_fee(
        self, qty: float, price: float, net_amount: float, is_option: bool,
        *, min_fee: float = 0.005, sanity_ratio: float = 0.25,
    ) -> float:
        """For brokerages whose CSV doesn't break out fees, infer the fee
        from the gross (qty * price * multiplier) and the net, SIGNED by
        the trade's direction (quantity sign): a buy's fee is what the
        net costs beyond the gross, a sale's what it falls short of it.

        Display only (fees.rpt and the .sum fee lines; gains use the
        net). Guards: a residual under `min_fee` — or on the wrong side
        (a buy that cost LESS than qty x a rounded price) — is rounding
        noise, not a fee: 0, never sign-flipped into a charge by abs()
        (audit R1-22). A residual beyond `sanity_ratio` of |net| PLUS a
        commission allowance (10 + 2 per option contract) is a units
        artifact (a wrong contract multiplier) and becomes 0; the ratio
        alone zeroed real flat commissions on cheap option fills (a
        1.99 fee on a 6.01 sale, RBC's 11.95 on a 43.95 buy — audit
        R1-22 / R1-88)."""
        multiplier = self.OPTION_MULTIPLIER if is_option else 1
        theoretical_gross = abs(qty) * price * multiplier
        if qty > 0:
            implicit = abs(net_amount) - theoretical_gross
        elif qty < 0:
            implicit = theoretical_gross - net_amount
        else:
            implicit = abs(theoretical_gross - abs(net_amount))
        if implicit < min_fee:
            return 0.0
        # net == 0 is exempt on purpose: an RBC sale of a few contracts
        # at 0.01 can net $0 when the commission eats the whole gross.
        # A $0 net that is a MISSING cell (audit R1-91) must be refused
        # by the parser before it gets here (Webull does).
        allowance = 10.0 + (2.0 * abs(qty) if is_option else 0.0)
        if (abs(net_amount) > 1e-9
                and implicit > sanity_ratio * abs(net_amount) + allowance):
            return 0.0
        return round(implicit, 4)

    def theoretical_gross(self, qty: float, price: float, is_option: bool) -> float:
        multiplier = self.OPTION_MULTIPLIER if is_option else 1
        return round(abs(qty) * price * multiplier, 4)

    # -------------------------------------------------------- date / settle

    @staticmethod
    def parse_date(raw: str, *formats: str) -> Optional[datetime]:
        """Try each format in order; return the first that parses. None if
        none match. Strips surrounding whitespace."""
        if not raw:
            return None
        s = raw.strip()
        for fmt in formats:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def newest_first(dates: List[Any]) -> bool:
        """True when an export lists its rows NEWEST FIRST: the row dates
        (file order; None/blank skipped) never increase and there are at
        least two distinct ones. A one-date file, or one whose dates go
        both ways, reads as oldest first (file order kept).

        Rows at one moment (Webull, Questrade and the generic importer
        print no clock time) are taken in the order a parser emits them
        (tax-logic CA-DATE-14 / US-DATE-13; every later stage sorts
        stably), so a parser reads a newest-first export bottom-up —
        otherwise a same-day sell-then-rebuy would replay rebuy first.
        RBC Direct does this with its own per-row times."""
        ds = [d for d in dates if d]
        return (len(set(ds)) > 1
                and all(a >= b for a, b in zip(ds, ds[1:])))

    @staticmethod
    def disambiguate_split_fills(transactions: List[Dict[str, Any]]) -> None:
        """Mark the second+ instance of identical-looking rows in this
        file as split fills. Some brokerages emit multiple CSV rows for
        one order that gets filled in pieces at the same price and time;
        they're physically distinct trades that produce byte-identical
        CSV rows. Without this, the deterministic id() hash collapses
        them to one entry under taxjson-sort --dedup.

        Mutates transactions in place. Modifies only the 2nd+ occurrence
        so the first instance keeps its original (stable) id.
        """
        seen: Dict[tuple, int] = {}
        for tx in transactions:
            key = (
                tx.get('date'), tx.get('time'), tx.get('symbol'),
                tx.get('action'), tx.get('quantity'),
                tx.get('price'), tx.get('net_amount'),
                tx.get('account'),
            )
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                existing = tx.get('description') or ''
                tx['description'] = f"{existing} [fill #{seen[key]}]".strip()

    @staticmethod
    def option_expiry_booking_date(posting_iso: str,
                                   expiry_mmddyy: str) -> str:
        """The date an EXPIRED-option row belongs on. Questrade and RBC
        post the expiry on the next business day (a Friday 07/18/25
        expiry arrives dated Monday 07/21), but the contract ceased to
        exist — the disposition happened — on its expiry date; a Dec-31
        expiry posted Jan 2 would otherwise land in the next tax year.
        Returns the contract's own expiry (from the description's
        MM/DD/YY) when it is at most a week before the posting date;
        otherwise the posting date unchanged (a garbled description
        must not relocate the row arbitrarily)."""
        try:
            exp = datetime.strptime(expiry_mmddyy.strip(), "%m/%d/%y")
            post = datetime.strptime(posting_iso, "%Y-%m-%d")
        except (ValueError, AttributeError):
            return posting_iso
        if timedelta(0) < post - exp <= timedelta(days=7):
            return exp.strftime("%Y-%m-%d")
        return posting_iso

    # "... AS OF mm/dd/yy EXPIRED", "... WARRANT EXP mm/dd/yy - EXPIRED"
    _DESC_EXPIRY_RE = re.compile(
        r'\b(?:AS\s+OF|EXP(?:IRY|IRES|IRED|\.)?)\s+'
        r'(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b', re.IGNORECASE)

    @classmethod
    def non_option_expiry_booking_date(cls, posting_iso: str,
                                       desc: str) -> str:
        """The date a rights/warrant EXPIRY row belongs on (audit
        S065-04): like an option, an expiring right or warrant has no
        settlement cycle and brokers post it a business day or so late,
        so a Dec-31 expiry posted Jan 2 moved its loss into the next
        year. The expiry date comes from the description's "AS OF
        mm/dd/yy" / "EXP mm/dd/yy" when it is at most a week before the
        posting date; otherwise the posting date is kept."""
        m = cls._DESC_EXPIRY_RE.search(desc or '')
        if not m:
            return posting_iso
        mm, dd, yy = m.groups()
        return cls.option_expiry_booking_date(
            posting_iso, f"{int(mm):02d}/{int(dd):02d}/{yy[-2:]}")

    def clamp_settlement_to_expiry(self, transactions: List[Dict[str, Any]],
                                   expiries: List[Dict[str, Any]]) -> None:
        """Clamp this file's trades (see `clamp_settlement_across`) and
        remember the expiry rows: taxjson-brokerage re-runs the clamp over
        ALL of an account's files, so a Dec-31 0DTE trade whose expiry row
        sits in the NEXT yearly export (Questrade/RBC/Webull post it the
        next business day) is clamped too (audit S055-22)."""
        self.expiry_rows = list(getattr(self, 'expiry_rows', [])) + list(
            expiries)
        self.clamp_settlement_across(transactions, expiries)

    @staticmethod
    def clamp_settlement_across(transactions: List[Dict[str, Any]],
                                expiries: List[Dict[str, Any]]) -> None:
        """An option expiry has no settlement cycle: the contract ceases
        to exist on its expiry date, so the expiry row is booked with
        date_settle == date (parsers set that themselves). A trade in
        the SAME contract executed on the expiry day (a 0DTE buy or
        sell-to-open) still carries a T+1 settle that lands AFTER the
        expiry — and Canada orders the book by settle date, so the
        expiry would close a position that doesn't exist yet: a long
        0DTE call that expired worthless read as a $0 short WRITE
        followed by a buy-to-close, and a Dec-31 0DTE trade's settle
        crossed the tax year while its expiry did not. Clamp such a
        trade's settle to the expiry date (never before its own trade
        date). Only contracts with an expiry row in `expiries` are
        touched; everything else keeps its broker/computed settle.

        Mutates `transactions` in place."""
        by_key: Dict[tuple, str] = {}
        for e in expiries:
            key = (e.get('symbol'), e.get('account'))
            d = e.get('date_settle') or e.get('date') or ''
            if d and (key not in by_key or d > by_key[key]):
                by_key[key] = d
        if not by_key:
            return
        for tx in transactions:
            if tx.get('action') not in ('BUYSELL', 'ASSIGN'):
                continue
            exp = by_key.get((tx.get('symbol'), tx.get('account')))
            if not exp:
                continue
            d, s = tx.get('date') or '', tx.get('date_settle') or ''
            if d and s and d <= exp < s:
                tx['date_settle'] = exp

    @staticmethod
    def check_settle_order(date_iso: str, settle_iso: str, *,
                           where: str = '', what: str = '') -> None:
        """A broker-printed settlement date EARLIER than the trade date is
        a garbled cell, not a settlement: the tax year follows the settle
        date, so it moved the disposition into the prior year with no
        warning (audit R1-75 / S065-05). Refused (CA-DATE-03 /
        US-DATE-04)."""
        if date_iso and settle_iso and settle_iso < date_iso:
            loc = f"{where}: " if where else ''
            raise BrokerageParseError(
                f"{loc}Settlement Date {settle_iso} is before the trade "
                f"date {date_iso}{f' ({what})' if what else ''} — a "
                f"settlement never precedes its trade, and the tax year "
                f"follows the settle date; refusing to guess. Fix the "
                f"cell (or blank it for the standard cycle).")
        # A printed settle date far AFTER the trade (a typo a year out)
        # moves the disposition into a later tax year just as silently:
        # trusted, but flagged on the console (audit A2-0104). No cycle
        # with its holidays runs past 7 calendar days.
        if date_iso and settle_iso and settle_iso > date_iso:
            try:
                lag = (datetime.strptime(settle_iso[:10], '%Y-%m-%d')
                       - datetime.strptime(date_iso[:10], '%Y-%m-%d')).days
            except ValueError:
                return
            if lag > SETTLE_LAG_FLAG_DAYS:
                import sys
                loc = f"{where}: " if where else ''
                print(f"warning: ATTENTION: {loc}Settlement Date "
                      f"{settle_iso} is {lag} days after the trade date "
                      f"{date_iso}{f' ({what})' if what else ''} — no "
                      f"settlement cycle is that long, and the tax year "
                      f"follows the settle date. Booked as printed; check "
                      f"the cell (blank it for the standard cycle).",
                      file=sys.stderr)

    @staticmethod
    def warn_zero_cost_buy(where: str, symbol: str, qty: float,
                           price: float, net: float) -> None:
        """A share BUY at $0 price and $0 cash is almost always a
        transfer or journal row, booked with no cost (the generic
        importer refuses it). Booked as printed, flagged on the console
        (audit A2-0619)."""
        if (qty or 0) > 0 and abs(price or 0) < 1e-9 \
                and abs(net or 0) < 0.005:
            import sys
            print(f"warning: ATTENTION: {where}: a buy of {qty:g} "
                  f"{symbol} at ZERO cost (price and cash both 0) — "
                  f"booked with no cost basis. If it is a transfer or a "
                  f"journal, book its real cost (a .tt BUYSELL) instead.",
                  file=sys.stderr)

    def settlement_date_t1(self, date_str: str, *formats: str,
                           currency: str = 'USD') -> str:
        """Add one settlement day (holiday-aware for USD and CAD) to the
        given trade date. Used by brokerages whose CSV doesn't carry a
        settlement-date column, for options (T+1 in every era).

        formats lists strptime patterns to try; if none parse, the input
        string is returned unchanged so callers can degrade gracefully on
        malformed dates."""
        dt = self.parse_date(date_str, *formats) if formats else None
        if dt is None:
            return date_str
        from taxjson.lib.market_calendar import add_settlement_days
        return add_settlement_days(dt, 1, currency).isoformat()

    def trade_date_from_settlement(self, settle_str: str,
                                   currency: str = 'USD',
                                   is_option: bool = False,
                                   *formats: str) -> str:
        """BACK-compute the trade date from a settlement date, for brokerages
        whose CSV date column IS the settlement date (Webull, per user
        verification against real statements). Options T+1 in all eras;
        equities T+2 before the T+1 cutover (US 2024-05-28 / CA 2024-05-27),
        T+1 after (T+3 before 2017-09-05). Walks back through weekends and
        the market's settlement holidays to the latest trading day that
        settles on that date (lib/market_calendar); the era is the trade
        date's, so the cutover days round-trip."""
        dt = self.parse_date(settle_str, *formats) if formats else None
        if dt is None:
            return settle_str
        from taxjson.lib.dates import (last_trade_date_settling_by,
                                       settlement_date, settlement_lag_days)
        from taxjson.lib.market_calendar import sub_settlement_days
        iso = dt.strftime("%Y-%m-%d")
        # The latest trading day that settles ON this date. The era is a
        # TRADE-date rule: keying it on the settle date gave a T+1 walk
        # back for the T+2 settles of the cutover days themselves (CAD
        # 2024-05-27, USD 2024-05-28 — audit S056-01).
        trade = last_trade_date_settling_by(iso, currency, is_option)
        if settlement_date(trade, currency, is_option) == iso:
            return trade
        days = settlement_lag_days(iso, currency, is_option)
        return sub_settlement_days(iso, days, currency).isoformat()

    def equity_settlement_date(self, date_str: str, currency: str = 'USD',
                               *formats: str) -> str:
        """Era-aware EQUITY settlement for brokerages whose CSV carries no
        settlement column: T+2 before the T+1 cutover, T+1 after (T+3
        before 2017-09-05), counted in the market's settlement days
        (weekends and USD/CAD settlement holidays skipped).
        The cutover is market-specific: US 2024-05-28; Canada 2024-05-27 (a
        TSX trading day — the US was closed for Memorial Day)."""
        dt = self.parse_date(date_str, *formats) if formats else None
        if dt is None:
            return date_str
        # The era/lag rule lives in lib/dates (shared with the wash
        # radar's rescue-deadline walk-back) — one copy, no drift.
        from taxjson.lib.dates import settlement_date
        return settlement_date(dt.strftime("%Y-%m-%d"), currency)
