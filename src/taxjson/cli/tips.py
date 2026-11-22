"""The Before you trade command for where to hold what: tips."""
import argparse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from taxjson.project.accounts import accounts_of
from taxjson.project.toml_io import read_toml
from taxjson.project.settings import settings_of
from taxjson.lib import project_layout as _PL
from taxjson.lib.cli_diag import note

from taxjson.cli.common import _accounts_skipped_for_no_inputs
from taxjson.pipeline.config import _country, load_config
from taxjson.pipeline.messages import _die_input
from taxjson.pipeline.sanity import _has_inputs
from taxjson.pipeline.stages import _account_plan, _read_work_doc


# Boilerplate an exchange appends to a listing's name that carries no
# issuer identity — dropped before comparing names across listings.
_ISSUER_NOISE = frozenset(
    "INC INCORPORATED CORP CORPORATION LTD LIMITED CO COMPANY PLC LP "
    "THE NEW CLASS CL A B C COM COMMON ORDINARY SHARES SHS STOCK "
    "SUBORDINATE VOTING NON RESTRICTED SV MV NPV ADR ADS UNITS UNIT "
    "TRUST FUND ETF INDEX INCOME REIT DEPOSITARY RECEIPT".split())


def _norm_issuer_name(name: str) -> str:
    """Issuer name reduced to its identity tokens, order kept:
    'B2Gold Corp.' -> 'B2GOLD', 'The Toronto-Dominion Bank' ->
    'TORONTO DOMINION BANK'. Empty when nothing survives."""
    import re as _re
    tokens = _re.sub(r"[^A-Za-z0-9 ]", " ", name.upper()).split()
    return " ".join(t for t in tokens if t not in _ISSUER_NOISE)


def _is_cdr_name(name: str) -> bool:
    """Exchange name says Canadian Depositary Receipt — a fractional,
    CAD-hedged receipt over a US share, NOT a listing equivalent (the
    receipt ratio floats with the hedge, so no static TOBASE ratio can
    ever be right)."""
    up = f" {name.upper()} "
    return (" CDR " in up or "(CDR)" in up.replace("( ", "(")
            or "CANADIAN DEPOSITARY" in up or "CAD HEDGED" in up
            or "CAD-HEDGED" in up)


def _issuer_names_match(a: str, b: str) -> bool:
    """Same issuer? Both names normalized; equal, or one a prefix of
    the other with the longer adding at most ONE token ('AGNICO
    EAGLE' vs 'AGNICO EAGLE MINES' matches; 'BROOKFIELD CORPORATION'
    vs 'BROOKFIELD RENEWABLE PARTNERS' does not). KNOWN LIMIT: a
    distinct issuer differing by exactly one meaningful token
    ('...ASSET MANAGEMENT' vs '...ASSET MANAGEMENT REINSURANCE' after
    noise-stripping) still matches — a MAP-BAD? finding says VERIFY,
    so false negatives are cheaper than false alarms."""
    na, nb = _norm_issuer_name(a), _norm_issuer_name(b)
    if not na or not nb:
        return True          # no evidence — do not accuse the map
    if na == nb:
        return True
    ta, tb = na.split(), nb.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # Multi-token prefix ('AGNICO EAGLE' vs 'AGNICO EAGLE MINES'):
    # same issuer — but only when the longer name adds at most ONE
    # token. Issuer FAMILIES extend a shared prefix by several
    # ('BROOKFIELD ASSET MANAGEMENT' vs 'BROOKFIELD ASSET MANAGEMENT
    # REINSURANCE PARTNERS' are different companies — round-five
    # audit finding 7).
    return (len(short) >= 2 and len(long_) - len(short) <= 1
            and long_[:len(short)] == short)


