# Glossary

The words taxjson's output and docs use, in plain terms, with where to
read more. Canadian terms first in each entry; the US counterpart where
there is one. taxjson is a calculator, not tax advice: the rules it
applies are stated by `tjs tax-logic` and explained in
[tax-rules.md](tax-rules.md).

## Tax terms

**ACB (adjusted cost base).** What a Canadian property cost you, for tax:
the purchase price plus commissions, adjusted later (raised by a denied
superficial loss or a reinvested distribution, lowered by a return of
capital). taxjson keeps one average ACB per security across all your
taxable accounts. The US equivalent is **basis**, kept per lot (FIFO).
[tax-rules.md](tax-rules.md#acb-average-cost-one-pool-across-your-taxable-accounts-s47)

**Identical property (s.47).** Canada averages the cost of identical
properties together: the same share bought in two of your taxable
accounts, or the TSX and US listings of one interlisted company, is one
pool with one ACB.
[tax-rules.md](tax-rules.md#identical-property-listings-cross-listings-broker-codes-and-renames)

**Superficial loss (s.54, s.40(2)(g)).** A loss on a sale is denied when
you (or an affiliated person) acquire identical property within 30 days
before or after the sale and still hold it at the end of day 30. The
denied amount is added to the replacement's ACB, so it comes back when
that is sold — unless the replacement is in a registered account, where
it is lost for good.
[tax-rules.md](tax-rules.md#superficial-loss-s54)

**Wash sale (US, §1091).** The US counterpart: a loss is disallowed when
substantially identical stock is bought within 30 days before or after
the sale; the disallowed amount is added to the replacement's basis.
[tax-rules.md](tax-rules.md#wash-sales-1091)

**Denied / DENIED.** The part of a loss the superficial-loss (or
wash-sale) rule took away. `tjs wash-sales` lists each one.

**Permanently denied.** A denied loss whose replacement was bought in a
registered account (RRSP, TFSA ...; an IRA in the US) or by an
affiliated person: it never comes back in your books.

**Sheltered / registered account.** An account whose gains are not taxed
as they happen: RRSP, RRIF, TFSA, FHSA, LIRA, RESP, RDSP (US: IRA, Roth,
401(k), HSA). In `taxjson.toml` it is `type = "sheltered"`. Its trades
are still read, because its purchases can deny a loss in a taxable
account.

**Taxable account.** A cash or margin account whose gains you report.
`type = "taxable"`.

**Affiliated person.** Canada: your spouse or common-law partner, or a
corporation you control (s.251.1). Their purchases can make your loss
superficial too. taxjson does not hold their books; see
[limits.md](limits.md) and
[tax-rules.md](tax-rules.md#registered-and-affiliated-holders-permanent-denial).

**ALLOWLOSS.** A `.tt` line that claims one specific loss although the
superficial-loss (or wash-sale) rule would deny it: a filing position you
take and must be able to support, listed under FILING POSITIONS by
`tjs sum`. [settings.md](settings.md#allowloss)

**ROC (return of capital).** A distribution that gives back part of what
you invested instead of paying income: it lowers the ACB (a T3 box 42
amount, for example). Entered as an `ADJUST` line or in
`[[distributions]]`. [tax-rules.md](tax-rules.md#return-of-capital)

**Capital-gains dividend (T5 box 18).** A dividend a mutual-fund or
split-share corporation designates as a capital gain.
`[[capital_gains_dividends]]` in `taxjson.toml`.

**Non-cash (reinvested) distribution.** A fund distribution paid in
units rather than cash, usually in December: it is income and raises the
ACB, and no broker export shows it. `[[distributions]]` in
`taxjson.toml`.

**PIL (payment in lieu of a dividend).** What your broker pays you
instead of a dividend when your shares were lent out (for a short sale).
It is ordinary income, not a dividend, except that a Canadian dealer's
payment on a Canadian corporation's share is deemed a taxable dividend
(s.260). `tjs dil` lists them.
[tax-rules.md](tax-rules.md#payments-in-lieu-of-a-dividend)

**Grant timing / close timing.** When a written option's premium is
taxed in Canada. Grant timing (s.49(1), the default): the premium is a
gain in the year you wrote the option; a buy-back is a loss in its own
year. Close timing nets premium and close at the close. Set by
`option_premium_timing` and `option_grant_timing_since`.
[tax-rules.md](tax-rules.md#options-premium-timing-s49)

**LEAPS.** Long-dated options. taxjson calls a long option bought more than
`leaps_months` (default 9) months before expiry a LEAPS for its reports
(`tjs leaps`, `tjs leaps-sum`); the tax rules are those of any option.

**Interlisted (cross-listed).** One company's shares listed on two
exchanges, typically the TSX and a US exchange or the US over-the-counter
market. For Canadian tax they are one security.

**CDR (Canadian depositary receipt).** A TSX-listed receipt for a US
company's shares. It is not the US share itself: taxjson keeps the two
apart.

**NR4, T5, T3, T5008, T1135.** Canadian slips and forms. **T5008**: the
broker's slip of each sale (proceeds; the cost when the broker knows it),
checked by `tjs reconcile-slips`. **T5**: investment income (dividends,
interest). **T3**: income from a trust (ETF, REIT, income fund), including
return of capital (box 42) and capital gains (box 21). **NR4**: income paid
to a non-resident. **T1135**: the foreign-property form, required when the
cost of your specified foreign property exceeded CAD 100,000 at any time
in the year (`tjs t1135`). US: **1099-B** (sales, like the T5008),
**Form 8949** and **Schedule D** (where the gains go).

**Schedule 3 lines.** Where Canadian capital gains go: line 4 shares and
fund units (13199/13200), line 6 options, futures and other properties
(15199/15300), line 7 crypto-assets (15200/15301, from 2025). `tjs sum`
ends with one row per line (FOR THE RETURN).

**P1 / P2 (Period 1, Period 2).** The 2024 Schedule 3 split each line at
June 24, 2024 (dispositions to June 24 on the Period 1 codes, from June 25
on the Period 2 codes), for a proposed inclusion-rate change. Only 2024
returns have them.

**AMT (alternative minimum tax).** A second tax calculation with fewer
deductions (Canada: form T691; capital gains counted at 100% since 2024).
You pay the higher of the two; the excess carries forward seven years.
`tjs amt`.
[tax-rules.md](tax-rules.md#alternative-minimum-tax-and-the-minimum-tax-carryover)

**Net capital loss carryover.** A year's net capital loss that you apply
against gains of the three previous years (carry-back, form T1A) or of
any later year. `tjs carryover`, `[carryover] claimed`.

**Settlement date / trade date.** The trade date is when the order
filled; the settlement date is when it settled (one business day later
for stocks since May 2024, two before). Canada dates a sale by
settlement (`tax_date = "settle"`), so a December 31 trade can belong to
the next year; the US uses the trade date.

**FIFO.** First in, first out: the US engine sells the oldest lot first.

**Holding period (US).** Short-term (one year or less) or long-term,
from each lot's purchase date.

**In-kind contribution / withdrawal.** Moving shares (not cash) between a
taxable account and a registered one. A contribution is a sale at fair
market value (a loss on it is denied for good in Canada); a withdrawal is
a purchase at fair market value.

**s.86.1 rollover / taxable deemed dividend.** The two Canadian
treatments of a foreign spin-off: split the parent's cost with no income
(the election), or a dividend equal to the new shares' value. `tjs elect`
asks which.

## taxjson terms

**Project.** A folder with a `taxjson.toml`: one tax year's books. With
the default layout (`tjs init`), `~/taxes/inputs/` holds every year's
exports and `~/taxes/2025/` is the 2025 project.
[getting-started.md](getting-started.md#one-folder-of-exports-for-every-year)

**Account.** An `[accounts.NAME]` table in `taxjson.toml` and its folder
`inputs/NAME/`: one broker account (or several of yours exported
together).

**Base currency.** The currency the books are kept in: CAD for Canada,
USD for the US. Other currencies are converted at a daily rate of each
row's date (the Bank of Canada's for a CAD base).

**`.tt` file.** A plain-text file of hand-entered lines in an account's
folder: a purchase from before your downloads (`BUYSELL`), an opening
balance (`OPENING`), a return of capital (`ADJUST`), a dated rename
(`RENAME`) ... [settings.md](settings.md#tt-files)

**ticker.map.** The year's own symbol rules: a rename, two spellings of
one security, a listing to keep apart (`DISTINCT`), a lookup spelling.
[settings.md](settings.md#tickermap)

**tobase.map / TOBASE.** Canada: the list of interlisted pairs that
ships with taxjson, one `TOBASE <US listing> <TSX listing>` line each,
joining the two into one security. One file every year reads;
`tjs update-tobase-map` keeps it current.
[settings.md](settings.md#tobasemap)

**Journal.** A broker moving your shares from one listing of a security
to another (the US-dollar line to the Canadian-dollar line, for
Norbert's gambit). Not a sale. `tjs journals` lists them.

**Rename (RENAME event).** A ticker change, booked as a dated event: the
position and its cost carry from the old symbol to the new one on that
date. `tjs renames` lists them.

**Missing history.** Purchases older than your downloads reach. They
show as a sale with no purchase (a negative position), a missing
holding, or a cost too low. `tjs find-missing-history` finds them;
[getting-started.md, step 5](getting-started.md#5-find-and-fill-missing-history)
fixes them.

**Opening balance (`OPENING`).** A `.tt` line that sets a position and
its cost on a date, from a positions report (`tjs opening` writes them).
It is not a purchase, so it never makes a loss superficial.

**`OPENING ... cost=unknown`.** An opening line for units you held before
your files start whose cost you cannot recover. Their sales are left out
of the totals and listed for you to report by hand (`tjs form-export`,
MANUAL REPORTING).

**Transfer-in.** Shares that arrived from another broker. Not a purchase:
your cost is what you paid at the first broker. `tjs transfers` lists
them; [getting-started.md, 5c](getting-started.md#5c-transfers-in).

**Holdings file / positions report.** The broker's list of what you hold
on a date, with book cost. taxjson checks its books against it
(`tjs sanity`) and can start from one (`tjs opening`).

**Book value / book cost.** The broker's record of what a position cost.
A good start, not always your ACB (it may miss a superficial loss, a
return of capital, or the same stock in another account).

**Election.** Your choice of tax treatment for a merger or spin-off,
asked once and saved in `inputs/<account>/manifest.json`. `tjs elect`.

**Wash radar.** `tjs wash-radar`: for each recent loss and each position
you might sell, whether a sale or a purchase today would trigger (or
undo) a superficial loss, with the date it becomes safe.
`tjs buy-check` and `tjs sell-check` ask the same for one symbol.

**Crypto send.** A withdrawal of coins from an exchange. Sent to another
of your own wallets or exchanges it is a move; a gift or a payment is a
disposition at fair value. `tjs crypto-sends`.

**Stablecoin.** A coin pegged to the US dollar (USDC, USDT ...). In a
Canada project the ones in the shipped market data are treated as US
dollar cash; in a US project, as property.

**Close-year lock (`filed/<year>.json`).** The figures you filed,
written by `tjs close-year` right after filing. `tjs check-filed` and
every later run compare against it; next year's `tjs handoff` checks the
new year starts from it.

**Checklist.** `tjs checklist`: every step from install to filing, each
checked and marked done or not, ending with the next step's command.

**Info: / Warning: / Error:.** The labels of console messages:
information, something to read, a failure. A warning you must act on is
marked ATTENTION in the saved reports (`.sum` DIAGNOSTICS) and shown as
a `Warning:` on the console ([output-style.md](output-style.md)).

**ESTIMATED.** A value taxjson looked up rather than read from your
files (a closing price for an in-kind move), marked as such.
