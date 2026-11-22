"""A normalized row: the record every parser emits and the engines read.

It left lib/core.py in step 6a, verbatim."""
import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class TaxTransaction:
    """Normalized transaction record: a parsed row as the JSON files and
    `.tt` lines carry it. Money fields are float (an int where the row
    holds one) and stay so: `compute_id` hashes their repr, so every
    dedup key, wash link and `replacement_lot_ids` depends on them, and
    the files hold floats. Exact money starts where a row is read: the
    gains engines take each figure through `model.money.of` (the Decimal
    of its repr) and return floats (`leave`). Never make these fields
    Decimal."""
    action: str
    date: str
    symbol: str = ''
    quantity: float = 0.0
    currency: str = ''
    time: str = '09:30:00'
    date_settle: str = ''
    price: float = 0.0
    proceeds: float = 0.0
    commission: float = 0.0
    fee: float = 0.0
    net_amount: float = 0.0
    account: str = 'PORTFOLIO'
    type: str = ''
    gross_amount: float = 0.0
    description: str = ''
    symbol_new: str = ''
    # Traceability back to the corp-action event/election that emitted
    # this row (empty for ordinary broker rows). NOT part of
    # compute_id — the identifying content is unchanged.
    corp_event_id: str = ''
    corp_election: str = ''
    # An s.86.1 spin-off's parent cost reduction: how many parent shares
    # the distribution was made on (0: not known), which sales it reduces
    # when the parent was sold before it (TJ-1026). NOT part of
    # compute_id, omitted from to_dict() when 0.
    corp_parent_qty: float = 0.0
    id: Optional[str] = None
    # Neutral income facts a parser read from the export (empty when the
    # export does not say). NOT part of compute_id, and left out of
    # to_dict() when empty. What they mean for the tax year or the
    # character of the income is decided per country in
    # characterize/income_dating — never by the parser.
    #   record_date     the record date the broker prints ("REC 12/30/24")
    #   ex_date         the ex-dividend date (IB's dividend accruals)
    #   income_label    "distribution" when the broker calls the payment
    #                   a distribution ("DIST ON ...", RBC "Distribution")
    #   dealer_country  the country of the dealer that paid the row
    #                   ("CA" for Interactive Brokers Canada Inc.)
    #   issuer_country  the issuer's country from its ISIN ("CA", "US")
    record_date: str = ''
    ex_date: str = ''
    income_label: str = ''
    dealer_country: str = ''
    issuer_country: str = ''
    # Original crypto reward/commission evidence, on the income leg only.
    # Source amounts retain their declared currencies through conversion;
    # nested `valuation` records the price and FX actually used by the books.
    # Evidence only: never part of compute_id or a second accounting event.
    staking_reward: Optional[Dict[str, Any]] = None
    # The broker's own clock stamp ("2025-01-02 22:00:00 ET") on a trade
    # whose exchange trade date differs from it — an overnight-session US
    # fill, an ASX fill stamped in the US Eastern evening (tax-logic
    # CA-DATE-SESSION / US-DATE-SESSION). Evidence only: NOT part of compute_id,
    # left out of to_dict() when empty.
    broker_time: str = ''
    # The security's name from the broker's instrument list, when the
    # row's description is only the ticker (IB: "SAMPLE PLATFORMS INC-CDR"
    # beside description "SAMPLE") — read by the cross-listing lint to tell
    # a CDR or another company from an interlisting (audit S057-24).
    # Evidence only: NOT part of compute_id, omitted when empty.
    security_name: str = ''
    # The broker's own open/close marker on a trade (IB Trades `Code`:
    # "O" opening, "C" closing, "C;O" a sale that closed a long and
    # opened a short in one fill). Evidence for the missing-history
    # checks (find-missing-history, option-boundary): a sale coded "O"
    # is a broker-declared short, a sale coded only "C" closed a
    # position bought before the data. NOT part of compute_id, omitted
    # from to_dict() when empty (audit S013-00, S058-02, S060-12).
    open_close: str = ''
    # The broker's own cost basis of the position a closing trade
    # closed, as "<amount> <currency>" (IB Trades `Basis` on a row coded
    # "C") — shown next to a closing sale that has no position in the
    # data. Evidence only, never booked.
    broker_basis: str = ''
    # The lots that closing trade closed, when the export lists them
    # (IB ClosedLot rows): "<open date> <qty> <cost>" per lot, joined by
    # ";", in broker_basis's currency ("invalid" when a lot row did not
    # read). Evidence for `find-missing-history --write-purchases`,
    # never booked; NOT part of compute_id, omitted when empty.
    broker_lots: str = ''
    # The commission refunds folded into this trade's cost (IB Commission
    # Adjustments, tax-logic CA-ACB-COMMREFUND): "<date> <amount>" per
    # refund, the cash it brought in the trade's own currency, joined by
    # "; " in date order. The cost is net of them, but the cash moved on
    # their own dates: the FX-on-cash ledger v2 reads them to walk the
    # trade's full commission on its date and each refund on its own
    # (backlog TJ-0748). NOT part of compute_id, omitted when empty.
    commission_refunds: str = ''
    # Contract size the parser read from the export (IB Financial
    # Instrument Information Multiplier: 100 per equity option, 1000 per
    # CL future or futures option, 0.1 per micro-crypto future) — kept
    # on option and futures rows only; 0 = not declared (audit S026-22).
    # NOT part of compute_id, omitted from to_dict() when 0.
    multiplier: float = 0.0
    # How a non-IB parser got `multiplier` on an option row: "assumed"
    # (the export does not state a contract's size; the standard 100
    # was used) or "derived" (the row's own money showed a mini's 10).
    # The engine says once per option root when it relied on an
    # assumed size (B10). NOT part of compute_id, omitted when empty.
    contract_size_basis: str = ''
    # The input file the row was read from ("questrade_2025.csv",
    # "history.tt"), stamped by taxjson-brokerage / convert-tt. Cross-
    # file dedup tells an overlapping re-export (one row, two files)
    # from separate records that happen to look alike (bin/taxjson_sort
    # .plan_dedup, audit R1-296). NOT part of compute_id, omitted when
    # empty.
    source: str = ''
    # When `source` is a MASKED name (an account-number token shown as
    # 55***), a short hash of the real file name keeps two files whose
    # names differ only in that token apart for dedup (sha256, first 10
    # hex — never the name itself; audit A2-0159). Empty otherwise.
    # NOT part of compute_id, omitted when empty.
    source_key: str = ''
    # The broker account the row came from, HASHED (sha256 of the id the
    # export prints, first 10 hex — never the id itself), stamped by
    # taxjson-brokerage from the parser's per-row `broker_account` or
    # the statement's single account. Cross-file dedup never collapses
    # two rows of different broker accounts (audit A2-0008, R1-296).
    # NOT part of compute_id, omitted when empty.
    source_account: str = ''
    # The share listing a WARRANT or RIGHT exercise delivers, set by the
    # parser on the warrant leg (an ASSIGN of the warrant): exercising is
    # not a disposition — the warrant's cost goes into the shares' cost
    # like an option's premium (CA s.49(3) / US basis carryover; owner
    # decision on audit A2-0090 / A2-0274). NOT part of compute_id,
    # omitted from to_dict() when empty.
    exercise_of: str = ''
    # The cash a corporate-action row's event paid (cash in lieu of a
    # fraction, boot), as "<amount> <CUR>[ @<paid>][; <amount> <CUR>...]"
    # — set by the corp-action emitters and read by fx-cash, which used to
    # tell cash from the description text (re-audit A2-1014); @<paid> is
    # cash paid after the row's date. Evidence only:
    # NOT part of compute_id, omitted from to_dict() when empty.
    corp_cash: str = ''
    # Why an ADJUST the parser booked as a cash return of capital carries
    # no cash of its own ('book-cost': RBC's year-end row, Value 0, whose
    # cash came with the distribution rows). Read by fx-cash (backlog
    # TJ-1033). Evidence only: NOT part of compute_id, omitted from
    # to_dict() when empty.
    no_cash: str = ''
    # Why an income row carries no share count (quantity 0) where one
    # would be expected (NO_SHARE_COUNT_ACCRUAL: an IB dividend posted
    # after the pay date that the statements' accrual does not pay,
    # backlog TJ-2598). Quantity 0 alone means the broker's description
    # names none; `tjs div-estimate` then borrows the nearest payment's
    # count, and leaves a row carrying this out, saying so. Evidence
    # only: NOT part of compute_id, omitted from to_dict() when empty.
    no_share_count: str = ''
    # The acquisition date of an OPENING lot (an opening balance from a
    # positions report, `type == OPENING_TYPE`): the US engine dates the
    # lot's holding period from it (US-OPEN-01); Canada pools the ACB
    # and only shows it (CA-OPEN-01). Its own text is also in the row's
    # description, so two lots that differ only by it keep two ids.
    # NOT part of compute_id, omitted from to_dict() when empty.
    lot_date: str = ''
    # On an OPENING row: the share rows the snapshot replaced
    # (lib/opening `_note_cut_legs`), "date qty price action" joined by
    # ";": whether an option's delivery is among them (the premium ledger,
    # backlog TJ-1332). NOT part of compute_id, omitted when empty.
    opening_cut_legs: str = ''
    # The market value the broker states for a TRANSFER row (IB's
    # Transfers `Market Value`), in the row's currency: the fair market
    # value of an in-kind move between a taxable and a registered
    # account (lib/in_kind, CA-INKIND-06 / US-INKIND-03) — never a cost
    # of its own. Evidence only: NOT part of compute_id, omitted from
    # to_dict() when 0.
    market_value: float = 0.0
    # The id a parser gives the two legs of one broker CURRENCY journal
    # (Questrade's BRW "JOURNAL POSITION TO USD" / "FROM CAD" pair,
    # brokerages/questrade._plan_qt_journals): kept on the TRANSFER row
    # whether the account keeps its transfers in the books or aside, so
    # the run joins the two lines as one security (lib/cross_listings,
    # CA-XLIST-03) and the missing-history walks read the pair as a
    # journal. Evidence only: NOT part of compute_id, omitted from
    # to_dict() when empty.
    journal_pair: str = ''
    # Where a DATED EVENT row came from when no broker row states it
    # (lib/dated_events): "tt" (a .tt JOURNAL leg or RENAME line), "map"
    # (a legacy ticker.map dated RENAME line) or "ib-conid" (a ticker
    # change IB shows as one contract id under two symbols). Evidence
    # only: NOT part of compute_id, omitted from to_dict() when empty.
    event_source: str = ''
    # The currency of the listing a row is booked under when the row's
    # cash is in another (a Questrade reinvestment paid on the USD side
    # buying the account's TSX listing: brokerages/questrade._rei_listing).
    # The converted books convert the cash as usual; the native-currency
    # books restate the row in this currency at the day's rate
    # (pipeline/inputs._raw_align_adjust_currency), one currency per pool.
    # Evidence only: NOT part of compute_id, omitted when empty.
    listing_currency: str = ''
    # A plain-futures fill on the settlement basis (lib/futures.py,
    # type FUTURES_SETTLEMENT): what that account's own settlement
    # booked, as "<leg> <money> <pl> <CUR>[ <rate>]" — leg LO/LC/SO/SC
    # (a Long or Short position Opened or Closed in its account), the
    # part's native fill money (a buy's cost, a sell's proceeds), the
    # account's own native P/L (0 on an opening), the native currency,
    # and the rate the conversion applied. The Canadian engine re-averages
    # the closes across your taxable accounts from it (s.47, tax-logic
    # CA-FX-04; issue #76). NOT part of compute_id, omitted when empty.
    settle_native: str = ''
    # The currency the trade was made in, on a row the base-currency
    # books relabel (taxjson_convert_currency): its settlement calendar
    # (lib/dates.settlement_calendar — a TSX US-dollar unit settles only
    # on days both Canada and the US settle, 'CAD+USD') for the wash
    # radar's rescue and re-entry dates (backlog TJ-0713). Evidence
    # only: NOT part of compute_id, omitted when empty.
    trade_currency: str = ''
    # The fee of a crypto-to-crypto swap whose legs the export left
    # unpriced, as "<units> received" or "<units> spent" (in the coin
    # of the leg that received or spent it), set by the
    # parser on both legs when its priced rows count the fee in the
    # exchange's one value (a Coinbase Advanced Trade buy on a crypto-
    # quoted pair: the quote coin's fee; a Kraken ledger swap: the
    # received coin's fee). taxjson-fill-crypto adds it when it values
    # the swap from the received coin (_value_swaps_once, backlog
    # TJ-0981 / TJ-0991). Evidence only: NOT part of compute_id,
    # omitted when empty.
    swap_fee: str = ''

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = self.compute_id()

    def compute_id(self) -> str:
        """Deterministic content-hash ID used for dedup, wash-sale
        linkage, and trace cross-references. Floats go in via `repr()`
        rather than fixed-decimal formatting so distinct values cannot
        collide on rounding: Python's float `repr` (≥3.1) is the
        shortest decimal that uniquely round-trips back to the same
        IEEE-754 bit pattern — deterministic across platforms and full
        precision. The earlier `:.8f` formatting truncated below the
        satoshi level, so two distinct sub-satoshi crypto quantities
        could hash to the same ID and dedup as duplicates."""
        q = repr(float(self.quantity))
        p = repr(float(self.price))
        n = repr(float(self.net_amount))
        g = repr(float(self.gross_amount))
        
        # Combine fields into a unique string
        components = [
            self.action,
            self.date,
            self.time,
            self.symbol,
            q,
            self.currency,
            p,
            n,
            g,
            self.account,
            self.type,
            self.date_settle,
            self.description
        ]
        raw_id = "|".join(str(c) for c in components)
        return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in INCOME_FACT_FIELDS + EVIDENCE_FIELDS:
            if not d.get(k):
                d.pop(k, None)
        # A trade read WITHOUT its quantity or net_amount key stays
        # without it when a pass-through tool (sort, merge, convert)
        # writes it out, so the gains computation still refuses it
        # (issue #14) instead of reading the written default 0.
        miss = getattr(self, '_missing_trade_fields', None)
        if miss:
            for k in miss[1]:
                if not d.get(k):
                    d.pop(k, None)
        return d


# The optional income facts on TaxTransaction (see the class): omitted
# from to_dict() when empty so every other row keeps its shape.
# `no_share_count`: no accrual in the statements pays the row.
NO_SHARE_COUNT_ACCRUAL = 'no-accrual'
INCOME_FACT_FIELDS = ('record_date', 'ex_date', 'income_label',
                      'dealer_country', 'issuer_country')
# The other optional evidence fields, omitted from to_dict() when empty.
EVIDENCE_FIELDS = ('broker_time', 'security_name', 'open_close',
                   'broker_basis', 'broker_lots', 'multiplier',
                   'commission_refunds', 'contract_size_basis',
                   'source', 'source_key',
                   'source_account', 'exercise_of', 'corp_cash', 'no_cash',
                   'no_share_count', 'corp_parent_qty', 'lot_date', 'opening_cut_legs',
                   'market_value', 'journal_pair',
                   'event_source', 'listing_currency', 'settle_native',
                   'staking_reward', 'trade_currency', 'swap_fee')