def cmd_tips(args: argparse.Namespace) -> None:
    """`tjs tips`: advice for next year — where you hold what costs
    tax it need not. It changes no number of this year. Canada checks
    today:

      US-LISTING   a cross-listed CANADIAN issuer (a ticker.map join, or
                   a same-root Canadian listing the exports do not show
                   apart — "verify" unless the names agree) held via its
                   US line in a taxable account or TFSA while receiving
                   dividends — USD dividend conversion drag, and brokers
                   can misclassify the payment; the .TO line gives clean
                   eligible-dividend treatment.
      TFSA-US-DIV  a US-domiciled dividend payer inside a TFSA — the 15%
                   US withholding is unrecoverable there (an RRSP is
                   treaty-exempt; a taxable account can claim the FTC).
                   The domicile is a ticker.map T1135 line, the ISIN's
                   country or tobase.map's, never the listing alone; a
                   US-listed payer with none is flagged as a condition
                   (backlog TJ-0562).

    With --online (Yahoo Finance; candidates to verify, not verdicts):
    MAP-GAP? — a .TO twin of a US-listed dividend payer the map doesn't
    know, and held listings clustered by issuer name to catch
    DIFFERENT-root dual listings (SAMPLQ.US/SAMPLP.TO); MAP-BAD? — a
    defined map pair whose two sides name different issuers; CDR-PAIR —
    a .TO line whose exchange name says CDR (Canadian Depositary Receipt
    — SAMPLR.TO over SAMPLR.US): the SAME issuer but NOT a listing
    equivalent (fractional, CAD-hedged, floating ratio) — never map it;
    nothing to do: look-alike listings are never joined.

    The map's own hygiene is `tjs ticker-map --suggest`: a loss on
    one listing with the other bought in its window (lib/
    xlist_loss_radar), a same-root listing pair the map does not answer
    (MAP-GAP, lib/map_hygiene.map_gaps) and the rules no symbol reaches
    (lib/map_hygiene.unused_rules).

    Exit 0 with or without tips (advice never fails a command); 2 when
    the project cannot be read (no holdings reports, a damaged report or
    book, an account left unread)."""
    import json
    root = Path(args.dir).resolve()
    cache = root / "work"
    reports = root / "reports"
    cfg = load_config(root)
    settings = settings_of(cfg)
    country = _country(settings)
    accounts = accounts_of(cfg)
    if not accounts:
        _die_input("no accounts in taxjson.toml.")

    holdings, _skipped_no_inputs, unscanned, _equity_accts = _tips_holdings(
        root, accounts, reports)

    div_syms, isin_country = _tips_dividends(root, cache, accounts, _skipped_no_inputs,
                                             unscanned)

    lst = _Listings(root, cache, country, holdings, div_syms, isin_country, _equity_accts)

    findings = _offline_tips(lst, accounts, holdings, div_syms, country)

    _online_tips(args, root, lst, holdings, div_syms, findings)

    if getattr(args, "json", False):
        # schema 2 (lib/json_contract): the unused-rule notes and
        # cross-listing loss findings are `tjs ticker-map --suggest`'s
        # now. A live exact Doc: the document printed when it is added,
        # as the print() it replaces printed it.
        from taxjson.lib import out as _out
        from taxjson.lib.json_contract import versioned
        d = _out.Doc(exact=True, width_=0, live=True)
        d.line(json.dumps(versioned({"findings": [
            {"check": c, "account": a, "symbol": sy, "message": m}
            for c, a, sy, m in findings],
            "unscanned": [{"account": n, "missing": w}
                          for n, w in sorted(unscanned.items())]}, "tips"),
            indent=2, sort_keys=True))
        raise SystemExit(2 if unscanned else 0)

    from taxjson.lib import out
    doc = out.Doc(f"TIPS — tax-efficiency advice for next year, {country}"
                  f"{', online map probe' if getattr(args, 'online', False) else ''}")
    if unscanned:
        out.warn("not read (no "
                 + "; ".join(f"{n}: {w}"
                             for n, w in sorted(unscanned.items()))
                 + ")", prog="tjs tips",
                 details=["Re-run `tjs run` to rebuild them."])
    if not findings:
        doc.blank()
        if unscanned:
            doc.para(f"No tips for the accounts read — "
                     f"{', '.join(sorted(unscanned))} could not be read "
                     f"(see above).")
            doc.print()
            raise SystemExit(2)
        doc.line("No tips — nothing to change in where you hold what.")
        doc.print()
        return
    doc.section("TIPS")
    doc.table(["CHECK", "ACCOUNT", "SYMBOL"],
              [[c, a, sy] for c, a, sy, _m in findings])
    doc.blank()
    for i, (c, a, sy, m) in enumerate(findings, 1):
        where = f" [{a}]" if a != "-" else ""
        doc.item(f"{c}{where} {sy}: {m}", bullet=f"{i}. ")
    doc.blank()
    doc.line(f"{len(findings)} tip(s) for next year — none changes a "
             f"number of this year.")
    doc.print()
    if unscanned:
        raise SystemExit(2)


