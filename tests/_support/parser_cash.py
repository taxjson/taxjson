"""Shared test helpers: the cash events a broker's parse builds of an export.

A broker whose parse builds its cash events (RBC since PLAN step 16 commit 7,
Coinbase since commit 8, Questrade since commit 9, IB since commit 10, Kraken
since commit 11, when the last reader went: the parser's `note_cash` rows and
its finish, `cash_events`) has no reader to call. A test that wants one
export's events, or the sidecar a run writes beside books the test built by
hand, parses the file as taxjson-brokerage does (a fresh parser, the
country's parse settings, the export's siblings found by the run's
detection: a Kraken ledger's other ledgers), its notes on stderr swallowed. A
cash cell the parse could not read (the parser's `cash_problem`) is the
sidecar's problem of that file, and a ValueError of `parsed_cash_events`, as
the reader's refusal was. A broker without cash events (Webull, the generic
importer) gets none from `cash_sidecar`.

Shared from: rules/ca/fx/test_foreign_cash_ledger_v2, _support/fx_cash_run,
features/summaries/test_cash_commands_refuse_old_or_missing_events (PLAN step
16 commit 7); the Coinbase cash tests (commit 8): features/parsers'
test_coinbase_blank_total, test_coinbase_execution_ids_and_price_candle_day
and test_coinbase_fees_and_kraken_cross_export_legs, rules/ca/fx's
test_coinbase_stablecoin_cash_legs and
test_crypto_sends_and_cash_readers_on_the_fx_ledger; the Questrade cash
tests (commit 9): rules/ca/fx's test_foreign_cash_inputs_read_or_refused and
test_foreign_cash_carry_and_damaged_inputs, and taxjson-fetch's
test_fix_1029_r7f_misc; the IB cash tests (commit 10): features/summaries'
test_untimed_cash_events_against_the_clock, features/parsers'
test_ib_retained_fees_and_commission_refunds, rules/ca/fx's
test_ib_forex_rows_and_refund_dates, test_fx_cash_ledger_v2_and_crypto_sends
and test_foreign_cash_inputs_read_or_refused; the Kraken cash tests (commit
11): features/parsers' test_kraken_ledger_rebates_and_repeats,
features/summaries' test_kraken_cash_reader, rules/ca/fx's
test_foreign_cash_ledger_v2, test_fx_cash_ledger_v2_and_crypto_sends,
test_crypto_sends_and_cash_readers_on_the_fx_ledger and
test_foreign_cash_wallets_rewards_and_move_order, rules/ca/rpt's
test_t1135_cash_rows_of_crypto_and_custodian_accounts and
test_fx_on_cash_ledgers_t1135_cash_and_coinbase_hours.
"""
import contextlib
import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def parsed(broker: str, path: Path, country: Optional[str] = "canada") -> Any:
    """`broker`'s parser (a parser id the registry marks `builds_cash`)
    after its parse of the export `path` at `country`'s basis: its
    `cash_events` and `cash_problem`. The export's siblings are found as
    taxjson-brokerage finds them (`sibling_finder`)."""
    from taxjson.lib.brokerages.detect import same_broker_siblings
    from taxjson.parsers.registry import parser_class
    from taxjson.parsers.settings import ParseSettings
    parser = parser_class(broker)()
    parser.configure(ParseSettings.for_country(country))
    parser.sibling_finder = same_broker_siblings
    with contextlib.redirect_stderr(io.StringIO()):
        parser.parse_file(Path(path))
    assert parser.cash_events is not None, f"{broker}'s parse builds no cash events"
    return parser


def parsed_cash_events(broker: str, path: Path, country: Optional[str] = "canada"
                       ) -> List[Dict[str, Any]]:
    """The cash events `broker`'s parse builds of the export `path`, its
    parser's `cash_events`; a ValueError naming its `cash_problem` when it
    could not build them (the sidecar's problem of the file)."""
    parser = parsed(broker, path, country)
    if parser.cash_problem:
        raise ValueError(parser.cash_problem)
    return parser.cash_events


def cash_sidecar(broker: str, account: str, paths: Sequence[Path], country: str
                 ) -> Dict[str, Any]:
    """The sidecar taxjson-brokerage --cash-out writes for one broker
    group's exports (lib/cash_events `cash_sidecar`), the events of a broker
    whose parse builds them its parse's (`parsed`), or the file's problem."""
    from taxjson.lib import cash_events as CE
    from taxjson.parsers.registry import cash_event_brokers
    parsers = ({str(Path(p)): parsed(broker, p, country) for p in paths}
               if broker in cash_event_brokers() else {})
    return CE.cash_sidecar(
        broker, account, [Path(p) for p in paths], country,
        built={k: ex.cash_events for k, ex in parsers.items()},
        problems={k: ex.cash_problem for k, ex in parsers.items() if ex.cash_problem})
