The broker's positions snapshots for this tax year, one [[holding]] TOML
per broker account (a download tool writes them; `tjs fetch
--positions` writes the same shape). Keep one taken at (or just after)
the year end. `tjs sanity` and the end of `tjs run` compare the
books with them at each file's date ([meta] as_of, else [meta]
generated_at), with no setting: a file belongs to the account whose
broker account id is its [meta] broker_account (or [meta] account)
([accounts.NAME] account or broker_accounts in taxjson.toml), else the
account its name starts with
(margin_holdings.toml, margin_ib_holdings.toml). To keep two dates of
one broker account, give each file its [meta] broker_account.

The broker's own report is read here too: an RBC Holdings Export or an
IB Activity Statement with Open Positions (.csv), named for its account
(margin_holdings.csv). Any other file here is named as not read.
