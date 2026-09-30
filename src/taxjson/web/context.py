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
        # Canonical spelling: " canada" / "CA" raised "Unsupported
        # country" in the what-if view (S031-24).
        c = str(self.settings.get("country", "canada")).strip().lower()
        return {"ca": "canada", "us": "usa"}.get(c, c)

    @property
    def base_currency(self) -> str:
        return str(self.settings.get("base_currency", "CAD"))

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
