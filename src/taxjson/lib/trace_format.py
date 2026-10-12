"""Shared tt-style trace rendering used by taxjson-gains and taxjson-explain.

Both tools take the per-gain `trace` list produced by core.compute_gains
(`trace=True`) and need to format it as a box with a header line and a footer
rule. Keeping the rendering here means the two tools never drift apart.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional


def _denoise(amount) -> float:
    """The amount with float noise below a micro-cent removed. A trace
    recomputes the engine's value (530.4250000000001) where the saved
    gains file holds 530.425: formatted as is, the trace printed 530.43
    and the wash-sales table 530.42 (re-audit A2-0708)."""
    return round(float(amount or 0.0), 6)


def _fmt_money(amount: float) -> str:
    """Compact accounting-style money: $1,234.56 or -$1,234.56."""
    amount = _denoise(amount)
    sign = '-' if amount < 0 else ''
    return f"{sign}${abs(amount):,.2f}"


def _fmt_signed_money(amount: float) -> str:
    """Explicit-sign money for gain columns: +$1,234.56 / -$1,234.56 / $0.00."""
    amount = _denoise(amount)
    if abs(amount) < 0.005:
        return "$0.00"
    sign = '+' if amount > 0 else '-'
    return f"{sign}${abs(amount):,.2f}"


def align_pipe_lines(lines: List[str]) -> List[str]:
    """Pad '|'-separated cells so the bars align vertically across rows.

    Lines without '|' (e.g. '# --- ACB CALCULATION TRACE: ...' headers and
    dividers) pass through unchanged. Rows with fewer cells than the global
    max are padded to whatever they have — trailing columns dangle, which is
    the readable thing when a BUY trace line doesn't match a GAIN line
    column-for-column.
    """
    parsed = []
    for line in lines:
        if '|' not in line:
            parsed.append(('plain', line))
            continue
        parts = [p.strip() for p in line.split('|')]
        parsed.append(('row', parts))

    n_cols = max((len(p[1]) for p in parsed if p[0] == 'row'), default=0)
    widths = [0] * n_cols
    for kind, parts in parsed:
        if kind != 'row':
            continue
        for i, p in enumerate(parts):
            if i < n_cols:
                widths[i] = max(widths[i], len(p))

    out = []
    for kind, val in parsed:
        if kind == 'plain':
            out.append(val)
        else:
            padded = [val[i].ljust(widths[i]) for i in range(len(val))]
            out.append(' | '.join(padded).rstrip())
    return out


# Role of each same-symbol transaction in a superficial-loss window:
# the trace's long tag, the report layout's short label, and the legend
# line that explains the short label.
_ROLE_TAGS = {
    'loss_sale': ("*** LOSS SALE ***", "loss sale", None),
    'trigger': ("*** TRIGGER (denied loss added to the ACB) ***", "trigger",
                "trigger: the denied loss is added to the ACB of these "
                "units"),
    'trigger_perm': ("*** TRIGGER (denial PERMANENT — no ACB bump) ***",
                     "trigger, permanent",
                     "trigger, permanent: a registered or affiliated "
                     "holder's units — the denial is permanent, no ACB "
                     "bump"),
    'candidate': ("candidate (eligible; not needed — allocation takes "
                  "buys after the sale first, then earlier buys latest "
                  "first)", "candidate",
                  "candidate: eligible but not needed — allocation takes "
                  "buys after the sale first, then earlier buys latest "
                  "first"),
    'cover': ("closes a short — acquires nothing (not a trigger)", "cover",
              "cover: closes a short — acquires nothing (not a trigger)"),
    'other_sell': ("other sell in window (may produce its own loss)",
                   "other sell", "other sell: may produce its own loss"),
    'other_buy': ("other buy in window", "other buy", None),
}


def _wash_window_rows(txs) -> List[tuple]:
    """One tuple per window transaction: (day, date, account, action,
    qty, price, pool_bal, acb/sh, long role tag, short role label, role
    key) — the first nine are the trace table's columns."""
    def disp_action(t):
        if t['action'] == 'BUYSELL':
            return 'BUY' if t['qty'] > 0 else 'SELL'
        return t['action']

    rows = []
    for t in txs:
        day = f"T{t['days_from_loss']:+d}"
        # Distinguish sheltered (your own RRSP/TFSA) from affiliated (spouse,
        # controlled corp, affiliated trust — s.251.1). Both feed superficial-loss
        # detection but the disallowance lands on different property.
        if t.get('affiliated'):
            sheltered = " [affiliated]"
        elif t.get('sheltered'):
            sheltered = " [sheltered]"
        else:
            sheltered = ""
        role = t.get('role', '')
        key = role
        if role == 'trigger' and (t.get('sheltered') or t.get('affiliated')):
            key = 'trigger_perm'
        tag, short, _ = _ROLE_TAGS.get(key, (role, role, None))
        tag += sheltered
        short += sheltered

        # Running affiliated balance after this tx (the wash test's
        # running tally, all accounts pooled).
        rb = t.get('running_bal')
        rb_str = f"{rb:+.4f}" if rb is not None else "—"

        # Per-share ACB of the taxable pool after this tx. Sheltered txs
        # inherit the prior snapshot since they don't move the taxable
        # pool — that's the correct semantics: the cost basis the pool
        # carries is unchanged across a sheltered event.
        acb_sh = t.get('acb_per_share_after')
        if acb_sh is None:
            acb_str = "—"
        elif abs(acb_sh) < 1e-6:
            acb_str = "—"   # pool empty
        else:
            acb_str = f"{acb_sh:.4f}"

        rows.append((day, t['date'], t.get('account', ''), disp_action(t),
                     f"{t['qty']:+.4f}", f"{t['price']:.4f}",
                     rb_str, acb_str, tag, short, key))
    return rows


