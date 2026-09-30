#!/usr/bin/env python3
"""check_tax_rules — tax-logic is the spec; the tests must cite it.

Every statement `taxjson tax-logic` prints has a rule id (lib/tax_logic,
`catalog()`). Tests cite the ids they pin with the markers in
tests/tax_rules (`@rule("CA-SL-02")`, `@rule_absent("CA-SL-02",
country="usa")`). This check reads the markers with the AST (it never
imports a test) and fails when:

  1. an id is malformed, unknown to tax-logic, or retired
     (tests/tax_rules/retired.txt);
  2. one @rule names ids of both countries, or a @rule_absent names the
     rule's own country;
  3. a test tagged for one country only names the other country's engine
     or project (USATaxRules, country="usa", --country usa,
     country = "usa" in a toml ... and the reverse);
  4. a rule has no @rule test and is not in the shrink-only baseline
     tests/tax_rules/baseline-unpinned.txt — or a baseline entry now has
     a test (delete the line), or names an id that is not one of the
     seed ids the baseline started from (tests/tax_rules/seed-ids.txt:
     a NEW rule can never be baselined, it needs its test);
  5. a partition rule (tax_logic.PARTITION_RULES) has no @rule_absent test
     and is not in tests/tax_rules/baseline-unpaired.txt (same ratchet);
  6. a [settings] key (lib/country.SETTING_COUNTRY) is named by no
     rule's `keys` and not listed in tax_logic.NON_RULE_SETTINGS, or a
     VARIANT_AXES key is not a known setting;
  7. a statement in the Canada section has a US- id or the reverse.

Usage: scripts/check_tax_rules.py [--summary]
Exit 0 when clean; 1 with one line per problem.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taxjson.lib import country as C          # noqa: E402
from taxjson.lib import tax_logic as TL       # noqa: E402

TESTS = ROOT / "tests"
RULES_DIR = TESTS / "tax_rules"
BASELINE = RULES_DIR / "baseline-unpinned.txt"
UNPAIRED = RULES_DIR / "baseline-unpaired.txt"
RETIRED = RULES_DIR / "retired.txt"
SEED = RULES_DIR / "seed-ids.txt"
ID_RE = re.compile(r"^(CA|US)-[A-Z0-9]+(?:-[A-Z0-9]+)+$")

# A test tagged for one country must not name the other country's engine
# or project. (The runtime guard in tests/tax_rules catches the indirect
# cases: helpers, fixtures.)
_US_NAMES = [r"\bUSATaxRules\b",
             r"""\bcountry\s*=\s*\\?["'](?:usa|us|USA|US)\\?["']""",
             r"""["']--country["']\s*,\s*["'](?:usa|us)["']""",
             r"""get_tax_rules\(\s*["'](?:usa|us)["']"""]
_CA_NAMES = [r"\bCanadaTaxRules\b",
             r"""\bcountry\s*=\s*\\?["'](?:canada|ca|Canada|CA)\\?["']""",
             r"""["']--country["']\s*,\s*["'](?:canada|ca)["']""",
             r"""get_tax_rules\(\s*["'](?:canada|ca)["']"""]
FORBIDDEN = {C.CANADA: [re.compile(p) for p in _US_NAMES],
             C.USA: [re.compile(p) for p in _CA_NAMES]}


def read_ids(path: Path) -> List[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line.split()[0])
    return out


class Marked:
    """One test (method or class) and its markers."""

    def __init__(self, path: Path, node, cls=None):
        self.path, self.node, self.cls = path, node, cls
        self.rules: List[Tuple[Tuple[str, ...], int]] = []
        self.absent: List[Tuple[str, str, int]] = []

    @property
    def where(self) -> str:
        rel = self.path.relative_to(ROOT)
        name = (f"{self.cls.name}.{self.node.name}" if self.cls is not None
                and self.cls is not self.node else self.node.name)
        return f"{rel}:{self.node.lineno} {name}"


