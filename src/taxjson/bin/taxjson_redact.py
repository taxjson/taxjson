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
  * holder identity — every IB `Account Information` field value
    except a safe list (Account Type, Base Currency, ...; ids get
    placeholders), every value cell after an identity label cell of
    IB's .html statements (inline tags kept), every `Label:` cell of a
    row and its value cells up to the next label, and
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
    stable same-shape pseudonym, shared by every file of one run, so
    rows that shared an id still share one (a Kraken trade's txid and
    its ledger refid) and distinct rows stay distinct;
  * anything matching the private denylist (`~/.config/taxjson/
    pii-denylist`, or $TAXJSON_PII_DENYLIST — the same file
    scripts/check-pii.sh uses, matched the same way: case-insensitive,
    a 4+ digit run also matching with spaces or dashes between digits)
    or `--also`, in the text and in the output FILE NAME. An invalid
    pattern, a TAXJSON_PII_DENYLIST naming a missing file, or a
    denylist that is a directory, unreadable or not UTF-8 (a leading
    BOM is fine) FAILS CLOSED on the command line (exit 2, nothing
    written).
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
# 5+ chars, 5+ digits (checked in _is_id); any letter case — a
# lower-case 'ab55500012' in an account column was never collected
# (A2-1386).
_COL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{3,}[A-Za-z0-9]$")
# An IB id in a FILE NAME or an HTML element id, any case
# ('u1234567_2025.csv', A2-0451). Content keeps the upper-case _IB_ID.
_IB_ID_ANYCASE = re.compile(r"(?<![A-Za-z0-9])(?:DU|U|F|I)\d{7,8}(?![0-9])",
                            re.IGNORECASE)
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


# Other parties a column or label can name (A2-0764, A2-0460): a fixed
# plan-party list let 'Payee Name', 'Recipient', 'Policyholder',
# 'Trustee', 'Authorized Trader' ... through with no REVIEW line. These
# are blanked too, but (unlike the holder words above) do not make a
# bare `Name` column the holder's name.
_PARTY_COL_RE = re.compile(
    r".*\b(?:payee|recipient|employee|policy[ _-]?holder|(?:co-?)?applicant"
    r"|participant|contact|trustee|guardian|executor|attorney|signatory"
    r"|nominee|remitter|co-?owner|co-?signer|joint[ _]holder"
    r"|authori[sz]ed[ _-](?:trader|person|user|signer|signatory|agent)"
    r"|ordering customer|mandataire|fiduciaire)s?\b.*",
    re.IGNORECASE)
# '<qualifier> Name' labels/columns that do NOT name a person. Any other
# '<x> Name' is a person's name in a label, and a REVIEW column in a
# table.
_NONPERSON_NAME = re.compile(
    r"(?:security|company|asset|instrument|financial instrument|fund"
    r"|product|symbol|issuer|plan|bank|broker|brokerage|institution"
    r"|currency|exchange|coin|token|network|file|event|sector|industry"
    r"|market|contract|option|underlying|strategy|program|report"
    r"|statement|stock|etf|bond|description|display|short|long|trade"
    r"|order|transaction|type|class|series|venue|custodian|dealer|firm"
    r"|branch|office|platform|wallet|chain|pair|sheet|table|column"
    r"|field|section|tag|label|host|domain|server|app|device|model"
    r"|city|street|country|province|state|region)[ _-]*name"
    r"|nom (?:du |de la |de l['’]|des |de )?(?:titre|titres|soci[ée]t[ée]"
    r"|fonds|[ée]metteur|produit|valeur|placement|courtier|instrument"
    r"|fichier|r[ée]gime|march[ée])",
    re.IGNORECASE)


def _is_other_name(h: str) -> bool:
    """'<x> Name' / '<x>name' / 'Nom <x>' that is not a known
    non-person name (security, company, file ...)."""
    return (len(h) > 4 and (h.endswith("name") or h.startswith("nom "))
            and h != "name" and not _NONPERSON_NAME.fullmatch(h))


def _is_holder_col(h: str) -> bool:
    h = h.strip().strip('"').strip().lower()
    return bool(h) and (h in _ALIAS_COLS or bool(_PERSON_COL_RE.fullmatch(h)))


def _is_person_col(h: str) -> bool:
    h = h.strip().strip('"').strip().lower()
    return bool(h) and (_is_holder_col(h) or bool(_PARTY_COL_RE.fullmatch(h)))


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
    return "identity" if _is_identity_label(t) else None


