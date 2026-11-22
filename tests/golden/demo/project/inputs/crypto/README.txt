inputs/crypto/ holds the files of the account [accounts.crypto] in
taxjson.toml.

Put this account's broker exports here. Any file name works:
taxjson recognises each export by its header, and `tjs run`
prints which broker it read each file as. Several files are fine;
rows that overlap are read once.

Download ALL the history the broker will give you, not just the
tax year: the cost of something sold this year comes from the day
you bought it, which may be years back.

  Kraken    Trades AND Ledgers, CSV: both, every year available.
  Coinbase  Transaction history, CSV: every year available.

Also save a positions report with book cost ("Holdings",
"Positions" or "Portfolio") for the date your download
starts and for the end of the tax year. An Interactive Brokers
Activity Statement (CSV) or an RBC Holdings Export is read as it
is; from another broker, type it into a [[holding]] .toml file.
The first becomes the opening balance (`tjs opening
ACCOUNT FILE` writes an opening_DATE.tt here); the second is
what `tjs sanity` checks the books against (quantities and
costs). `tjs run` skips a positions report left here.

Purchases the download does not reach go in a .tt file in this
folder: docs/getting-started.md, step 5.

A ticker change or a journal between two listings that the
export does not show is a dated event: one .tt line, date first
(docs/settings.md, .tt files), e.g.
  RENAME 2025-04-01 OLDC NEWC

No such account? Delete this folder and its [accounts.crypto] section
from taxjson.toml.
