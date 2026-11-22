"""The books of an account taxjson.toml no longer names: a full run reads
the configured accounts' books only, its roster of the pass across accounts
(TJ-4302) and its checks across accounts before its sweep (TJ-4301).
Synthetic data only: made-up account names, empty books."""
import tempfile
import unittest
from pathlib import Path

from _support.view_wash_staleness import _older


class TestTJ4302ARemovedAccountReAdded(unittest.TestCase):
    """Backlog TJ-4302: the roster names the project's accounts only. A
    full run passes over the books before it sweeps those of an account
    taxjson.toml no longer names, so that account was in the roster, and
    once added back and built alone (`run --account`) its newer books never
    made the blend stale."""

    def test_an_account_no_longer_configured_is_not_in_the_roster(self):
        from taxjson.lib import report_model as rm
        from taxjson.lib.records import read_record
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs = []
            for acct in ("a", "b", "c"):        # c: the last run's books
                for suffix in ("_base.json", "_gains.json"):
                    p = root / (acct + suffix)
                    p.write_text('{"transactions":[]}')
                    if acct != "c":
                        inputs.append(p)
            wash = root / "a_gains_wash.json"
            wash.write_text('{"transactions":[]}')
            rm.record_wash_inputs(root, [wash], inputs, blend="ca",
                                  configured=["a", "b"])
            self.assertEqual(read_record(wash)["wash"]["books"], ["a", "b"])
            # c added back and built alone: newer than the pass
            for suffix in ("_base.json", "_gains.json"):
                _older(root / ("c" + suffix), wash, 10)
            self.assertEqual(rm.stale_wash_inputs(wash),
                             ["c_base.json", "c_gains.json"])


class TestTJ4301TheConfiguredAccountsBooksOnly(unittest.TestCase):
    """Backlog TJ-4301: the run's checks across accounts (the cross-listing
    and SIMILAR loss checks, the in-kind results) run before its sweep, so
    they read the configured accounts' books only, never those of an
    account taxjson.toml no longer names."""

    def test_the_resolver_reads_the_named_accounts_only(self):
        from taxjson.lib import report_model as rm
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("a_gains.json", "a_gains_wash.json", "gone_gains_wash.json",
                         "b_gains.json"):
                (root / name).write_text('{"transactions":[]}')
            self.assertEqual(sorted(rm.resolve_gains_files(root, warn_stale=False)),
                             ["a", "b", "gone"])
            got = rm.resolve_gains_files(root, warn_stale=False, accounts=["b", "a"])
            self.assertEqual(got, {"a": root / "a_gains_wash.json",
                                   "b": root / "b_gains.json"})

    def test_the_in_kind_reader_skips_a_removed_accounts_books(self):
        from taxjson.pipeline import stages
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2024\n\n[accounts.a]\ntype = "taxable"\n')
            work = root / "work"
            work.mkdir()
            for name in ("a_gains.json", "gone_gains.json"):
                (work / name).write_text('{"transactions":[]}')
            docs, _refused = stages._checked_gains_docs(work)
            self.assertEqual(len(docs), 1)


if __name__ == "__main__":
    unittest.main()
