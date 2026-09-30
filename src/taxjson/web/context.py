"""Project context for the web UI: read taxjson.toml and expose the account
list and the canonical cache/reports paths. Pure Python (no FastAPI)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.tomlcompat import tomllib


@dataclass
class Account:
    name: str
    type: str            # 'taxable' | 'sheltered'
    crypto: bool = False


@dataclass
class ProjectContext:
    root: Path
    settings: Dict[str, Any]
    accounts: List[Account]

    # Canonical layout — mirrors taxjson_run.py.
    @property
    def cache(self) -> Path:
        return self.root / "work"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def country(self) -> str:
        # Canonical (lib/country); load() refused a missing / unknown
        # country, so this never guesses (S031-24, partition INPUTS-08).
        from taxjson.lib.country import settings_country
        return settings_country(self.settings)

    @property
    def tax_date(self) -> str:
        """The date basis in force (lib/country.settings_tax_date)."""
        from taxjson.lib.country import settings_tax_date
        return settings_tax_date(self.settings)

    @property
    def base_currency(self) -> str:
        from taxjson.lib.country import home_currency
        return str(self.settings.get("base_currency")
                   or home_currency(self.country))

    @property
    def year(self) -> Optional[int]:
        return self.settings.get("year")

    def taxable(self) -> List[Account]:
        return [a for a in self.accounts if a.type == "taxable"]

    def sheltered(self) -> List[Account]:
        return [a for a in self.accounts if a.type == "sheltered"]

    def account(self, name: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.name == name), None)

    @classmethod
    def load(cls, root) -> "ProjectContext":
        root = Path(root).resolve()
        toml_path = root / "taxjson.toml"
        if not toml_path.exists():
            raise FileNotFoundError(
                f"no taxjson.toml in {root} — run `taxjson serve` from a "
                f"project directory (or pass `taxjson -C DIR serve`).")
        cfg = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        # A missing / typo'd account type used to default to sheltered
        # here, hiding a taxable account's gains; refuse it like every
        # CLI command does (R1-268).
        from taxjson.lib.config_check import account_type_problems
        problems = account_type_problems(cfg)
        # Flags are TOML booleans, as `taxjson run` requires
        # (validate_config): bool("false") is True, so a quoted "false"
        # turned an equity account into a crypto one here (S078-15).
        for name, a in (cfg.get("accounts") or {}).items():
            if not isinstance(a, dict):
                continue
            for flag in ("crypto", "transfers"):
                if flag in a and not isinstance(a[flag], bool):
                    problems.append(
                        f"[accounts.{name}] {flag} must be true/false "
                        f"(a TOML boolean, unquoted), got {a[flag]!r}")
        if not problems:
            # Country (required), date basis, base currency and every
            # setting the country does not own: the same check as the
            # CLI's config readers (lib/config_check.settings_problems).
            from taxjson.lib.config_check import settings_problems
            problems = settings_problems(cfg)
        if problems:
            raise ValueError(f"{toml_path}: " + "; ".join(problems))
        settings = cfg.get("settings", {})
        accounts = [
            Account(name=name,
                    type=a["type"],
                    crypto=bool(a.get("crypto", False)))
            for name, a in (cfg.get("accounts") or {}).items()
        ]
        return cls(root=root, settings=settings, accounts=accounts)
