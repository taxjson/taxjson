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

The core taxjson package holds no broker API client and never reads a
broker credential. Its pipeline reaches the network in exactly two
places, both during `taxjson run`, and only when a cache miss requires
it:

- **FX rates** — `taxjson-to-base-curr` downloads the base-currency
  pairs listed under `source_currencies` into `work/to_base.csv`: from
  the Bank of Canada Valet API (www.bankofcanada.ca) for a CAD base
  (its daily rate from 2017-03-01, its legacy noon rate from 2007-05-01
  to 2017-02-28), and from Yahoo Finance for dates before 2007-05-01,
  currencies the Bank does not publish, and non-CAD bases. Only currency-pair symbols
  and date ranges are sent.
- **Crypto prices** — `taxjson-fill-crypto` looks up any crypto row
  that carries no price (Kraken staking rewards) from Yahoo Finance:
  the symbol and trade date are sent, nothing else.

Set `TAXJSON_OFFLINE=1` (or `true`/`yes`/`on`; `0`/`false`/`no`/`off`
leave it off) to forbid both. A crypto-price cache miss then fails the
stage with a message naming what it needed; the FX stage serves cached
rates only, and a transaction whose date has no cached rate is a
validation error at the conversion stage. The same switch covers the
current-price chain behind `harvest` and `watch --harvest` (IBKR
gateway / Yahoo Finance): they serve
`work/.price_cache.json` only and refuse the lookup on a miss, and
`tips --online` skips its Yahoo Finance name probe with a note (the
offline checks still run). `taxjson fetch` refuses outright (one line,
before any fetcher plugin runs; `--list` still works). The release commands (`taxjson channels`,
and on a development machine `promote` / `deploy`) run `git fetch` in a
taxjson checkout against that checkout's own remote (GitHub for an
installed copy) — it sends nothing about your books; `TAXJSON_OFFLINE=1`
or `channels --offline` skips it. Everything else that touches the
network is opt-in by command: `taxjson-generate-parser`, which sends the first `--sample-lines`
(default 30) lines of the sample CSV you hand it to an LLM API. Those
lines are where broker exports keep the holder's name, account number
and address, so it scans them first and refuses to send a sample that
still carries an identity shape (`--allow-unredacted` overrides) — run
`taxjson redact` on the sample first.

**Broker fetch is a separate package.** `taxjson fetch` in the core is
a dispatcher over installed fetcher plugins (entry-point group
`taxjson.fetchers`); with none installed it only prints how to install
one. The Questrade / IBKR Flex fetcher is the `taxjson-fetch`
distribution (`packages/taxjson-fetch` in this repository; stdlib HTTP,
no third-party dependency). The one-line installer installs it by
default, so a default install carries this network code; it stays a
separate package, and `--without-fetch` (remembered for upgrades)
leaves it out or removes it. An install from before v0.19.0 (core only)
gains it on its next upgrade, and the installer says so: "adding
taxjson-fetch (installed by default since v0.19.0; re-run with
--without-fetch to keep it out)". It does nothing unless you run `taxjson
fetch` with an account that names a `brokerage` in `taxjson.toml`;
then it adds the broker egress: the
Questrade login and REST API (`https://login.questrade.com`, the
`https://*.questrade.com` API server the login names; `fetch
--positions` reads live positions) and the IBKR Flex Web Service
(`https://ndcdyn.interactivebrokers.com`), with your credentials, and
nothing else. A third-party fetcher plugin runs with your user's
rights inside `taxjson fetch` — install only ones you trust.

**Install only from the installer or a checkout.** taxjson and
taxjson-fetch are not published on PyPI yet, so a `taxjson` or
`taxjson-fetch` package there is not ours (anyone may register a free
name). The installer clones the GitHub release and installs the plugin
from that same checkout; from a checkout, install the core (`pip install -e .`)
before `pip install --no-deps -e packages/taxjson-fetch`, so the plugin's
`taxjson` dependency is met by the checkout and never fetched by name
(the installer and `scripts/dev-setup.sh` pass `--no-deps` too).

**What Yahoo Finance learns.** Every Yahoo lookup (FX fallback, crypto
prices, `harvest` / `watch --harvest` current prices, `tips --online`)
is a plain request from your IP address naming a symbol and a date
range — so Yahoo can see which tickers you hold or trade and roughly
when, though never quantities, prices paid or account numbers. Use
`TAXJSON_OFFLINE=1` (with a populated cache) if that matters to you.

