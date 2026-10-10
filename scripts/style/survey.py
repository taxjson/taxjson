#!/usr/bin/env python3
"""Run every `taxjson help --all` command on the synthetic style projects
(tests/_style.py; tests/fixtures/style/) and save each command's
stdout+stderr, as a person piping it would see it (width 100, offline).

    python3 scripts/style/survey.py OUTDIR          # captures
    python3 scripts/style/measure.py OUTDIR          # ranking, worst first

Each capture is OUTDIR/{canada,usa,none}/<label>.txt, starting
`$ tjs <args>` and `# exit N`. Synthetic data only. Not run: fetch
(network; `fetch --list` is), deploy and promote (they change this
machine), checklist --walk (interactive). docs/output-style.md.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))
import _style  # noqa: E402

COMMON = [
    ("format", ["format"]), ("migrate", ["migrate", "--dry-run"]),
    ("elect", ["elect"]), ("elect_margin", ["elect", "margin"]),
    ("crypto-sends", ["crypto-sends"]),
    ("find-missing-history", ["find-missing-history"]),
    ("opening", ["opening", "margin", "holdings.toml", "--dry-run"]),
    ("estimate", ["estimate"]), ("fx-cash", ["fx-cash"]),
    ("fx-cash_events", ["fx-cash", "--events"]),
    ("instalments", ["instalments"]), ("stats", ["stats"]),
    ("sum", ["sum"]), ("sum_verbose", ["sum", "--verbose"]),
    ("list", ["list"]), ("shares", ["shares", "--options"]),
    ("dil", ["dil"]), ("divs", ["divs"]), ("events", ["events"]),
    ("fees", ["fees"]), ("gains", ["gains"]), ("leaps", ["leaps"]),
    ("roc", ["roc"]), ("trades", ["trades"]), ("transfers", ["transfers"]),
    ("ccd-sum", ["ccd-sum"]), ("dil-sum", ["dil-sum"]),
    ("divs-sum", ["divs-sum"]), ("fees-sum", ["fees-sum"]),
    ("leaps-sum", ["leaps-sum"]), ("roc-sum", ["roc-sum"]),
    ("trades-sum", ["trades-sum"]), ("winners", ["winners"]),
    ("wash-radar", ["wash-radar"]),
    ("wash-radar_date", ["wash-radar", "--date", "2024-11-25", "--all"]),
    ("buy-check", ["buy-check", "QZQ{sfx}"]),
    ("sell-check", ["sell-check", "SAMPA{sfx}"]),
    ("harvest", ["harvest", "--no-ibkr", "--options"]),
    ("harvest_crypto", ["harvest", "--no-ibkr", "--crypto"]),
    ("tips", ["tips"]), ("checklist", ["checklist"]),
    ("form-export", ["form-export"]),
    ("reconcile-slips", ["reconcile-slips", "{slip}"]),
    ("carryover", ["carryover"]), ("handoff", ["handoff"]),
    ("audit_summary", ["audit", "--summary"]),
    ("audit_sym", ["audit", "QZQ{sfx}", "--no-color"]),
    ("wash-sales", ["wash-sales"]),
    ("wash-sales_explain", ["wash-sales", "--explain"]),
    ("tax-logic", ["tax-logic"]), ("edge-cases", ["edge-cases"]),
    ("check-dates", ["check-dates"]),
    ("sanity", ["sanity", "margin=holdings.toml"]),
    ("renames", ["renames"]), ("spinoffs", ["spinoffs"]),
    ("splits", ["splits"]), ("amt", ["amt"]), ("t1135", ["t1135"]),
    ("option-boundary", ["option-boundary"]),
    ("journals", ["journals"]), ("ticker-map", ["ticker-map", "--suggest"]),
    ("years", ["years"]),
    # --details: the long form the default view leaves out
    # (docs/output-style.md, Essentials first).
    ("sum_details", ["sum", "--details"]),
    ("checklist_details", ["checklist", "--all"]),
    ("t1135_details", ["t1135", "--details"]),
    ("sanity_details", ["sanity", "margin=holdings.toml", "--details"]),
    ("wash-sales_details", ["wash-sales", "--details"]),
    ("find-missing-history_details", ["find-missing-history",
                                      "--details"]),
    ("carryover_details", ["carryover", "--details"]),
    ("wash-radar_details", ["wash-radar", "--details"]),
    ("form-export_details", ["form-export", "--details"]),
]
PER_COUNTRY = {"canada": [("estimate_province",
                           ["estimate", "--province", "ON"]),
                          ("estimate_province_details",
                           ["estimate", "--province", "ON", "--details"]),
                          ("slip-audit_details", ["slip-audit", "--details"]),
                          ("form-export_schedule3",
                           ["form-export", "--form", "schedule3"]),
                          ("slip-audit", ["slip-audit"]),
                          ("update-tobase-map", ["update-tobase-map"])],
               "usa": [("form-export_txf", ["form-export", "--form", "txf"])]}
NOCOUNTRY = [("help", ["help"]), ("help_all", ["help", "--all"]),
             ("help_wash-sales", ["help", "wash-sales"]),
             ("fetch_list", ["fetch", "--list"]),
             ("channels", ["channels", "--offline"]),
             ("redact_check", ["redact", "--check",
                               str(REPO / "examples/questrade_demo.csv")])]


def _save(out: Path, label: str, args, r) -> int:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}.txt").write_text(
        f"$ tjs {' '.join(args)}\n# exit {r.returncode}\n"
        + (r.stdout or "") + (r.stderr or ""))
    return r.returncode


def main(argv):
    if len(argv) != 1:
        sys.exit("usage: survey.py OUTDIR")
    outdir = Path(argv[0])
    codes = {}
    for country in ("canada", "usa"):
        p = _style.project(country)
        sfx = ".TO" if country == "canada" else ".US"
        slip = "inputs/slips/" + ("t5008.csv" if country == "canada"
                                  else "1099b.csv")
        od = outdir / country
        for label, a in COMMON + PER_COUNTRY[country]:
            a = [x.format(sfx=sfx, slip=slip) for x in a]
            codes[f"{country}/{label}"] = _save(od, label, a, p.run(*a))
        # Commands that write: on a copy.
        w = _style.Project(Path(str(p.root) + "_w"), country)
        shutil.rmtree(w.root, ignore_errors=True)
        shutil.copytree(p.root, w.root)
        for label, a in (("run", ["run", "--no-input"]),
                         ("watch", ["watch", "--no-ibkr", "--state",
                                    str(w.root / "watch.json")]),
                         ("close-year", ["close-year", "--force"]),
                         ("check-filed", ["check-filed"]),
                         ("run_details", ["run", "--no-input", "--fast",
                                          "--details"]),
                         ("redact", ["redact"]),
                         ("new-year", ["new-year", "2025"])):
            codes[f"{country}/{label}"] = _save(od, label, a, w.run(*a))
        pend = _style.project(country, pending=True)
        pw = _style.Project(Path(str(pend.root) + "_w"), country)
        shutil.rmtree(pw.root, ignore_errors=True)
        shutil.copytree(pend.root, pw.root)
        agg = json.loads((pw.root / "work" /
                          "pending_elections.json").read_text())
        eid = agg["accounts"]["margin"]["pending"][0]["event_id"]
        for label, a in (
                ("elect_pending", ["elect", "--pending"]),
                ("run_pending", ["run", "--no-input"]),
                ("elect_set_bad", ["elect", "margin", "--set",
                                   f"{eid}=bogus"]),
                ("elect_set", ["elect", "margin", "--set",
                               f"{eid}={_style._ELECTION[country]}"])):
            codes[f"{country}/{label}"] = _save(od, label, a, pw.run(*a))
        ini = Path(str(p.root) + "_init")
        a = ["init", "--country", country, "--year", "2024", str(ini)]
        codes[f"{country}/init"] = _save(od, "init", a, p.run(*a))
    for label, a in NOCOUNTRY:
        r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                            *a], cwd=REPO, capture_output=True, text=True,
                           env=_style.env(), stdin=subprocess.DEVNULL)
        codes[f"none/{label}"] = _save(outdir / "none", label, a, r)
    (outdir / "exit_codes.json").write_text(json.dumps(codes, indent=1))
    print(f"{len(codes)} captures in {outdir}")


if __name__ == "__main__":
    main(sys.argv[1:])