def _tips_holdings(root: Path, accounts: Dict[str, Any],
                   reports: Path) -> Tuple[Dict[str, List[Any]], Set[str], Dict[str, str],
                                       List[str]]:
    """`tjs tips`' per-listing positions: each equity account's holdings
    report, the accounts the last run skipped for no inputs, the ones
    tips cannot read (`unscanned`), and the equity accounts; refused
    when nothing was read."""
    # Per-listing positions (holdings.toml is built PRE-TOBASE, so the
    # US/TO line actually held is visible — the gains inventory is
    # already consolidated and would hide it).
    holdings: Dict[str, list] = {}
    # Accounts tips cannot read (no holdings report / raw book): "no
    # tips" over them was a false all-clear (re-audit A2-0404).
    _skipped_no_inputs = _accounts_skipped_for_no_inputs(root)
    unscanned: Dict[str, str] = {}
    for name in accounts:
        if accounts[name].crypto:
            # Coins have no listings to compare: a crypto account's
            # holdings report is not a cross-listing sighting.
            continue
        f = reports / f"{name}_holdings.toml"
        if not f.exists():
            if (name not in _skipped_no_inputs
                    and _has_inputs(root, name)):
                unscanned[name] = f"reports/{f.name}"
            continue
        try:
            _hl = (read_toml(f)
                   .get("holding", []))
            # `holding = "x"` or a quantity "abc" was an AttributeError
            # / ValueError traceback further down (re-audit A2-0792).
            if not isinstance(_hl, list) \
                    or any(not isinstance(h, dict) for h in _hl):
                raise ValueError("`holding` is not a list of [[holding]] "
                                 "tables")
            for h in _hl:
                float(h.get("quantity", 0) or 0)
            holdings[name] = _hl
        except Exception as e:
            # A warning, then a clean report and exit 0 turned an
            # unreadable report into a false all-clear: the findings on
            # that account's positions vanished (S049-10).
            _die_input(f"could not read reports/{f.name}: {e}",
                       "Re-run `tjs run` to rebuild it (tips would "
                       "otherwise leave that account's positions out).")
    _equity_accts = [n for n, c in accounts.items()
                     if not c.crypto]
    if not holdings and _equity_accts:
        # A clean report (exit 0) over books it read nothing of was a
        # false all-clear (2026-09 CLI audit B22).
        if set(_equity_accts) <= _accounts_skipped_for_no_inputs(root):
            _die_input("no account has any input yet — nothing to check.",
                       "Drop broker CSVs into inputs/<account>/ and "
                       "`tjs run`.")
        _die_input("no holdings reports in reports/ — nothing was checked",
                   "Run `tjs run` first (tips read the per-listing "
                   "positions).")
    return holdings, _skipped_no_inputs, unscanned, _equity_accts


def _tips_dividends(root: Path, cache: Path, accounts: Dict[str, Any],
                    _skipped_no_inputs: Set[str],
                    unscanned: Dict[str, str]) -> Tuple[Set[str], Dict[str, Set[str]]]:
    """The dividend payers in the raw books (work/<account>_raw.json),
    per raw symbol, and each one's ISIN countries; a book tips cannot
    read joins `unscanned`."""
    # Dividend payers, per raw (pre-consolidation) symbol.
    div_syms: set = set()
    isin_country: Dict[str, set] = {}
    for name in accounts:
        f = cache / f"{name}_raw.json"
        if not f.exists():
            if (name not in _skipped_no_inputs
                    and not accounts[name].crypto
                    and _has_inputs(root, name)):
                unscanned.setdefault(name, f"work/{f.name}")
            continue
        # A truncated raw book turned a real finding into a clean report
        # with exit 0 (audit S042-05).
        try:
            data = _read_work_doc(f)
        except (OSError, ValueError) as e:
            _die_input(f"could not read {f}: {e}",
                       "Re-run `tjs run` (tips would otherwise leave "
                       "that file's rows out).")
        for t in data.get("transactions", []):
            if t.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                _ds = str(t.get("symbol") or "").upper()
                # A dividend-in-lieu PAID (a short's charge, negative)
                # is an expense, not a dividend received (backlog
                # TJ-0914).
                try:
                    _amt = (float(t.get("gross_amount") or 0)
                            or float(t.get("net_amount") or 0))
                except (TypeError, ValueError):
                    _amt = 0.0
                if not (t.get("action") == "DIVIDEND_IN_LIEU"
                        and _amt < 0):
                    div_syms.add(_ds)
                # The issuer's country the export's ISIN gives (IB):
                # domicile evidence beyond the listing (backlog TJ-0562).
                _ic = str(t.get("issuer_country") or "").upper()
                if _ic:
                    isin_country.setdefault(_ds, set()).add(_ic)
    return div_syms, isin_country


