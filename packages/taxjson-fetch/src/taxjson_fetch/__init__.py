"""taxjson-fetch: the Questrade and IBKR Flex fetcher for `taxjson fetch`.

The taxjson core holds no broker API client and reads no broker
credential; this package does both, and plugs into the core through
the `taxjson.fetchers` entry-point group (taxjson.lib.fetchers).

    api      HTTP clients (Questrade REST, IBKR Flex), the CSV / TOML
             writers, the fetch window
    command  the fetch itself: token file, merge, overlap trim, Flex
             span guard, live positions
    plugin   the object the core loads (BrokerFetcher)
"""
