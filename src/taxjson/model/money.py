"""Money as Decimal, and its exact way in from a row's float and back (step 6).

A row (model/transaction.py) holds money as float or int: what the parsers
and the JSON files carry, and what its id hashes. The engines hold it as
Decimal. `to_money` takes the shortest decimal that reads back as the same
float (its repr), so `from_money(to_money(x))` is `x` again, bit for bit and
of its own type: an int stays an int, a float a float, -0.0 keeps its sign.

The presentation rounding is here too (from lib/numeric, step 6c): `D`,
`round_half_up` and `round_floats`, which writes the gains JSON
(model/result `Result.to_json`). The engines accumulate money in Decimal
where a float sum would drift by cents, and the output rounds half-up, as
statements and forms do (Python's round() rounds half to even)."""
import contextlib
import decimal
import functools
import math
import os
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any, Callable, Iterator, Optional, Set, Tuple, TypeVar, Union, cast

Money = Decimal

# A float's repr has a decimal point or an exponent, so its Decimal's
# exponent is not 0, except for a 17-digit repr at e+16
# (1.2345678901234568e+16, exponent 0): that one is written with one place
# (…568.0), so exponent 0 always means an int. The place is added to the
# digits, not by quantize(), which rounds in the thread's decimal context
# and fails under a precision below 18.