class _Listings:
    """`tjs tips`' listings: the ticker.map consolidations and declared-
    distinct pairs, each symbol's recorded domicile, every (root, suffix)
    sighting, and the Canadian suffixes; the closures cmd_tips read them
    through are its methods, under their names. The exports' security
    names are read once, when a pair first needs them (`_xl`)."""

    def __init__(self, root: Path, cache: Path, country: str,
                 holdings: Dict[str, List[Any]], div_syms: Set[str],
                 isin_country: Dict[str, Set[str]], _equity_accts: List[str]) -> None:
        from taxjson.lib import map_hygiene as _MH
        # ticker.map consolidations (GLOBAL + TOBASE + JOURNAL) and the
        # user's declared-distinct pairs (CDRs etc. — see DISTINCT).
        renames: Dict[str, str] = {}
        glob_renames: Dict[str, str] = {}
        distinct_pairs: set = set()
        map_file = _PL.ticker_map_path(root)
        if map_file.exists():
            try:
                from taxjson.lib.ticker_map_rules import merge_renames
                from taxjson.lib.map_file import load_map_file
                _tmap = load_map_file(map_file)
                renames = merge_renames(_tmap, to_base=True)
                glob_renames = merge_renames(_tmap, to_base=False)
                distinct_pairs = {frozenset(s.upper() for s in pair)
                                  for pair in _tmap.distinct}
            except Exception as e:
                from taxjson.lib.out import warn as _warn
                _warn(f"could not read ticker.map: {e}", prog="tjs tips")
        renames_u = {k.upper(): v.upper() for k, v in renames.items()}
        # The issuer's domicile, never the listing alone (a US listing of a
        # Canadian issuer is no US dividend payer: backlog TJ-0562): the
        # project's declaration (a ticker.map `T1135 SYMBOL COUNTRY` line),
        # else the export's ISIN country, else tobase.map's (the interlisted
        # master's domicile of an issuer it books under its US listing).
        t1135_dom: Dict[str, str] = {}
        master_dom: Dict[str, str] = {}
        if map_file.exists():
            try:
                from taxjson.lib.ticker_map import read_side_rules
                t1135_dom = {str(k).upper(): str(v).upper() for k, v
                             in read_side_rules(map_file).t1135.items()}
            except Exception:                                  # noqa: BLE001
                t1135_dom = {}      # (run refuses the map, naming the line)
            try:
                from taxjson.lib.tobase_map import t1135_countries
                master_dom = {str(k).upper(): str(v).upper() for k, v
                              in t1135_countries(map_file).items()}
            except Exception:                                  # noqa: BLE001
                master_dom = {}
        # Every (root, suffix) sighting across holdings + dividend history +
        # the map itself — the cross-listing evidence base (an option is a
        # sighting of its underlying, S042-04).
        # (The map's own lines only: a tobase.map pair is not a sighting,
        # lib/map_hygiene.own_renames.)
        own_u = _MH.own_renames(map_file) if map_file.is_file() else {}
        seen_suffixes = _MH.sightings(
            [str(h.get("symbol") or "") for rows in holdings.values()
             for h in rows] + sorted(div_syms) + list(own_u)
            + list(own_u.values()))
        from taxjson.lib.markets import canadian_suffixes as _ca_sufs
        _CA_SUFS = tuple(sorted(_ca_sufs()))
        # The listings' security names in the parsed exports: the only
        # identity evidence beyond a map ruling (read once, when a pair
        # first needs it).
        _xl: Dict[str, Any] = {}

        self.cache = cache
        self.country = country
        self.isin_country = isin_country
        self._equity_accts = _equity_accts
        self.glob_renames = glob_renames
        self.distinct_pairs = distinct_pairs
        self.renames_u = renames_u
        self.t1135_dom = t1135_dom
        self.master_dom = master_dom
        self.seen_suffixes = seen_suffixes
        self._CA_SUFS = _CA_SUFS
        self._xl = _xl

    def _domicile(self, sym_u: str) -> Optional[str]:
        """"USA", another country ("CAN": Canada, or a T1135 line's
        not-foreign word), or None when nothing in the project says."""
        for s in dict.fromkeys((sym_u, self.renames_u.get(sym_u, sym_u))):
            v = self.t1135_dom.get(s)
            if v:
                return "CAN" if v == "CA" else v
        ic = self.isin_country.get(sym_u) or set()
        if len(ic) == 1:
            c = next(iter(ic))
            return {"US": "USA", "CA": "CAN"}.get(c, c)
        for s in dict.fromkeys((sym_u, self.renames_u.get(sym_u, sym_u))):
            if self.master_dom.get(s):
                return self.master_dom[s]
        return None

    def _declared_distinct(self, a: str, b: str) -> bool:
        return frozenset((a.upper(), b.upper())) in self.distinct_pairs

    def _verdict(self, a: str, b: str) -> Tuple[str, str]:
        from taxjson.lib import map_hygiene as _MH
        if "names" not in self._xl:
            self._xl["names"], self._xl["shown"] = _MH.listing_names(
                self.cache, self._equity_accts,
                {k.upper(): v.upper() for k, v in self.glob_renames.items()})
        if "receipts" not in self._xl:
            # A CDR and its US share (the interlisted master, Canada).
            from taxjson.lib.tobase_map import receipt_pairs
            self._xl["receipts"] = receipt_pairs(self.country == "canada")
        return _MH.pair_verdict(a.upper(), b.upper(), self._xl["names"],
                                  self._xl["shown"], self._xl["receipts"])

    def _ca_twins(self, rt: str, sym_u: str) -> List[str]:
        from taxjson.lib import map_hygiene as _MH
        # ticker.map (GLOBAL/TOBASE onto a Canadian listing) is the
        # identity ruling; without one, a sighting of the same root on
        # ANY Canadian venue (.TO, .V, .CN, .NE, .VN — not only the TSX)
        # is a candidate (MAP-GAP asks for the ruling too) — unless the
        # exports show the two apart (map_hygiene.pair_verdict: the Canadian
        # line is a receipt, or the names name different companies) or
        # a DISTINCT line rules it: "hold it instead" would then be
        # wrong advice (audit S042-06, S049-09).
        tgt = self.renames_u.get(sym_u, "")
        if _MH.symbol_root(tgt)[1] in self._CA_SUFS:
            return [] if self._declared_distinct(sym_u, tgt) else [tgt]
        return [f"{rt}.{s}" for s in sorted(self.seen_suffixes.get(rt) or ())
                if s in self._CA_SUFS
                and not self._declared_distinct(sym_u, f"{rt}.{s}")
                and self._verdict(sym_u, f"{rt}.{s}")[0] not in _MH.APART]

    def _twins_proved(self, rt: str, sym_u: str) -> bool:
        from taxjson.lib import map_hygiene as _MH
        # A map ruling, or equal names for every twin named.
        tgt = self.renames_u.get(sym_u, "")
        if _MH.symbol_root(tgt)[1] in self._CA_SUFS:
            return True
        return all(self._verdict(sym_u, c)[0] == "same"
                   for c in self._ca_twins(rt, sym_u))

    def _has_ca_twin(self, rt: str, sym_u: str) -> bool:
        return bool(self._ca_twins(rt, sym_u))


