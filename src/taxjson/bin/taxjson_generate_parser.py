#!/usr/bin/env python3
"""Draft a Python brokerage parser from a sample CSV using an LLM.

Maintainer-side tool. Reads a sample CSV from a new brokerage, feeds the
chosen LLM:
  - the BaseBrokerage class (so the model sees what helpers exist)
  - a complete example subclass (Questrade)
  - the canonical transaction schema
  - the CSV sample

…and writes a draft `BaseBrokerage` subclass to disk. You review the file,
fix anything the model got wrong, register the class in
taxjson_brokerage.py, and commit.

The runtime parsing path never calls this tool or any LLM — generated
parsers are checked-in Python files like the hand-written ones.

Provider:
  - claude (default) — uses Anthropic's claude-opus-4-7. Requires
    ANTHROPIC_API_KEY and `pip install '.[generate-parser]'`. The stable
    system prefix is prompt-cached, so re-running for a second brokerage
    in the same hour costs ~10% of the first call's input tokens.
  - gemini — uses gemini-2.5-flash. Requires GEMINI_API_KEY and the same
    install extra.

Usage:
    taxjson-generate-parser <sample.csv> -o <output.py> \\
        --brokerage-name "Schwab" --class-name SchwabBrokerage
    taxjson-generate-parser <sample.csv> -o <out.py> --provider gemini
"""

import argparse
import os
import re
import sys
import warnings
from pathlib import Path

from taxjson.lib.brokerages.schema import render_schema_prompt


# Stable across every invocation: instructions + canonical schema + BaseBrokerage
# source + a complete example. Claude caches this block (cache_control) so
# subsequent runs in the same ~5-minute window pay ~10% of input cost on it.
_SYSTEM_TEMPLATE = """You are an expert Python developer writing brokerage CSV parsers.

# Task
Write a single Python class that parses a brokerage's CSV export. The
class MUST subclass `BaseBrokerage` (provided below) and use the helper
methods it provides rather than re-implementing logic.

# Canonical transaction schema
The parser returns a list of dicts. This block is GENERATED from
`taxjson.lib.brokerages.schema` — the same table `taxjson-brokerage`
validates every parse against, so following it exactly means the
validator and conformance kit pass on the first try.

{schema_block}

Numeric field notes: price is the per-share execution price;
gross_amount is qty*price notional (self.theoretical_gross); fee is
absolute (self.back_compute_fee when the CSV doesn't break fees out);
commission only when the CSV has a real Commission column; quantity
sign via self.signed_quantity.

# BaseBrokerage source (the helpers available to you)
```python
{base_source}
```

# Complete example: a working subclass for Questrade
```python
{example_source}
```

# Output requirements
- Output ONLY valid Python source code for a single file.
- Subclass `BaseBrokerage` from `taxjson.lib.brokerages.base`.
- Implement `parse_file(self, path: Path) -> List[Dict[str, Any]]`.
- Prefer self.* helpers (apply_currency_suffix, signed_quantity,
  clean_number, back_compute_fee, theoretical_gross, parse_date,
  settlement_date_t1, parse_option_from_description, format_occ_symbol).
- Include the imports the file needs (csv, re, Path, etc.).
- DO NOT include the BaseBrokerage source — import it.
- DO NOT include any prose, markdown, or explanation around the code.
- DO NOT wrap the output in code fences — emit raw Python.
- Do not invent column names. If a field you'd want isn't in the sample,
  leave it out rather than guess.
- Do not invent action codes. If the sample doesn't show an action value
  for a case, omit handling for it; a human will add it on review.
"""


_USER_TEMPLATE = """Write the parser for the following brokerage.

Brokerage name: {brokerage_name}
Class name: {class_name}
DEFAULT_ACCOUNT class attribute: "{default_account}"

Sample CSV lines (first {n_lines}):
```
{sample_csv}
```
"""


def _load_text(p: Path) -> str:
    return p.read_text(encoding='utf-8')


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:python|py)?\n', '', text)
    text = re.sub(r'\n```$', '', text)
    return text.strip() + '\n'


def _read_sample(csv_path: Path, n: int) -> str:
    lines = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        for _ in range(n):
            line = f.readline()
            if not line:
                break
            lines.append(line.rstrip('\n'))
    return '\n'.join(lines)


def _default_class_name(brokerage_name: str) -> str:
    parts = re.findall(r'[A-Za-z0-9]+', brokerage_name)
    if not parts:
        return "NewBrokerage"
    return ''.join(p.capitalize() for p in parts) + "Brokerage"


