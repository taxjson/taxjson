"""taxjson fetch — broker auto-fetch (offline: injected HTTP)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from taxjson.bin.taxjson_fetch import (flex_fetch, looks_like_ib_statement,
                                       qt_activities, qt_refresh, qt_to_csv,
                                       qt_window)

REPO_ROOT = Path(__file__).resolve().parent.parent

# tradeDate and transactionDate DIFFER on purpose: the API's
# transactionDate is the posting (settlement) date; the export column
# "Transaction Date" is the trade date. The CSV must use tradeDate.
_ACT = {"tradeDate": "2026-08-10T00:00:00.000000-04:00",
        "transactionDate": "2026-08-11T00:00:00.000000-04:00",
        "settlementDate": "2026-08-11T00:00:00.000000-04:00",
        "action": "Buy", "symbol": "XEI.TO", "symbolId": 123,
        "description": "ISHARES SP TSX COMP HIGH DIV",
        "currency": "CAD", "quantity": 100, "price": 10.0,
        "grossAmount": -1000.0, "commission": -4.95,
        "netAmount": -1004.95, "type": "Trades"}


class TestQuestradeSession(unittest.TestCase):
    def test_refresh_returns_rotated_token(self):
        def http(url):
            self.assertIn("refresh_token=OLD", url)
            return json.dumps({"api_server": "https://api.q.com/",
                               "access_token": "AT",
                               "refresh_token": "NEW"}).encode()
        sess = qt_refresh("OLD", http)
        self.assertEqual(sess["refresh_token"], "NEW")

    def test_refresh_missing_fields_is_loud(self):
        with self.assertRaises(RuntimeError) as cm:
            qt_refresh("OLD", lambda u: b'{"access_token": "AT"}')
        self.assertIn("api_server", str(cm.exception))

    def test_activities_chunked_within_31_day_cap(self):
        calls = []

        def http(url):
            calls.append(url)
            # A DISTINCT activity per chunk (identical ones would be
            # cross-chunk-deduped, which is its own test below).
            return json.dumps({"activities": [
                dict(_ACT, symbol=f"S{len(calls)}.TO")]}).encode()
        sess = {"api_server": "https://api.q.com/", "access_token": "AT"}
        acts = qt_activities(sess, "123", date(2026, 1, 1),
                             date(2026, 3, 15), http)
        # 74 days -> 3 chunks, none longer than 31 days.
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(acts), 3)
        for u in calls:
            self.assertIn("/v1/accounts/123/activities", u)


class TestQuestradeCsv(unittest.TestCase):
    def test_csv_round_trips_through_the_real_parser(self):
        # The whole point: the generated file must parse with the
        # EXISTING Questrade parser, no changes.
        import os
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        text = qt_to_csv([dict(_ACT)], "12345678")
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write(text)
            name = f.name
        try:
            txs = QuestradeBrokerage().parse_file(Path(name))
        finally:
            os.remove(name)
        t = next(t for t in txs if t["action"] == "BUYSELL")
        self.assertEqual(t["symbol"], "XEI.TO")
        self.assertEqual(t["quantity"], 100)
        self.assertAlmostEqual(t["price"], 10.0)
        self.assertEqual(t["date"], "2026-08-10",
                         "date must be the TRADE date (tradeDate), "
                         "not the API's posting-date transactionDate")
        self.assertEqual(t["date_settle"], "2026-08-11")

    def test_description_whitespace_collapsed_for_dedup(self):
        # The API pads descriptions ("INC  CASH DIV  ON     500 SHS")
        # where the manual export single-spaces them — and description
        # is part of the row's content-hash id, so without collapsing,
        # a manually exported copy of the same activity never dedups.
        text = qt_to_csv([dict(_ACT, description="TC ENERGY CORP  COM  "
                               "WE ACTED AS AGENT")], "1")
        self.assertIn("TC ENERGY CORP COM WE ACTED AS AGENT", text)
        self.assertNotIn("CORP  COM", text)

    def test_rows_sorted_and_byte_stable(self):
        a2 = dict(_ACT, transactionDate="2026-08-01T00:00:00-04:00",
                  symbol="AAA.TO")
        self.assertEqual(qt_to_csv([dict(_ACT), a2], "1"),
                         qt_to_csv([a2, dict(_ACT)], "1"))


class TestQtWindow(unittest.TestCase):
    def test_default_covers_the_tax_year_window_every_time(self):
        # The pipeline is tax-year scoped — every fetch re-covers the
        # whole window (like a manual YTD export), starting mid-Dec of
        # the PRIOR year: a late-December trade settles in January and
        # belongs to the new year under settle-date rules, so a hard
        # Jan 1 start would miss it at the boundary.
        s, e = qt_window(None, None, today=date(2026, 8, 20),
                         year=2026)
        self.assertEqual(s.isoformat(), "2025-12-15")
        self.assertEqual(e.isoformat(), "2026-08-20")

    def test_prior_tax_year_covered_in_filing_season(self):
        # Fetching in Feb 2027 for year=2026 still spans the whole
        # 2026 window (plus its own boundary margin).
        s, _e = qt_window(None, None, today=date(2027, 2, 10),
                          year=2026)
        self.assertEqual(s.isoformat(), "2025-12-15")

    def test_without_year_falls_back_90_days(self):
        s, e = qt_window(None, None, today=date(2026, 8, 20))
        self.assertEqual((e - s).days, 90)

    def test_explicit_from_wins(self):
        s, _e = qt_window(None, "2026-06-01", today=date(2026, 8, 20),
                          year=2026)
        self.assertEqual(s.isoformat(), "2026-06-01")

    def test_explicit_days_wins(self):
        s, _e = qt_window(30, None, today=date(2026, 8, 20), year=2026)
        self.assertEqual(s.isoformat(), "2026-07-21")


class TestFlex(unittest.TestCase):
    def test_two_step_flow_with_poll(self):
        stmt = ('"Statement","Header","Field Name","Field Value"\n'
                '"Trades","Header","DataDiscriminator"\n')
        calls = []

        def http(url):
            calls.append(url)
            if "SendRequest" in url:
                return (b"<FlexStatementResponse><Status>Success"
                        b"</Status><ReferenceCode>REF1</ReferenceCode>"
                        b"</FlexStatementResponse>")
            if len(calls) == 2:
                return (b"<FlexStatementResponse><ErrorCode>1019"
                        b"</ErrorCode><ErrorMessage>generating"
                        b"</ErrorMessage></FlexStatementResponse>")
            return stmt.encode()
        raw = flex_fetch("TOK", "42", http, sleep=lambda s: None)
        self.assertEqual(raw.decode(), stmt)
        self.assertEqual(len(calls), 3)          # send, poll, done

    def test_error_message_surfaces(self):
        def http(url):
            return (b"<FlexStatementResponse><ErrorCode>1012"
                    b"</ErrorCode><ErrorMessage>Token expired"
                    b"</ErrorMessage></FlexStatementResponse>")
        with self.assertRaises(RuntimeError) as cm:
            flex_fetch("TOK", "42", http, sleep=lambda s: None)
        self.assertIn("Token expired", str(cm.exception))

    def test_statement_shape_detector(self):
        self.assertTrue(looks_like_ib_statement(
            'Statement,Header,Field Name,Field Value\n'))
        self.assertTrue(looks_like_ib_statement(
            '"Trades","Header","DataDiscriminator"\n'))
        self.assertFalse(looks_like_ib_statement(
            "ClientAccountID,CurrencyPrimary\nU1,USD\n"))


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestFetchCli(unittest.TestCase):
    def test_no_fetch_config_is_a_clean_error_with_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            r = _cli(root, "fetch")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('brokerage = "questrade"', r.stderr)
        self.assertIn("QUESTRADE_REFRESH_TOKEN", r.stderr)

    def test_unknown_account_named_on_cli_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                'brokerage = "questrade"\naccount = "1"\n')
            r = _cli(root, "fetch", "ghost")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no [accounts.ghost]", r.stderr)

    def test_missing_token_is_a_clean_error(self):
        import os
        env = {k: v for k, v in os.environ.items()
               if k != "QUESTRADE_REFRESH_TOKEN"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Hermetic token resolution: without this, a developer's
            # real shared ~/.questrade_token (which takes precedence
            # over the project file) is FOUND, and the test
            # authenticates against the live Questrade API — burning a
            # rotation of a real credential to test an error message.
            env["QUESTRADE_TOKEN_FILE"] = str(root / "no_such_token")
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                'brokerage = "questrade"\naccount = "12345678"\n')
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run",
                 "-C", str(root), "fetch"],
                cwd=REPO_ROOT, capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refresh token", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_fetch_config_does_not_warn_as_unknown(self):
        # [fetch.*] must not trip validate_config's unknown-key warnings
        # on a normal run.
        from taxjson.bin.taxjson_run import validate_config
        warnings = validate_config(
            {"settings": {"year": 2026},
             "accounts": {"margin": {"type": "taxable"}},
             "fetch": {"margin": {"source": "questrade",
                                  "number": "1"}}})
        self.assertFalse([w for w in warnings if "fetch" in w], warnings)


class TestMergeCsvText(unittest.TestCase):
    def test_union_merge_dedups_and_counts_new(self):
        from taxjson.bin.taxjson_run import _merge_csv_text
        hdr = "A,B\n"
        merged, added = _merge_csv_text(hdr + "1,2\n2,3\n",
                                        hdr + "2,3\n3,4\n")
        self.assertEqual(added, 1)
        self.assertEqual(merged, "A,B\n1,2\n2,3\n3,4\n")

    def test_header_mismatch_refuses(self):
        from taxjson.bin.taxjson_run import _merge_csv_text
        with self.assertRaises(ValueError):
            _merge_csv_text("A,B\n1,2\n", "A,B,C\n1,2,3\n")

    def test_empty_existing_takes_new(self):
        from taxjson.bin.taxjson_run import _merge_csv_text
        merged, added = _merge_csv_text("", "A,B\n1,2\n")
        self.assertEqual(added, 1)
        self.assertEqual(merged, "A,B\n1,2\n")




class TestMergeCsvVerifiedBugs(unittest.TestCase):
    """Fixes from the post-build adversarial pass."""

    def test_multiline_quoted_rows_merge_as_logical_rows(self):
        # csv.writer quotes embedded newlines into multi-line rows; a
        # physical-line merge interleaved fragments of two rows into
        # data that PARSED without error but was garbage.
        from taxjson.bin.taxjson_run import _merge_csv_text
        import csv as _csv
        import io as _io

        def mk(rows):
            b = _io.StringIO()
            w = _csv.writer(b, lineterminator="\n")
            w.writerow(["A", "B"])
            w.writerows(rows)
            return b.getvalue()
        one = mk([["x\nline2", "1"]])
        two = mk([["x\nline2", "1"], ["y\nother", "2"]])
        merged, added = _merge_csv_text(one, two)
        self.assertEqual(added, 1)
        parsed = list(_csv.reader(_io.StringIO(merged)))
        self.assertEqual(parsed[0], ["A", "B"])
        self.assertIn(["x\nline2", "1"], parsed)
        self.assertIn(["y\nother", "2"], parsed)
        self.assertEqual(len(parsed), 3)

    def test_byte_identical_split_fills_survive_refetch(self):
        # Two physically distinct fills can serialize byte-identically
        # (API dates are midnight) — set-union deleted one on the
        # overlap re-fetch. Multiset merge keeps the max count seen.
        from taxjson.bin.taxjson_run import _merge_csv_text
        hdr = "A,B\n"
        both = hdr + "fill,100\nfill,100\n"
        merged, added = _merge_csv_text(both, both)
        self.assertEqual(added, 0)
        self.assertEqual(merged.count("fill,100"), 2)

    def test_unpadded_from_date_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                'brokerage = "questrade"\naccount = "12345678"\n')
            r = _cli(root, "fetch", "--from", "2026-8-1")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("2026-8-1", r.stderr)

    def test_flex_unrelated_1019_in_message_does_not_poll(self):
        calls = []

        def http(url):
            calls.append(url)
            if "SendRequest" in url:
                return (b"<FlexStatementResponse><ReferenceCode>R"
                        b"</ReferenceCode></FlexStatementResponse>")
            return (b"<FlexStatementResponse><ErrorCode>1012"
                    b"</ErrorCode><ErrorMessage>bad token id 1019x"
                    b"</ErrorMessage></FlexStatementResponse>")
        with self.assertRaises(RuntimeError):
            flex_fetch("T", "1", http, sleep=lambda s: None)
        self.assertEqual(len(calls), 2)          # no poll loop


class TestAccountLevelFetchConfig(unittest.TestCase):
    """Fetch config lives on the account itself (brokerage + account /
    query_id keys); a stray standalone [fetch.*] table is ignored."""

    def test_account_keys_do_not_warn_and_resolve(self):
        from taxjson.bin.taxjson_run import (_fetch_sources,
                                             validate_config)
        cfg = {"settings": {"year": 2026},
               "accounts": {"margin": {"type": "taxable",
                                       "brokerage": "questrade",
                                       "account": 12345678},
                            "ibkr": {"type": "taxable",
                                     "brokerage": "ibkr_flex",
                                     "query_id": "42"}}}
        self.assertEqual(validate_config(cfg), [])
        src = _fetch_sources(cfg)
        self.assertEqual(src["margin"]["source"], "questrade")
        self.assertEqual(src["margin"]["number"], "12345678")  # int OK
        self.assertEqual(src["ibkr"]["query_id"], "42")

    def test_unknown_brokerage_warns_with_suggestion(self):
        from taxjson.bin.taxjson_run import validate_config
        w = validate_config(
            {"settings": {"year": 2026},
             "accounts": {"m": {"type": "taxable",
                                "brokerage": "questrde"}}})
        self.assertTrue(any("questrade" in x for x in w), w)

    def test_stray_fetch_table_is_simply_ignored(self):
        # [fetch.*] was never adopted — no aliasing, no deprecation
        # machinery; a stray table contributes nothing.
        from taxjson.bin.taxjson_run import _fetch_sources
        src = _fetch_sources(
            {"accounts": {"m": {"type": "taxable"}},
             "fetch": {"m": {"source": "questrade", "number": "1"}}})
        self.assertEqual(src, {})

    def test_account_without_brokerage_named_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                'brokerage = "questrade"\naccount = "1"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            r = _cli(root, "fetch", "rrsp")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("declares no `brokerage`", r.stderr)


class TestHttpErrorDiagnostics(unittest.TestCase):
    def test_api_error_body_reaches_the_user(self):
        # 'HTTP Error 400: Bad Request' alone is undebuggable —
        # Questrade puts the actual reason in the response body.
        import io as _io
        import urllib.error

        def http(url):
            raise urllib.error.HTTPError(
                url, 400, "Bad Request", None,
                _io.BytesIO(b'{"code":1002,"message":"Invalid or '
                            b'malformed argument: endTime"}'))
        sess = {"api_server": "https://api.q.com/",
                "access_token": "AT"}
        with self.assertRaises(RuntimeError) as cm:
            qt_activities(sess, "123", date(2026, 8, 1),
                          date(2026, 8, 10), http)
        msg = str(cm.exception)
        self.assertIn("Invalid or malformed argument", msg)
        self.assertIn("400", msg)
        self.assertNotIn("access_token", msg)   # nothing secret leaks


class TestTaxYearStampedFilename(unittest.TestCase):
    def test_year_stamped_name_parses_as_questrade(self):
        # detect_broker must still route questrade_2026.csv to the
        # Questrade parser (content-based header detection).
        from taxjson.bin.taxjson_run import detect_broker
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "questrade_2026.csv"
            f.write_text(
                "Transaction Date,Settlement Date,Action,Symbol,"
                "Description,Quantity,Price,Gross Amount,Commission,"
                "Net Amount,Currency,Account #,Activity Type,"
                "Account Type\n")
            self.assertEqual(detect_broker(f), "questrade")


class TestCrossChunkDedup(unittest.TestCase):
    def test_boundary_activity_returned_by_two_chunks_kept_once(self):
        # Questrade re-emits boundary-day activities in both adjacent
        # windows — 55 phantom duplicate trades in real data.
        calls = []

        def http(url):
            calls.append(url)
            return json.dumps({"activities": [dict(_ACT)]}).encode()
        sess = {"api_server": "https://api.q.com/", "access_token": "AT"}
        acts = qt_activities(sess, "1", date(2026, 1, 1),
                             date(2026, 3, 15), http)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(acts), 1,
                         "the same activity from later chunks must "
                         "be dropped")

    def test_same_chunk_duplicates_are_real_split_fills(self):
        def http(url):
            return json.dumps(
                {"activities": [dict(_ACT), dict(_ACT)]}).encode()
        sess = {"api_server": "https://api.q.com/", "access_token": "AT"}
        acts = qt_activities(sess, "1", date(2026, 8, 1),
                             date(2026, 8, 10), http)
        self.assertEqual(len(acts), 2,
                         "within-chunk duplicates are genuine split "
                         "fills and must survive")


class TestOverlapTrim(unittest.TestCase):
    _HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Account #,Activity Type,Account Type\n")

    def _manual(self, d):
        p = d / "lira.csv"
        p.write_text(
            self._HDR +
            "2025-06-03 12:00:00 AM,2025-06-04 12:00:00 AM,Buy,XEI.TO,"
            "D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Ind\n"
            "2026-02-01 12:00:00 AM,2026-02-02 12:00:00 AM,Buy,XEI.TO,"
            "D,50,11.00,550.00,0.00,-550.00,CAD,1,Trades,Ind\n")
        return p

    def test_overlap_detected_only_inside_window(self):
        from taxjson.bin.taxjson_run import _qt_window_overlap
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = self._manual(d)
            out = d / "questrade_2026.csv"
            out.write_text(self._HDR)
            hits = _qt_window_overlap(d, out, "2025-12-15",
                                      "2026-08-31")
            self.assertEqual(hits, [(manual, 1)])
            self.assertEqual(_qt_window_overlap(d, out, "2026-03-01",
                                                "2026-08-31"),
                             [])
            # Rows dated AFTER the window's end are NOT overlap — the
            # fetched file does not own them, and --trim-overlap
            # deleting them silently removed real trades from the
            # active books (2026-09 audit).
            self.assertEqual(_qt_window_overlap(d, out, "2025-12-15",
                                                "2026-01-15"),
                             [])

    def test_trim_keeps_history_and_backs_up(self):
        from taxjson.bin.taxjson_run import _qt_trim_file
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = self._manual(d)
            cut = _qt_trim_file(manual, "2025-12-15", "2026-08-31")
            self.assertEqual(cut, 1)
            text = manual.read_text()
            self.assertIn("2025-06-03", text)      # history kept
            self.assertNotIn("2026-02-01", text)   # window row gone
            bak = d / "lira.csv.bak"
            self.assertTrue(bak.exists())
            self.assertIn("2026-02-01", bak.read_text())

    def test_trim_never_removes_post_window_rows(self):
        from taxjson.bin.taxjson_run import _qt_trim_file
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = self._manual(d)
            # Window ends before the 2026-02-01 row: nothing to trim.
            self.assertEqual(
                _qt_trim_file(manual, "2025-12-15", "2026-01-15"), 0)
            self.assertIn("2026-02-01", manual.read_text())

    def test_trim_noop_leaves_no_backup(self):
        from taxjson.bin.taxjson_run import _qt_trim_file
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            manual = self._manual(d)
            self.assertEqual(
                _qt_trim_file(manual, "2027-01-01", "2027-12-31"), 0)
            self.assertFalse((d / "lira.csv.bak").exists())




class TestYearBackfillAndJson(unittest.TestCase):
    def test_past_year_window_capped_at_jan_15(self):
        s, e = qt_window(None, None, today=date(2026, 8, 20),
                         year=2024)
        self.assertEqual(s.isoformat(), "2023-12-15")
        self.assertEqual(e.isoformat(), "2025-01-15")

    def test_current_year_window_still_ends_today(self):
        s, e = qt_window(None, None, today=date(2026, 8, 20),
                         year=2026)
        self.assertEqual(e.isoformat(), "2026-08-20")

    def test_activity_type_counts(self):
        from taxjson.bin.taxjson_fetch import activity_type_counts
        acts = [dict(_ACT), dict(_ACT),
                dict(_ACT, type="Dividends"), dict(_ACT, type="")]
        self.assertEqual(activity_type_counts(acts),
                         {"Trades": 2, "Dividends": 1, "?": 1})

    def test_year_refuses_from_and_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                'brokerage = "questrade"\naccount = "1"\n')
            r = _cli(root, "fetch", "--year", "2024", "--days", "30")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--year", r.stderr)
            r = _cli(root, "fetch", "--year", "1999")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outside", r.stderr)

    def test_json_error_paths_keep_stdout_clean(self):
        # Under --json, config errors go to stderr and stdout carries
        # no partial document.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            r = _cli(root, "fetch", "--json")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class TestFlexMultiAccountGuard(unittest.TestCase):
    def test_statement_count(self):
        from taxjson.bin.taxjson_fetch import flex_statement_count
        one = ('"Statement","Header","Field Name","Field Value"\n'
               '"Statement","Data","BrokerName","IB"\n'
               '"Trades","Header","X"\n')
        two = one + ('"Statement","Header","Field Name","Field Value"\n'
                     '"Statement","Data","BrokerName","IB"\n')
        self.assertEqual(flex_statement_count(one), 1)
        self.assertEqual(flex_statement_count(two), 2)
        self.assertEqual(flex_statement_count(
            "Statement,Data,BrokerName,IB\n"), 1)


class TestLiveHoldings(unittest.TestCase):
    _POS = [{"symbol": "XEI.TO", "openQuantity": 100.0,
             "averageEntryPrice": 10.0,
             "currentMarketValue": 1100.0},
            {"symbol": "AAPL", "openQuantity": 60.0,
             "averageEntryPrice": 150.0},
            {"symbol": "GONE.TO", "openQuantity": 0.0},
            {"symbol": "BMO20Jan27C88.00", "openQuantity": -1.0}]

    def test_toml_renders_and_parses(self):
        from taxjson.bin.taxjson_fetch import positions_to_holdings_toml
        from taxjson.lib.tomlcompat import tomllib
        text = positions_to_holdings_toml(self._POS, "lira", "123",
                                          "2026-08-24 10:00:00")
        doc = tomllib.loads(text)
        by = {h["symbol"]: h for h in doc["holding"]}
        self.assertEqual(by["XEI.TO"]["quantity"], 100.0)
        self.assertEqual(by["AAPL.US"]["quantity"], 60.0)
        self.assertNotIn("GONE.TO", by)          # zero-qty dropped
        # No .TO equity for BMO root in this payload -> .US OCC.
        self.assertIn("BMO270120C00088000.US", by)
        self.assertEqual(doc["meta"]["account"], "lira")

    def test_generated_file_round_trips_through_sanity(self):
        from taxjson.bin.taxjson_fetch import positions_to_holdings_toml
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.lira]\ntype = "sheltered"\n')
            (work / "lira_gains.json").write_text(json.dumps(
                {"summary": {"year": 2026}, "transactions": [],
                 "inventory": [
                     {"symbol": "XEI.TO", "qty": 100,
                      "total_cost": 1000.0},
                     {"symbol": "AAPL.US", "qty": 60,
                      "total_cost": 9000.0}],
                 "wash_sales": []}))
            toml_path = work / "lira_live_holdings.toml"
            toml_path.write_text(positions_to_holdings_toml(
                self._POS[:2], "lira", "123", "t"))
            r = _cli(root, "sanity", "lira", str(toml_path))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # Now a drifted broker book must FAIL the check.
            toml_path.write_text(positions_to_holdings_toml(
                [dict(self._POS[0], openQuantity=90.0),
                 self._POS[1]], "lira", "123", "t"))
            r = _cli(root, "sanity", "lira", str(toml_path))
        self.assertEqual(r.returncode, 1)
        self.assertIn("XEI.TO", r.stdout + r.stderr)

    def test_live_holdings_fetch_with_injected_http(self):
        from taxjson.bin.taxjson_run import _qt_live_holdings
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HOME": tmp},
                                clear=False):
            # HOME is pinned inside the tmp dir so the shared
            # ~/.questrade_token cannot leak in from the developer's real
            # home (which would both fail this assertion and overwrite a
            # live credential). The chain lives in the shared
            # ~/.questrade_token (here: inside the pinned HOME).
            os.environ.pop("QUESTRADE_TOKEN_FILE", None)
            root = Path(tmp)
            cache = root / "work"
            cache.mkdir(parents=True)
            (root / ".questrade_token").write_text("OLD\n")
            cfg = {"accounts": {"lira": {"type": "sheltered",
                                         "brokerage": "questrade",
                                         "account": "123"}}}

            def http(url):
                if "oauth2" in url:
                    return json.dumps(
                        {"api_server": "https://api.q/",
                         "access_token": "AT",
                         "refresh_token": "NEW"}).encode()
                return json.dumps({"positions": self._POS}).encode()
            out = _qt_live_holdings(root, cache, cfg, ["lira"],
                                    http, lambda m: None)
            self.assertIn("lira", out)
            self.assertTrue(out["lira"].exists())
            # The rotation lands back in the shared chain.
            self.assertEqual(
                (root / ".questrade_token").read_text().strip(), "NEW")


class QuestradeTokenFileTest(unittest.TestCase):
    """The shared ~/.questrade_token chain (portoml-ai uses the same file)."""

    def test_fresh_machine_defaults_to_the_shared_file(self):
        from taxjson.bin.taxjson_run import _questrade_token_file
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HOME": tmp}, clear=False):
            os.environ.pop("QUESTRADE_TOKEN_FILE", None)
            cache = Path(tmp) / "work"
            self.assertEqual(_questrade_token_file(cache),
                             Path(tmp) / ".questrade_token")

    def test_legacy_project_file_is_ignored(self):
        # The pre-1.0 per-project work/.questrade_refresh_token fallback
        # (and its one-time auto-migration) were removed: the resolver
        # returns the shared path regardless of what a project's work/
        # directory contains, and never reads or writes the old file.
        from taxjson.bin.taxjson_run import _questrade_token_file
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HOME": tmp}, clear=False):
            os.environ.pop("QUESTRADE_TOKEN_FILE", None)
            cache = Path(tmp) / "work"
            cache.mkdir()
            (cache / ".questrade_refresh_token").write_text("LEGACY\n")
            shared = Path(tmp) / ".questrade_token"
            self.assertEqual(_questrade_token_file(cache), shared)
            self.assertFalse(shared.exists())        # nothing auto-copied
            self.assertEqual((cache / ".questrade_refresh_token")
                             .read_text().strip(), "LEGACY")   # untouched

    def test_env_override_wins_over_both(self):
        from taxjson.bin.taxjson_run import _questrade_token_file
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ,
                                {"HOME": tmp,
                                 "QUESTRADE_TOKEN_FILE": tmp + "/x"},
                                clear=False):
            (Path(tmp) / ".questrade_token").write_text("TOK\n")
            self.assertEqual(_questrade_token_file(Path(tmp) / "work"),
                             Path(tmp) / "x")

    def test_write_is_atomic_and_mode_600(self):
        from taxjson.bin.taxjson_run import _questrade_token_write
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sub" / ".questrade_token"
            _questrade_token_write(dest, "ROTATED")
            self.assertEqual(dest.read_text().strip(), "ROTATED")
            self.assertEqual(oct(dest.stat().st_mode & 0o777), "0o600")
            self.assertFalse(dest.with_name(dest.name + ".part").exists())


if __name__ == "__main__":
    unittest.main()
