"""The account-type (and quoted-boolean) checks every config reader applies.

Every filing command partitions accounts with an exact match on
`type == "taxable"` / `"sheltered"`. A typo'd or missing type matches
neither, so the account silently vanished from estimate, instalments,
`sum` FOR THE RETURN, form-export and close-year, while only `taxjson
run` (through validate_config) refused the same config (R1-268). This
is the one home for that check, so the run, every read-only command
and the web UI refuse the same configs with the same message.
"""
import difflib
from typing import Any, Dict, List

ACCOUNT_TYPES = ("taxable", "sheltered")


def account_type_problems(cfg: Dict[str, Any]) -> List[str]:
    """One message per [accounts.*] entry that is not a table or whose
    `type` is missing or not exactly "taxable"/"sheltered". Empty on a
    valid config."""
    out: List[str] = []
    accounts = cfg.get("accounts") or {}
    if not isinstance(accounts, dict):
        return ["[accounts] must be a table of [accounts.<name>] sections"]
    for name, acfg in accounts.items():
        if not isinstance(acfg, dict):
            out.append(f"[accounts.{name}] must be a table")
            continue
        atype = acfg.get("type")
        if atype is None:
            # The old "default to sheltered" silently dropped an
            # untyped TAXABLE account's gains from every filing
            # command (2026-09 CLI audit).
            out.append(
                f"[accounts.{name}] has no `type` — it is required: "
                f"add type = \"taxable\" or type = \"sheltered\" "
                f"(taxable | sheltered). An untyped account would "
                f"otherwise be left out of the return.")
        elif atype not in ACCOUNT_TYPES:
            close = difflib.get_close_matches(str(atype).lower(),
                                              ACCOUNT_TYPES, n=1)
            hint = f" (did you mean {close[0]!r}?)" if close else ""
            out.append(
                f"[accounts.{name}] type must be \"taxable\" or "
                f"\"sheltered\", got {atype!r} — this account would "
                f"otherwise be silently dropped from the run{hint}")
    return out + bool_setting_problems(cfg)


def bool_setting_problems(cfg: Dict[str, Any]) -> List[str]:
    """A quoted boolean (`= "false"`) is truthy: every command but `run`
    read it as ON (audit S021-07 / S076-17 — carryover, check-filed,
    audit and close-year applied the strict buy-back rule; a crypto =
    "false" account was treated as crypto). Refused by every config
    reader, like `run` does."""
    out: List[str] = []
    settings = cfg.get("settings") or {}
    if isinstance(settings, dict):
        v = settings.get("option_buyback_loss_superficial")
        if v is not None and not isinstance(v, bool):
            out.append(f"[settings] option_buyback_loss_superficial must "
                       f"be true or false, unquoted (got {v!r})")
    accounts = cfg.get("accounts") or {}
    if isinstance(accounts, dict):
        for name, acfg in accounts.items():
            if isinstance(acfg, dict):
                v = acfg.get("crypto")
                if v is not None and not isinstance(v, bool):
                    out.append(f"[accounts.{name}] crypto must be true or "
                               f"false, unquoted (got {v!r})")
    return out
