"""Step 5d: a generic mapping's [signature] and named layouts. A CSV is
read with the folder's generic.toml, or with a layout (the project's
layouts/ or the user's folder), whose signature fits its header, under any
file name; a sidecar's [layout] picks one by hand; two fitting layouts, or
a supported export's match beside a signature's, are refused."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _tmpfiles import isolate_layouts
from taxjson.lib.brokerages.detect import AmbiguousBroker, detect
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.brokerages.generic import (GenericBrokerage, LayoutError,
                                            MappingAmbiguity,
                                            effective_mapping,
                                            mapping_broker_name,
                                            mapping_bytes, mapping_documents)
from _support.generic_toml import _TOML
import _paths

REPO_ROOT = _paths.REPO
CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
       "01/15/2025,BUY,XEI,100,10.00,-1000.00,CAD\n"
       "06/20/2025,SELL,XEI,100,15.00,1500.00,CAD\n")
SIG = '\n[signature]\nheaders = ["Transaction type", "Symbol", "Amount"]\n'
LAYOUT = _TOML + SIG + '\n[broker]\nname = "wealthsimple"\n'
QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
      "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
      "Account #,Activity Type,Account Type\n")


def _project(root: Path) -> Path:
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        'source_currencies = []\n[accounts.margin]\ntype = "taxable"\n')
    csv = root / "inputs" / "margin" / "activity.csv"
    csv.write_text(CSV)
    return csv


isolate_layouts()


class _Layouts(unittest.TestCase):
    """A project of its own per test; the user's layouts folder is none
    (isolate_layouts), so the box's own layouts never route a file."""

    def setUp(self):
        self._env = os.environ.get("TAXJSON_LAYOUTS")
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TAXJSON_LAYOUTS", None)
        else:
            os.environ["TAXJSON_LAYOUTS"] = self._env
        self._td.cleanup()


