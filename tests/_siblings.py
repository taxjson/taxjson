"""A parser that reads an export's siblings as the run does.

A bare parser reads its file alone: the sibling finder (the other CSVs
in the folder that `tjs run` routes to the same parser) is the caller's
(BaseBrokerage.sibling_finder; taxjson-brokerage and the conformance
check set lib/brokerages/detect same_broker_siblings). A test
of a Kraken trades export beside its ledger, or of a Webull export's
other years, sets it the same way."""


def with_siblings(parser):
    """`parser` with the run's sibling finder set, returned."""
    from taxjson.lib.brokerages.detect import same_broker_siblings
    parser.sibling_finder = same_broker_siblings
    return parser