def to_money(x: Union[int, float]) -> Decimal:
    """An int or a finite float as Decimal: Decimal(x) for an int, the
    float's repr for a float. A bool, a Decimal, a string or a non-finite
    float is refused (TypeError, ValueError): a row's money is never one."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise TypeError(f"money is an int or a float, not {type(x).__name__}: {x!r}")
    if isinstance(x, int):
        return Decimal(x)
    if not math.isfinite(x):
        raise ValueError(f"money is finite, not {x!r}")
    return float_decimal(x)


def float_decimal(x: float) -> Decimal:
    """A float as the Decimal of its repr (a NaN or an infinity too), a
    17-digit repr at e+16 given its one place (see above). A repr with no
    exponent has a point, so only one with an `e` can come out at 0."""
    text = repr(x)
    d = Decimal(text)
    if "e" in text:
        sign, digits, exponent = d.as_tuple()
        if exponent == 0:
            d = Decimal((sign, digits + (0,), -1))
    return d


def exact_decimal(token: str) -> Optional[Decimal]:
    """The Decimal a token spells, or None when Decimal cannot read it
    (no number, or an exponent past Decimal's: 1e999999999999999999999),
    whatever the thread's decimal context traps."""
    with localcontext() as ctx:
        ctx.traps[InvalidOperation] = True
        try:
            return Decimal(token)
        except InvalidOperation:
            return None


def text_decimal(token: str) -> Decimal:
    """A number written out as the Decimal it spells; ValueError for one
    float() does not read (the readers took float() of it). An exponent
    past Decimal's is the float the token reads as, inf or 0.0."""
    f = float(token)
    d = exact_decimal(token)
    return float_decimal(f) if d is None else d


def from_money(d: Decimal) -> Union[int, float]:
    """What to_money was given: an int when the Decimal has no places and
    no exponent (exponent 0), else the float. A NaN or an infinity is
    refused (ValueError), as to_money refuses it: no row's money is one."""
    if not isinstance(d, Decimal):
        raise TypeError(f"money is a Decimal, not {type(d).__name__}: {d!r}")
    if not d.is_finite():
        raise ValueError(f"money is finite, not {d!r}")
    if d.as_tuple().exponent == 0:
        return int(d)
    return float(d)


# --- the engines' money (steps 6d to 6f) ----------------------------------------
#
# The engines read a money figure through `of` (`M.of(tx.net_amount)`), never
# float(): the exact Decimal (an int as itself, a float as its repr, see
# to_money). Their money is Decimal from their reads to their return:
# `engine` wraps each compute_gains, runs it trapping decimal.FloatOperation
# (a Decimal ordered against a float, or Decimal(a float), stops on a
# TypeError instead of answering at a boundary by the float's binary value;
# == and != stay silent) and leaves its result's money as floats (`leave`),
# so the rows before it (TaxTransaction) and every reader after it hold
# floats; a report cuts one with `shown`, half up from its repr (step 6h).
# The trap is the engine run's own decimal context (`trapped`): code
# outside the engines is not trapped.
# Step 6f took the float mode out: exact money is the only one.


def of(x: Any) -> Decimal:
    """What float(x) reads, as the exact Decimal: a Decimal as itself, an
    int as Decimal(int) (a bool as 0 or 1), a float as its repr, a string
    as the number it spells (text_decimal). Anything else is refused as
    float() refuses it (TypeError: None, a list)."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, int):
        return Decimal(int(x))
    if isinstance(x, float):
        return float_decimal(x)
    if isinstance(x, str):
        return text_decimal(x)
    return float_decimal(float(x))


def kept(x: Any) -> Any:
    """A money figure kept as the row holds it: its exact Decimal, an int
    still itself (exact already), so a figure stored as it is writes as
    before: "10", not "10.0" (TJ-3076, TJ-3099)."""
    return x if type(x) is int else of(x)


@contextlib.contextmanager
def trapped() -> Iterator[None]:
    """The block in a decimal context of its own that traps FloatOperation
    (a Decimal ordered against a float stops on a TypeError instead of
    answering) and rounds half up: a Decimal's round() and its format in a
    trace line round as the gains file writes (round_half_up) and as
    present() shows. The engines' money runs in it (`engine`), and a test
    that calls an engine's helper outside compute_gains; the code reading
    what they return holds floats (step 6e)."""
    with decimal.localcontext() as ctx:
        ctx.traps[decimal.FloatOperation] = True
        ctx.rounding = ROUND_HALF_UP
        yield


class MoneySiteError(TypeError):
    """The trap's TypeError, raised again by `engine` with the site it
    stopped at (`site`, `cause`) and what to do (TJ-3104)."""
    site = "?"
    cause = ""


def money_error(e: BaseException) -> bool:
    """Whether `e` is exact money's own TypeError: the trap (a Decimal
    ordered against a float) or a Decimal and a float in one sum, a money
    site still mixing float in. Any other TypeError is not (a Decimal
    against None, say: TJ-3114)."""
    text = str(e)
    return isinstance(e, (decimal.FloatOperation, MoneySiteError)) or (
        isinstance(e, TypeError) and "decimal.Decimal" in text and "'float'" in text)


_HERE = os.path.abspath(__file__)
_PKG = os.path.dirname(os.path.dirname(_HERE))


def _site(e: BaseException) -> str:
    """The innermost line of taxjson's own code `e` passed through
    ("taxjson/engine/ca/walk.py:1234"; this module's own frames left out), else
    the innermost line outside this module, else "?"."""
    inner: Optional[Tuple[str, int]] = None
    pkg: Optional[Tuple[str, int]] = None
    tb = e.__traceback__
    while tb is not None:
        path = os.path.abspath(tb.tb_frame.f_code.co_filename)
        if path != _HERE:
            inner = (path, tb.tb_lineno)
            if path.startswith(_PKG + os.sep):
                pkg = inner
        tb = tb.tb_next
    site = pkg or inner
    if site is None:
        return "?"
    path, line = site
    if path.startswith(_PKG + os.sep):
        return f"taxjson/{os.path.relpath(path, _PKG).replace(os.sep, '/')}:{line}"
    return f"{os.path.basename(path)}:{line}"


# How `stopped_text` starts: a relay of a child's error line reads it there.
STOPPED = "the gains engine's exact money stopped at"


def stopped_text(site: str, cause: str) -> str:
    """What a money site still mixing float in is said as: where the
    engine stopped, and that it is a bug to report."""
    return (f"{STOPPED} {site} ({cause}): a figure there is still a binary "
            f"float. This is a bug in taxjson: please report it with this "
            f"line.")


_Fn = TypeVar("_Fn", bound=Callable[..., Any])


def engine(fn: _Fn) -> _Fn:
    """An engine's compute_gains: run in the trap (`trapped`), its
    result's money left as floats (`leave`): Decimal from the engine's
    reads to its return, float on either side (step 6e). A money site
    still mixing float in stops it on an error naming the site
    (`MoneySiteError`, its `site` and `cause` kept: TJ-3104)."""
    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        with trapped():
            try:
                result = fn(*args, **kwargs)
            except TypeError as e:
                if isinstance(e, MoneySiteError) or not money_error(e):
                    raise
                site, cause = _site(e), str(e)
                err = MoneySiteError(stopped_text(site, cause))
                err.site, err.cause = site, cause
                raise err from e
            return leave(result)
    return cast(_Fn, run)   # the same signature: a typed caller is checked


# Below any money figure: what 28-digit division leaves of a zero (a fully
# denied loss's gain of -1E-25, TJ-3098).
_RESIDUE = Decimal("1e-18")


def leave(obj: Any) -> Any:
    """`obj` with every Decimal in it a float, in place: dicts, lists and
    sets are changed where they are (a part shared by two rows is changed
    once, for both), a tuple or frozenset is rebuilt (a namedtuple or a
    subclass as its own type), a dict's Decimal key too. A Decimal
    becomes its float, an integral one too (a quotient's exponent is no
    type: 10.0 / 2.0 is Decimal('5')); a zero, or a residue below
    1e-18, is 0.0, unsigned. Anything else is kept: the traces' lines
    are text. An object's attributes are not walked: the engines return
    no money in one (TestTheBoundary scans for it)."""
    seen: Set[int] = set()

    def walk(x: Any) -> Any:
        if isinstance(x, Decimal):
            # A zero has no sign: -0.0 would show as "-0.00".
            return 0.0 if not x or abs(x) < _RESIDUE else float(x)
        if isinstance(x, (str, bytes, int, float, type(None))):
            return x
        if id(x) in seen:
            return x
        if isinstance(x, dict):
            seen.add(id(x))
            if any(isinstance(k, Decimal) for k in x):
                items = [(walk(k), v) for k, v in x.items()]
                x.clear()
                x.update(items)
            for k, v in x.items():
                w = walk(v)
                if w is not v:
                    x[k] = w
            return x
        if isinstance(x, list):
            seen.add(id(x))
            for i, v in enumerate(x):
                w = walk(v)
                if w is not v:
                    x[i] = w
            return x
        if isinstance(x, set):
            seen.add(id(x))
            items = [walk(v) for v in x]
            if not all(a is b for a, b in zip(items, x)):
                x.clear()
                x.update(items)
            return x
        if isinstance(x, (tuple, frozenset)):
            items = [walk(v) for v in x]
            if all(a is b for a, b in zip(items, x)):
                return x
            if hasattr(x, "_fields"):
                return type(x)(*items)
            return type(x)(items)
        return x

    return walk(obj)


# `shown`'s own decimal context: wide enough to quantize any finite float
# at the places a report asks (a float has at most 309 digits before the
# point), nothing trapped, whatever the thread's context is (an engine's
# `trapped`, say: a call inside it neither reads nor changes it). A cut
# sets its Inexact and Rounded flags, which nothing reads.
_SHOWN = decimal.Context(prec=400, rounding=ROUND_HALF_UP, traps=[])


def shown(x: Any, places: int) -> Any:
    """A money figure as a report shows it, cut to `places` half up from
    the figure as taxjson holds it, a float back (step 6h(4)): the
    decimal a float's repr spells (float_decimal: the gains file's four
    places for a figure read from it, the shortest decimal of a report's
    own float otherwise; 2.675 is 2.68, where round() gave 2.67 from the
    binary float just below the half; 0.125 is 0.13, where round() gave
    0.12, half to even), or a Decimal's own. The reports' one cut, as the
    gains file (round_floats) cuts; `present` parts from it only by
    TJ-3357's residue nudge, one ulp below a half (0.004999999999999999
    at 2 places is 0.0 here, 0.01 there). It differs from round() only
    where that decimal is an exact half at the cut. A float sum
    that lands a hair off a half (-42.52499999999999 for -42.525) keeps
    round()'s cut, so a total a report adds in float can sit a cent from
    its parts until the reports' arithmetic is exact (a later step:
    TJ-3408, TJ-2792). A quantity or a ratio goes through `units` instead
    (step 6g).

    A float not finite comes back as it is, as round() passes it
    (TJ-3345); an int as it is (an exact figure: at the places of 0 or
    more every report asks, round() leaves an int alone too); None or a
    str is refused (TypeError), as round() refuses it. A zero keeps the
    sign round() gives, on purpose: -0.001 is -0.0, as before the flip,
    so the `M.shown(x, 2) + 0.0` sites still drop it and no report gains
    a "-0.00". The cut runs in `_SHOWN`, so a figure too wide for a
    28-digit context (1e26 at 2 places) cuts too, and a call inside an
    engine's `trapped` context leaves that context as it is (TJ-3403);
    a cut too wide even for it (the float maximum at 100 places, which
    no report asks) gives the operand back, as round() does (TJ-3418)."""
    if isinstance(x, float):
        if not math.isfinite(x):
            return x
        d = float_decimal(x)
    elif isinstance(x, Decimal):
        if not x.is_finite():
            return float(x)
        d = x
    elif isinstance(x, int):
        return int(x)
    else:
        raise TypeError(f"type {type(x).__name__} doesn't define __round__ method")
    q = d.quantize(Decimal(1).scaleb(-places, _SHOWN), rounding=ROUND_HALF_UP, context=_SHOWN)
    if not q.is_finite():
        return x if isinstance(x, float) else float(x)
    return float(q)


def units(x: Any, places: int) -> Any:
    """A quantity or a ratio cut to `places` as round() cuts it (half to
    even on the binary value): what the engines did before step 6. A
    Decimal is cut as its float, so units stay float."""
    return round(float(x) if isinstance(x, Decimal) else x, places)


# --- money in a `--json` document (TJ-4930) ------------------------------------
#
# A JSON number is a float, and a float sum or quotient printed its binary
# error there (80.72999999999999 for 80.73). A `--json` money figure is
# printed as taxjson holds it: a total of row figures as their exact sum
# (`exact_sum`, whose float's repr is that decimal), a sum of the gains
# files' figures at their GAINS_PLACES, and a figure derived by a quotient
# or a rate (an average, a fee per unit, a share of an ACB, a step of the
# tax estimate, the engine's own unrounded figure) at DERIVED_PLACES
# (`json_money`). tests/features/model/test_json_money.py holds the
# golden views to it.
GAINS_PLACES = 4        # round_floats' places: the gains files' money
DERIVED_PLACES = 6      # option-yield's ratios; below them, binary error


def exact_sum(values: Any) -> Decimal:
    """The sum of money figures as taxjson holds them (each read as `of`
    reads it: a float as its repr, a Decimal as itself; None as 0), with
    no binary error whatever the count or the order: float() of it is the
    float whose repr is that sum (up to 15 significant digits)."""
    return sum((of(v) for v in values if v is not None), Decimal(0))


def exact_total(figures: Any) -> float:
    """`exact_sum` as the float a `--json` document prints: its repr is
    the exact decimal sum of the figures."""
    return float(exact_sum(figures))


def json_money(x: Any, places: int) -> Any:
    """A money figure as a `--json` document prints it: at `places`, half
    up from the figure as taxjson holds it, a float back, a negative zero
    as 0.0. A float is cut as `shown` cuts it; a Decimal (an exact sum or
    product) as `present` cuts it, so a quotient's residue below an exact
    half still cuts up (TJ-3357), and the shown log (tests/_views_ab.py)
    holds only the reports' cuts. An int (exact already) and None are
    kept as they are."""
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, Decimal):
        return float(present(x, places)) + 0.0 if x.is_finite() else float(x)
    v = shown(x, places)
    return v + 0.0 if isinstance(v, float) else v


def present(x: Any, places: int) -> Decimal:
    """A money figure as shown: `places` places, half up, as a Decimal
    (D's reading of a float, its str); round_half_up is its float.

    A quotient is exact to 28 digits only: an exact half can come out a
    residue of 1E-18 or less below it (650.21025 as 650.2102499...),
    which a plain quantize cuts down. The figure is moved 1E-18 from zero
    first, so it cuts as the half it is (TJ-3357); a real figure that
    close below a half has more than 18 places, which no product or
    quotient of inputs holds. A figure with more than 10 places before
    the point has no digit there in a 28-digit context, so the move
    leaves it as it is; in a context of fewer digits than 18 and its own
    before the point, the move rounds it first (no code lowers it). A
    float under about 0.0156 one ulp below a decimal half (0.004999...
    for 0.005) is within 1E-18 of it, so round_half_up cuts it up, as
    its decimal value is (TJ-3374)."""
    d = D(x)
    if d.is_finite() and d:
        d += -_RESIDUE if d.is_signed() else _RESIDUE
    return d.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)


def cents_apart(a: Any, b: Any) -> Union[int, float]:
    """How far apart two money figures are in whole cents: each cut to the
    cent as `present` cuts it (half up from its exact value, `of`), the
    difference taken in integers. For two figures in cents, a check that
    allows a cent asks for `<= 1`: two such figures a cent apart are 1
    whatever their binary floats (whose difference is a bit over 0.01 for
    most such pairs), so a figure cut a cent the other way passes and float
    error never trips the check (TJ-3119). A figure with more places is
    cut first, so the gap is the cuts': 0.005 and -0.005 are 2 apart, 0.005
    and 0.02499 are 1 (TJ-3382). A NaN or an infinity has no cents: the
    float gap in cents (NaN, inf), so `> 1` and `<= 1` answer as the float
    comparisons did; so does a figure past the context's digits. None is
    refused (TypeError), as float() refuses it."""
    da, db = of(a), of(b)
    if da.is_finite() and db.is_finite():
        try:
            return abs(int((present(da, 2) - present(db, 2)).scaleb(2)))
        except (InvalidOperation, ValueError):
            pass
    return abs(float(da) - float(db)) * 100


# --- presentation rounding (moved from lib/numeric, step 6c) ------------------

def D(value: Union[float, int, str, Decimal, None]) -> Decimal:
    """Coerce a numeric input to Decimal without losing precision.

    Going through ``str()`` is essential — ``Decimal(0.1)`` captures the
    binary-float representation noise, while ``Decimal(str(0.1))`` gives
    the user-visible 0.1.
    """
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_half_up(value: Union[float, Decimal], places: int = 4) -> float:
    """Round ``value`` to ``places`` decimal places using ROUND_HALF_UP."""
    if value is None:
        return value
    try:
        return float(present(value, places))
    except (InvalidOperation, ValueError):
        return float(value) if not isinstance(value, float) else value


# Field names whose values are NOT money — share counts (fractional shares
# from DRIPs can have 5–8 dp), running balances, and day counts. Rounding
# these to 4 dp would silently lose precision that the engine needs to
# track correctly across many small accumulations.
_QTY_FIELDS = frozenset({
    'qty', 'quantity',
    'match_qty', 'qty_total',
    'loss_qty', 'disallowed_qty',
    'pool_qty', 'pool_qty_after',
    'running_bal', 'bal_at_end',
    'days_held', 'days_from_loss', 'hold_days',
    # Share/coin counts the gains JSON also carries (audit R1-317:
    # wash_trigger.trigger_qty printed 6.76e-06 AVAX as 0.0; S074-22:
    # missing_history_log opening_qty).
    'trigger_qty', 'opening_qty', 'option_qty', 'covered_shares',
    'open_qty', 'remaining_qty',
})


def round_floats(obj: Any, places: int = 4, _key: Optional[str] = None) -> Any:
    """Recursively round all floats/Decimals in a JSON-shaped object to
    `places` decimals, EXCEPT for values under quantity-like keys (see
    _QTY_FIELDS). Share counts, running balances, and day counts pass
    through unchanged so fractional-share precision is preserved."""
    if isinstance(obj, (float, Decimal)):
        if _key in _QTY_FIELDS:
            return float(obj) if isinstance(obj, Decimal) else obj
        return round_half_up(obj, places)
    if isinstance(obj, dict):
        return {k: round_floats(v, places, _key=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats(x, places, _key=_key) for x in obj]
    return obj