def _render_wash_window(g: Dict[str, Any]) -> List[str]:
    """Render the ±30 day window listing for a Canada wash sale.

    Shows every same-symbol transaction in the window across all accounts
    (taxable + sheltered), with a role tag, so the reader can see exactly
    which buys were eligible candidates and which was picked as the ACB-bump
    anchor — plus the affiliated-balance test that drove the disallowance.
    """
    ww = g.get('wash_window')
    if not ww:
        return []

    out: List[str] = []
    # Canada only (wash_window comes from the s.54 engine): its own
    # name, never "wash sale" (audit A2-1352).
    out.append("# --- SUPERFICIAL-LOSS WINDOW (±30 days, all accounts) ---")
    out.append(f"#   T-30  = {ww['window_start']}")
    out.append(f"#   LOSS  = {ww['loss_date']}")
    out.append(f"#   T+30  = {ww['window_end']}")

    bal = float(ww.get('bal_at_end', 0.0) or 0.0)
    loss_qty = float(ww.get('loss_qty', 0.0) or 0.0)
    disallowed_qty = float(ww.get('disallowed_qty', 0.0) or 0.0)
    direction = ww.get('loss_direction', 'LONG')
    # The rule the engine applies (REFERENCES.md, ITA s.54): per HOLDER
    # — your taxable accounts together, each registered or affiliated
    # account on its own — only units ACQUIRED inside the window and
    # still held at T+30 back a denial. The class-wide balance is shown
    # for context only; it does not decide the result (audit R1-159).
    out.append(
        "#   Superficial-loss test (ITA s.54, per holder): units a holder "
        "ACQUIRED in the window and still holds at T+30 back the denial"
    )
    out.append(
        "#     (holders: your taxable accounts together; each registered "
        "or affiliated account on its own)"
    )
    out.append(f"#     all-account qty at T+30 = {bal:+.4f}   (context only)")
    # Relative test (audit S078-06): an absolute 0.001-unit tolerance
    # called 0.0006 of 0.0015 BTC a "full" disallowance.
    def _q(x: float) -> str:
        return f"{x:.4f}" if abs(x) >= 0.01 or x == 0 else f"{x:.8g}"
    if abs(disallowed_qty - loss_qty) <= 1e-6 * max(abs(loss_qty), 1e-9):
        out.append(
            f"#   Result: full disallowance — {_q(disallowed_qty)} of "
            f"{_q(loss_qty)} units backed by substituted property"
        )
    else:
        out.append(
            f"#   Result: partial disallowance — {_q(disallowed_qty)} of "
            f"{_q(loss_qty)} units backed by substituted property "
            f"(acquired in the window and still held at T+30)"
        )

    txs = list(ww.get('transactions') or [])
    if not txs:
        return out

    rows = [r[:9] for r in _wash_window_rows(txs)]

    headers = ('day', 'date', 'account', 'action', 'qty', 'price',
               'pool_bal', 'acb/sh', 'role')
    # qty/price/pool_bal/acb/sh right-align (numeric), others left-align.
    numeric_cols = {4, 5, 6, 7}
    widths = [
        max(len(h), max(len(r[i]) for r in rows))
        for i, h in enumerate(headers)
    ]
    out.append("#")
    out.append("#   Same-symbol activity in the window (across all accounts):")
    out.append("#   pool_bal = running qty across ALL accounts (context); acb/sh = taxable pool's per-share ACB after this tx")

    def fmt_row(r):
        parts = []
        for i, val in enumerate(r):
            if i in numeric_cols:
                parts.append(val.rjust(widths[i]))
            else:
                parts.append(val.ljust(widths[i]))
        return "     ".join(parts)

    out.append("#     " + fmt_row(headers))
    out.append("#     " + fmt_row(tuple('-' * w for w in widths)))
    for r in rows:
        out.append("#     " + fmt_row(r))
    return out


