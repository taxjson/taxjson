# Security Policy

## Reporting a vulnerability

If you believe you've found a security issue in taxjson — particularly anything that could lead to incorrect tax computation in a way an attacker could trigger, or any code path that mishandles file contents on a user's machine — please report it privately rather than opening a public issue.

**Email:** ckscijdtest@gmail.com

Please include:

- A short description of the issue and its impact
- Steps or a minimal fixture that reproduces it
- The version / commit hash you tested against

I aim to acknowledge reports within 7 days and to ship a fix or mitigation as quickly as the severity warrants. Coordinated disclosure is appreciated — please give me a reasonable window to ship a fix before public disclosure.

## Scope

In scope:

- Bugs in cost-basis, wash-sale, superficial-loss, or corp-action math that produce demonstrably wrong tax numbers
- CSV parser bugs that could lead to data corruption or crashes given a maliciously crafted input file
- Anywhere the tool writes files outside its expected output paths

Out of scope:

- Numerical disagreement with your broker's tax slip when the slip itself is wrong (open a regular issue)
- Missing brokerage support, missing country rules, missing features (open a regular feature request)

## Network access and data egress

The core pipeline reaches the network in exactly two places, both
during `taxjson run`, and only when a cache miss requires it:

- **FX rates** — `taxjson-to-base-curr` downloads the base-currency
  pairs listed under `source_currencies` from Yahoo Finance into
  `work/to_base.csv`. Only currency-pair symbols and date ranges are
  sent.
- **Crypto prices** — `taxjson-fill-crypto` looks up any crypto row
  that carries no price (Kraken staking rewards) from Yahoo Finance:
  the symbol and trade date are sent, nothing else.

Set `TAXJSON_OFFLINE=1` to forbid both; a cache miss then fails the
stage with a message naming what it needed. The same switch covers the
current-price chain behind `harvest`, `watch --harvest` and the GUI's
Harvest tab (IBKR gateway / Yahoo Finance): they serve
`work/.price_cache.json` only and refuse the lookup on a miss.
Everything else that touches the network is opt-in by command: `fetch`
(your broker's API, with your credentials), `scan --online` (Yahoo
Finance names), `verify` (Questrade positions), and
`taxjson-generate-parser`, which sends the ENTIRE sample CSV you hand
it to an LLM API — redact account numbers and names first.

Credentials: `~/.questrade_token` is written 0600 and rotated
atomically; tokens never appear in logs, `.diag` files or `work/`
artifacts. Prefer `$QUESTRADE_REFRESH_TOKEN` / `$IBKR_FLEX_TOKEN` over
the `--refresh-token` / `--flex-token` flags, which are visible in
`ps` and shell history. Credentialed requests refuse redirects and
non-HTTPS API servers. Project directories (`work/`, `reports/`,
`inputs/<account>/`) are created 0700; the fetched statements inside
carry your account numbers.

## Supported versions

This project is pre-1.0. Security fixes are applied to `main` only. There are no LTS branches.