def _is_identity_label(t: str) -> bool:
    """A label (CSV `Label:` cell, HTML label cell) whose value is a
    person's identity: the fixed English / French list, any holder /
    party word, and any '<x> Name' that is not a security / company /
    file name (A2-0460, A2-0455). Bilingual 'English / Français' labels
    match on either half."""
    t = t.strip().strip('"').strip().rstrip(":").strip().lower()
    if not t:
        return False
    parts = [t] + [p.strip() for p in t.split("/") if p.strip()]
    return any(p in _IDENTITY_LABELS or p in _IDENTITY_ROWS
               or _is_person_col(p) or _is_other_name(p) for p in parts)


def _is_label_cell(c: str) -> bool:
    """Any `Label:` cell (identity or not): where one label's value
    cells end."""
    t = c.strip().strip('"').strip()
    return t.endswith(":") and len(t) <= 60 and any(ch.isalpha() for ch in t)


_IDENTITY_ROWS = ("name", "account alias", "address", "street", "city",
                  "state", "postal code", "zip", "country", "phone",
                  "email", "customer id", "client name", "holder",
                  "owner", "primary owner", "customer", "province",
                  "address 1", "address 2", "mailing address")
# IB `Account Information` fields whose value is NOT identity. Every
# other field's value is blanked: a fixed identity list let Legal Name,
# Joint Name, Master Name, Account Title, Beneficiary, Trustee, SIN,
# Tax ID, Telephone ... through with no REVIEW line (A2-0046, A2-0455).
# 'Account' / 'Accounts Included' hold ids, which get placeholders.
_IB_INFO_SAFE = ("account", "accounts included", "account type",
                 "customer type", "account capabilities", "capabilities",
                 "trading permissions", "base currency", "currency",
                 "account status", "status", "statement period", "period",
                 "platform", "field name")
# A comma-form identity line ('Name,Jane Sample'): the bilingual set of
# _IDENTITY_LABELS' name labels (A2-1390). Never applied to a column
# header row.
_HEADER_LINE = re.compile(
    r'^(\s*"?)((?:name|nom|nom du client|nom complet|full name|client'
    r'|client name|account holder|holder|titulaire|owner|primary owner'
    r'|customer|customer name|user|user name|payee|beneficiary'
    r'|b[ée]n[ée]ficiaire)\s*"?\s*[:,]\s*)',
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
# After a label, any separator form: a SIN 3-3-3 and an SSN 3-2-4 with
# spaces, dashes, dots or none ('SIN: 046.454.286', 'SSN: 078 05 1120'
# were kept, A2-1389).
_SIN_CTX = re.compile(
    r"(\b(?:SIN|S\.I\.N\.?|NAS|social insurance(?: number)?|SSN|TIN|tax id)\b\s*[:#]?\s*)"
    r"(\d{3}([ .-]?)\d{3}\3\d{3}|\d{3}([ .-]?)\d{2}\4\d{4})(?![\d.-]?\d)",
    re.IGNORECASE)
# The label in its own CSV cell, the value in the next one, no colon:
# 'SIN,046…', 'Phone,416…', '"Tax ID","078…"', or both inside one
# quoted cell ('"SIN,046…"') (A2-0456). The label must START the line
# (or the quoted cell): a ticker cell mid-row ('…,USD,TEL,1000.0000')
# is not a label. A date is never a value; digit counts are checked in
# _redact_contact.
_LABEL_NUM_WORDS = (r"(?:(sin|s\.i\.n\.?|nas|ssn|tin|tax id"
                    r"|social insurance(?: number)?)"
                    r"|phone|tel|telephone|mobile|cell|fax)")
_LABEL_NUM_VALUE = (r"(?!\d{4}-\d{2}-\d{2}(?!\d))(\+?\d[\d ().-]{7,16}\d)")
_LABEL_CELL_NUM = re.compile(
    r'(^[ \t]*"?[ \t]*' + _LABEL_NUM_WORDS
    + r'[ \t]*"?[ \t]*,[ \t]*"?[ \t]*)' + _LABEL_NUM_VALUE
    + r'(?=[ \t]*"?[ \t]*(?:,|\r?$))', re.IGNORECASE)
_QUOTED_LABEL_NUM = re.compile(
    r'((?:^|,)[ \t]*"[ \t]*' + _LABEL_NUM_WORDS + r'[ \t]*[:,]?[ \t]*)'
    + _LABEL_NUM_VALUE + r'(?=[ \t]*")', re.IGNORECASE)
# A US address split across cells: 'Springfield,IL,62704' — the
# capitalised city, the state code and the ZIP each a whole cell
# (A2-0763).
_US_ZIP_CELLS = re.compile(
    r'^[ \t]*"?(?-i:[A-Z][A-Za-z.\'’-]*)(?:[ ](?-i:[A-Z][A-Za-z.\'’-]*)){0,3}"?'
    r'[ \t]*,[ \t]*"?(?-i:(?:' + _US_STATES + r'))"?[ \t]*,[ \t]*'
    r'"?\d{5}(?:-\d{4})?"?[ \t]*,*[ \t]*$')
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")

# Crypto wallets and transaction ids (pseudonymised, stable per value).
_WALLET = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?:bc1|tb1|ltc1)[ac-hj-np-z02-9]{11,87}"                    # bech32
    r"|(?:BC1|TB1|LTC1)[AC-HJ-NP-Z02-9]{11,87}"                   # BECH32 (A2-0452)
    r"|0x[0-9a-fA-F]{40}(?![0-9a-fA-F])"                          # EVM
    r"|[13][a-km-zA-HJ-NP-Z1-9]{25,34}"                           # base58
    r")(?![A-Za-z0-9])")