def _call_claude(system_text: str, user_text: str, model_id: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("taxjson-generate-parser: error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        sys.exit(1)
    try:
        import anthropic
    except ImportError:
        print(
            "taxjson-generate-parser: error: anthropic SDK not installed. Run:\n"
            "    pip install '.[generate-parser]'",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=8192,
            system=[{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.APIStatusError as e:
        print(f"taxjson-generate-parser: error: Claude API error ({e.status_code}): {e.message}", file=sys.stderr)
        sys.exit(1)

    usage = response.usage
    cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
    cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0
    print(
        f"Claude usage: input={usage.input_tokens} "
        f"cache_write={cache_write} cache_read={cache_read} "
        f"output={usage.output_tokens}",
        file=sys.stderr,
    )
    return "".join(b.text for b in response.content if b.type == "text")


def _call_gemini(system_text: str, user_text: str, model_id: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("taxjson-generate-parser: error: GEMINI_API_KEY is not set", file=sys.stderr)
        sys.exit(1)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=FutureWarning)
            import google.generativeai as genai
    except ImportError:
        print(
            "taxjson-generate-parser: error: google-generativeai not installed. Run:\n"
            "    pip install '.[generate-parser]'",
            file=sys.stderr,
        )
        sys.exit(1)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_id)
    # Gemini has no system+cache split; concatenate.
    response = model.generate_content(system_text + "\n\n" + user_text)
    return response.text


_DEFAULT_MODELS = {
    "claude": "claude-opus-4-7",
    "gemini": "gemini-2.5-flash",
}


def main():
    here = Path(__file__).resolve().parents[1]
    base_path = here / 'lib' / 'brokerages' / 'base.py'
    example_path = here / 'lib' / 'brokerages' / 'questrade.py'

    parser = argparse.ArgumentParser(
        description=(
            "Draft a Python brokerage parser from a sample CSV using an LLM. "
            "Output is a Python file for manual review, NOT parsed transactions."
        ),
    )
    parser.add_argument("input_file", help="Sample CSV from the new brokerage")
    parser.add_argument(
        "-o", "--output", required=True,
        help="Path to write the draft parser .py file to",
    )
    parser.add_argument(
        "--provider", choices=["claude", "gemini"], default="claude",
        help="Which LLM to use (default: claude).",
    )
    parser.add_argument(
        "--model", default=None,
        help=(
            "Override the model ID. Defaults: "
            f"claude→{_DEFAULT_MODELS['claude']}, gemini→{_DEFAULT_MODELS['gemini']}"
        ),
    )
    parser.add_argument(
        "--brokerage-name", default=None,
        help="Human-readable brokerage name (e.g. 'Charles Schwab'). "
             "Defaults to the input file stem.",
    )
    parser.add_argument(
        "--class-name", default=None,
        help="Override the generated class name. Default derived from --brokerage-name.",
    )
    parser.add_argument(
        "--default-account", default=None,
        help="DEFAULT_ACCOUNT class attribute. Defaults to --brokerage-name.",
    )
    parser.add_argument(
        "--sample-lines", type=int, default=30,
        help="Number of leading CSV lines to send to the model (default: 30)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"taxjson-generate-parser: error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    if not base_path.exists() or not example_path.exists():
        print(f"taxjson-generate-parser: error: base.py or questrade.py not found in {here / 'lib' / 'brokerages'}",
              file=sys.stderr)
        sys.exit(1)

    brokerage_name = args.brokerage_name or input_path.stem
    class_name = args.class_name or _default_class_name(brokerage_name)
    default_account = args.default_account or brokerage_name
    model_id = args.model or _DEFAULT_MODELS[args.provider]

    sample = _read_sample(input_path, args.sample_lines)
    if not sample:
        print(f"taxjson-generate-parser: error: {input_path} is empty", file=sys.stderr)
        sys.exit(1)

    system_text = _SYSTEM_TEMPLATE.format(schema_block=render_schema_prompt(), 
        base_source=_load_text(base_path),
        example_source=_load_text(example_path),
    )
    user_text = _USER_TEMPLATE.format(
        brokerage_name=brokerage_name,
        class_name=class_name,
        default_account=default_account,
        n_lines=args.sample_lines,
        sample_csv=sample,
    )

    print(
        f"Calling {args.provider} ({model_id}) for {brokerage_name} ({class_name})...",
        file=sys.stderr,
    )
    if args.provider == "claude":
        raw = _call_claude(system_text, user_text, model_id)
    else:
        raw = _call_gemini(system_text, user_text, model_id)

    code = _strip_code_fences(raw)

    try:
        compile(code, str(output_path), 'exec')
    except SyntaxError as e:
        print(
            f"taxjson-generate-parser: error: generated code has syntax errors: {e}\n"
            "Writing it to <output>.bad for inspection.",
            file=sys.stderr,
        )
        bad_path = output_path.with_suffix(output_path.suffix + '.bad')
        bad_path.write_text(code, encoding='utf-8')
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(code, encoding='utf-8')

    print(f"Draft parser written to {output_path}", file=sys.stderr)
    print(
        "\nNext steps:\n"
        f"  1. Read {output_path} top to bottom. Verify column names match the CSV.\n"
        "  2. Hand-test with a sample file:\n"
        f"       python -c \"from {output_path.stem} import {class_name}; "
        f"print({class_name}().parse_file('{input_path}')[:3])\"\n"
        "  3. Confirm the model used self.* helpers — not inlined regex/date logic.\n"
        "  4. Register the class in taxjson/bin/taxjson_brokerage.py.\n"
        "  5. Add a test against a real CSV sample.\n",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
