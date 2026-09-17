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
        return str(self.settings.get("country", "canada"))

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
                f"project directory (or pass --dir).")
        cfg = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        settings = cfg.get("settings", {})
        accounts = [
            Account(name=name,
                    type=a.get("type", "sheltered"),
                    crypto=bool(a.get("crypto", False)))
            for name, a in (cfg.get("accounts") or {}).items()
        ]
        return cls(root=root, settings=settings, accounts=accounts)