def _offline_tips(lst: _Listings, accounts: Dict[str, Any],
                  holdings: Dict[str, List[Any]], div_syms: Set[str],
                  country: str) -> List[Tuple[str, str, str, str]]:
    """`tjs tips`' offline checks, Canada's: a Canadian issuer held
    through its US listing in a taxable account or a TFSA (US-LISTING),
    a US dividend payer in a TFSA (TFSA-US-DIV); each finding is (check,
    account, symbol, message)."""
    from taxjson.lib import map_hygiene as _MH
    findings = []                     # (check, account, symbol, message)
    if country == "canada":
        for name, acfg in accounts.items():
            plan = _account_plan(name, acfg, country)
            if plan not in ("taxable", "tfsa"):
                continue              # RRSP/LIRA: treaty-exempt, no check
            for h in holdings.get(name, []):
                sym = str(h.get("symbol") or "")
                if float(h.get("quantity", 0) or 0) <= 0:
                    # A short position pays the dividend (in lieu); it
                    # receives none: both checks are about dividends
                    # received (backlog TJ-0914).
                    continue
                rt, suf = _MH.symbol_root(sym)
                if suf != "US":
                    continue
                sym_u = sym.upper()
                # The US line itself must pay: borrowing the .TO line's
                # dividends through the bare root flagged a US holding
                # that pays nothing (audit S049-09).
                pays = sym_u in div_syms
                if not pays:
                    continue
                # An issuer the project records as domiciled outside
                # Canada is no Canadian issuer, Canadian line or not: its
                # dividends are foreign wherever it trades, and the
                # twin branch called a declared US issuer Canadian and
                # hid TFSA-US-DIV (backlog TJ-0913).
                dom = lst._domicile(sym_u)
                if dom in (None, "CAN") and lst._has_ca_twin(rt, sym_u):
                    _ca = " or ".join(lst._ca_twins(rt, sym_u))
                    _chk = ("" if lst._twins_proved(rt, sym_u) else
                            f" (verify {_ca} is the same security first: "
                            f"only the letters match — `tjs "
                            f"ticker-map --suggest` lists the pair)")
                    findings.append((
                        "US-LISTING", name, sym,
                        f"Canadian issuer held via its US listing in a "
                        f"{plan} account while paying dividends — hold "
                        f"{_ca} instead for clean eligible-dividend "
                        f"treatment (and no USD conversion drag){_chk}."))
                elif plan == "tfsa":
                    # US withholding follows the issuer's domicile, not
                    # its listing: a payer the project says is domiciled
                    # elsewhere is not flagged; one it says nothing of
                    # is flagged as a condition (backlog TJ-0562).
                    if dom == "USA":
                        findings.append((
                            "TFSA-US-DIV", name, sym,
                            "US dividend payer inside a TFSA: the 15% US "
                            "withholding is unrecoverable here. Prefer the "
                            "RRSP (treaty-exempt) or a taxable account "
                            "(foreign tax credit claimable)."))
                    elif dom is None:
                        findings.append((
                            "TFSA-US-DIV", name, sym,
                            f"US-listed dividend payer inside a TFSA: if "
                            f"its issuer is US-domiciled, the 15% US "
                            f"withholding is unrecoverable here; prefer "
                            f"the RRSP (treaty-exempt) or a taxable "
                            f"account (foreign tax credit claimable). The "
                            f"listing alone does not say: a ticker.map "
                            f"`T1135 {sym} COUNTRY` line records the "
                            f"issuer's country."))
    return findings