def _render_wash_explanation(g: Dict[str, Any]) -> List[str]:
    """Render the inline wash-sale explanation that goes inside a gain block.

    Pulls from `wash_trigger` (Canada — one trigger lot) or `wash_replacements`
    (USA — possibly multiple) populated by core.compute_gains. Returns [] when
    the gain isn't a wash sale.
    """
    raw = g.get('raw_gain', 0.0)
    dis = g.get('disallowed_amount', 0.0)
    out: List[str] = []

    wt = g.get('wash_trigger')
    if wt:
        # Canada (CRA superficial-loss rule, ITA 54).
        out.append("# --- SUPERFICIAL LOSS (ITA s.54) ---")
        if wt.get('is_full_disallowance'):
            out.append(f"#   raw loss {_fmt_signed_money(raw)} -> fully disallowed (+{_fmt_money(dis)})")
        else:
            out.append(
                f"#   raw loss {_fmt_signed_money(raw)} -> {_fmt_money(dis)} disallowed (partial)"
            )
        if 'trigger_date' in wt:
            if wt.get('trigger_affiliated'):
                sheltered = " [affiliated]"
            elif wt.get('trigger_sheltered'):
                sheltered = " [sheltered]"
            else:
                sheltered = ""
            out.append(
                f"#   triggered by {wt['trigger_date']} BUY "
                f"{wt['trigger_qty']:+.4f} @ {wt['trigger_price']:.4f}   "
                f"account={wt.get('trigger_account', '')}{sheltered}"
            )
        perm = float(g.get('permanently_disallowed', 0.0) or 0.0)
        deferred = float(dis or 0.0) - perm
        if deferred > 0.005:
            out.append(
                f"#   deferred {_fmt_money(deferred)}: added to the ACB of the "
                f"replacement units in your taxable pool (recovered when "
                f"they are sold)"
                + (f"; first bump {_fmt_signed_money(wt['adjust_amount'])} "
                   f"on {wt['adjust_date']}"
                   if 'adjust_amount' in wt and perm > 0.005 else
                   f", on {wt['adjust_date']}" if 'adjust_date' in wt else "")
            )
        if perm > 0.005:
            # An affiliated person's purchase is permanent for THIS
            # return, but that person adds it to their own ACB
            # (s.53(1)(f)) — not "lost for good" (S033-03 / A2-1225).
            if wt.get('trigger_affiliated'):
                why = ("the replacement was bought by an affiliated "
                       "person — no ACB bump on your return; that person "
                       "adds it to their own ACB (s.53(1)(f))")
            elif wt.get('trigger_sheltered'):
                why = ("the replacement is held in a registered account "
                       "— no ACB bump, the loss is lost for good")
            else:
                why = ("the replacement is in a registered account (lost "
                       "for good) or bought by an affiliated person (who "
                       "adds it to their own ACB, s.53(1)(f)) — no ACB "
                       "bump on your return")
            out.append(f"#   PERMANENTLY denied {_fmt_money(perm)}: {why}")
        return out

    reps = g.get('wash_replacements')
    if reps:
        # USA (IRC §1091). Direction determines:
        #   LONG  loss → replacement is a BUY,  disallowed amount = basis bump
        #   SHORT loss → replacement is a SELL, disallowed amount = proceeds reduction
        direction = g.get('direction', 'LONG')
        is_short = direction == 'SHORT'
        perm = g.get('permanently_disallowed', 0.0) or 0.0
        all_perm = perm > 0.001 and abs(perm - dis) < 0.01
        side_label = "short" if is_short else ""
        if all_perm:
            if all(r.get('is_affiliated') for r in reps):
                why = ("§1091(d): the basis adjustment goes to the "
                       "affiliated holder's replacement shares, outside "
                       "these books")
            elif any(r.get('is_affiliated') for r in reps):
                why = ("sheltered replacement (Rev. Rul. 2008-5) and an "
                       "affiliated holder's replacement (§1091(d) basis "
                       "goes to their shares)")
            else:
                why = "Rev. Rul. 2008-5: sheltered replacement"
            out.append(f"# --- WASH SALE (IRC §1091 {side_label}, permanent disallowance) ---")
            out.append(
                f"#   raw loss {_fmt_signed_money(raw)} -> {_fmt_money(dis)} "
                f"permanently disallowed ({why})"
            )
        else:
            out.append(f"# --- WASH SALE (IRC §1091{(' '+side_label) if side_label else ''}) ---")
            mixed = perm > 0.001
            if is_short:
                tail = " (mixed: deferred + permanent)" if mixed else " (deferred via proceeds reduction on replacement short)"
            else:
                tail = " (mixed: deferred + permanent)" if mixed else " (deferred via basis transfer)"
            out.append(f"#   raw loss {_fmt_signed_money(raw)} -> {_fmt_money(dis)} disallowed{tail}")
        out.append("#   replacement lot(s):")
        action_word = "SELL" if is_short else "BUY"
        # Short replacements carry 'proceeds_reduction'; long carry 'basis_bump'.
        # Be tolerant in either direction.
        for r in reps:
            if r.get('is_affiliated'):
                sheltered = " [affiliated]"
            elif r.get('is_sheltered'):
                sheltered = " [sheltered]"
            else:
                sheltered = ""
            disallowance = r.get('basis_bump', r.get('proceeds_reduction', 0.0))
            adjust_word = "proceeds cut" if is_short else "basis bump"
            out.append(
                f"#     - {r['date']} {action_word} {'-' if is_short else '+'}{r['match_qty']:.4f} @ {r['price']:.4f}   "
                f"account={r.get('account', '')}   "
                f"{adjust_word}=+{_fmt_money(disallowance)}{sheltered}"
            )
        if not all_perm and not is_short:
            out.append("#   holding period inherits from loss lot (IRC §1223(3))")
        return out

    return out


