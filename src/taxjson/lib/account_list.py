"""A comma-separated ACCOUNT list: `tjs gains-base margin,crypto`.

Pure: no I/O, no exit. The callers (cli/main's checks before every
command, cli/common `_accounts`) turn an AccountListError into the
command's refusal with `refusal`.

The rules:
  * A comma is never part of an account name (ACCOUNT_NAME_RE), nor of
    a period, symbol or date, so `margin,crypto` splits one way only.
    A list stands in the ACCOUNT slot (the positional, `--account`) and
    in the lone `[PERIOD]` of `[PERIOD] [ACCOUNT]` (and list's second
    positional), except a token there that starts with a digit: that is
    a PERIOD (or a date), as before lists, and its command reads it
    (`tjs divs 1y,margin` is a malformed period). A list whose first
    name starts with a digit goes in the ACCOUNT slot after a period:
    `tjs divs ytd 2fa,margin`.
  * The names are kept in the order given. None (or "") is every
    account; one name is a list of one.
  * Refused, each naming the bad name: an empty name (`margin,,crypto`,
    a leading or trailing comma), a pipeline artifact's name
    (`margin_raw`: the reserved suffixes), a path or a pattern, a name
    given twice, a list where one account is wanted (`one=True`: `tjs
    elections`, `tjs crypto-sends --set`), and a name not in [accounts]
    (when the configured names are given).
  * Exit codes (`refusal`): every refusal of a list (a comma, or
    several tokens) exits 1, the code any comma token got before lists,
    a name not in [accounts] too; one name keeps the code its command
    gives it today (2 for one not in [accounts], cli/main's single-name
    check; 1 for a path or an artifact name).

How a command takes a list:
  * Its registration says so: `p.set_defaults(account_list=True)`.
    cli/main refuses a list to a command without it ("`tjs elections` takes
    one account"), so a command not converted yet never reads one.
  * Before every command cli/main refuses a list's shape: an empty,
    artifact, path or repeated name (exit 1). It sets nothing: the
    command reads the names through cli/common `_accounts(args, root)`
    (`[ACCOUNT]`, `--account`; `text=` the account text a command with
    its own positional parsing decided) or `_view_scope(args, root)`
    (`[PERIOD] [ACCOUNT]`), which parse them on every call and refuse a
    list's name not in [accounts] (exit 1).

The JSON convention: where a command's --json echoes one account
today, it keeps "account" byte-for-byte as today (the text given: one
name, or the comma text of a list; its every-account form unchanged:
null, or "" in wash-radar's) and adds "account_names": the names in the
order given, or null for every account; always present once the
command takes a list (an additive change: its goldens are recorded
again). gains-cra puts it inside "scope", beside "account". The key is
not "accounts": stats, win-rates, slip-audit, crypto-sends,
option-yield and fetch already emit an "accounts" list meaning the
accounts read, which stays as it is.
"""
from typing import Collection, Iterable, List, Optional, Tuple, Union

from taxjson.lib.config_check import (
    ACCOUNT_NAME_RE, ACCOUNT_NAME_RULE, RESERVED_NAME_SUFFIXES,
)

__all__ = ("ACCOUNT_NAME_RE", "AccountListError", "SEPARATOR",
           "artifact_suffix", "is_account_list", "names_of",
           "not_an_account", "not_an_account_name", "parse_accounts",
           "refusal", "unknown_accounts_text")

SEPARATOR = ","

# What an AccountListError can say, in the order parse_accounts checks.
KINDS = ("one", "empty", "artifact", "name", "duplicate", "unknown")


