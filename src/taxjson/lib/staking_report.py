"""Reward disclosure from final books, without changing income or basis.

Only DIVIDEND rows are rewards. A paired BUYSELL supplies acquisition cost;
it is never a second reward. ``staking_reward`` retains source-currency
facts, while the transaction itself contains the selected book currency.
This module never reads source exports, retrieves prices, or applies fees.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from taxjson.lib import out
from taxjson.lib.country import canonical_country
from taxjson.lib.report_model import fmt_money, fmt_qty6

_MONEY = ("gross_amount", "commission_amount", "income_amount", "acquisition_cost")
_QUANTITIES = ("gross_quantity", "net_quantity")
_KINDS = {"staking": "staking", "other_reward": "other reward",
          "unclassified": "unclassified"}


def _number(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _amount(tx: dict, *, acquisition: bool = False) -> Decimal | None:
    fields = ("net_amount", "gross_amount") if acquisition else (
        "gross_amount", "net_amount")
    first, second = (_number(tx.get(f)) for f in fields)
    # The books' income views fall back to net when gross is a zero placeholder.
    if not acquisition and first == 0 and second is not None:
        return second
    return first if first is not None else second


def _stats(values: Iterable[Any]) -> dict:
    numbers = [_number(v) for v in values]
    known = [v for v in numbers if v is not None]
    total = sum(known, Decimal(0))
    missing = len(numbers) - len(known)
    return {"known_total": float(total), "known_count": len(known),
            "unknown_count": missing, "total": None if missing else float(total)}


def _aggregate(rewards: list[dict], *, quantities: bool = False) -> dict:
    result = {"reward_count": len(rewards),
              "kind_counts": dict(sorted(Counter(r["kind"] for r in rewards).items()))}
    for name in _MONEY + (_QUANTITIES if quantities else ()):
        result[name] = _stats(r[name] for r in rewards)
    if quantities:
        fees: dict[str, list] = defaultdict(list)
        for reward in rewards:
            # A CAD-valued Coinbase commission is not a CAD cash quantity.
            if reward["fee_currency"] and reward["fee_quantity"] is not None:
                fees[reward["fee_currency"]].append(reward["fee_quantity"])
        result["fee_quantities"] = {cur: _stats(vals) for cur, vals in sorted(fees.items())}
        result["unknown_fee_quantity_count"] = sum(
            r["fee_quantity"] is None for r in rewards)
    return result


def _pair(account: str, tx: dict, buys: list[tuple[str, dict]],
          used: set[int]) -> tuple[dict | None, str]:
    meta = tx.get("staking_reward") or {}
    event_id = str(tx.get("id") or "")
    ids = set()
    if event_id.endswith("-div"):
        ids.add(event_id[:-4] + "-buy")
    if meta.get("source_id"):
        ids.add(str(meta["source_id"]) + "-buy")
    candidates = [(i, buy) for i, (acct, buy) in enumerate(buys)
                  if acct == account and i not in used
                  and buy.get("symbol") == tx.get("symbol")
                  and (_number(buy.get("quantity")) or Decimal(0)) > 0]
    exact = [(i, buy) for i, buy in candidates if str(buy.get("id") or "") in ids]
    if len(exact) == 1:
        i, buy = exact[0]
        used.add(i)
        return buy, "paired transaction id"
    if len(exact) > 1:
        return None, "ambiguous paired transaction id"
    # Conservative legacy fallback: a reward-labelled purchase at the same
    # timestamp and quantity, not an ordinary same-day acquisition.
    fallback = [(i, buy) for i, buy in candidates
                if buy.get("date") == tx.get("date")
                and buy.get("time") == tx.get("time")
                and _number(buy.get("quantity")) == _number(tx.get("quantity"))
                and any(word in str(buy.get("description") or "").lower()
                        for word in ("reward", "staking"))]
    if len(fallback) == 1:
        i, buy = fallback[0]
        used.add(i)
        return buy, "unique reward timestamp, symbol and quantity"
    return None, "ambiguous reward purchases" if fallback else "no paired acquisition in selected books"


def _reward(account: str, tx: dict, buy: dict | None, pairing: str,
            currency: str, account_groups: dict, local_timezone: str) -> dict:
    raw = tx.get("staking_reward")
    meta = raw if isinstance(raw, dict) else {}
    valuation = meta.get("valuation") or {}
    kind = meta.get("kind") if meta.get("kind") in _KINDS else "unclassified"
    notes = []
    income = _amount(tx)
    cost = _amount(buy, acquisition=True) if buy is not None else None
    book_currency = str(tx.get("currency") or "").upper()
    if book_currency != currency:
        notes.append("Book currency differs from the report currency; amounts are unknown.")
        income = cost = None
    if buy is not None and str(buy.get("currency") or "").upper() != currency:
        notes.append("Paired acquisition currency differs from the report currency.")
        cost = None

    quantity = _number(tx.get("quantity"))
    net_quantity = _number(meta.get("net_quantity")) if meta else quantity
    gross_quantity = _number(meta.get("gross_quantity"))
    fee_quantity = _number(meta.get("fee_quantity"))
    asset = str(meta.get("asset") or tx.get("symbol") or "unknown")
    fee_currency = str(meta.get("fee_currency") or "")
    source_currency = str(meta.get("valuation_currency") or "").upper()
    native_currency = str(valuation.get("currency") or source_currency).upper()
    native_price = _number(valuation.get("price"))
    if native_price is None and native_currency == source_currency:
        native_price = _number(meta.get("price"))
    if native_price is not None and native_price <= 0:
        native_price = None
    book_price = _number(tx.get("price"))
    if book_price is not None and book_price <= 0:
        book_price = None
    if book_currency != currency:
        book_price = None

    fx_rate = None
    fx_source = "unknown"
    stated_fx = _number(valuation.get("fx_rate"))
    if (stated_fx is not None and stated_fx > 0
            and str(valuation.get("target_currency") or "").upper() == currency):
        fx_rate, fx_source = stated_fx, "recorded book conversion rate"
    elif native_currency == currency:
        fx_rate, fx_source = Decimal(1), "same currency; no conversion"
    elif native_currency and native_price and book_price:
        fx_rate = book_price / native_price
        fx_source = "inferred from final book price / native valuation price"
    elif native_currency and native_currency == source_currency and income is not None:
        native_net = _number(meta.get("net_value"))
        if native_net is not None and native_net != 0:
            fx_rate = income / native_net
            if fx_rate <= 0:
                fx_rate = None
            else:
                fx_source = "inferred from final booked income / source net value"

    unit_value = native_price * fx_rate if native_price and fx_rate else book_price
    unit_source = (str(valuation.get("source") or "source price")
                   if native_price and fx_rate else "final book price" if book_price else "unknown")
    method = str(meta.get("commission_method") or "unknown")
    commission_known = meta.get("commission_known") is True
    same_asset_fee = bool(fee_currency and fee_currency.upper() == asset.upper())
    if unit_value is None and quantity and quantity > 0:
        if cost is not None and cost > 0:
            unit_value, unit_source = cost / quantity, "inferred from paired acquisition cost / book quantity"
        elif method == "withheld_from_reward" and income is not None and income > 0:
            unit_value, unit_source = income / quantity, "inferred from booked net reward / book quantity"

    # The chosen quote can be USD even when an export's original money
    # fields were CAD. A quote's FX is not an FX rate for every source field.
    source_fx = Decimal(1) if source_currency == currency else (
        fx_rate if source_currency == native_currency else None)
    gross = _number(meta.get("gross_value"))
    fee = _number(meta.get("fee_value"))
    gross = gross * source_fx if gross is not None and source_fx is not None else None
    fee = fee * source_fx if fee is not None and source_fx is not None else None
    gross_source = "source gross value at book FX" if gross is not None else "unknown"
    fee_source = "source commission value at book FX" if fee is not None else "unknown"
    if gross is None and gross_quantity is not None and unit_value is not None:
        gross = gross_quantity * unit_value
        gross_source = "source gross quantity at reward valuation"
    if fee is None and commission_known and fee_quantity is not None:
        if fee_quantity == 0:
            fee, fee_source = Decimal(0), "source explicitly records zero commission"
        elif same_asset_fee and unit_value is not None:
            fee, fee_source = fee_quantity * unit_value, "source commission quantity at reward valuation"
    if fee is None and commission_known and _number(meta.get("fee_value")) == 0:
        fee, fee_source = Decimal(0), "source explicitly records zero commission"
    if gross is None and fee is not None and income is not None:
        gross, gross_source = income + fee, "booked income plus known commission"
    if (fee is None and commission_known and gross is not None and income is not None
            and gross_source == "source gross value at book FX"):
        difference = gross - income
        if difference >= 0:
            fee, fee_source = difference, "inferred from reward gross value less booked income"

    source_gross_base = gross
    reconciliation = gross - fee - income if all(
        value is not None for value in (gross, fee, income)) else None
    if income is not None and fee is not None:
        gross = income + fee
        gross_source = "recorded income plus valued commission; same book valuation"
    expected_cost = quantity * unit_value if quantity is not None and unit_value is not None else None
    source_gross_value = _number(meta.get("gross_value"))
    if (method == "paid_in_other_asset" and source_gross_value is not None
            and source_fx is not None and gross_quantity and quantity is not None):
        # A net-income-derived book price cannot independently validate the
        # cost of coins retained in full after a fee in a different asset.
        expected_cost = source_gross_value * source_fx * quantity / gross_quantity
    cost_difference = cost - expected_cost if cost is not None and expected_cost is not None else None
    if cost_difference is not None and abs(cost_difference) > Decimal("0.01"):
        notes.append("Acquisition cost differs from reward quantity at the recorded valuation; books are unchanged.")
    if reconciliation is not None and abs(reconciliation) > Decimal("0.01"):
        notes.append("Source gross less commission differs from recorded income; books are unchanged.")
    if not meta:
        notes.append("Legacy crypto income has no structured reward evidence; kind and commission are unknown.")
    elif not commission_known:
        notes.append("Source commission is unknown; it has not been assumed to be zero.")
    valuation_missing = bool(quantity and quantity != 0 and unit_value is None)
    if valuation_missing:
        notes.append("No usable reward valuation is present in the selected books.")
    return {
        "account": account, "account_group": account_groups.get(account) or "unknown",
        "broker": str(meta.get("broker") or tx.get("broker") or "unknown"),
        "symbol": str(tx.get("symbol") or "unknown"), "asset": asset, "kind": kind,
        "date": str(tx.get("date") or ""), "time": str(tx.get("time") or ""),
        "local_timezone": local_timezone, "transaction_id": str(tx.get("id") or ""),
        "source": {"file": str(meta.get("source_file") or tx.get("source") or ""),
                   "line": meta.get("source_line"), "timestamp": str(meta.get("source_timestamp") or ""),
                   "id": str(meta.get("source_id") or ""), "type": str(meta.get("source_type") or "")},
        "source_evidence": dict(meta), "has_source_evidence": bool(meta),
        "gross_quantity": _float(gross_quantity), "net_quantity": _float(net_quantity),
        "book_quantity": _float(quantity), "fee_quantity": _float(fee_quantity),
        "fee_currency": fee_currency, "commission_method": method,
        "commission_known": commission_known, "currency": currency,
        "gross_amount": _float(gross), "commission_amount": _float(fee),
        "source_gross_base": _float(source_gross_base),
        "income_amount": _float(income), "acquisition_cost": _float(cost),
        "gross_provenance": gross_source, "commission_provenance": fee_source,
        "native_price": _float(native_price), "native_currency": native_currency,
        "book_price": _float(book_price), "valuation_price_base": _float(unit_value),
        "valuation_source": unit_source, "valuation_date": str(valuation.get("date") or ""),
        "valuation_market_symbol": str(valuation.get("market_symbol") or ""),
        "fx_rate": _float(fx_rate), "fx_provenance": fx_source,
        "source_value_fx_rate": _float(source_fx),
        "fx_date": str(valuation.get("fx_date") or ""),
        "fx_source": str(valuation.get("fx_source") or "unknown"),
        "acquisition_transaction_id": str(buy.get("id") or "") if buy else "",
        "acquisition_pairing": pairing, "expected_acquisition_cost": _float(expected_cost),
        "acquisition_difference": _float(cost_difference),
        "income_reconciliation_difference": _float(reconciliation),
        "valuation_missing": valuation_missing, "notes": notes,
    }


def build_report(rows: Iterable[tuple[str, dict]], *, currency: str,
                 scope: str = "", country: str = "canada",
                 account_groups: dict | None = None,
                 local_timezone: str = "America/Toronto") -> dict:
    """Build a JSON-safe disclosure from selected *final base-currency* rows.

    The caller selects crypto accounts, date window and any broker/symbol
    filters. Keep acquisition rows available for pairing. Unknown amounts
    are None; every monetary aggregate reports its known/unknown coverage.
    """
    rows = list(rows)
    currency = currency.upper()
    country = canonical_country(country)
    buys = [(account, tx) for account, tx in rows if tx.get("action") == "BUYSELL"]
    used: set[int] = set()
    rewards = []
    for account, tx in rows:
        if tx.get("action") != "DIVIDEND":
            continue
        buy, pairing = _pair(account, tx, buys, used)
        rewards.append(_reward(account, tx, buy, pairing, currency,
                               account_groups or {}, local_timezone))
    rewards.sort(key=lambda r: (r["date"], r["time"], r["account"], r["symbol"], r["transaction_id"]))
    buckets: dict[tuple, list] = defaultdict(list)
    for reward in rewards:
        buckets[(reward["broker"], reward["symbol"], reward["account_group"], reward["asset"])].append(reward)
    groups = []
    for (broker, symbol, group, asset), entries in sorted(buckets.items()):
        groups.append({"broker": broker, "symbol": symbol, "asset": asset, "account_group": group,
                       "accounts": sorted({r["account"] for r in entries}),
                       **_aggregate(entries, quantities=True)})
    by_scope: dict[str, list] = {"taxable": [], "sheltered": [], "unknown": []}
    for reward in rewards:
        by_scope.setdefault(reward["account_group"], []).append(reward)
    totals_by_account_group = {group: _aggregate(entries) for group, entries in by_scope.items()}
    coverage = {"source_evidence": sum(r["has_source_evidence"] for r in rewards),
                "unclassified": sum(r["kind"] == "unclassified" for r in rewards),
                "unknown_commission": sum(r["commission_amount"] is None for r in rewards),
                "unknown_gross": sum(r["gross_amount"] is None for r in rewards),
                "missing_valuation": sum(r["valuation_missing"] for r in rewards),
                "paired_acquisitions": sum(r["acquisition_cost"] is not None for r in rewards),
                "acquisition_discrepancies": sum(abs(r["acquisition_difference"] or 0) > .01 for r in rewards),
                "income_discrepancies": sum(abs(r["income_reconciliation_difference"] or 0) > .01 for r in rewards)}
    rule = "US-INC-STAKING" if country == "usa" else "CA-INC-STAKING"
    notes = [
        "Amounts disclose the existing books; this view does not change income, fees or acquisition cost.",
        "Totals cover booked reward events; fully consumed or omitted dust rewards have no entries here.",
        "Recorded income already reflects commissions netted by the importer; do not deduct those commissions again.",
        "Gross and commission totals with missing evidence are known subtotals, not complete totals.",
        "Quantities are per asset. Commission quantities in another asset are shown separately and never summed with reward units.",
        "An absent acquisition can be intentional for rewards treated as cash; it does not prove a missing cost basis.",
        "Source timestamps retain their original timezone; the book date/time use the project's configured local timezone.",
        "Display cents are allocated within each money column to foot to its aggregate; JSON retains unrounded book values.",
        f"Software treatment: {rule}; see tjs tax-logic --ids for assumptions and country-specific authorities.",
    ]
    return {"schema_version": 1, "currency": currency, "scope": scope, "country": country,
            "local_timezone": local_timezone, "rule": rule, "rewards": rewards, "groups": groups,
            "totals": _aggregate(rewards), "totals_by_account_group": totals_by_account_group,
            "coverage": coverage, "notes": notes}


def _money(value: Any) -> str:
    number = _number(value)
    return "?" if number is None else fmt_money(number.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _precise(value: Any) -> str:
    """Saved numeric precision for source evidence, without a float recast."""
    number = _number(value)
    if number is None:
        return "?"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _quantity(value: Any) -> str:
    if value is None:
        return "?"
    # Preserve a real tiny reward that the standard six-decimal view would
    # render as zero. Detailed JSON always retains the exact numeric value.
    number = _number(value)
    if number and abs(number) < Decimal("0.000001"):
        return f"{float(number):.8g}"
    return fmt_qty6(value)


def _display_column(values: list[Any]) -> list[str]:
    """Allocate rounding cents so the displayed rows foot to the exact sum."""
    raw = [_number(v) for v in values]
    rounded = [v.quantize(Decimal(".01"), rounding=ROUND_HALF_UP) if v is not None else None for v in raw]
    known = [i for i, value in enumerate(raw) if value is not None]
    if known:
        target = sum((raw[i] for i in known), Decimal(0)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        difference = int((target - sum((rounded[i] for i in known), Decimal(0))) * 100)
        order = sorted(known, key=lambda i: raw[i] - rounded[i], reverse=difference > 0)
        for offset in range(abs(difference)):
            rounded[order[offset % len(order)]] += Decimal(".01") if difference > 0 else Decimal("-.01")
    return [_money(value) for value in rounded]


def render(report: dict, *, summary: bool = False, details: bool = False) -> list[str]:
    """Render concise rows or broker/asset/tax-scope groups via ``out``."""
    currency = report["currency"]
    command = "tjs staking-sum" if summary else "tjs staking"
    title = "CRYPTO REWARD SUMMARY" if summary else "CRYPTO REWARDS"
    doc = out.Doc(f"{title} — {currency}" + (f", {report['scope']}" if report.get("scope") else ""))
    doc.para("GROSS = NET INCOME + COMMISSION at book valuation (before rounding). COST is acquisition cost.")
    doc.para("? unknown; * known subtotal. S staking; O other; U unclassified. Quantities are per asset.")
    entries = report["groups"] if summary else report["rewards"]
    columns = {}
    for name in _MONEY:
        values = [entry[name]["known_total"] if summary and entry[name]["known_count"]
                  else None if summary else entry[name] for entry in entries]
        columns[name] = _display_column(values)
    body = []
    if summary:
        headers = ["BROKER", "ASSET", "SCOPE", "KINDS", "COUNT", "GROSS", "COMMISSION", "NET INCOME", "COST"]
        for i, group in enumerate(entries):
            money = [columns[name][i] + ("*" if group[name]["unknown_count"] and group[name]["known_count"] else "")
                     for name in _MONEY]
            kinds = " ".join(f"{label}{group['kind_counts'][kind]}"
                             for kind, label in (("staking", "S"), ("other_reward", "O"), ("unclassified", "U"))
                             if group["kind_counts"].get(kind))
            asset = group["asset"] if group["asset"] == group["symbol"] else f"{group['asset']} ({group['symbol']})"
            body.append([group["broker"], asset, group["account_group"], kinds,
                         str(group["reward_count"]), *money])
    else:
        headers = ["DATE", "BROKER", "ASSET", "SCOPE", "KIND", "NET UNITS", "GROSS", "COMMISSION", "NET INCOME", "COST"]
        for i, reward in enumerate(entries):
            kind = {"staking": "S", "other_reward": "O", "unclassified": "U"}[reward["kind"]]
            body.append([reward["date"], reward["broker"], reward["symbol"], reward["account_group"],
                         kind, _quantity(reward["net_quantity"]),
                         *[columns[name][i] for name in _MONEY]])
    if body:
        totals = report["totals"]
        total_money = [_money(totals[name]["known_total"]) + ("*" if totals[name]["unknown_count"] else "")
                       if totals[name]["known_count"] else "?" for name in _MONEY]
        present_scopes = [group for group, stat in report["totals_by_account_group"].items() if stat["reward_count"]]
        total_scope = "all scopes" if len(present_scopes) > 1 else present_scopes[0]
        body.append((["TOTAL", "", total_scope, "", str(totals["reward_count"])] if summary
                     else ["TOTAL", "", "", total_scope, "", "—"]) + total_money)
        doc.table(headers, body, key=(0, 1), drop=())
        if len(present_scopes) > 1:
            doc.section("INCOME BY ACCOUNT SCOPE")
            scope_totals = list(report["totals_by_account_group"].items())
            displayed = _display_column([stat["income_amount"]["known_total"]
                                         if stat["income_amount"]["known_count"] or not stat["reward_count"]
                                         else None for _, stat in scope_totals])
            scope_rows = []
            for (group, stat), amount in zip(scope_totals, displayed):
                income_stat = stat["income_amount"]
                if income_stat["unknown_count"] and income_stat["known_count"]:
                    amount += "*"
                scope_rows.append([group, str(stat["reward_count"]), amount,
                                   f"{income_stat['known_count']} / {stat['reward_count']}"])
            doc.table(["ACCOUNT SCOPE", "COUNT", "NET INCOME", "KNOWN / EVENTS"], scope_rows)
    else:
        doc.para("No crypto rewards in the selected final books.")
    doc.line(f"Commissions already netted in recorded income must not be deducted again — {command} --details")
    coverage = report["coverage"]
    if coverage["unknown_commission"] or coverage["unclassified"] or coverage["unknown_gross"]:
        doc.line(f"Unknown: gross {coverage['unknown_gross']}, commission {coverage['unknown_commission']}; "
                 f"unclassified {coverage['unclassified']} of {report['totals']['reward_count']} — {command} --details")
    if coverage["missing_valuation"] or coverage["acquisition_discrepancies"] or coverage["income_discrepancies"]:
        doc.line(out.act(f"Valuation gaps {coverage['missing_valuation']}; value mismatches "
                         f"{coverage['acquisition_discrepancies'] + coverage['income_discrepancies']}",
                         f"{command} --details"))
    if details:
        doc.section("REWARD EVIDENCE")
        for reward in report["rewards"]:
            doc.blank().para(f"{reward['date']} {reward['time']} {reward['broker']}/{reward['account']} "
                             f"{reward['symbol']} ({_KINDS[reward['kind']]})")
            source = reward["source"]
            fee_description = (f"Coin quantity not supplied; commission valued in {reward['fee_currency'] or '?'}"
                               if reward["fee_quantity"] is None and reward["source_evidence"].get("fee_value") is not None
                               else f"{_precise(reward['fee_quantity'])} {reward['fee_currency'] or '?'}")
            fee_description += "; " + reward["commission_method"].replace("_", " ")
            valuation_description = (f"{reward['valuation_source']}; date {reward['valuation_date'] or '?'}"
                                     + (f"; market symbol {reward['valuation_market_symbol']}"
                                        if reward["valuation_market_symbol"] else ""))
            doc.kv([
                ("Book time", f"{reward['date']} {reward['time']} {reward['local_timezone']}"),
                ("Source timestamp", source["timestamp"] or "unknown"),
                ("Source", (f"{source['file'] or 'unknown file'}; line {source['line'] if source['line'] is not None else '?'}; "
                 f"id {source['id'] or '?'}; type {source['type'] or '?'}")),
                ("Book transaction", reward["transaction_id"] or "unknown"),
                ("Account scope", reward["account_group"]),
                ("Gross / net units", f"{_precise(reward['gross_quantity'])} / {_precise(reward['net_quantity'])} {reward['asset']}"),
                ("Commission units", fee_description),
                ("Native price", f"{_precise(reward['native_price'])} {reward['native_currency'] or '?'} / unit"),
                ("Final book price", f"{_precise(reward['book_price'])} {currency} / unit"),
                ("Valuation", valuation_description),
                ("FX to base", (f"{_precise(reward['fx_rate'])} {currency}/{reward['native_currency'] or '?'}; "
                 f"{reward['fx_provenance']}; {reward['fx_source'].replace('_', ' ')}; "
                 f"rate date {reward['fx_date'] or 'not recorded or undated'}")),
                ("Source gross / fee / net", " / ".join(
                    _precise(reward["source_evidence"].get(name))
                    for name in ("gross_value", "fee_value", "net_value"))
                 + f" {reward['source_evidence'].get('valuation_currency') or '?'}"),
                ("Source gross at book FX", f"{_money(reward['source_gross_base'])} {currency}"),
                ("Gross", f"{_money(reward['gross_amount'])} {currency}; {reward['gross_provenance']}"),
                ("Commission", f"{_money(reward['commission_amount'])} {currency}; {reward['commission_provenance']}"),
                ("Recorded income", f"{_money(reward['income_amount'])} {currency}"),
                ("Acquisition cost", (f"{_money(reward['acquisition_cost'])} {currency}; {reward['acquisition_pairing']}; "
                 f"id {reward['acquisition_transaction_id'] or '?'}")),
                ("Cost / income differences", (f"{_money(reward['acquisition_difference'])} / "
                 f"{_money(reward['income_reconciliation_difference'])} {currency}")),
            ])
            for note in reward["notes"]:
                doc.para(note)
        doc.section("GROUP QUANTITIES")
        for group in report["groups"]:
            quantities = []
            for name in _QUANTITIES:
                stat = group[name]
                amount = _quantity(stat["known_total"]) if stat["known_count"] else "?"
                quantities.append(f"{name.removesuffix('_quantity')} {amount} "
                                  f"({stat['known_count']}/{group['reward_count']} known)")
            fees = ", ".join(f"{_quantity(stat['known_total'])} {asset}" for asset, stat in group["fee_quantities"].items()) or "unknown"
            doc.para(f"{group['broker']} / {group['asset']} (book {group['symbol']}) / {group['account_group']}: "
                     f"{'; '.join(quantities)}; commission units {fees}. "
                     f"Kinds: {', '.join(f'{kind} {count}' for kind, count in group['kind_counts'].items())}.")
        doc.section("METHOD AND COVERAGE")
        for name in _MONEY:
            stat = report["totals"][name]
            doc.kv([(name.replace("_", " ").capitalize(),
                     (f"{stat['known_count']} known, {stat['unknown_count']} unknown; known subtotal "
                     f"{_money(stat['known_total'])} {currency}"))])
        for note in report["notes"]:
            doc.para(note)
    return doc.lines()