def filing_position_text(g: Dict[str, Any],
                         country: Optional[str] = None) -> str:
    """The sentence for a gain row carrying a .tt ALLOWLOSS filing
    position (`loss_override`, lib/loss_overrides), '' otherwise. The
    country defaults from the row: only the US engine writes a term."""
    n = g.get('loss_override')
    if not isinstance(n, dict):
        return ''
    from taxjson.lib.loss_overrides import note_text
    return note_text(n, country or ('usa' if g.get('term') else 'canada'))


def render_gain_block(g: Dict[str, Any], align: bool = True,
                      manual: bool = False) -> List[str]:
    """Render one gain as a tt-style block: rule, header, trace lines, rule.

    The header is two lines:
        # SYMBOL  DATE  qty=N  gain=$X  (raw -$Y, disallowed +$Z)  [TERM]
        #   days_held=N  account=ACCT  id=HHHHHHHH

    A wash-sale block is appended after the trace when the gain has wash
    info attached.

    Returns the lines without trailing newlines. Returns an empty list when
    the gain has no trace attached.

    `manual`: an unknown-cost disposition (the pipeline's
    manual_reporting_required): its cost is unknown, so the header shows
    no gain and no holding period (the engine's figures come from a
    zero-cost pool and a 1970 sentinel date — audit R1-165 / S029-22).
    """
    trace = list(g.get('trace') or [])
    if not trace:
        return []
    if align:
        trace = align_pipe_lines(trace)

    longest = max((len(l) for l in trace), default=80)
    rule = "# " + "=" * max(80, longest - 2)

    sym = g.get('symbol', '?')
    date = g.get('date', '?')
    qty = g.get('qty', 0.0)
    gain_amt = g.get('gain', 0.0)
    raw = g.get('raw_gain', gain_amt)
    dis = g.get('disallowed_amount', 0.0)
    days = g.get('days_held')
    account = g.get('account', '')
    term = g.get('term')
    # Unmasked on purpose (owner decision, audit A2-1379): the id is
    # the handle `taxjson audit --id` / explain take; a broker-issued id
    # (a Kraken ledger txid) is an exchange reference, not an account id.
    gid = (g.get('id') or '')[:16]

    if manual:
        proceeds = float(g.get('proceeds', 0.0) or 0.0)
        primary = [f"# {sym}", date, f"qty={qty:.4f}",
                   f"proceeds={_fmt_money(abs(proceeds))}",
                   "MANUAL REPORTING — no purchase in your files (cost "
                   "unknown): gain not computed, not in the gains total"]
        days = None
        dis = 0.0
        term = None
    else:
        primary = [f"# {sym}", date, f"qty={qty:.4f}", f"gain={_fmt_signed_money(gain_amt)}"]
    if dis > 0.001:
        primary.append(f"(raw {_fmt_signed_money(raw)}, disallowed +{_fmt_money(dis)})")
    if term:
        primary.append(f"[{term}]")
    line1 = "   ".join(primary)

    secondary = []
    if days is not None:
        secondary.append(f"days_held={days}")
    if account:
        secondary.append(f"account={account}")
    secondary.append(f"id={gid}")
    line2 = "#   " + "   ".join(secondary)

    body: List[str] = [rule, line1, line2, rule] + trace
    wash_lines = _render_wash_explanation(g)
    window_lines = _render_wash_window(g)
    if wash_lines:
        body.append("#")
        body.extend(wash_lines)
    if window_lines:
        body.append("#")
        body.extend(window_lines)
    fp = filing_position_text(g)
    if fp:
        body += ["#", f"# {fp[0].upper()}{fp[1:]}"]
    body.append(rule)
    return body


