# Security Policy

## Reporting a vulnerability

If you believe you've found a security issue in taxjson — particularly anything that could lead to incorrect tax computation in a way an attacker could trigger, or any code path that mishandles file contents on a user's machine — please report it privately rather than opening a public issue.

- **GitHub:** the repository's **Security** tab → **Report a
  vulnerability** (GitHub's private vulnerability reporting:
  https://github.com/taxjson/taxjson/security/advisories/new). The
  report is visible only to you and the maintainers.
- **Email:** ckscijdtest@gmail.com

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
broker credential. Parsing, the books, the superficial-loss / wash-sale
pass and every report run on your machine. Below is every place taxjson
reaches the network, what it sends, and how to turn it off. Every lookup
is cached under the project's `work/`, so a repeat run sends nothing new.

| When | Where | What is sent |
| --- | --- | --- |
| `taxjson run`, FX stage (`taxjson-to-base-curr`), on a cache miss | Bank of Canada Valet API (www.bankofcanada.ca) for a CAD base: the daily rate from 2017-03-01, the legacy noon rate from 2007-05-01 to 2017-02-28 | currency-pair series names and a date range |
| the same stage, on a cache miss | Yahoo Finance (the `[fx]` extra, yfinance): rates before 2007-05-01, currencies the Bank of Canada does not publish, and every rate for a non-CAD base | the pair symbol (e.g. `EURCAD=X`) and a date range |
| `taxjson run`, crypto stage (`taxjson-fill-crypto`): a crypto row the export left unpriced (staking rewards, for example) | Yahoo Finance chart API | the coin's Yahoo id (`<COIN>-USD`) and the day |
| `taxjson crypto-sends` and `taxjson run`: a send you marked `gift` or `payment` whose row carries no price of its own | Yahoo Finance chart API (the same lookup as above) | the coin's Yahoo id and the day of the send |
| `taxjson run`: an in-kind move between a taxable and a registered account with no `INKIND` line and no market value on the broker's row | Yahoo Finance (yfinance): the security's close on the transfer date, marked ESTIMATED | the symbol's Yahoo spelling and the date |
| `taxjson harvest`, `taxjson watch --harvest` | first a running IB TWS / IB Gateway on this machine (127.0.0.1, the `[ibkr]` extra; it asks Interactive Brokers with your own session), then Yahoo Finance | the symbols you hold |
| `taxjson tips --online` | Yahoo Finance | the symbols it probes for a cross-listed twin |
| `taxjson channels`; on a development machine `promote` and `deploy` | `git fetch` against the taxjson checkout's own remote (GitHub for an installed copy) | nothing about your books |
| `taxjson fetch` (only with the taxjson-fetch plugin, below) | your broker's API | your credentials and the account and date range to download |
| `taxjson-generate-parser` (a developer tool, opt-in) | the LLM API you choose (Anthropic or Google, with your API key) | the first `--sample-lines` lines (default 30) of the sample CSV you give it |
| the installer | taxjson.com (the script), github.com (the release), PyPI (the dependencies pip installs, never taxjson itself) | an ordinary download |

**Turning it off.** Set `TAXJSON_OFFLINE=1` (or `true`/`yes`/`on`;
`0`/`false`/`no`/`off` leave it off) to forbid every lookup in the
first seven rows. Each then serves its cache only:

- FX: a transaction whose date has no cached rate is a validation error
  at the conversion stage.
- Crypto prices: a cache miss fails the stage with a message naming what
  it needed; an unpriced crypto send is listed as having no fair value.
- In-kind moves: the run stops and prints the `INKIND` line to add (the
  value from your own records).
- `harvest` / `watch --harvest` serve `work/.price_cache.json` and refuse
  the lookup on a miss; `tips --online` skips its probe with a note (the
  offline checks still run).
- `taxjson fetch` refuses outright (one line, before any fetcher plugin
  runs; `--list` still works); `taxjson channels` skips its `git fetch`
  (as does `channels --offline`).

`taxjson-generate-parser` sends only when you run it. Its sample lines are
where broker exports keep the holder's name, account number and address,
so it scans them first and refuses a sample that still carries an
identity shape (`--allow-unredacted` overrides): run `taxjson redact` on
the sample first.

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
prices, crypto-send values, in-kind move closes, `harvest` / `watch
--harvest` current prices, `tips --online`) is a plain request from your
IP address naming a symbol and a date or date range — so Yahoo can see
which tickers you hold or trade and roughly when, though never
quantities, prices paid or account numbers. Use `TAXJSON_OFFLINE=1`
(with a populated cache) if that matters to you.

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
at startup (a tool run as `python -m taxjson.bin.<tool>` too), so everything they create — `work/`, `reports/`,
`filed/`, `export/`, `checklist.json`, a new project's
`inputs/<account>/` — is `0600` (files) / `0700` (directories)
whatever your shell's umask. `taxjson fetch` (the taxjson-fetch
plugin) also tightens an existing `inputs/<account>/` to `0700` and writes the fetched
statements (which carry your account numbers) `0600`. Directories
created by earlier versions keep their old mode; `taxjson run` warns
once per run when the project folder, `inputs/` or `reports/` is open
to other users, naming the command that tightens the project once:
`chmod -R go-rwx <project>`.