class AccountListError(ValueError):
    """An ACCOUNT list refused. `kind`: one of KINDS; `names`: the names
    it is about (the empty ones as ""), in the order given; `text`: the
    token as given (several: joined by spaces); `configured`: the
    configured names (kind "unknown"); `count`: how many names the list
    holds (kind "one"); `listed`: the text was a list (a comma, or
    several tokens), whose every refusal exits 1 (`refusal`)."""

    def __init__(self, kind: str, names: Iterable[str], text: str = "",
                 configured: Iterable[str] = (), count: int = 0,
                 listed: Optional[bool] = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"kind {kind!r}: one of {', '.join(KINDS)}")
        self.kind = kind
        self.names: Tuple[str, ...] = tuple(names)
        self.text = text
        self.configured: Tuple[str, ...] = tuple(configured)
        self.count = count
        self.listed = (listed if listed is not None else
                       SEPARATOR in text or count > 1 or len(self.names) > 1)
        super().__init__(refusal(self, "")[0])


def is_account_list(tok: object) -> bool:
    """`tok` is a comma list of account names (or a malformed one:
    `margin,`): a string with a comma in it."""
    return isinstance(tok, str) and SEPARATOR in tok


def _items(text: Union[str, Iterable[str]]) -> List[str]:
    """Every item of `text` (one token, or several: fetch's ACCOUNT
    ...), split at each comma, empty ones kept."""
    toks = [text] if isinstance(text, str) else [str(t) for t in text]
    return [item for tok in toks for item in tok.split(SEPARATOR)]


def artifact_suffix(name: str) -> Optional[str]:
    """The reserved suffix `name` ends with (`_raw`, `_gains` ...: a
    pipeline work file's, never an account's; config_check), or None."""
    low = name.lower()
    return next((x for x in RESERVED_NAME_SUFFIXES if low.endswith(x)),
                None)


def parse_accounts(text: Union[None, str, Iterable[str]],
                   configured: Optional[Collection[str]] = None, *,
                   one: bool = False) -> Optional[Tuple[str, ...]]:
    """The account names `text` gives, in the order given: None for
    every account (None, "" or no token at all). `text` is one token
    (`margin,crypto`) or several (fetch's `ACCOUNT ...`, each may be a
    list). `configured`: the project's account names, when known (a
    name not among them is refused); `one`: one name wanted.

    Raises AccountListError, checking in this order: "one" (more than
    one name where one is wanted), "empty", "artifact" (a pipeline work
    file's name), "name" (not an account name: a path, a pattern),
    "duplicate", "unknown"."""
    if text is None:
        return None
    shown = text if isinstance(text, str) else " ".join(str(t) for t in text)
    items = _items(text)
    if items in ([], [""]):
        return None                         # "" or no token: every account
    listed = len(items) > 1

    def refused(kind: str, names: Iterable[str],
                configured: Iterable[str] = (),
                count: int = 0) -> AccountListError:
        return AccountListError(kind, names, shown, configured, count,
                                listed)
    given = [n for n in items if n]
    if one and len(given) > 1:
        raise refused("one", given, count=len(given))
    if len(given) != len(items):
        raise refused("empty", [n for n in items if not n])
    made = [n for n in given if artifact_suffix(n)]
    if made:
        raise refused("artifact", made)
    bad = [n for n in given if not ACCOUNT_NAME_RE.fullmatch(n)]
    if bad:
        raise refused("name", bad)
    seen: List[str] = []
    twice: List[str] = []
    for n in given:
        if n in seen and n not in twice:
            twice.append(n)
        seen.append(n)
    if twice:
        raise refused("duplicate", twice)
    if configured is not None:
        known = set(configured)
        unknown = [n for n in given if n not in known]
        if unknown:
            raise refused("unknown", unknown, configured=sorted(known))
    return tuple(given)


def names_of(accounts: Union[None, str, Iterable[str]]
             ) -> Optional[Tuple[str, ...]]:
    """`accounts` as the shared helpers take it, normalized: None (or
    "") for every account; a string is ONE name, as before lists (a
    command not converted passes its text whole: `stats 1y,margin
    --all-history` names '1y,margin', refused as it always was); a
    collection of names (a set comes back sorted, anything else in its
    order), repeats dropped. An empty collection, or one holding only
    "", is NO account: `()`, and a helper given it reads nothing (a
    subset rule that left no name never widens to every account).
    Nothing is checked or split: a caller gives names already parsed
    (`parse_accounts`, cli/common `_accounts`)."""
    if accounts is None:
        return None
    if isinstance(accounts, str):
        return (accounts,) if accounts else None    # "": every account
    if isinstance(accounts, (set, frozenset)):
        out = sorted(str(n) for n in accounts if n)
    else:
        out = [str(n) for n in accounts if n]
    return tuple(dict.fromkeys(out))