_BECH32 = re.compile(r"(?:bc1|tb1|ltc1)[ac-hj-np-z02-9]{11,87}", re.IGNORECASE)
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


class Pseudonyms:
    """Wallet and transaction-id pseudonyms of one invocation. Per-file
    tables restarted the counter in every file, so two Coinbase exports'
    distinct ids both became ...0001 (and taxjson-sort --dedup dropped a
    real trade) and a Kraken trades txid no longer equalled its ledger
    refid (A2-0454, A2-0766) — the S036-18 fix shared account ids only."""

    def __init__(self) -> None:
        self.wallets: Dict[str, str] = {}
        self.txids: Dict[str, str] = {}


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
        self.pseudonyms = Pseudonyms()
        # Ids found only in the input's FILE NAME (an IB download is
        # named after the account): masked shapes, never the id.
        self.name_ids: List[str] = []

    def found_anything(self) -> bool:
        return bool(self.accounts or self.identity_rows or self.emails
                    or self.names or self.phones or self.postal_codes
                    or self.addresses or self.sins or self.wallets
                    or self.txids or self.patterns or self.name_ids)

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
    letters/digits/hyphens, not a date."""
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
        # One account whatever its case: the replacement is
        # case-insensitive.
        if v and v.upper() not in seen and check(v):
            seen.add(v.upper()); ids.append(v)

    col_idx: Dict[str, List[int]] = {}
    for line in lines:
        for m in _IB_ID.finditer(line):
            add(m.group(0))
        # A lower-case IB id inside an HTML element id
        # (`tblaccountinformation_u1234567body`, A2-0762).
        if "<" in line:
            for m in re.finditer(r"(?:id|name|class)\s*=\s*\"[^\"]*\"",
                                 line, re.IGNORECASE):
                for t in _IB_ID_ANYCASE.finditer(m.group(0)):
                    add(t.group(0))
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


def _pseudo(v: str, shared: Dict[str, str], mine: Dict[str, str]) -> str:
    """The invocation-wide pseudonym of `v` (recorded in this file's
    table for the report). A bech32 address is case-insensitive
    (BIP173): both spellings share one pseudonym, in the input's case."""
    key = v.lower() if _BECH32.fullmatch(v) else v
    if key not in shared:
        shared[key] = _pseudonym(key, len(shared) + 1)
    ph = shared[key]
    if key != v and v.isupper():
        ph = ph.upper()
    mine[key] = ph
    return ph


def _mapper(shared: Dict[str, str], mine: Dict[str, str],
            accept: Callable[[str], bool] = lambda v: True
            ) -> Callable[[re.Match], str]:
    def repl(m: re.Match) -> str:
        v = m.group(0)
        if not accept(v):
            return v
        return _pseudo(v, shared, mine)
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
    def label_cell(m: re.Match) -> str:
        v = m.group(3)
        digits = sum(c.isdigit() for c in v)
        if re.fullmatch(r"\d+\.\d+", v):
            return m.group(0)                   # an amount, not a number
        if m.group(2):
            if digits != 9:
                return m.group(0)
            rep.sins += 1
        else:
            if not 10 <= digits <= 15:
                return m.group(0)
            rep.phones += 1
        return m.group(1) + "REDACTED"
    line = _LABEL_CELL_NUM.sub(label_cell, line)
    line = _QUOTED_LABEL_NUM.sub(label_cell, line)
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


