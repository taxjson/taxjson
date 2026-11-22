"""What every parser needs and used to spell itself (step 5a of the engine
refactor): `masking`, how a message prints an identifier; `numbers`, the
strict number and money parsers; `errors`, the error a parser raises rather
than guess; `descriptions` (step 5f), what a broker's description text
says (a payment in lieu, a reinvestment's price marker); `money` (step 5),
a trade row's money identities; `expiry` (PLAN step 14), when an expiry
happened: its row's booking date and the settle clamp of a trade on its
day; `fee_coins` (PLAN step 14), the sale of a ledger fee taken in coins;
`staking` (PLAN step 14), a reward's value and its income and acquisition
legs; `assets` (PLAN step 14), the fiat currencies, the USD stablecoins and
the books' zero for a quantity.
A duplicate leaves a parser each time the parser is touched."""