class TestSignaturesAndLayouts(_Layouts):
    def test_the_folders_mapping_by_its_signature(self):
        csv = _project(self.root)
        (csv.parent / "generic.toml").write_text(_TOML + SIG)
        d = detect(csv)
        self.assertEqual((d.broker, d.how, d.reason),
                         ("generic", "mapping", "mapping generic.toml, by its header"))
        self.assertEqual(len(GenericBrokerage().parse_file(csv)), 2)
        self.assertEqual(effective_mapping(csv).how, "signature")
        # Without the signature the file, not named generic_, is nobody's.
        (csv.parent / "generic.toml").write_text(_TOML)
        self.assertIsNone(detect(csv).broker)
        self.assertIsNone(effective_mapping(csv).path)

    def test_a_project_layout_by_its_signature(self):
        csv = _project(self.root)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        d = detect(csv)
        self.assertEqual((d.broker, d.reason), ("generic", "layout ws, by its header"))
        self.assertEqual(len(GenericBrokerage().parse_file(csv)), 2)
        self.assertEqual(mapping_broker_name(csv), "wealthsimple")
        choice = effective_mapping(csv)
        self.assertEqual((choice.how, choice.name), ("layout", "ws"))
        self.assertIn(self.root / "layouts" / "ws.toml", choice.deps)
        self.assertEqual(choice.manifest(), "ws.toml@ws")

    def test_the_users_layouts_folder(self):
        csv = _project(self.root)
        user = self.root / "user-layouts"
        user.mkdir()
        (user / "ws.toml").write_text(LAYOUT)
        os.environ["TAXJSON_LAYOUTS"] = str(user)
        self.assertEqual(detect(csv).reason, "layout ws, by its header")
        # The project's layout of the same name wins (the first folder).
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT.replace("wealthsimple", "mine"))
        self.assertEqual(mapping_broker_name(csv), "mine")

    def test_a_sidecar_names_a_layout(self):
        csv = _project(self.root)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        sidecar = csv.with_name(csv.name + ".toml")
        sidecar.write_text('[layout]\nname = "ws"\n\n[broker]\naccount = "A1"\n')
        d = detect(csv)
        self.assertEqual(d.reason, "mapping activity.csv.toml (layout ws)")
        txs = GenericBrokerage().parse_file(csv)
        self.assertEqual(len(txs), 2)
        self.assertTrue(txs[0].get("broker_account"))       # the sidecar's own
        self.assertEqual(mapping_broker_name(csv), "wealthsimple")
        choice = effective_mapping(csv)
        self.assertEqual((choice.how, choice.name), ("sidecar", "ws"))
        self.assertIn(self.root / "layouts" / "ws.toml", choice.deps)
        cases = (('[layout]\nname = "ws"\n\n[columns]\ndate = "Date"\n', "describes nothing else"),
                 ('[layout]\nname = "zz"\n', "no layout 'zz'"),
                 ('[layout]\nname = "../ws"\n', "letters, digits"),
                 ('[layout]\nuse = "ws"\n', "Did you mean 'name'"))
        for text, words in cases:
            sidecar.write_text(text)
            with self.assertRaises(ValueError) as cm:
                GenericBrokerage().parse_file(csv)
            self.assertIn(words, str(cm.exception), words)
        (self.root / "layouts" / "ws.toml").write_text('[layout]\nname = "ws"\n')
        sidecar.write_text('[layout]\nname = "ws"\n')
        with self.assertRaises(ValueError) as cm:
            GenericBrokerage().parse_file(csv)
        self.assertIn("a layout does not name a layout", str(cm.exception))

    def test_two_fitting_layouts_are_refused_and_a_sidecar_picks(self):
        csv = _project(self.root)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        (self.root / "layouts" / "ws2.toml").write_text(LAYOUT.replace("wealthsimple", "other"))
        with self.assertRaises(MappingAmbiguity) as cm:
            effective_mapping(csv)
        self.assertIn("fits 2 layouts (ws, ws2)", str(cm.exception))
        with self.assertRaises(AmbiguousBroker):
            detect(csv)
        csv.with_name(csv.name + ".toml").write_text('[layout]\nname = "ws2"\n')
        self.assertEqual(detect(csv).reason, "mapping activity.csv.toml (layout ws2)")
        self.assertEqual(mapping_broker_name(csv), "other")

    def test_a_signature_beside_a_supported_exports_match_is_ambiguous(self):
        csv = _project(self.root)
        qt = csv.with_name("qt.csv")
        qt.write_text(QT)
        (self.root / "layouts").mkdir()
        broad = _TOML + '\n[signature]\nheaders = ["Symbol", "Quantity", "Price"]\n'
        (self.root / "layouts" / "ws.toml").write_text(broad)
        with self.assertRaises(AmbiguousBroker) as cm:
            detect(qt)
        self.assertIn("fits both Questrade", str(cm.exception))
        self.assertIn('[layout] name = "ws"', str(cm.exception))
        # The export's own files keep their parser when no signature fits.
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        self.assertEqual(detect(qt).broker, "questrade")
        # The folder's own mapping: the advice is the mapping itself.
        (csv.parent / "generic.toml").write_text(broad)
        with self.assertRaises(AmbiguousBroker) as cm:
            detect(qt)
        self.assertIn("holding the mapping itself", str(cm.exception))

    def test_a_positions_report_the_signature_fits_stays_one(self):
        # The one positions report known today (an RBC holdings export)
        # opens with a preamble line, which is the header the signature
        # is matched against; a signature fitting it still yields.
        from taxjson.lib.brokerages.detect import positions_only_text
        csv = _project(self.root)
        text = ("Holdings Export as of November 7, 2025\n\n"
                "Symbol,Quantity,Market Value,Currency\n"
                "XEI,100,1500.00,CAD\n")
        self.assertIsNotNone(positions_only_text(text))
        rep = csv.with_name("holdings.csv")
        rep.write_text(text)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(
            _TOML + '\n[signature]\nheaders = ["Holdings Export as of November 7", "2025"]\n')
        self.assertEqual(effective_mapping(rep).name, "ws")
        d = detect(rep)
        self.assertEqual((d.broker, d.how), (None, "positions"))

    def test_the_choice_follows_the_layouts_state(self):
        csv = _project(self.root)
        self.assertIsNone(effective_mapping(csv).path)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        self.assertEqual(effective_mapping(csv).name, "ws")
        import time
        time.sleep(0.02)
        (self.root / "layouts" / "ws.toml").write_text(
            LAYOUT.replace('"Transaction type", "Symbol", "Amount"', '"Nothing", "Here"'))
        self.assertIsNone(effective_mapping(csv).path)
        # A blank first line before the header: the header is the record
        # that holds the signature (HEADER_WITHIN), so the layout fits and
        # the parse reads past the line, as it reads past a title.
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        csv.write_text("\n" + CSV)
        self.assertEqual(effective_mapping(csv).name, "ws")

    def test_a_shared_inputs_folder_keeps_the_projects_layouts(self):
        # inputs_dir = "../inputs": the CSV sits beside the year folders;
        # TAXJSON_PROJECT_ROOT names the project, whose layouts/ applies.
        year = self.root / "2025"
        (year).mkdir()
        (year / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\ninputs_dir = "../inputs"\n[accounts.margin]\ntype = "taxable"\n')
        (self.root / "inputs" / "margin").mkdir(parents=True)
        csv = self.root / "inputs" / "margin" / "activity.csv"
        csv.write_text(CSV)
        (year / "layouts").mkdir()
        (year / "layouts" / "ws.toml").write_text(LAYOUT)
        old = os.environ.get("TAXJSON_PROJECT_ROOT")
        os.environ["TAXJSON_PROJECT_ROOT"] = str(year)
        try:
            self.assertEqual(effective_mapping(csv).name, "ws")
        finally:
            if old is None:
                os.environ.pop("TAXJSON_PROJECT_ROOT", None)
            else:
                os.environ["TAXJSON_PROJECT_ROOT"] = old

    def test_the_folders_mapping_wins_over_two_fitting_layouts(self):
        csv = _project(self.root)
        (csv.parent / "generic.toml").write_text(_TOML + SIG)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "a.toml").write_text(LAYOUT)
        (self.root / "layouts" / "b.toml").write_text(LAYOUT)
        self.assertEqual(effective_mapping(csv).how, "signature")
        self.assertEqual(detect(csv).reason, "mapping generic.toml, by its header")

    def test_a_bad_layout_stops_the_run_naming_it(self):
        csv = _project(self.root)
        (self.root / "layouts").mkdir()
        for text, words in (('[signature]\nheaders = ["Symbol"]\n', "two or more"),
                            ("[signature\nheaders = 1\n", "cannot be read")):
            (self.root / "layouts" / "ws.toml").write_text(text)
            with self.assertRaises(LayoutError) as cm:
                effective_mapping(csv)
            self.assertIn(words, str(cm.exception), words)
            self.assertIn("ws.toml", str(cm.exception))
            with self.assertRaises(AmbiguousBroker):
                detect(csv)
        # A layout with no [signature] at all is simply never chosen.
        (self.root / "layouts" / "ws.toml").write_text(_TOML)
        self.assertIsNone(effective_mapping(csv).path)

    def test_a_layout_never_names_a_layout(self):
        csv = _project(self.root)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "base.toml").write_text(_TOML)
        (self.root / "layouts" / "ws.toml").write_text(
            LAYOUT + '\n[layout]\nname = "base"\n')
        self.assertEqual(effective_mapping(csv).name, "ws")
        with self.assertRaises(ValueError) as cm:
            GenericBrokerage().parse_file(csv)
        self.assertIn("a layout does not name a layout", str(cm.exception))
        csv.with_name(csv.name + ".toml").write_text("[layout]\n")
        with self.assertRaises(ValueError) as cm:
            GenericBrokerage().parse_file(csv)
        self.assertIn('needs name = "<layout>"', str(cm.exception))

    def test_the_projects_layouts_on_request_and_the_drift_checks_views(self):
        # A frozen year judges its shared copies with its own layouts.
        csv = _project(self.root)
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        other = self.root / "elsewhere" / "inputs" / "margin"
        other.mkdir(parents=True)
        shared = other / "activity.csv"
        shared.write_text(CSV)
        self.assertIsNone(effective_mapping(shared).path)
        self.assertEqual(effective_mapping(shared, project=self.root).name, "ws")
        docs = mapping_documents(shared, project=self.root)
        self.assertEqual([n for n, _d in docs], ["ws.toml"])
        self.assertEqual(mapping_bytes(shared, project=self.root), LAYOUT.encode())
        # A sidecar naming the layout: its bytes and the layout's.
        sidecar = csv.with_name(csv.name + ".toml")
        sidecar.write_text('[layout]\nname = "ws"\n')
        self.assertEqual(mapping_bytes(csv), sidecar.read_bytes() + b"\0" + LAYOUT.encode())
        self.assertEqual([n for n, _d in mapping_documents(csv)], ["activity.csv.toml", "ws.toml"])

    def test_a_frozen_year_hashes_the_layouts_its_books_are_read_with(self):
        from taxjson.cli.file import _user_layout_copies
        from taxjson.lib.checklist import _fp_key, input_files
        proj = self.root / "proj"
        csv = _project(proj)
        cfg = {"accounts": {"margin": {"type": "taxable"}}}
        (proj / "layouts").mkdir()
        (proj / "layouts" / "ws.toml").write_text(LAYOUT)
        self.assertIn(proj / "layouts" / "ws.toml", input_files(proj, cfg))
        self.assertEqual(_user_layout_copies(proj, cfg), [])
        user = self.root / "user-layouts"       # outside the project
        user.mkdir()
        (user / "mine.toml").write_text(LAYOUT.replace("wealthsimple", "mine"))
        (proj / "layouts" / "ws.toml").unlink()
        os.environ["TAXJSON_LAYOUTS"] = str(user)
        self.assertIn(user / "mine.toml", input_files(proj, cfg))
        self.assertEqual(_fp_key(proj, user / "mine.toml"), "layouts/mine.toml")
        self.assertEqual(_user_layout_copies(proj, cfg),
                         [(user / "mine.toml", proj / "layouts" / "mine.toml")])
        csv.unlink()
        self.assertNotIn(user / "mine.toml", input_files(proj, cfg))

    def test_a_signature_needs_two_headers(self):
        csv = _project(self.root)
        for sig in ('[signature]\nheaders = ["Symbol"]\n', '[signature]\nheaders = "Symbol"\n',
                    '[signature]\nheader = ["Symbol", "Amount"]\n'):
            csv.with_name(csv.name + ".toml").write_text(_TOML + "\n" + sig)
            with self.assertRaises(ValueError) as cm:
                GenericBrokerage().parse_file(csv)
            self.assertTrue("two or more" in str(cm.exception)
                            or "Did you mean 'headers'" in str(cm.exception), str(cm.exception))