def _online_tips(args: argparse.Namespace, root: Path, lst: _Listings,
                 holdings: Dict[str, List[Any]], div_syms: Set[str],
                 findings: List[Tuple[str, str, str, str]]) -> None:
    """`tjs tips --online`: Yahoo Finance asked whether a US payer's
    root trades on the TSX (MAP-GAP?), then the issuer names
    (`_name_cluster_tips`); the findings added to `findings`. Skipped,
    with a note, when TAXJSON_OFFLINE is set or yfinance is missing."""
    from taxjson.lib import map_hygiene as _MH
    from taxjson.lib.offline import offline_enabled as _offline
    if getattr(args, "online", False) and _offline():
        # The documented kill switch covers this probe too: it sends
        # every held and dividend ticker to Yahoo (S042-12, S048-00).
        note("tjs tips", "TAXJSON_OFFLINE is set — the --online "
             "Yahoo Finance probe is skipped",
             details=["MAP-GAP?/MAP-BAD?/CDR-PAIR are not checked; the "
                      "offline checks below still ran."])
    elif getattr(args, "online", False):
        try:
            import yfinance as yf
        except ImportError:
            from taxjson.lib.out import warn as _warn
            _warn("--online needs yfinance (pip install -e '.[fx]'); "
                  "skipping the probe.", prog="tjs tips")
        else:
            # The book symbol (ZZQ.US, ZZQ.B.TO) is not Yahoo's spelling:
            # every probe goes through the project's QUOTE lines, else
            # the one converter the price chain uses (lib/price_chain).
            # A raw `ZZQ.US` lookup came back empty for every US symbol,
            # so MAP-BAD? could never be raised.
            from taxjson.lib.price_chain import load_yf_map, yf_symbol_for
            try:
                _quote = load_yf_map([str(root)])
            except Exception:
                _quote = {}

            def _yf(sym: str) -> Optional[str]:
                s = str(sym or "").upper()
                if s in _quote:
                    return _quote[s][0]
                return yf_symbol_for(s)

            def _issuer_names(sym: str) -> Tuple[str, str]:
                """(longName, shortName) — BOTH matter: for a CDR the
                longName is the clean issuer ('Abbott Laboratories')
                and only the shortName carries the receipt marker
                ('ABBOTT LABS CDR (CAD HEDGED)'). Falls back to the
                chart metadata when the info endpoint is throttled.
                `sym` is the book symbol; Yahoo is asked for its
                spelling (_yf)."""
                ln = sn = ""
                sym = _yf(sym)
                if not sym:
                    return ln, sn
                try:
                    info = yf.Ticker(sym).info or {}
                    ln = str(info.get("longName") or "")
                    sn = str(info.get("shortName") or "")
                except Exception:
                    pass
                if not (ln or sn):
                    try:
                        t = yf.Ticker(sym)
                        t.history(period="5d", timeout=5)
                        md = t.history_metadata or {}
                        ln = str(md.get("longName") or "")
                        sn = str(md.get("shortName") or "")
                    except Exception:
                        pass
                return ln, sn

            probed = []
            for sym in sorted(div_syms):
                rt, suf = _MH.symbol_root(sym)
                if (suf != "US" or lst._has_ca_twin(rt, sym.upper())
                        or lst._declared_distinct(f"{rt}.US", f"{rt}.TO")):
                    continue
                # The exports already name the two differently (a CDR,
                # another issuer): nothing to verify. A pair the books
                # cannot compare stays a candidate TO VERIFY.
                if lst._verdict(f"{rt}.US", f"{rt}.TO")[0] in _MH.APART:
                    continue
                _twin = _yf(f"{rt}.TO")
                if not _twin:
                    continue
                try:
                    hist = yf.Ticker(_twin).history(period="5d",
                                                    timeout=5)
                    if hist is not None and not hist.empty:
                        probed.append(rt)
                except Exception:
                    continue
            for rt in probed:
                # A .TO line that exists is very often the CDR, not a
                # cross-listing — check what the exchange calls it
                # before suggesting a merge that would be wrong. An
                # unheld CDR twin is silently skipped (nothing in the
                # books to rule on); a HELD one is surfaced as
                # CDR-PAIR by the issuer-name clustering below.
                _tl, _ts = _issuer_names(f"{rt}.TO")
                if _is_cdr_name(_tl) or _is_cdr_name(_ts):
                    continue
                findings.append((
                    "MAP-GAP?", "-", f"{rt}.US",
                    f"{rt}.TO trades on the TSX — VERIFY whether it is "
                    f"the same issuer; if so, add "
                    f"`TOBASE {rt}.US {rt}.TO` to ticker.map."))

            _name_cluster_tips(lst, holdings, findings, _issuer_names)