Files are replaced through a temporary file of their own: a new,
uniquely named sibling (`<file>.<random>.part`, made by
`tempfile.mkstemp` — `O_CREAT | O_EXCL | O_NOFOLLOW`, mode `0600`, so it
never reuses or follows anything already at a name), written, flushed
and fsync'd, then renamed over the final name. A symlink planted at a
final name in `work/`, `reports/` or the project is therefore never
written through: the link itself is replaced by the new file, and its
target, inside or outside the project, is left alone. Lock files
(`work/.run.lock`, the price caches', the fetch plugin's) are opened
with `O_NOFOLLOW` too; a link at a lock's name is replaced by a lock
file of its own.
`taxjson migrate` (which rewrites your `ticker.map` / `taxjson.toml`)
refuses, before writing anything, when either is a symlink to a file
outside the project; a link inside the project is kept and its target
updated. The same goes for the folders taxjson writes into: every
command stops when `work/`, `reports/`, `filed/`, `export/`, `inputs/`
or an `inputs/<account>/` folder is a symlink leaving the project.

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
without them gets the generic checks. Those include secrets: a PEM
private key, GitHub, Anthropic, Slack and AWS key formats, and a
high-entropy value after a key / secret / token name. CI also runs
gitleaks (a pinned release, checked against its sha256) on every commit
a push or pull request adds. `scripts/check-public.sh` applies the same
scan to what GitHub serves beside the code (release notes, issues, pull
requests and comments), read-only; `scripts/promote.sh` runs it before
moving a channel forward.

**Sharing a file with the project.** Never attach a raw broker export
or anything copied from one. The first choice is a synthetic
reproduction: copy the broker's `examples/*_demo.csv`, edit its rows to
the same shape as the ones that fail (same columns, actions and wording;
made-up symbols, ids and amounts) and confirm it fails the same way —
its amounts are made up, so they are fine to share. When that is not
possible, `taxjson redact` copies an export (or the project's whole
`inputs/` to `inputs_redact/`) and strips the account numbers, names
and contact details it recognises. It is pattern-based and keeps
amounts, prices, quantities, dates and symbols, so read the whole copy
(the report lists the lines to check) and attach it only after that
review. The bug-report template, README and CONTRIBUTING.md say the same.

## Release integrity

Releases are the `vX.Y.Z` tags of github.com/taxjson/taxjson; the
installer clones that repository over HTTPS and checks out the tag
`channels.json` on `main` names for your channel. What protects them:

- **Tags:** a repository rule lets only the repository's admins create
  a `v*` tag, and nobody may move or delete one once it exists. The
  maintainer's pre-push hook refuses any pushed tag that is not an
  annotated `vX.Y.Z` on `main`, and every tag delete or move.
- **Releases:** GitHub releases are immutable — a published release's
  tag and assets cannot be changed afterwards. `scripts/release.sh`
  scans a release's notes with `scripts/check-pii.sh --message` before
  it creates the release.
- **`main`:** a repository rule refuses deleting the branch and force
  pushes, so published history is not rewritten.
- **Channels:** `scripts/promote.sh` moves `stable` / `beta` only to an
  annotated release tag on `origin/main`, from a `main` equal to
  `origin/main`, and forward only when the tag's GitHub Actions run
  passed.
- **Dependencies:** Dependabot security updates are on (the core's
  only required dependencies are `tomli` before Python 3.11 and `tzdata`
  on Windows; the extras have more).

Nothing is signed yet: commits, tags and releases carry no GPG/SSH or
Sigstore signature, so an install trusts GitHub, its HTTPS and the
maintainer's account. To check an install by hand, compare
`git -C ~/.local/share/taxjson rev-parse HEAD` with the commit the tag
shows on GitHub.

## Supported versions

This project is pre-1.0. Security fixes are applied to `main` only. There are no LTS branches.