def not_an_account_name(token: str) -> str:
    """The refusal of a token that is no account name (a path, a
    pattern): the words cli/main has always said."""
    return (f"{token!r} is not an account name — give the name of an "
            f"[accounts.<name>] section in taxjson.toml "
            f"({ACCOUNT_NAME_RULE}), not a path or a pattern.")


def not_an_account(token: str, suffix: str) -> str:
    """The refusal of a pipeline artifact's name (`margin_raw`, its
    reserved `suffix`): the words cli/main has always said."""
    return (f"{token!r} is not an account: names ending in {suffix!r} "
            f"are pipeline work files (an account's native or "
            f"intermediate books), never accounts — use an account name "
            f"from [accounts] in taxjson.toml.")


def unknown_accounts_text(names: Iterable[str],
                          configured: Iterable[str]) -> str:
    """The refusal of names not in [accounts]: for one name the words
    the single-account check has always said."""
    names = list(names)
    have = ", ".join(sorted(configured))
    if len(names) == 1:
        return (f"no [accounts.{names[0]}] in taxjson.toml — check the "
                f"name (configured: {have}).")
    tables = ", ".join(f"[accounts.{n}]" for n in names)
    return (f"no {tables} in taxjson.toml — check the names "
            f"(configured: {have}).")


def _fixed(text: str) -> str:
    """`text` (one token, or several joined by spaces) as one list, its
    empty and repeated names dropped."""
    return SEPARATOR.join(dict.fromkeys(n for n in _items(text.split(" "))
                                        if n))


def _empty_fix(fixed: str, takes_list: bool) -> str:
    """The remedy of an empty name, `fixed` the list without it."""
    if SEPARATOR in fixed:
        return (f"Put one comma between two names and none at either "
                f"end: {fixed!r}.")
    one = f"the name alone ({fixed!r})" if fixed else "an account name"
    if not takes_list:
        return f"Give {one}."
    return (f"Give {one}, or put one comma between two names and none "
            f"at either end.")


def refusal(err: AccountListError, cmd: str, mode: str = "", *,
            takes_list: bool = True) -> Tuple[str, Tuple[str, ...], int]:
    """(headline, details, exit code) of `err`, for `tjs <cmd>` (and its
    `mode`, a flag such as `--set`, when only that mode takes one
    account; `takes_list` False: the command takes one name, so a
    remedy names no list). Exit 1 for every refusal of a list (the code
    a comma token had before lists) and for a path or artifact name;
    2 for one name not in [accounts], as the single-name check's."""
    what = " ".join(x for x in (f"tjs {cmd}" if cmd else "tjs", mode) if x)
    if err.kind == "one":
        return (f"`{what}` takes one account: {err.text!r} names "
                f"{err.count}", ("Run it once for each account.",), 1)
    if err.kind == "empty":
        return (f"{err.text!r} has an empty account name",
                (_empty_fix(_fixed(err.text), takes_list),), 1)
    if err.kind == "duplicate":
        twice = ", ".join(err.names)
        return (f"{err.text!r} names {twice} more than once",
                (f"Name each account once: {_fixed(err.text)!r}.",), 1)
    if err.kind == "artifact":
        name = err.names[0]
        return (not_an_account(name, artifact_suffix(name) or ""), (), 1)
    if err.kind == "name":
        return (not_an_account_name(err.names[0]), (), 1)
    return (unknown_accounts_text(err.names, err.configured), (),
            1 if err.listed else 2)
