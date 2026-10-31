"""The run's gains passes over a project's hand-written books (tests only).

A test that writes `work/<account>_base.json` itself, with no inputs/,
gets the files `tjs run` leaves after its books: each account's own
gains pass (`<account>_gains.json` and its cost timeline), then the
blended pass over the taxable equity accounts (`<account>_gains_wash.json`
and `.blend_gains_wash.timeline.json`), through the run's own stage
function — what the views (`list --date`) read."""
import contextlib
import io
from pathlib import Path


def run_books(root: Path) -> None:
    from taxjson.bin import taxjson_run as R
    from taxjson.lib.pipeline import income_dating_flags, option_timing_flags
    root = Path(root)
    cfg = R.load_config(root)
    settings = cfg["settings"]
    cache = root / "work"
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    country = R._normalize_country(settings["country"])
    blend = []
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        for name, acfg in (cfg.get("accounts") or {}).items():
            base = cache / f"{name}_base.json"
            if not base.exists():
                continue
            taxable = acfg.get("type") == "taxable"
            crypto = bool(acfg.get("crypto"))
            gains = cache / f"{name}_gains.json"
            cmd = (R._cmd("taxjson-gains")
                   + ["--country", country, "--year", str(settings["year"]),
                      "--tax-date", R._tax_date(settings)]
                   + R._wash_flags(taxable, crypto, country)
                   + option_timing_flags(settings)
                   + income_dating_flags(settings)
                   + ["--cost-timeline", str(R.timeline_beside(gains)),
                      str(base)])
            R.run_to_file(cmd, gains)
            if taxable and not crypto:
                blend.append(name)
        if blend:
            sheltered = cache / "sheltered_base.json"
            R.stage_blended_wash_pass(
                blend, settings, cache, reports,
                sheltered if sheltered.exists() else None)