def render_document_header(
    *,
    input_path: Optional[str],
    country: str,
    year: Optional[str],
    tax_date_basis: str,
    disposition_count: int,
    wash_sale_count: int,
    total_gain: float,
    total_disallowed: float,
    generated_at: Optional[datetime] = None,
) -> List[str]:
    """Self-documenting metadata block for the top of the trace file."""
    when = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    src = input_path or "<stdin>"
    yr = f"{year} ({tax_date_basis} basis)" if year else f"all years ({tax_date_basis} basis)"
    rule = "# " + "=" * 90
    lines = [
        rule,
        "# taxjson-gains audit trace",
        rule,
        f"# input         {src}",
        f"# country       {country.upper()}",
        f"# tax year      {yr}",
        f"# generated     {when}",
        "#",
        f"# dispositions  {disposition_count:>6,d}",
        f"# wash sales    {wash_sale_count:>6,d}",
        f"# total gain    {_fmt_signed_money(total_gain):>14}",
        f"# disallowed    {_fmt_money(total_disallowed):>14}",
        rule,
    ]
    return lines


def render_summary_table(gains: List[Dict[str, Any]]) -> List[str]:
    """Per-symbol summary table — one row per symbol with counts and totals.

    `gains` is the post-year-filter list of gain entries (skips DIVIDEND).
    """
    by_sym: Dict[str, Dict[str, float]] = {}
    for g in gains:
        if g.get('action') == 'DIVIDEND':
            continue
        s = g.get('symbol', '')
        row = by_sym.setdefault(s, {'sales': 0, 'qty': 0.0, 'gain': 0.0, 'disallowed': 0.0})
        row['sales'] += 1
        row['qty'] += float(g.get('qty', 0.0))
        row['gain'] += float(g.get('gain', 0.0))
        row['disallowed'] += float(g.get('disallowed_amount', 0.0))

    if not by_sym:
        return []

    rows = sorted(by_sym.items(), key=lambda kv: kv[0])

    sym_w = max(len("Symbol"), max(len(s) for s, _ in rows))
    sales_w = 5
    qty_w = max(len("Total qty"), max(len(f"{r['qty']:,.4f}") for _, r in rows))
    gain_w = max(len("Total gain"), max(len(_fmt_signed_money(r['gain'])) for _, r in rows))
    dis_w = max(len("Disallowed"), max(len(_fmt_money(r['disallowed'])) for _, r in rows))

    def fmt(sym, sales, qty, gain, dis):
        return (
            f"# {sym:<{sym_w}}  "
            f"{sales:>{sales_w}}  "
            f"{qty:>{qty_w}}  "
            f"{gain:>{gain_w}}  "
            f"{dis:>{dis_w}}"
        )

    header_rule = "# " + "=" * 90
    out = [
        header_rule,
        "# Per-symbol summary",
        header_rule,
        fmt("Symbol", "Sales", "Total qty", "Total gain", "Disallowed"),
        "# " + "-" * (sym_w + sales_w + qty_w + gain_w + dis_w + 8),
    ]
    total_gain = 0.0
    total_disallowed = 0.0
    total_sales = 0
    for sym, r in rows:
        out.append(fmt(
            sym,
            str(r['sales']),
            f"{r['qty']:,.4f}",
            _fmt_signed_money(r['gain']),
            _fmt_money(r['disallowed']),
        ))
        total_gain += r['gain']
        total_disallowed += r['disallowed']
        total_sales += int(r['sales'])
    out.append("# " + "-" * (sym_w + sales_w + qty_w + gain_w + dis_w + 8))
    out.append(fmt(
        "TOTAL",
        str(total_sales),
        "",
        _fmt_signed_money(total_gain),
        _fmt_money(total_disallowed),
    ))
    out.append(header_rule)
    return out