class TestShippedLayouts(_Layouts):
    """The layouts taxjson ships (generic.BUILTIN_LAYOUTS, step 5's
    layout broker, TJ-2322): the last folder, weighed only when no
    project or user layout fits; here a temporary folder stands in."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        from taxjson.lib.brokerages import generic
        self.shipped = self.root / "shipped"
        self.shipped.mkdir()
        patcher = mock.patch.object(generic, "BUILTIN_LAYOUTS", self.shipped.resolve())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_shipped_layout_by_its_signature(self):
        from taxjson.lib.brokerages.generic import is_builtin_layout
        csv = _project(self.root)
        (self.shipped / "ws.toml").write_text(LAYOUT)
        d = detect(csv)
        self.assertEqual((d.broker, d.reason), ("generic", "layout ws, by its header"))
        self.assertEqual(len(GenericBrokerage().parse_file(csv)), 2)
        self.assertEqual(mapping_broker_name(csv), "wealthsimple")
        choice = effective_mapping(csv)
        self.assertEqual((choice.how, choice.name), ("layout", "ws"))
        self.assertTrue(is_builtin_layout(choice.path))
        # A sidecar picks it by name too.
        csv.with_name(csv.name + ".toml").write_text('[layout]\nname = "ws"\n')
        self.assertEqual(detect(csv).reason, "mapping activity.csv.toml (layout ws)")

    def test_a_project_or_user_layout_that_fits_wins_without_an_ambiguity(self):
        csv = _project(self.root)
        (self.shipped / "ws.toml").write_text(LAYOUT)
        user = self.root / "user-layouts"
        user.mkdir()
        (user / "mine.toml").write_text(LAYOUT.replace("wealthsimple", "mine"))
        os.environ["TAXJSON_LAYOUTS"] = str(user)
        self.assertEqual(effective_mapping(csv).name, "mine")
        self.assertEqual(mapping_broker_name(csv), "mine")
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ours.toml").write_text(LAYOUT.replace("wealthsimple", "ours"))
        # Two of the user's and project's fitting are still refused.
        with self.assertRaises(MappingAmbiguity):
            effective_mapping(csv)
        (user / "mine.toml").unlink()
        self.assertEqual(mapping_broker_name(csv), "ours")
        # A layout of the shipped one's name shadows it.
        (self.root / "layouts" / "ours.toml").unlink()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT.replace("wealthsimple", "edited"))
        choice = effective_mapping(csv)
        self.assertEqual((choice.path, mapping_broker_name(csv)),
                         (self.root / "layouts" / "ws.toml", "edited"))

    def test_an_unbooked_row_names_where_an_edited_copy_goes(self):
        import io
        from contextlib import redirect_stderr
        csv = _project(self.root)
        csv.write_text(CSV + "07/02/2025,CONTRIB,,,,500.00,CAD\n")
        (self.shipped / "ws.toml").write_text(LAYOUT)
        buf = io.StringIO()
        from unittest import mock
        with redirect_stderr(buf), mock.patch.dict(os.environ, {"HOME": str(self.root.resolve())}):
            GenericBrokerage().parse_file(csv)
        out = buf.getvalue()
        self.assertIn("UNBOOKED", out)
        # Named inside the package: no install path (a user name) in a
        # message a bug report quotes, the same bytes wherever taxjson is
        # installed (TJ-2578, TJ-2590).
        self.assertIn("the layout taxjson ships, ws (the taxjson package's "
                      "data/layouts/ws.toml): copy it to the project's "
                      "layouts/ws.toml", out)
        self.assertNotIn(str(self.root.resolve()), out)
        self.assertNotIn("~/", out)
        # A project layout of its own: no shipped-layout hint.
        (self.root / "layouts").mkdir()
        (self.root / "layouts" / "ws.toml").write_text(LAYOUT)
        buf = io.StringIO()
        with redirect_stderr(buf):
            GenericBrokerage().parse_file(csv)
        self.assertIn("UNBOOKED", buf.getvalue())
        self.assertNotIn("taxjson ships", buf.getvalue())

    def test_a_frozen_year_copies_the_shipped_layout_its_books_are_read_with(self):
        from taxjson.cli.file import _user_layout_copies
        from taxjson.lib.checklist import _fp_key, input_files
        proj = self.root / "proj"
        _project(proj)
        cfg = {"accounts": {"margin": {"type": "taxable"}}}
        (self.shipped / "ws.toml").write_text(LAYOUT)
        shipped = self.shipped.resolve() / "ws.toml"
        self.assertIn(shipped, [p.resolve() for p in input_files(proj, cfg)])
        self.assertEqual(_fp_key(proj, shipped), "layouts/ws.toml")
        self.assertEqual([(a.resolve(), b) for a, b in _user_layout_copies(proj, cfg)],
                         [(shipped, proj / "layouts" / "ws.toml")])


class TestAnUnverifiedLayout(_Layouts):
    """`[broker] verified = false` (TJ-2573, the owner's decision of
    2026-11-21): a layout never run on a real export says so once per
    account's parse, marked for `tjs run`'s end-of-run list, and in its
    detection's reason; a layout without the key, or with `true`, says
    nothing. A temporary folder stands in for the shipped one."""

    SAID = ("warning: ATTENTION: [end of run] generic importer: the layout ws is "
            "unverified against a real export: check this account's totals against "
            "the statements; report a mismatch with the header row and made-up rows, "
            "never real ones (docs/brokers.md).")

    def setUp(self):
        super().setUp()
        from unittest import mock
        from taxjson.lib.brokerages import generic
        self.shipped = self.root / "shipped"
        self.shipped.mkdir()
        patcher = mock.patch.object(generic, "BUILTIN_LAYOUTS", self.shipped.resolve())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _said(self, csvs, ctx=None):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            for c in csvs:
                g = GenericBrokerage()
                g.account_context = ctx
                g.parse_file(c)
        return [ln for ln in buf.getvalue().splitlines() if "unverified" in ln]

    def test_said_once_per_account_marked_for_the_end_of_run_list(self):
        from taxjson.lib.out import END_OF_RUN_MARK
        csv = _project(self.root)
        (self.shipped / "ws.toml").write_text(LAYOUT + "verified = false\n")
        self.assertEqual(self._said([csv]), [self.SAID])
        self.assertIn(END_OF_RUN_MARK, self.SAID)
        self.assertEqual(detect(csv).reason,
                         "layout ws, by its header; unverified against a real export")
        # An account's two exports, parsed with its context: said once.
        second = csv.with_name("activity_2024.csv")
        second.write_text(CSV.replace("2025", "2024"))
        ctx = GenericBrokerage.prepare_files([csv, second])
        self.assertEqual(self._said([csv, second], ctx), [self.SAID])
        # Each file, parsed on its own with no context: each says it.
        self.assertEqual(self._said([csv, second]), [self.SAID, self.SAID])

    def test_a_verified_or_unmarked_layout_says_nothing(self):
        csv = _project(self.root)
        for extra in ("", "verified = true\n"):
            with self.subTest(extra=extra):
                (self.shipped / "ws.toml").write_text(LAYOUT + extra)
                self.assertEqual(self._said([csv]), [])
                self.assertEqual(detect(csv).reason, "layout ws, by its header")

    def test_a_named_layout_says_it_and_its_mapping_cannot_change_it(self):
        csv = _project(self.root)
        (self.shipped / "ws.toml").write_text(LAYOUT + "verified = false\n")
        sidecar = csv.with_name(csv.name + ".toml")
        sidecar.write_text('[layout]\nname = "ws"\n')
        self.assertEqual(self._said([csv]), [self.SAID])
        self.assertEqual(detect(csv).reason, "mapping activity.csv.toml (layout ws); "
                                             "unverified against a real export")
        # The layout's own key: a file standing for it cannot set it.
        sidecar.write_text('[layout]\nname = "ws"\n[broker]\nverified = true\n')
        with self.assertRaises(ValueError) as cm:
            GenericBrokerage().parse_file(csv)
        self.assertIn("[broker].verified is the layout's own key", str(cm.exception))

    def test_a_value_that_is_not_a_boolean_is_refused(self):
        csv = _project(self.root)
        (self.shipped / "ws.toml").write_text(LAYOUT + 'verified = "no"\n')
        with self.assertRaises(ValueError) as cm:
            GenericBrokerage().parse_file(csv)
        self.assertIn("[broker].verified must be true or false, got 'no'", str(cm.exception))


WS_HEADER = ("transaction_date,settlement_date,account_id,account_type,activity_type,"
             "activity_sub_type,direction,symbol,name,currency,quantity,unit_price,"
             "commission,net_cash_amount\n")
WS_BUY = "2025-01-15,2025-01-16,{a},{t},Trade,BUY,LONG,ZZQ,ZZQ Sample Fund,CAD,100,10.00,0.00,-1000.00\n"
WS_DEP = "2025-01-06,2025-01-06,{a},{t},MoneyMovement,EFT,,,,CAD,,,,5000.00\n"


# The shipped Wealthsimple layout is never run on a real export
# (`[broker] verified = false`, TJ-2573): each parse of an account says so
# once, whatever else it says.
UNVERIFIED = "is unverified against a real export"


def _said_unverified_once(case, out):
    """`out` without the parse's one unverified-layout line (the plan
    checks below assert on everything else)."""
    said = [ln for ln in out.splitlines(keepends=True) if UNVERIFIED in ln]
    case.assertEqual(len(said), 1, out)
    return out.replace(said[0], "", 1)


class TestWealthsimpleLayout(_Layouts):
    """The shipped Wealthsimple layout's rules over its own rows (no
    project: the shipped folder alone fits)."""

    def _parse(self, rows, **attrs):
        import io
        from contextlib import redirect_stderr
        csv = self.root / "activity.csv"
        csv.write_text(WS_HEADER + rows)
        g = GenericBrokerage()
        g.country = "canada"
        for k, v in attrs.items():
            setattr(g, k, v)
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = g.parse_file(csv)
        return txs, _said_unverified_once(self, buf.getvalue())

    def _rows(self, a="99900001", t="Non-registered"):
        return WS_DEP.format(a=a, t=t) + WS_BUY.format(a=a, t=t)

    def test_a_deposit_is_a_cash_move_and_shares_moved_in_kind_are_unbooked(self):
        txs, out = self._parse(self._rows())
        self.assertEqual([t["action"] for t in txs], ["BUYSELL"])
        self.assertNotIn("UNBOOKED", out)
        self.assertIn("recognized non-event", out)
        # TJ-2574, TJ-2586: a deposit's row carrying units is no cash
        # move: its own UNBOOKED line, which names the .tt TRANSFER line
        # (mapping it to skip would drop the shares again).
        kind = "2025-02-03,2025-02-03,99900001,Non-registered,MoneyMovement,EFT,,ZZQ,ZZQ Sample Fund,CAD,25,,,\n"
        txs, out = self._parse(self._rows() + kind)
        self.assertEqual([t["action"] for t in txs], ["BUYSELL"])
        self.assertIn("1 row(s) mapped to cash carry units and are NOT in the books: "
                      "activity.csv line 4: action 'MONEYMOVEMENT' carries a quantity", out)
        self.assertIn(".tt TRANSFER line", out)
        self.assertNotIn("unmapped action", out)
        self.assertNotIn("unclassified", out)
        # TJ-2589: a MoneyMovement of another sub-type (shares moved in,
        # a fee) is no cash move either: UNBOOKED, never listed calmly.
        for sub, rest in (("TRANSFER", "ZZQ,ZZQ Sample Fund,CAD,25,,,"),
                          ("FEE", ",,CAD,,,,-12.50")):
            row = f"2025-02-03,2025-02-03,99900001,Non-registered,MoneyMovement,{sub},,{rest}\n"
            txs, out = self._parse(self._rows() + row)
            self.assertEqual([t["action"] for t in txs], ["BUYSELL"])
            self.assertIn("UNBOOKED", out)
            self.assertIn("'MONEYMOVEMENT' (not in [actions], no [[classify]] rule)", out)

    def test_a_buy_sub_type_is_a_trade_only_on_a_trade_row(self):
        # TJ-2575: another activity type's BUY is not this layout's trade.
        other = "2025-02-10,2025-02-11,99900001,Non-registered,OptionsTrade,BUY,LONG,ZZQ,ZZQ Sample Fund,CAD,1,2.00,0.00,-200.00\n"
        txs, out = self._parse(self._rows() + other)
        self.assertEqual(len([t for t in txs if t["action"] == "BUYSELL"]), 1)
        self.assertIn("UNBOOKED", out)
        self.assertIn("'OPTIONSTRADE'", out)

    def test_a_tfsa_export_in_a_taxable_account_is_an_attention(self):
        # TJ-2576: the account type against the taxjson account's.
        _txs, out = self._parse(self._rows(t="TFSA"), account_taxable=True)
        self.assertIn("ATTENTION", out)
        self.assertIn("the account_type says wealthsimple account", out)
        self.assertIn("is a registered plan (TFSA), but this taxjson account is taxable", out)
        _txs, out = self._parse(self._rows(t="TFSA"), account_taxable=False)
        self.assertNotIn("ATTENTION", out)

    def test_a_non_registered_export_in_a_sheltered_account_is_an_attention(self):
        _txs, out = self._parse(self._rows(), account_taxable=False)
        self.assertIn("is a taxable account (Non-registered), but this taxjson account is sheltered", out)
        _txs, out = self._parse(self._rows(), account_taxable=True)
        self.assertNotIn("ATTENTION", out)

    def test_two_accounts_in_one_export(self):
        # TJ-2577: rows of two broker accounts are booked to one: said,
        # refused when one of them is a plan the taxable label would tax.
        two = self._rows() + self._rows(a="99900002")
        _txs, out = self._parse(two, account_taxable=True)
        self.assertIn("rows of 2 wealthsimple accounts", out)
        self.assertIn("every row is booked to ONE account", out)
        _txs, out = self._parse(two, account_taxable=True, combined_broker_accounts=True)
        self.assertNotIn("ATTENTION", out)
        self.assertIn("2 wealthsimple accounts", out)
        self.assertIn("combined_broker_accounts = true", out)
        with self.assertRaises(ValueError) as cm:
            self._parse(self._rows() + self._rows(a="99900002", t="TFSA"), account_taxable=True)
        self.assertIn("is a registered plan but the file sits in a taxable taxjson account", str(cm.exception))

    def _parse_files(self, files, **attrs):
        # The account's exports as taxjson-brokerage hands them: one
        # context from all of them (prepare_files), then each parsed.
        import io
        from contextlib import redirect_stderr
        paths = []
        for name, rows in files:
            p = self.root / name
            p.write_text(WS_HEADER + rows)
            paths.append(p)
        ctx = GenericBrokerage.prepare_files(paths)
        buf = io.StringIO()
        with redirect_stderr(buf):
            for p in paths:
                g = GenericBrokerage()
                g.country = "canada"
                g.account_context = ctx
                for k, v in attrs.items():
                    setattr(g, k, v)
                g.parse_file(p)
        return _said_unverified_once(self, buf.getvalue())

    def test_two_accounts_in_two_exports(self):
        # TJ-2584: a TFSA's export beside a non-registered account's, each
        # file of one account, in a taxable folder: refused as Questrade's
        # are; two taxable accounts' files said once.
        with self.assertRaises(ValueError) as cm:
            self._parse_files([("nonreg.csv", self._rows()),
                               ("tfsa.csv", self._rows(a="99900002", t="TFSA"))],
                              account_taxable=True)
        self.assertIn("these exports of one taxjson account hold rows of 2 wealthsimple "
                      "accounts", str(cm.exception))
        self.assertIn("is a registered plan but the files sit in a taxable taxjson account",
                      str(cm.exception))
        out = self._parse_files([("a.csv", self._rows()), ("b.csv", self._rows(a="99900002"))],
                                account_taxable=True)
        self.assertEqual(out.count("every row is booked to ONE account"), 1)
        self.assertIn("a.csv, b.csv: these exports of one taxjson account hold rows of 2", out)
        # One account across its yearly files: nothing to say.
        out = self._parse_files([("a.csv", self._rows()), ("b.csv", self._rows())],
                                account_taxable=True)
        self.assertNotIn("ATTENTION", out)

    def test_a_row_with_a_blank_account_is_no_second_account(self):
        # TJ-2585: a blank account_id names no account (Questrade's blank
        # Account # is skipped too).
        blank = "2025-03-01,2025-03-01,,Non-registered,Interest,,,,,CAD,,,,1.25\n"
        _txs, out = self._parse(self._rows() + blank, account_taxable=True)
        self.assertNotIn("ATTENTION", out)
        _txs, out = self._parse(self._rows(t="TFSA") + blank.replace("Non-registered", "TFSA"),
                                account_taxable=False, combined_broker_accounts=True)
        self.assertNotIn("ATTENTION", out)

    def test_an_account_type_the_check_cannot_judge_is_said(self):
        # TJ-2588: NON_REGISTERED is a taxable account's type; a type that
        # names neither a plan nor a taxable account is a note, not silence.
        _txs, out = self._parse(self._rows(t="NON_REGISTERED"), account_taxable=False)
        self.assertIn("is a taxable account (NON_REGISTERED), but this taxjson account is sheltered", out)
        _txs, out = self._parse(self._rows(t="Personal"), account_taxable=True)
        self.assertNotIn("ATTENTION", out)
        self.assertIn("the account_type 'Personal' names no account type taxjson knows", out)
        self.assertIn("whether the export belongs in this taxable account is not checked", out)

    def test_a_file_with_no_account_column_names_no_number(self):
        # TJ-2593: an account_type mapped with no account column: one
        # account of no number, said as such (no "#1  " gap).
        from taxjson.lib.brokerages.generic import BUILTIN_LAYOUTS
        layout = (BUILTIN_LAYOUTS / "wealthsimple.toml").read_text()
        self.assertIn('account     = "account_id"\n', layout)
        (self.root / "activity.csv.toml").write_text(
            layout.replace('account     = "account_id"\n', ""))
        _txs, out = self._parse(self._rows(t="TFSA"), account_taxable=True)
        self.assertIn("wealthsimple account #1 (no account number) is a registered plan (TFSA)", out)


class TestShippedLayoutFiles(unittest.TestCase):
    def test_the_shipped_layouts_load(self):
        # Every layout taxjson ships, where this install has them (the
        # package data): valid, signed, named, none naming a [layout]; and
        # no signature inside another's, so no header fits two.
        from taxjson.lib.brokerages.generic import BUILTIN_LAYOUTS, _signature_of
        from taxjson.project.toml_io import read_toml
        files = sorted(BUILTIN_LAYOUTS.glob("*.toml"))
        self.assertTrue(files, f"no layout in {BUILTIN_LAYOUTS}")
        sigs = {}
        for f in files:
            doc = read_toml(f)
            self.assertNotIn("layout", doc, f.name)
            self.assertRegex((doc.get("broker") or {}).get("name", ""), r"^[a-z0-9_-]+$", f.name)
            self.assertIsInstance((doc.get("broker") or {}).get("verified", True), bool, f.name)
            sigs[f.stem] = set(_signature_of(f, "layout") or ())
            self.assertTrue(sigs[f.stem], f"{f.name}: no [signature]")
        # Every header the repository holds (the fixtures, the demo
        # exports): at most one shipped layout fits it, and a shipped
        # layout's own fixture (tests/fixtures/<name>/) is fitted by it alone
        # (TJ-2579: a subset test let two signatures fit one header).
        from taxjson.lib.brokerages.generic import _head_records, header_index
        # Never run on a real export (TJ-2573): marked, so every run says so.
        self.assertIs(read_toml(BUILTIN_LAYOUTS / "wealthsimple.toml")["broker"]["verified"],
                      False)
        csvs = sorted(REPO_ROOT.glob("tests/fixtures/*/*.csv")) + sorted(REPO_ROOT.glob("examples/*.csv"))
        self.assertTrue(csvs)
        for c in csvs:
            rows = _head_records(c)     # each signature's header, as detection finds it
            fits = sorted(n for n, sig in sigs.items()
                          if header_index(rows, sig) is not None)
            self.assertLessEqual(len(fits), 1, f"{c.name}: {fits}")
            if c.parent.name in sigs:
                self.assertEqual(fits, [c.parent.name], c)


class TestLayoutRun(_Layouts):
    def test_the_run_rebuilds_on_a_layout_edit(self):
        _project(self.root)
        (self.root / "layouts").mkdir()
        layout = self.root / "layouts" / "ws.toml"
        layout.write_text(LAYOUT)
        env = dict(os.environ)
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run",
                               ["-C", str(self.root), "run", "--no-input"]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        gains = json.loads((self.root / "work" / "margin_gains.json").read_text())
        self.assertAlmostEqual(gains["summary"]["total_gain"], 500.0, places=2)
        self.assertIn("layout ws, by its header", r.stdout + r.stderr)
        import time
        time.sleep(0.05)
        layout.write_text(LAYOUT.replace('"BUY" = "buy"', '"BUY" = "skip"'))
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run",
                               ["-C", str(self.root), "run", "--no-input"]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        base = (self.root / "work" / "margin_base.json").read_text()
        self.assertEqual(base.count('"BUYSELL"'), 1, "the layout edit did not reach the books")


    def test_a_wealthsimple_export_runs_with_no_mapping_of_its_own(self):
        # Step 5's layout broker: the shipped layout reads the export; the
        # project has no layouts/, no sidecar, no generic.toml.
        _project(self.root)
        csv = self.root / "inputs" / "margin" / "activity.csv"
        csv.write_text((REPO_ROOT / "tests" / "fixtures" / "wealthsimple" / "sample.csv").read_text())
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run",
                               ["-C", str(self.root), "run", "--no-input"]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("layout wealthsimple, by its header", r.stdout + r.stderr)
        gains = json.loads((self.root / "work" / "margin_gains.json").read_text())
        # 40 sold at 15.00 against 10.00 a share: 200.00.
        self.assertAlmostEqual(gains["summary"]["total_gain"], 200.0, places=2)

    def test_the_unverified_layout_reaches_the_end_of_run_list(self):
        # TJ-2573: the shipped layout says `verified = false`: each account
        # reading it is warned once (margin's two exports, cash's one), in
        # the end-of-run list and run_summary.json, unmarked in the .sum.
        from taxjson.lib import first_run as FR
        from taxjson.lib.out import END_OF_RUN_MARK
        _project(self.root)
        with (self.root / "taxjson.toml").open("a") as f:
            f.write('[accounts.cash]\ntype = "taxable"\n')
        lines = (REPO_ROOT / "tests" / "fixtures" / "wealthsimple" / "sample.csv"
                 ).read_text().splitlines(keepends=True)
        margin = self.root / "inputs" / "margin"
        (margin / "activity.csv").write_text("".join(lines[:3]))
        (margin / "activity_2.csv").write_text("".join(lines[:1] + lines[3:]))
        (self.root / "inputs" / "cash").mkdir()
        (self.root / "inputs" / "cash" / "activity.csv").write_text(
            "".join(lines).replace("99900001", "99900002"))
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run",
                               ["-C", str(self.root), "run", "--no-input"]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        text = ("the layout wealthsimple is unverified against a real export: check this "
                "account's totals against the statements; report a mismatch with the header "
                "row and made-up rows, never real ones (docs/brokers.md).")
        tail = r.stdout[r.stdout.index("Done. Reports"):]
        block = tail[tail.index("==> Before you trust"):]
        for acct in ("margin", "cash"):
            self.assertEqual(block.count(f"Warning: [{acct}] Generic importer: {text}"), 1, block)
        doc = json.loads((self.root / "reports" / FR.SUMMARY_FILE).read_text())
        self.assertEqual(sorted(w["account"] for w in doc["parser_warnings"]), ["cash", "margin"])
        self.assertEqual({w["text"] for w in doc["parser_warnings"]},
                         {"generic importer: " + text})
        self.assertNotIn(END_OF_RUN_MARK, r.stdout + r.stderr)
        sums = (self.root / "reports" / "margin.sum").read_text()
        self.assertEqual(sums.count("warning: ATTENTION: generic importer: " + text), 1)
        self.assertNotIn(END_OF_RUN_MARK, sums)

    def test_a_tfsa_export_beside_a_margin_export_is_refused(self):
        # TJ-2584 through the run: taxjson-brokerage hands the generic
        # importer the account's exports' context (prepare_files).
        _project(self.root)
        folder = self.root / "inputs" / "margin"
        sample = (REPO_ROOT / "tests" / "fixtures" / "wealthsimple" / "sample.csv").read_text()
        (folder / "activity.csv").write_text(sample)
        (folder / "tfsa.csv").write_text(
            sample.replace("99900001", "99900002").replace("Non-registered", "TFSA"))
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run",
                               ["-C", str(self.root), "run", "--no-input"]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=dict(os.environ))
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("these exports of one taxjson account hold rows of 2 wealthsimple accounts", out)
        self.assertIn("is a registered plan but the files sit in a taxable taxjson account", out)

if __name__ == "__main__":
    unittest.main()
