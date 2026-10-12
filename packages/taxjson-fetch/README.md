# taxjson-fetch

Broker auto-fetch for [taxjson](https://github.com/taxjson/taxjson): the
Questrade REST API and the IBKR Flex Web Service, as a `taxjson fetch`
plugin.

The taxjson core holds no broker API client and never reads a broker
credential; its `taxjson fetch` command is a dispatcher over installed
fetcher plugins. This package is that plugin for Questrade and
Interactive Brokers. It depends on `taxjson` and registers itself under
the entry-point group `taxjson.fetchers`.

## Install

Into the same Python environment as taxjson:

```bash
# with the one-line installer: it installs this plugin by default into
# its own environment (--without-fetch leaves it out; an install that
# opted out takes it back with --with-fetch):
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
# or, from a taxjson checkout, into the environment taxjson is installed in
# (install the core first, `pip install -e .`):
pip install --no-deps -e packages/taxjson-fetch
```

taxjson is not published on PyPI yet, so a `taxjson` or `taxjson-fetch`
package there is not ours: never install either by name from PyPI.
The plugin's `taxjson` dependency must be met by
the core already installed from the installer or the checkout.

`taxjson fetch --list` then shows `taxjson-fetch: questrade, ibkr_flex`.

## Use

Declare the source on the account in `taxjson.toml`:

```toml
[accounts.margin]
type = "taxable"
brokerage = "questrade"
account = "12345678"     # Questrade account number

[accounts.ibkr]
type = "taxable"
brokerage = "ibkr_flex"
query_id = "123456"      # an Activity Flex query: format CSV, with
                         # "include section code and line descriptor" ON
```

```bash
taxjson fetch                 # every account with a brokerage
taxjson fetch run             # fetch, then rebuild the books
taxjson fetch margin --dry-run
```

Options this plugin adds to `taxjson fetch`: `--year N` (backfill a past
tax year's whole window), `--days N` / `--from YYYY-MM-DD` (override the
Questrade window), `--refresh-token` (Questrade, first run) /
`--flex-token` (IBKR), `--positions` (snapshot live Questrade holdings
into the year's holdings folder, `holdings/<account>_live_holdings.toml`,
where `taxjson sanity` and the end of `taxjson run` find it) and
`--trim-overlap` (trim manually exported Questrade rows inside the
fetched window, keeping a `.bak`; a symlink at that name is skipped,
never followed, for the next free `.bakN`). The core adds `--list`, `--fetcher`,
`--json` and `--dry-run`.

Downloads land as `inputs/<account>/questrade_<year>.csv` (the whole
tax-year window, union-merged on every fetch) and
`inputs/<account>/ib_flex.csv` (replaced, the previous copy kept as
`.bak`; a download that would drop activity of the tax year is refused
and saved as `ib_flex.csv.new`) — the formats the core's parsers read
from manual exports, which keep working side by side. In a year folder
whose exports are shared by every year (`[settings] inputs_dir =
"../inputs"`), they land in that shared folder (`../inputs/<account>/`,
said with a note: the download applies to every year), an
`ib_flex.csv` replacement is refused when it would drop activity of ANY
year, and a fetch in another year folder waits on the shared folder's
`.fetch.lock`. Every file is
written to a new owner-only temp file of its own and renamed into place,
and one `taxjson fetch` runs per project at a time (a second one waits
on `work/.fetch.lock`), so two fetches never publish each other's
unfinished file or lose each other's merged rows. The waiting fetch says
so and waits as long as the first runs (Ctrl-C stops it, nothing
written); a symlink at a lock file's name is replaced by a real lock
file (its target is never touched), and the fetch stops with an error if
it cannot be — it never runs unlocked because of one.

## Credentials and network

Credentials never go in `taxjson.toml`. Questrade takes a refresh token
once (`--refresh-token` or `$QUESTRADE_REFRESH_TOKEN`) and caches the
rotated token in `~/.questrade_token` (0600, rotated atomically; shared
machine-wide because Questrade runs one rotating chain per API app;
`$QUESTRADE_TOKEN_FILE` overrides). The read, refresh and save of the
token happen under a lock beside it (`~/.questrade_token.lock`): a second
fetch waits and uses the token the first one saved, since each refresh
kills the token it used. IBKR reads `$IBKR_FLEX_TOKEN`.
Prefer the environment variables over the flags, which show in `ps`.

Network, only when you run `taxjson fetch`: `https://login.questrade.com`
and the `https://*.questrade.com` API server it names, and
`https://ndcdyn.interactivebrokers.com` (Flex). Credentialed requests
refuse redirects; tokens never appear in logs or `work/` files.

## Development

Layout: `src/taxjson_fetch/api.py` (HTTP clients, CSV / TOML writers,
the fetch window), `command.py` (the fetch: token file, merge, overlap
trim, Flex span guard, live positions), `plugin.py` (the object the
core loads). The plugin contract is `taxjson/lib/fetchers.py` in the
core.

Tests are offline (the HTTP layer is injected) and run from a checkout
without installing (`tests/_support.py` registers the entry point):

```bash
PYTHONPATH=packages/taxjson-fetch/src python -m unittest discover -s packages/taxjson-fetch/tests -p "test_*.py"
```

`scripts/ci.sh` runs them; releases are cut in lockstep with taxjson
(`scripts/release.sh` bumps both versions).