# ------------------------------------------------------------------------
# The report layout (docs/output-style.md): the same facts as
# render_gain_block, for a reader at a terminal — no '#' comment column, prose
# wrapped to the width, the pool history and the window as tables that fit
# (a row too wide becomes a per-record block). `taxjson wash-sales
# --explain` prints it, and `taxjson audit` its trace sections
# (render_report_trace); render_gain_block stays the layout of the trace
# files.

def _strip_hash(line: str) -> str:
    return line[1:] if line.startswith('#') else line


def _trace_records(trace: List[str], width_: Optional[int],
                   indent: str) -> List[str]:
    """The engine's pool-history lines ('# DATE ACTION QTY @ PRICE |
    Key: value | ...') as an aligned table when it fits, else one block
    per line: its first cell, then its `key value` cells packed under
    it. Lines without cells ('--- ... ---' headers) are dropped — the
    caller prints its own heading."""
    from taxjson.lib.out import width as _width, wrap
    w = _width() if width_ is None else width_
    rows = [_strip_hash(l).strip() for l in trace]
    rows = [r for r in rows if r and not r.startswith('---')]
    aligned = [indent + r for r in align_pipe_lines(rows)]
    if w <= 0 or max((len(r) for r in aligned), default=0) <= w:
        return aligned
    out: List[str] = []
    for r in rows:
        cells = [c.strip() for c in r.split('|')]
        out.append(indent + " ".join(cells[0].split()))
        rest = [" ".join(c.split()).replace(" ", " ")
                for c in cells[1:] if c]
        if rest:
            out.extend(ln.replace(" ", " ") for ln in wrap(
                "   ".join(rest), w, indent + "    ", indent + "    "))
    return out