Credentials (taxjson-fetch only — the core reads none):
`~/.questrade_token` is written 0600 and rotated
atomically (the temporary file is created fresh — never through a
symlink); tokens never appear in logs, `.diag` files or `work/`
artifacts. Prefer `$QUESTRADE_REFRESH_TOKEN` / `$IBKR_FLEX_TOKEN` over
the `--refresh-token` / `--flex-token` flags, which are visible in
`ps` and shell history. Credentialed requests refuse redirects, and
the Questrade access token is only ever sent to
`https://*.questrade.com` — an `api_server` elsewhere in the login
response is refused.

## Code that runs

taxjson never imports code from your project folder. The `taxjson` /
`tjs` console scripts do not put the current directory on `sys.path`,
and every child Python process taxjson starts (the corp-action
election prompt of `taxjson run`, `taxjson checklist`'s sub-commands,
`taxjson-safe-to-sell`'s radar) runs with `-P` (Python 3.11+) or a
bootstrap that drops the current directory before importing anything
(3.9 / 3.10). Running `python -m taxjson...` yourself from inside a
project folder is not covered: `python -m` imports from the current
directory first, so a `json.py` someone left there would run. Use the
console script, or `python -P -m` on 3.11+.

## Files on disk

`taxjson` and every `taxjson-*` tool set an owner-only umask (`077`)
at startup, so everything they create — `work/`, `reports/`,
`filed/`, `export/`, `checklist.json`, a new project's
`inputs/<account>/` — is `0600` (files) / `0700` (directories)
whatever your shell's umask. `taxjson fetch` (the taxjson-fetch
plugin) also tightens an existing `inputs/<account>/` to `0700` and writes the fetched
statements (which carry your account numbers) `0600`. Directories
created by earlier versions keep their old mode; tighten a project
once with `chmod -R go-rwx <project>`.

Files are replaced through a temporary sibling (`<file>.part`) that is
created fresh — an existing entry at that name is removed first and the
create refuses a symlink — then renamed into place. A symlink planted at
a temporary or a final name in `work/`, `reports/` or the project is
therefore never written through: the link itself is replaced by the new
file, and its target, inside or outside the project, is left alone.
`taxjson migrate` (which rewrites your `ticker.map` / `taxjson.toml`)
refuses, before writing anything, when either is a symlink to a file
outside the project; a link inside the project is kept and its target
updated.

Ids in audit output: `taxjson audit` and the gains traces (`explain`,
`--trace`) print each row's own id unmasked, on purpose — it is the
handle `--id` takes. For most brokers it is a content hash; for Kraken
it is the exchange's ledger txid (an exchange reference, never an
account number or wallet address). Parser messages mask such refs
(`LG***`). Review audit and trace output before sharing it.

**Keep a tax project repository PRIVATE.** A project is designed to be
versioned — `taxjson init` writes a `.gitignore` that commits
`inputs/` (your broker statements) and `taxjson.toml` (your accounts)
so the books can be rebuilt. That makes the repository itself
sensitive: host it only as a private repository (or not at all), never
fork it publicly, and never push it to the public taxjson repo. The
public repo's own `.gitignore` ignores `inputs/`, `work/`, `reports/`,
`filed/`, `export/`, spreadsheets and PDFs so a run inside a dev clone
cannot be staged by accident.

The development scan `scripts/check-pii.sh` (run by `scripts/ci.sh` and
the `pre-push` hook) reads two private files that live outside every
repository: the maintainer's denylist (`~/.config/taxjson/pii-denylist`,
regexes for account numbers and names) and the maintainer's private
figure list (`~/.config/taxjson/pii-amounts`, written by
`scripts/check-pii.sh --collect-amounts PROJECT_DIR...`: the distinctive
money figures of the maintainer's own project outputs, and the
distinctive amounts, prices, quantities, broker reference codes and
dated clock times of the raw exports under each project's `inputs/`, as
salted SHA-256 hashes, mode `0600`, no plain figures). A tree line, a pushed diff line,
or a commit or tag message holding one of those figures is refused with
its file and line only. Neither file is ever committed; a contributor
without them gets the generic checks.

`taxjson redact` strips the account numbers, names and contact details
it recognises from an export so it can be shared as a parser sample —
it is pattern-based, so review the output (the report lists the lines
to read) before attaching it anywhere.

## Supported versions

This project is pre-1.0. Security fixes are applied to `main` only. There are no LTS branches.
