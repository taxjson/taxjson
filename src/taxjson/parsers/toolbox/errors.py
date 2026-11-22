"""The error a parser raises rather than guess (step 5a of the engine
refactor: moved from `lib/brokerages/base.py`, which re-exports it)."""


class BrokerageParseError(ValueError):
    """A broker export the parser refuses to read rather than guess at:
    a required column is missing, a required money/quantity/date cell
    is unparseable, a row's money does not add up (|proceeds| far from
    |qty| x price x multiplier), the file is a different report than the
    parser reads, or the parsed rows disagree with the broker's own
    totals. Reading a missing column as 0 can inflate a return by
    thousands — failing closed is the point. `taxjson-brokerage` turns it
    into a one-line error and a nonzero exit."""
