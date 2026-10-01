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
  pairs listed under `source_currencies` into `work/to_base.csv`: from
  the Bank of Canada Valet API (www.bankofcanada.ca) for a CAD base,
  and from Yahoo Finance for dates before 2017-01-03, currencies the
  Bank does not publish, and non-CAD bases. Only currency-pair symbols
  and date ranges are sent.
- **Crypto prices** — `taxjson-fill-crypto` looks up any crypto row
  that carries no price (Kraken staking rewards) from Yahoo Finance:
  the symbol and trade date are sent, nothing else.

Set `TAXJSON_OFFLINE=1` (or `true`/`yes`/`on`; `0`/`false`/`no`/`off`
leave it off) to forbid both. A crypto-price cache miss then fails the
stage with a message naming what it needed; the FX stage serves cached
rates only, and a transaction whose date has no cached rate is a
validation error at the conversion stage. The same switch covers the
current-price chain behind `harvest`, `watch --harvest` and the GUI's
Harvest tab (IBKR gateway / Yahoo Finance): they serve
`work/.price_cache.json` only and refuse the lookup on a miss, and
`scan --online` skips its Yahoo Finance name probe with a note (the
offline checks still run). Everything else that touches the network is
opt-in by command: `fetch` (your broker's API, with your credentials;
`fetch --positions` reads live Questrade positions), and
`taxjson-generate-parser`, which sends the first `--sample-lines`
(default 30) lines of the sample CSV you hand it to an LLM API. Those
lines are where broker exports keep the holder's name, account number
and address, so it scans them first and refuses to send a sample that
still carries an identity shape (`--allow-unredacted` overrides) — run
`taxjson redact` on the sample first.

**What Yahoo Finance learns.** Every Yahoo lookup (FX fallback, crypto
prices, `harvest` / `watch --harvest` current prices, `scan --online`)
is a plain request from your IP address naming a symbol and a date
range — so Yahoo can see which tickers you hold or trade and roughly
when, though never quantities, prices paid or account numbers. Use
`TAXJSON_OFFLINE=1` (with a populated cache) if that matters to you.

Credentials: `~/.questrade_token` is written 0600 and rotated
atomically (the temporary file is created fresh — never through a
symlink); tokens never appear in logs, `.diag` files or `work/`
artifacts. Prefer `$QUESTRADE_REFRESH_TOKEN` / `$IBKR_FLEX_TOKEN` over
the `--refresh-token` / `--flex-token` flags, which are visible in
`ps` and shell history. Credentialed requests refuse redirects, and
the Questrade access token is only ever sent to
`https://*.questrade.com` — an `api_server` elsewhere in the login
response is refused.

## Files on disk

`taxjson` and every `taxjson-*` tool set an owner-only umask (`077`)
at startup, so everything they create — `work/`, `reports/`,
`filed/`, `export/`, `checklist.json`, a new project's
`inputs/<account>/` — is `0600` (files) / `0700` (directories)
whatever your shell's umask. `taxjson fetch` also tightens an
existing `inputs/<account>/` to `0700` and writes the fetched
statements (which carry your account numbers) `0600`. Directories
created by earlier versions keep their old mode; tighten a project
once with `chmod -R go-rwx <project>`.

**Keep a tax project repository PRIVATE.** A project is designed to be
versioned — `taxjson init` writes a `.gitignore` that commits
`inputs/` (your broker statements) and `taxjson.toml` (your accounts)
so the books can be rebuilt. That makes the repository itself
sensitive: host it only as a private repository (or not at all), never
fork it publicly, and never push it to the public taxjson repo. The
public repo's own `.gitignore` ignores `inputs/`, `work/`, `reports/`,
`filed/`, `export/`, spreadsheets and PDFs so a run inside a dev clone
cannot be staged by accident.

`taxjson redact` strips the account numbers, names and contact details
it recognises from an export so it can be shared as a parser sample —
it is pattern-based, so review the output (the report lists the lines
to read) before attaching it anywhere.

## Local web UI

`taxjson serve` binds `127.0.0.1` by default and refuses requests whose
`Host` header is not a loopback name (DNS rebinding). Binding any other
address (`--host 0.0.0.0`) exposes the books over plain HTTP, so it
also requires a random per-run access token: the startup line prints
`http://HOST:PORT/?token=…`; the first request with it sets an
HttpOnly, SameSite=Strict cookie, and every request without either gets
401. Prefer an SSH tunnel to the loopback server. `/healthz` reports no
filesystem paths.

A loopback bind has no token: `127.0.0.1` is reachable by every account
on the same machine, so while `taxjson serve` runs, any other local user
(or anyone who can reach that host's loopback, e.g. through their own SSH
tunnel) can read your holdings, cost bases and trade history through it,
even though the project files themselves are owner-only (0600). On a
shared or multi-user host, run it only while you use it, or pass
`--token` to require the per-run token on loopback as well.

## Supported versions

This project is pre-1.0. Security fixes are applied to `main` only. There are no LTS branches.