def _name_review_cols(cells: List[str], low: List[str],
                      blanked: List[int]) -> List[Tuple[int, str]]:
    """'<x> Name' columns that are neither blanked nor a known non-person
    name ('Agent Name'): their values go through the REVIEW pass, so a
    person's name there is at least listed (A2-0764)."""
    return [(i, cells[i].strip()) for i, c in enumerate(low)
            if i not in blanked and _is_other_name(c)
            and c not in _FREE_TEXT_COLS]


def _identity_cols(low: List[str]) -> List[int]:
    """Header positions holding holder identity: alias columns always;
    a name column only beside an account or alias column; the address
    columns (Street2, City, State, Country ...) of a header that names
    an account and carries an address block — IB Flex
    AccountInformation, columnar or flat. Only the Field Name/Field
    Value layout was covered, so city, province and unit survived
    (S037-03)."""
    cols = [i for i, c in enumerate(low) if _is_person_col(c)]
    if (any(_is_holder_col(c) for c in low)
            or any(_is_account_col(c) for c in low)):
        cols += [i for i, c in enumerate(low) if c in _HOLDER_NAME_COLS]
        if any(c in _ADDRESS_ANCHORS for c in low):
            cols += [i for i, c in enumerate(low) if c in _ADDRESS_COLS]
    return sorted(set(cols))


# HTML statements (IB's .html reports): a label cell naming an identity
# field, then the value cell(s) — on the same line or the next ones.
_HTML_LABEL = re.compile(r"<t([dh])\b[^>]*>\s*([^<>]{1,60}?)\s*:?\s*</t\1>",
                         re.IGNORECASE)
# One value cell: anything but another cell or row boundary inside
# (inline tags like <b>…</b> are kept and their text redacted).
_HTML_VALUE = re.compile(
    r"\s*<td\b[^>]*>((?:(?!</?t[dhr]\b|</?table\b).)*?)</td>",
    re.IGNORECASE | re.DOTALL)
_HTML_TEXT = re.compile(r"(^|>)([^<]*[^<\s][^<]*)(?=<|$)")
_HTML_NOT_PERSONAL = ("country", "state", "province")


def _redact_html_identity(text: str, rep: "Report") -> str:
    """Every value cell after an identity label cell, up to the end of
    the row or the next label, has its text replaced — tags kept. Only
    the first plain-text cell was replaced, so a multi-cell address kept
    its city and province, a <b>name</b> was not replaced at all, and
    the label list missed SIN / Tax ID / Telephone / Legal Name (A2-0457,
    A2-0455, A2-0460)."""
    if "<td" not in text.lower():
        return text
    out: List[str] = []
    pos = 0
    for m in _HTML_LABEL.finditer(text):
        if m.start() < pos:
            continue
        label = " ".join(m.group(2).split()).lower()
        if (label in _HTML_NOT_PERSONAL or _is_account_col(label)
                or not _is_identity_label(label)):
            continue
        out.append(text[pos:m.end()])
        pos = m.end()
        hit = False
        while True:
            v = _HTML_VALUE.match(text, pos)
            if not v:
                break
            inner = v.group(1)
            plain = re.sub(r"<[^>]*>", "", inner).strip()
            if plain and (_is_identity_label(plain) or _is_account_col(plain)
                          or plain.lower() in _IB_INFO_SAFE):
                break                       # the next label/value pair
            if plain:
                inner = _HTML_TEXT.sub(lambda t: t.group(1) + "REDACTED", inner)
                hit = True
            out.append(text[pos:v.start(1)] + inner + text[v.end(1):v.end()])
            pos = v.end()
        if hit:
            rep.identity_rows += 1
    out.append(text[pos:])
    return "".join(out)