def _report_wash_lines(g: Dict[str, Any], width_: Optional[int],
                       indent: str) -> List[str]:
    """_render_wash_explanation's facts as a heading and `- ` items."""
    import re
    from taxjson.lib.out import wrap
    out: List[str] = []
    for line in _render_wash_explanation(g):
        text = _strip_hash(line)
        body = text.strip()
        if not body:
            continue
        if body.startswith('---') and body.endswith('---'):
            # 'SUPERFICIAL LOSS (ITA s.54)' -> 'Superficial loss (ITA
            # s.54)': a sub-block heading, like the others in the block.
            head = body.strip('- ').strip()
            out.append(indent + re.sub(
                r"^[A-Z][A-Z -]*[A-Z](?= \(|$)",
                lambda m: m.group(0).capitalize(), head))
            continue
        depth = len(text) - len(text.lstrip())
        sub = "  " if depth > 3 else ""
        if body.startswith("- "):
            body = body[2:]
        # The report's money has no '$' (the figures above it have
        # none), and one space between words.
        body = re.sub(r"([+-]?)\$(\d[\d,]*\.\d\d)", r"\1\2",
                      " ".join(body.split()))
        out.extend(wrap(body, width_, indent + "  " + sub + "- ",
                        indent + "  " + sub + "  "))
    return out


def _report_window_lines(g: Dict[str, Any], width_: Optional[int],
                         indent: str) -> List[str]:
    """The superficial-loss window: its dates, the test and its result as
    `label:  value` lines, then the same-symbol activity as a table that
    fits (pool_bal, context only, is the first column to go) with a
    legend for the role labels used."""
    from taxjson.lib.out import fit_table, kv_lines, wrap
    ww = g.get('wash_window')
    if not ww:
        return []
    bal = float(ww.get('bal_at_end', 0.0) or 0.0)
    loss_qty = float(ww.get('loss_qty', 0.0) or 0.0)
    dq = float(ww.get('disallowed_qty', 0.0) or 0.0)

    def _q(x: float) -> str:
        return f"{x:.4f}" if abs(x) >= 0.01 or x == 0 else f"{x:.8g}"
    full = abs(dq - loss_qty) <= 1e-6 * max(abs(loss_qty), 1e-9)
    result = (f"full disallowance — {_q(dq)} of {_q(loss_qty)} units "
              f"backed by substituted property" if full else
              f"partial disallowance — {_q(dq)} of {_q(loss_qty)} units "
              f"backed by substituted property (acquired in the window "
              f"and still held at T+30)")
    out = [indent + "Superficial-loss window (±30 days, all accounts)"]
    out += kv_lines([
        ("window", f"{ww['window_start']} to {ww['window_end']} (loss "
                   f"{ww['loss_date']})"),
        ("test", "ITA s.54, per holder: units a holder ACQUIRED in the "
                 "window and still holds at T+30 back the denial (holders: "
                 "your taxable accounts together; each registered or "
                 "affiliated account on its own)"),
        ("context", f"all-account quantity at T+30 = {bal:+.4f} (does not "
                    f"decide the result)"),
        ("result", result),
    ], indent + "  ", width_)
    txs = list(ww.get('transactions') or [])
    if not txs:
        return out
    rows = _wash_window_rows(txs)
    # The legend before the table it explains (docs/output-style.md,
    # Essentials first).
    legend = []
    for key in dict.fromkeys(r[10] for r in rows):
        line = _ROLE_TAGS.get(key, (None, None, None))[2]
        if line:
            legend.append(line)
    legend.append("POOL_BAL: the running quantity across all accounts "
                  "(context); ACB/SH: the taxable pool's ACB per unit "
                  "after the row")
    out.append("")
    for ln in legend:
        out += wrap(ln, width_, indent + "  ", indent + "    ")
    out += fit_table(
        ["DAY", "DATE", "ACCOUNT", "ACTION", "QTY", "PRICE", "POOL_BAL",
         "ACB/SH", "ROLE"],
        [r[:8] + (r[9],) for r in rows],
        aligns=["<", "<", "<", "<", ">", ">", ">", ">", "<"],
        drop=(6, 5), width_=width_, indent=indent + "  ")
    return out