def _str_args(call: ast.Call) -> List[str]:
    return [a.value for a in call.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _decorators(node) -> Tuple[list, list]:
    rules, absent = [], []
    for d in node.decorator_list:
        if not isinstance(d, ast.Call):
            continue
        f = d.func
        name = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else "")
        if name == "rule":
            rules.append((tuple(_str_args(d)), d.lineno))
        elif name == "rule_absent":
            args = _str_args(d)
            country = next((k.value.value for k in d.keywords
                            if k.arg == "country"
                            and isinstance(k.value, ast.Constant)), "")
            absent.append((args[0] if args else "", str(country), d.lineno))
    return rules, absent


def collect(paths) -> List[Marked]:
    out: List[Marked] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            print(f"tax-rules: cannot parse {path}: {e}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                crules, cabsent = _decorators(node)
                if crules or cabsent:
                    m = Marked(path, node, node)
                    m.rules, m.absent = crules, cabsent
                    out.append(m)
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef,
                                        ast.AsyncFunctionDef)):
                        r, a = _decorators(sub)
                        if r or a or crules or cabsent:
                            m = Marked(path, sub, node)
                            m.rules = r + crules
                            m.absent = a + cabsent
                            out.append(m)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pass
        # module-level test functions (rare in this unittest suite)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                r, a = _decorators(node)
                if r or a:
                    m = Marked(path, node)
                    m.rules, m.absent = r, a
                    out.append(m)
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    problems: List[str] = []
    catalog = TL.catalog()
    known = set(catalog)
    retired = set(read_ids(RETIRED))
    seed = set(read_ids(SEED))

    # 7. prefix matches the section's country
    for c, prefix in ((C.CANADA, "CA-"), (C.USA, "US-")):
        for st in TL.variants(c):
            for _t, rules in TL.rule_sections(c, st):
                for r in rules:
                    if not r.id.startswith(prefix):
                        problems.append(f"{r.id} is in the {c} section of "
                                        f"tax-logic but has no {prefix} prefix")
                    if not ID_RE.match(r.id):
                        problems.append(f"{r.id}: malformed rule id")
    for rid in known & retired:
        problems.append(f"{rid} is in retired.txt but tax-logic still "
                        f"states it")

    files = sorted(p for p in TESTS.rglob("*.py")
                   if RULES_DIR not in p.parents)
    marked = collect(files)
    pinned: Dict[str, List[str]] = {}
    paired: Dict[str, List[str]] = {}
    for m in marked:
        # Class-level entries are listed once for the class itself and
        # once per method; check ids and the static country scan on the
        # methods (and on a marked class with no methods).
        is_class_entry = m.cls is m.node
        countries: Set[str] = set()
        for ids, line in m.rules:
            cs = set()
            for rid in ids:
                if not ID_RE.match(rid):
                    problems.append(f"{m.where}: @rule({rid!r}): malformed "
                                    f"id")
                    continue
                if rid in retired:
                    problems.append(f"{m.where}: @rule({rid!r}) names a "
                                    f"retired id")
                elif rid not in known:
                    problems.append(f"{m.where}: @rule({rid!r}): tax-logic "
                                    f"states no such rule")
                cs.add(TL.rule_country(rid))
                if not is_class_entry:
                    pinned.setdefault(rid, []).append(m.where)
            if len(cs) > 1:
                problems.append(f"{m.where}: one @rule{ids} names ids of "
                                f"both countries — one @rule per country")
            countries |= cs
        for rid, ctry, line in m.absent:
            if rid not in known:
                problems.append(f"{m.where}: @rule_absent({rid!r}): "
                                f"tax-logic states no such rule")
                continue
            try:
                cc = C.canonical_country(ctry)
            except C.CountryError:
                problems.append(f"{m.where}: @rule_absent({rid!r}, "
                                f"country={ctry!r}): unknown country")
                continue
            if cc == TL.rule_country(rid):
                problems.append(f"{m.where}: @rule_absent({rid!r}, "
                                f"country={ctry!r}) names the rule's own "
                                f"country")
            if not is_class_entry:
                paired.setdefault(rid, []).append(m.where)
            countries |= {cc, TL.rule_country(rid)}
        # 3. static cross-country scan of a single-country test
        if len(countries) == 1 and not is_class_entry:
            own = next(iter(countries))
            seg = ast.get_source_segment(m.path.read_text(encoding="utf-8"),
                                         m.node) or ""
            for pat in FORBIDDEN[own]:
                hit = pat.search(seg)
                if hit:
                    problems.append(
                        f"{m.where} is tagged {own}-only but names "
                        f"{hit.group(0)!r} — use @rule_absent(..., "
                        f"country=...) for a dual-country test")
                    break

    # 4. unpinned rules vs the shrink-only baseline
    baseline = read_ids(BASELINE)
    for rid in baseline:
        if rid not in known:
            problems.append(f"baseline-unpinned.txt: {rid} is not a "
                            f"tax-logic rule (delete the line)")
        elif rid in pinned:
            problems.append(f"baseline-unpinned.txt: {rid} now has a test "
                            f"({pinned[rid][0]}) — delete the line (the "
                            f"baseline only shrinks)")
        elif rid not in seed:
            problems.append(f"baseline-unpinned.txt: {rid} was not in the "
                            f"seed (seed-ids.txt) — a new rule needs a "
                            f"test, not a baseline entry")
    for rid in sorted(known - set(pinned) - set(baseline)):
        problems.append(f"{rid} has no test (add @rule({rid!r}) to the "
                        f"test that pins it)")

    # 5. partition rules need a @rule_absent pair
    unpaired = read_ids(UNPAIRED)
    for rid in unpaired:
        if rid not in TL.PARTITION_RULES:
            problems.append(f"baseline-unpaired.txt: {rid} is not in "
                            f"tax_logic.PARTITION_RULES (delete the line)")
        elif rid in paired:
            problems.append(f"baseline-unpaired.txt: {rid} now has a "
                            f"@rule_absent test ({paired[rid][0]}) — "
                            f"delete the line")
        elif rid not in seed:
            problems.append(f"baseline-unpaired.txt: {rid} was not in the "
                            f"seed — a new partition rule needs its "
                            f"@rule_absent test")
    for rid in sorted(TL.PARTITION_RULES):
        if rid not in known:
            problems.append(f"PARTITION_RULES: {rid} is not a tax-logic "
                            f"rule")
        elif rid not in paired and rid not in unpaired:
            problems.append(f"{rid} is a partition rule with no "
                            f"@rule_absent test (the same book under the "
                            f"other country)")

    # 6. settings coverage
    named: Set[str] = set()
    for r in catalog.values():
        named |= set(r.keys)
    for key in C.SETTING_COUNTRY:
        if key not in named and key not in TL.NON_RULE_SETTINGS:
            problems.append(f"[settings] {key} changes behaviour but no "
                            f"tax-logic rule names it (Rule.keys) and it "
                            f"is not in NON_RULE_SETTINGS")
    for c, axes in TL.VARIANT_AXES.items():
        for key in axes:
            if key not in C.SETTING_COUNTRY:
                problems.append(f"VARIANT_AXES[{c}] {key}: not a known "
                                f"[settings] key (lib/country)")

    for p in problems:
        print(f"tax-rules: {p}")
    if "--summary" in argv or not problems:
        n_pin = len(set(pinned) & known)
        print(f"tax-rules: {len(known)} rules, {n_pin} pinned by a test, "
              f"{len(set(baseline) & known)} in the unpinned baseline; "
              f"{len(TL.PARTITION_RULES)} partition rules, "
              f"{len(set(paired) & set(TL.PARTITION_RULES))} with a "
              f"@rule_absent pair, {len(unpaired)} in the unpaired "
              f"baseline; {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