def _id_pattern(orig: str) -> re.Pattern:
    """Where an account id is replaced — the SAME boundaries it was
    collected with. An IB id is found when it is followed by letters
    (`tblAccountInformation_U1234567Heading`), so it is replaced
    there too; other ids keep the strict boundary (not inside a longer
    token or a decimal). Case-insensitive: a lower-case copy of the
    id (`tblaccountinformation_u1234567body`, a description quoting
    `ab55500012`) was left in the copy while the report said every
    occurrence was replaced (A2-0762, A2-1386)."""
    if _IB_ID_ANYCASE.fullmatch(orig):
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(orig) + r"(?![0-9])",
                          re.IGNORECASE)
    return re.compile(r"(?<![A-Za-z0-9.])" + re.escape(orig)
                      + r"(?![A-Za-z0-9]|\.\d)", re.IGNORECASE)


def _known_placeholder(table: Dict[str, str], orig: str) -> str:
    """The invocation-wide placeholder of an id, matched
    case-insensitively (U1234567 and u1234567 are one account)."""
    if orig in table:
        return table[orig]
    up = orig.upper()
    for k, v in table.items():
        if k.upper() == up:
            table[orig] = v
            return v
    table[orig] = _placeholder(orig, len(set(table.values())) + 1)
    return table[orig]