def render_report_block(g: Dict[str, Any], width_: Optional[int] = None,
                        manual: bool = False,
                        country: Optional[str] = None) -> List[str]:
    """One disposition in the report layout: a heading line, its figures
    as `label:  value` lines, then the pool history, the denial and the
    window as indented blocks separated by one blank line. [] when the
    gain carries no trace (as render_gain_block)."""
    from taxjson.lib.out import fmt_money, fmt_qty, kv_lines, width
    trace = list(g.get('trace') or [])
    if not trace:
        return []
    w = width() if width_ is None else width_
    gain = _denoise(g.get('gain', 0.0))
    raw = _denoise(g.get('raw_gain', gain))
    dis = _denoise(g.get('disallowed_amount', 0.0))
    head = " — ".join(str(x) for x in (
        g.get('symbol', '?'), g.get('date', '?'), g.get('account'))
        if x)
    pairs = [("quantity", fmt_qty(g.get('qty', 0.0)))]
    if manual:
        pairs.append(("proceeds", fmt_money(abs(float(
            g.get('proceeds', 0.0) or 0.0)))))
        pairs.append(("gain", "not computed — no purchase in your files "
                              "(cost unknown); not in the gains total"))
    else:
        g_txt = fmt_money(gain)
        if dis > 0.001:
            g_txt += f" (raw {fmt_money(raw)}, denied {fmt_money(dis)})"
        pairs.append(("gain", g_txt))
        if g.get('term'):
            pairs.append(("term", str(g['term'])))
        if g.get('days_held') is not None:
            pairs.append(("days held", str(g['days_held'])))
    pairs.append(("id", (g.get('id') or '')[:16]))
    fp = filing_position_text(g, country)
    if fp:
        # A .tt ALLOWLOSS position: the gain is the claimed loss and the
        # rule's denial is not applied (pre-release review M7).
        pairs.append(("position", fp[0].upper() + fp[1:]))
    out = [head] + kv_lines(pairs, "  ", w)
    return out + render_report_trace(g, w)


def render_report_trace(g: Dict[str, Any],
                        width_: Optional[int] = None) -> List[str]:
    """The sections of render_report_block after its figures — the pool
    history, the denial and the window — each opening with a blank line
    and an indented sub-heading. `taxjson audit` prints them under its
    own figures (render_gain_block's '#' lines stay the trace files').
    [] when the gain carries no trace."""
    from taxjson.lib.out import width
    trace = list(g.get('trace') or [])
    if not trace:
        return []
    w = width() if width_ is None else width_
    # Canada traces an ACB pool (its lines open with an 'ACB CALCULATION
    # TRACE' header); the US engine traces basis lots.
    acb = any('ACB CALCULATION TRACE' in l for l in trace)
    out = ["", "  Pool history (ACB trace)" if acb
           else "  Lot history (basis trace)"]
    out += _trace_records(trace, w, "    ")
    wash = _report_wash_lines(g, w, "  ")
    if wash:
        out += [""] + wash
    window = _report_window_lines(g, w, "  ")
    if window:
        out += [""] + window
    return out