def _name_cluster_tips(lst: _Listings, holdings: Dict[str, List[Any]],
                       findings: List[Tuple[str, str, str, str]],
                       _issuer_names: Callable[[str], Tuple[str, str]]) -> None:
    """`tjs tips --online`'s issuer names: each held listing's (and its
    map partners'), at most 80 probed; a ticker.map pair the exchanges
    name apart (MAP-BAD?), and a US and a Canadian listing held under
    one issuer's name with no map line (MAP-GAP?, or CDR-PAIR when one
    is a receipt); the findings added to `findings`."""
    from taxjson.lib import map_hygiene as _MH
    # Issuer-name evidence — the only way to catch
    # DIFFERENT-root dual listings (SAMPLQ.US/SAMPLP.TO) and typo'd
    # map pairs, which same-root scanning can never see.

    held_syms = {str(h.get("symbol") or "").upper()
                 for rows in holdings.values() for h in rows}
    held_syms = {s for s in held_syms
                 if _MH.symbol_root(s)[1]
                 in lst._CA_SUFS + ("US",)}
    _NAME_CAP = 80
    # The held listings and the map partners OF THEM — never
    # every tobase.map pair: the master's 3,800 lines spent the
    # cap on AAB.TO..AFE.TO and the holdings were never probed
    # (2026-10 workflow audit).
    _probe_list = sorted(
        held_syms
        | {lst.renames_u[s] for s in held_syms if s in lst.renames_u}
        | {k for k, v in lst.renames_u.items() if v in held_syms})
    if len(_probe_list) > _NAME_CAP:
        note("tjs tips",
             f"probing issuer names for the first {_NAME_CAP} "
             f"of {len(_probe_list)} held listings",
             details=["Alphabetical; the remainder are NOT "
                      "probed — every run scans the same "
                      "window."])
        _probe_list = _probe_list[:_NAME_CAP]
    # yfinance logs every miss as a raw line of its own: the
    # findings below say what matters.
    import logging as _lg
    _lg.getLogger("yfinance").setLevel(_lg.CRITICAL)
    _pairs = {s: _issuer_names(s) for s in _probe_list}
    names = {s: (ln or sn) for s, (ln, sn) in _pairs.items()}
    is_cdr = {s: _is_cdr_name(ln) or _is_cdr_name(sn)
              for s, (ln, sn) in _pairs.items()}

    # MAP-VERIFY: each defined pair must name ONE issuer — a
    # typo'd pair silently merges two companies' ACB pools.
    # A CDR paired with anything is wrong even when the issuer
    # matches: the receipt ratio floats, so no consolidation
    # ratio exists.
    for old_, new in sorted(lst.renames_u.items()):
        na, nb = names.get(old_, ""), names.get(new, "")
        if (na and nb
                and is_cdr.get(old_, False)
                != is_cdr.get(new, False)):
            findings.append((
                "MAP-BAD?", "-", f"{old_}->{new}",
                f"ticker.map pairs a CDR with its underlying "
                f"({na!r} vs {nb!r}) — a CDR is a fractional "
                f"CAD-hedged receipt whose ratio floats; "
                f"remove the entry (look-alike listings are "
                f"never joined: no DISTINCT line is needed)."))
        elif na and nb and not _issuer_names_match(na, nb):
            findings.append((
                "MAP-BAD?", "-", f"{old_}->{new}",
                f"ticker.map pairs these, but the exchanges "
                f"name them {na!r} vs {nb!r} — VERIFY: a "
                f"wrong pair merges two companies' ACB "
                f"pools."))

    # MAP-GAP (different roots): held listings whose issuer
    # names match across a US and a Canadian line with no map
    # entry connecting them.
    _by_name: Dict[str, set] = {}
    for s, n in names.items():
        if s in held_syms and n:
            _by_name.setdefault(_norm_issuer_name(n),
                                set()).add(s)
    for key, syms in sorted(_by_name.items()):
        if not key or len(syms) < 2:
            continue
        # sorted: `syms` is a set, and its iteration order
        # numbered the findings differently on every run (S042-13).
        us = [s for s in sorted(syms)
              if _MH.symbol_root(s)[1] == "US"]
        ca = [s for s in sorted(syms)
              if _MH.symbol_root(s)[1] in lst._CA_SUFS]
        for u in us:
            for c in ca:
                if (u in lst.renames_u or c in lst.renames_u
                        or lst.renames_u.get(u) == c
                        or lst.renames_u.get(c) == u
                        or lst._declared_distinct(u, c)):
                    continue
                if is_cdr.get(c) or is_cdr.get(u):
                    # Same issuer, but one side is a CDR —
                    # NOT a mappable listing pair. Suggest
                    # recording the ruling, not a TOBASE.
                    findings.append((
                        "CDR-PAIR", "-", f"{u}/{c}",
                        f"{names.get(c) or names.get(u)!r} "
                        f"is a CDR over {u} — a fractional "
                        f"CAD-hedged receipt, not a listing "
                        f"equivalent; nothing to do: "
                        f"look-alike listings are never "
                        f"joined."))
                    continue
                findings.append((
                    "MAP-GAP?", "-", f"{u}/{c}",
                    f"both held and both named "
                    f"{names[u]!r} — looks like one issuer's "
                    f"two listings with no ticker.map entry; "
                    f"VERIFY and add `TOBASE {u} {c}` if "
                    f"so."))


def _register_tips(sub: argparse._SubParsersAction) -> None:
    p_tips = sub.add_parser(
        "tips",
        help="Tax-efficiency tips for next year",
        description="Advice for next year on where you hold what: "
                    "Canadian dividend payers held through their US "
                    "listing in a taxable account or a TFSA, US dividend "
                    "payers in a TFSA (unrecoverable withholding). It "
                    "changes no number of this year (listing pairs "
                    "ticker.map does not join: `tjs ticker-map "
                    "--suggest`). Exit 0 with or without tips; 2 when "
                    "the project cannot be read.")
    p_tips.add_argument("--online", action="store_true",
                        help="Also probe Yahoo Finance for .TO twins of "
                             "unmapped US-listed dividend payers and "
                             "check the map's pairs by issuer name")
    p_tips.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    p_tips.set_defaults(func=cmd_tips)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_tips(sub)