def redact_text(text: str, extra_patterns: Optional[List[str]] = None,
                known_ids: Optional[Dict[str, str]] = None,
                pseudonyms: Optional[Pseudonyms] = None
                ) -> Tuple[str, Report]:
    """Redact one export. `known_ids` (original -> placeholder) and
    `pseudonyms` (wallets, transaction ids) are shared by every file of
    one invocation: a value seen in an earlier file keeps its stand-in,
    and a new one gets the next number — two accounts' files used to
    both become U9990001 (S036-18), two files' distinct txids both
    ...0001 (A2-0454)."""
    rep = Report()
    if known_ids is not None:
        rep.known_ids = known_ids
    if pseudonyms is not None:
        rep.pseudonyms = pseudonyms
    compiled, bad = compile_patterns(extra_patterns or [])
    rep.name_patterns = compiled
    for b in bad:
        rep.notes.append(f"pattern skipped (not a valid regex): {b}")
    text = _redact_html_identity(text, rep)
    lines = text.splitlines(keepends=True)
    for orig in _collect_ids(lines):
        rep.accounts[orig] = _known_placeholder(rep.known_ids, orig)
    acct_upper = {a.upper() for a in rep.accounts}
    # Longest first so a shorter id that is a substring of a longer one
    # cannot pre-empt it.
    ordered = sorted(rep.accounts.items(), key=lambda kv: -len(kv[0]))
    id_pats = {orig: _id_pattern(orig) for orig, _ in ordered}
    alias_cols: Dict[str, List[int]] = {}
    txid_cols: Dict[str, List[int]] = {}
    free_cols: Dict[str, List[Tuple[int, str]]] = {}
    in_preamble = True
    wallet_repl = _mapper(rep.pseudonyms.wallets, rep.wallets, _plausible_wallet)
    txid_repl = _mapper(rep.pseudonyms.txids, rep.txids)
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
            free_cols[low[0]] += _name_review_cols(cells, low, alias_cols[low[0]])
        elif low and len(nonempty) >= 3 and not is_ib_row and not any(
                _is_label_cell(c) for c in nonempty) and (
                any(c in _TXID_COLS + _FREE_TEXT_COLS or _is_holder_col(c)
                    or _is_account_col(c) for c in low)
                or all(not any(ch.isdigit() for ch in c) for c in nonempty)):
            # (A row of `Label:,value` pairs is never a header: four
            # digit-free cells were read as one, and the label pass
            # skipped them, A2-0765.)
            # A flat CSV's column header row.
            header_row = True
            alias_cols["__flat__"] = _identity_cols(low)
            txid_cols["__flat__"] = [i for i, c in enumerate(low) if c in _TXID_COLS]
            free_cols["__flat__"] = [(i, cells[i].strip()) for i, c in enumerate(low)
                                     if c in _FREE_TEXT_COLS]
            for _i, _name in free_cols["__flat__"]:
                if _name not in rep.description_columns:
                    rep.description_columns.append(_name)
            free_cols["__flat__"] += _name_review_cols(cells, low,
                                                       alias_cols["__flat__"])
        if len(nonempty) >= 3:
            in_preamble = False
        sect = low[0] if is_ib_row else "__flat__"
        # Identity rows (IB Account Information) and alias columns.
        if (low and len(low) > 3 and low[0] == "account information"
                and low[1] == "data" and low[2] not in _IB_INFO_SAFE):
            # Every value cell: an unquoted multi-cell address kept its
            # city and province (A2-1391).
            hit = False
            for i in range(3, len(cells)):
                if cells[i].strip() and cells[i].strip().upper() not in acct_upper:
                    line = _replace_field(line, cells, i, "REDACTED")
                    cells = _split(line.rstrip("\r\n")) or []
                    hit = True
            rep.identity_rows += hit
        elif low and not header_row:
            for i in alias_cols.get(sect, []):
                # Blanked unless it is a COLLECTED account id (which gets
                # its placeholder below): an id-SHAPED ZIP in a Zip
                # column was skipped as if it were one (A2-0763).
                if (i < len(cells) and cells[i].strip()
                        and cells[i].strip().upper() not in acct_upper):
                    line = _replace_field(line, cells, i, "REDACTED")
                    cells = _split(line.rstrip("\r\n")) or []
                    rep.identity_rows += 1
            for i in txid_cols.get(sect, []):
                v = cells[i].strip() if i < len(cells) else ""
                if len(v) >= 6 and any(c.isdigit() for c in v):
                    ph = _pseudo(v, rep.pseudonyms.txids, rep.txids)
                    line = _replace_field(line, cells, i, ph)
                    cells = _split(line.rstrip("\r\n")) or []
        label_done = False
        if cells and not is_ib_row and not header_row:
            # Every `Label:` cell of the row, wherever it sits: only the
            # first non-empty cell was looked at, so 'Account Number:,X,
            # Name:,Y' kept the name and 'Contact,Phone:,416…' the phone
            # (A2-0759, A2-0765). An identity label's value is every
            # non-empty cell up to the next `Label:` cell (a multi-cell
            # address), except a collected account id (replaced by its
            # placeholder below). An id-SHAPED phone or SIN after
            # 'Phone:' / 'SIN:' is blanked like any value (S037-08).
            i = 0
            while i < len(cells):
                if cells[i].strip() and _label_kind(cells[i]) == "identity":
                    label_done = True
                    hit = False
                    j = i + 1
                    while j < len(cells) and not _is_label_cell(cells[j]):
                        v = cells[j].strip()
                        if v and v.upper() not in acct_upper:
                            line = _replace_field(line, cells, j, "REDACTED")
                            cells = _split(line.rstrip("\r\n")) or []
                            hit = True
                        j += 1
                    rep.identity_rows += hit
                    i = j
                    continue
                i += 1
        m = None if label_done or header_row else _HEADER_LINE.match(line)
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
        # A US address split across cells ('Springfield,IL,62704'),
        # wherever it sits — three cells end the preamble (A2-0763).
        if not is_ib_row and not header_row and _US_ZIP_CELLS.match(body):
            line = _blank_cells(line)
            rep.postal_codes += 1
        elif in_preamble and not is_ib_row and nonempty:
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
        lowline = line.lower()
        for orig, ph in ordered:
            if orig.lower() in lowline:
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
        o = orig.lower()
        hits = [n for n, ln in enumerate(out, start=1) if o in ln.lower()]
        if hits:
            rep.unreplaced[orig] = sum(ln.lower().count(o) for ln in out)
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


class DenylistError(Exception):
    """The private denylist cannot be used as given — the command must
    stop (exit 2, nothing written) rather than run without it."""


class DenylistMissing(DenylistError):
    """TAXJSON_PII_DENYLIST names a file that does not exist."""

    def __str__(self) -> str:
        return (f"TAXJSON_PII_DENYLIST names {self.args[0]}, which does "
                f"not exist")


class DenylistUnreadable(DenylistError):
    """The denylist exists but is not a readable UTF-8 text file (a
    directory, no permission, UTF-16, cp1252). Read as plain UTF-8
    with errors='replace', a BOM became part of the first pattern and a
    UTF-16 or cp1252 save matched nothing — the guard turned itself off
    without a word (A2-0045, A2-0158)."""


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


