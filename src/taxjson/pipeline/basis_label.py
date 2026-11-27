"""The basis label of a view's gains files, and what a full-history build checked.

A view names the basis of the gains files it read (the text header and
--json `basis`): what the superficial-loss / wash-sale rule checked of
their sales (lib/report_model `gains_basis_label`). The tax year's files
are a run's, and the run records what checked each (pipeline/run
`_record_loss_checks`); a full-history file (`<account>_gains_all.json`)
is built by the view that reads it (cli/common `_gains_all_file`,
`_gains_all_blend`), so what its build read is decided and recorded here,
with the file (backlog TJ-5441, TJ-5473); the registered accounts' book
such a build reads, and what the view's note says its denials check, are
here too.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from taxjson.lib.project_layout import _GAINS_ALL_SUFFIX
from taxjson.lib.in_kind import CONTRIBUTION, IN_KIND_FILE
from taxjson.pipeline import run_state
from taxjson.pipeline.config import load_config
from taxjson.pipeline.sanity import _has_inputs, loss_check_kinds
from taxjson.pipeline.stages import _wash_flags
from taxjson.project.accounts import account_of, accounts_of


def basis_label(root: Path, files: Any) -> str:
    """The basis label of `files` (lib/report_model.gains_basis_label), so
    it says what is checked. A tax-year file: from the run's record of what
    checked each account's gains file (lib/report_model.loss_checks;
    pipeline/run `_record_loss_checks` writes it; with no record, what the
    files served name: a wash-adjusted file is a pass's). A full-history
    file: from what its own build read (build_check: this command's own
    word for a file it built, the file's record for another).
    "wash-adjusted" when every account of the view
    the superficial-loss / wash-sale rule reaches was checked completely
    (by the run's pass across accounts, by a full-history build that read
    all the rule needs, or by the account's own engine pass where that is
    the whole check), "pre-wash" otherwise (an account built by `tjs run
    --account` alone or waiting on elections, one configured or retyped in
    taxjson.toml since, a full-history build that read part of what the
    rule needs, or no account the rule reaches). After `tjs run --account`,
    or an edit of taxjson.toml, an earlier full run's wash-adjusted file is
    still the one served, with the staleness warning (resolve_gains_files),
    and keeps that run's word (TJ-5473); the record is read with the
    accounts configured now (pipeline/sanity `loss_check_kinds`)."""
    from taxjson.lib.report_model import gains_basis_label, loss_checks
    return gains_basis_label(files, checks=loss_checks(root, loss_check_kinds(root)),
                             built=run_state.current.view_checks)


def _registered_book(cache: Path, country: str, *, taxable: bool,
                     crypto: bool) -> Optional[Path]:
    """The registered accounts' book a full-history gains run of such an
    account reads, as the run's pass across accounts reads it
    (stage_blended_wash_pass, stage_wash_pass): work/sheltered_base.json,
    every sheltered account's whole book merged (run._merge_sheltered),
    the in-kind plan acquisitions marked in it
    (stage_in_kind_context). A taxable account's, never a US crypto
    account's (§1091 does not reach digital assets: the run's crypto
    pass carries no rule). None when the account reads none. Declared
    present or absent (the stage table's `@sheltered`), given to the
    engine (`--sheltered`) when it is a file, so a sale in any year sees
    a registered account's purchase in its window (TJ-5347)."""
    from taxjson.lib.country import is_usa
    if not taxable or (crypto and is_usa(country)):
        return None
    return cache / "sheltered_base.json"


def _sheltered_flags(book: Optional[Path]) -> List[str]:
    """`--sheltered BOOK` for a full-history run (_registered_book), when
    the book is a file."""
    return ["--sheltered", str(book)] if book is not None and book.is_file() else []


def _denials_checked(books: Dict[str, Optional[Path]]) -> str:
    """What a full-history view's note says its superficial-loss /
    wash-sale denials check, from each account's registered book
    (_registered_book: None where no rule reaches the account's sales,
    a registered account's own or a US crypto account's): a purchase in
    any taxable account, and in any registered one when a run read
    their book; the accounts no rule reaches named, or said alone when
    the view holds no other (TJ-5347)."""
    bare = sorted(a for a, b in books.items() if b is None)
    if len(bare) == len(books):
        return "no superficial-loss / wash-sale rule applies to these accounts' sales."
    whose = ("taxable or registered accounts"
             if any(_sheltered_flags(b) for b in books.values()) else "taxable accounts")
    none = f" (no rule for the sales in {', '.join(bare)})" if bare else ""
    return (f"superficial-loss / wash-sale denials as the tax year's: a purchase in any "
            f"of your {whose} is checked, in every year{none}.")


def _book_sources(book: Path) -> Set[str]:
    """The names of the books merged into the registered accounts' book
    `book` (taxjson-merge's `metadata.sources`; the in-kind marks keep
    them: stage_in_kind_context). Empty for one that cannot be read, or one
    written for the in-kind rows alone."""
    import json as _json
    try:
        doc = _json.loads(book.read_text(encoding="utf-8"))
        sources = doc["metadata"]["sources"]
        return {Path(str(s["file"])).name for s in sources}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _in_kind_contribution_booked(cache: Path) -> bool:
    """Whether the last run booked an in-kind contribution
    (work/in_kind.json): its plan acquisition is a purchase for the loss
    rule, marked in the registered accounts' book, or in one written for
    it alone (stage_in_kind_context, CA-INKIND-04)."""
    import json as _json
    try:
        moves = _json.loads((cache / IN_KIND_FILE).read_text(encoding="utf-8"))["moves"]
        return any(m.get("booked") and m.get("kind") == CONTRIBUTION for m in moves)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _older(book: Path, than: Path) -> bool:
    """Whether `book` was written before `than` (or either cannot be read)."""
    try:
        return book.stat().st_mtime_ns < than.stat().st_mtime_ns
    except OSError:
        return True


def build_check(root: Path, cache: Path, account: str, read: List[str],
                book: Optional[Path], country: str) -> str:
    """What a full-history build of `account` checks for the
    superficial-loss / wash-sale rule (lib/report_model LOSS_RULE_*), from
    what it read: the taxable accounts' books `read` (the account's own, or
    its blend's members), and the registered accounts' book `book` (None:
    none read). The views build the file, not the run, from the books work/
    holds as they stand, so the run's record of the tax year says nothing
    of it (TJ-5473, the verify's V3: "pre-wash, full history" over rows the
    build's registered book had denied; V1: "wash-adjusted, full history"
    over an account built alone).

    No rule: a registered account's own sales, a US crypto account's
    (_wash_flags). Else every check the rule needs must have been in the
    build: every taxable account of the account's kind (crypto with crypto,
    as the run blends them: pipeline/run `_run_taxable`) that has inputs or
    a book, and, when a registered account has inputs or the run booked an
    in-kind contribution, the registered accounts' book, merged from every
    registered account with inputs, and no older than the booking
    (work/in_kind.json, written when the moves change: a run that books a
    contribution and merges no book, `tjs run --account <taxable>`, leaves
    the book without the plan acquisition's mark; the second verify's N2).
    Then the pass across accounts, or the
    account's own where it is alone and there is no book to read; else not
    yet: the rows still carry what the part that ran denied."""
    from taxjson.lib.report_model import (LOSS_RULE_NONE, LOSS_RULE_NOT_YET, LOSS_RULE_OWN,
                                          LOSS_RULE_PASS)
    acfgs = accounts_of(load_config(root, missing_ok=True))
    mine = account_of(acfgs, account)
    flags = _wash_flags(mine.taxable, bool(mine.crypto), country)
    if "--taxable" not in flags or "--no-wash" in flags:
        return LOSS_RULE_NONE

    def there(name: str) -> bool:
        return _has_inputs(root, name) or (cache / f"{name}_base.json").is_file()

    peers = {n for n, c in acfgs.items()
             if c.taxable and bool(c.crypto) == bool(mine.crypto) and there(n)}
    if not peers <= set(read):
        return LOSS_RULE_NOT_YET
    registered = [n for n, c in acfgs.items() if not c.taxable and _has_inputs(root, n)]
    contributed = _in_kind_contribution_booked(cache)
    if registered or contributed:
        if book is None:
            return LOSS_RULE_NOT_YET
        if registered and not {f"{n}_base.json" for n in registered} <= _book_sources(book):
            return LOSS_RULE_NOT_YET
        if contributed and _older(book, cache / IN_KIND_FILE):
            return LOSS_RULE_NOT_YET
        return LOSS_RULE_PASS
    return LOSS_RULE_PASS if len(read) > 1 or book is not None else LOSS_RULE_OWN


def record_full_history_checks(root: Path, cache: Path, read: List[str],
                               book: Optional[Path], country: str) -> None:
    """Each account of `read`'s full-history file, just built, given what
    its build checked (build_check): kept for this command's label
    (run_state `view_checks`: the build and the label are one command), and
    in the file's own record for a reader that did not build it
    (lib/report_model `record_full_history_check`). A record that cannot be
    written is removed if it can be (`forget_full_history_check`): an
    earlier build's check never stands for this build's bytes."""
    from taxjson.lib.report_model import forget_full_history_check, record_full_history_check
    for name in read:
        out = cache / f"{name}{_GAINS_ALL_SUFFIX}"
        if not out.is_file():
            continue
        check = build_check(root, cache, name, read, book, country)
        run_state.current.view_checks[str(out.resolve())] = check
        try:
            record_full_history_check(out, check)
        except OSError:
            forget_full_history_check(out)
