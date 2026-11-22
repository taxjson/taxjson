"""work/<account>_report.json, the machine twin of the text reports
(`tjs run` writes it after the .sum stage): the gains and income
summaries over one account's books. Above lib/gain_summary and
lib/income_summary; out of lib/report_model, which the modules under
them import (step 8 of the architecture plan: lib's import cycle)."""

from datetime import datetime
from typing import Optional

from taxjson.lib.gain_summary import summarize_gains
from taxjson.lib.income_summary import capitalized_pil_shares, summarize_income
from taxjson.model import money as M


def build_account_report(gains_data, account: str,
                         basis: Optional[str] = None,
                         base_transactions=None, rules=None) -> dict:
    """The structured dict work/<account>_report.json carries — the machine
    twin of the text reports, written by `tjs run` after the .sum stage.

    Schema (schema_version 1):
      account         str — account name
      generated_at    ISO timestamp
      year            the gains run's tax year (or 'all')
      gains           gain_summary.summarize_gains(gains_data) output:
                      {ticker_stats, returns_by_asset, total_fees,
                       option_fees, target_year, ...}
      income          income_summary.summarize_income(transactions, year)
      wash            {count, total_disallowed} from the gains run's
                      wash_sales records

    ADDITIVE artifact: the .sum text pipeline is unchanged; consumers
    (`tjs sum` today; future exports) read this instead of
    re-deriving the same aggregates from the raw gains JSON."""
    year = (gains_data.get("summary") or {}).get("year")
    washes = gains_data.get("wash_sales") or []

    def _amt(w):
        # 'disallowed_amount' is the field BOTH engines emit on a wash
        # record; the US engine emits nothing else, so reading only
        # 'amount' reported total_disallowed 0 for every US project
        # (audit S077-14). The old keys stay as a fallback.
        v = w.get("disallowed_amount")
        if v is None:
            v = (w.get("amount")
                 or (w.get("loss_tx") or {}).get("amount", 0))
        return float(v or 0)

    return {
        "schema_version": 1,
        "account": account,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        # Which gains basis the aggregates were computed from
        # ("pre-wash" | "wash-adjusted") — `tjs sum`'s fast path
        # serves this file only when it matches the basis it resolved,
        # since after `run --account X` the report is freshly pre-wash
        # while the stale wash file still wins resolve_gains_files.
        "basis": basis,
        "year": year,
        "gains": summarize_gains(gains_data),
        # Income comes from the BASE book: the gains file's income
        # entries carry `dividend`/`pil` fields (not gross/net_amount)
        # and no TAX/INTEREST rows at all, so summarizing them here
        # produced an all-zero income section — the machine twin
        # disagreed with its own text twin (2026-09 audit).
        "income": summarize_income(
            (base_transactions if base_transactions is not None
             else gains_data.get("transactions", [])),
            target_year=year, rules=rules,
            capitalized=capitalized_pil_shares(
                gains_data.get("transactions", []), f"{account}'s gains")),
        "wash": {"count": len(washes),
                 "total_disallowed": M.shown(sum(_amt(w) for w in washes), 2)},
    }