def decode_denylist(raw: bytes, where: str) -> str:
    """The denylist's text: UTF-8, a leading BOM stripped. Anything else
    — UTF-16 (BOM or NUL-stuffed), cp1252, binary — raises
    DenylistUnreadable: decoding it with replacement characters
    silently disabled patterns (A2-0045, A2-0158). scripts/check-pii.sh
    reads the same file the same way."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw:
        raise DenylistUnreadable(
            f"denylist {where} is UTF-16 (or holds NUL bytes); save it as "
            f"UTF-8 text")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DenylistUnreadable(
            f"denylist {where} is not UTF-8 text (byte {e.start + 1}); "
            f"save it as UTF-8") from None


def load_denylist(path: Optional[str] = None) -> List[str]:
    """The private denylist's patterns, digit runs loosened exactly as
    check-pii.sh loosens them. FAILS CLOSED: a TAXJSON_PII_DENYLIST (or
    `path`) that names a missing file raises DenylistMissing, and a
    denylist (explicit or the default path) that is a directory,
    unreadable or not UTF-8 raises DenylistUnreadable — a guard must
    never weaken silently. Only an ABSENT default path means 'no
    denylist'."""
    explicit = path or os.environ.get("TAXJSON_PII_DENYLIST")
    p = Path(explicit or Path.home() / ".config" / "taxjson" / "pii-denylist")
    if not p.exists() and not p.is_symlink():
        if explicit:
            raise DenylistMissing(str(p))
        return []
    if not p.is_file():
        raise DenylistUnreadable(
            f"denylist {p} is not a file (a directory, a dangling link or "
            f"a device)")
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise DenylistUnreadable(
            f"denylist {p} cannot be read: {e.strerror or e}") from None
    pats = []
    for ln in decode_denylist(raw, str(p)).splitlines():
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
    for tok in name_only_ids(stem, accounts):
        subs[tok] = _known_placeholder(table, tok)
    if subs:
        # Case-insensitive, like the content (A2-0451): an IB download
        # saved as 'u1234567_2025.csv' kept the id in the copy's name.
        bykey = {o.upper(): ph for o, ph in subs.items()}
        alt = re.compile("|".join(re.escape(o) for o in sorted(
            subs, key=len, reverse=True)), re.IGNORECASE)
        stem = alt.sub(lambda m: bykey[m.group(0).upper()], stem)
    return f"{stem}.redacted{src.suffix}"


def name_only_ids(stem: str, accounts: Dict[str, str]) -> List[str]:
    """Account-id tokens of a file NAME that the content did not
    already supply: an IB id (any case) or an 8+ digit run that is not
    a YYYYMMDD date."""
    known = [o.upper() for o in accounts]
    found: List[str] = []
    for m in (list(_IB_ID_ANYCASE.finditer(stem))
              + list(re.finditer(r"(?<!\d)\d{8,}(?!\d)", stem))):
        tok = m.group(0)
        if (_DATE8.fullmatch(tok) or any(tok.upper() in o for o in known)
                or tok.upper() in (f.upper() for f in found)):
            continue
        found.append(tok)
    return found


def redact_file(src: Path, out_dir: Optional[Path], extra: List[str],
                check_only: bool, force: bool = False,
                known_ids: Optional[Dict[str, str]] = None,
                written: Optional[set] = None,
                pseudonyms: Optional[Pseudonyms] = None
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
    try:
        text, enc, bom = decode_export(raw)
    except UnicodeDecodeError as e:
        # A truncated UTF-16 file was a traceback that stopped the
        # batch (A2-1392).
        raise InputRefused(
            f"{src.name} starts with a UTF-16 byte-order mark but is not "
            f"valid UTF-16 (byte {e.start}: {e.reason}; a truncated "
            f"file?) — nothing written. Re-export it.") from None
    new, rep = redact_text(text, extra, known_ids, pseudonyms)
    # A denylisted string in the file NAME counts as a finding too.
    rep.patterns += sum(len(p.findall(src.stem)) for p in rep.name_patterns)
    # So does an account id that only the NAME carries: --check said
    # 'account ids: none found' and exited 0 while the write mode
    # renamed the copy (A2-0459).
    rep.name_ids = [Report.masked(t) for t in name_only_ids(src.stem, rep.accounts)]
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
    if rep.name_ids:
        print(f"  account ids in the FILE NAME only: {len(rep.name_ids)} "
              f"({', '.join(rep.name_ids)}) — the copy's name gets a placeholder")
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
    except DenylistError as e:
        print(f"taxjson redact: {e} — fix it (or pass --no-denylist). "
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
    pseudonyms = Pseudonyms()
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
                                   known_ids, written, pseudonyms)
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
