"""Shared test helpers: one row for the wash radar, and a book as the
converted books hold it.

Shared from: test_fix_a2_pins1_02_radar
"""


def _row(d, sym, qty, px, acct="margin", action="BUYSELL", settle=None,
         currency="USD", rid=None, net=None, **kw):
    r = {"action": action, "date": d, "date_settle": settle or d,
         "time": "10:00:00", "symbol": sym, "quantity": qty, "price": px,
         "net_amount": (-qty * px if net is None else net),
         "currency": currency, "account": acct, "commission": 0.0}
    if rid:
        r["id"] = rid
    r.update(kw)
    return r


def base_rows(rows, country="canada"):
    """`rows` as the books taxjson-convert-currency writes hold them, the
    radar's input: a row in another currency than the country's
    relabelled to it, the currency it traded in kept as `trade_currency`
    (the exchange calendar the radar dates it on), its amounts as they
    are (a rate of 1). The radar refuses a book in another currency
    (backlog TJ-5068); these books stood for converted ones."""
    from taxjson.lib.country import home_currency
    base = home_currency(country)
    out = []
    for r in rows:
        c = str(r.get("currency") or "").strip().upper()
        if c and c != base:
            r = dict(r, currency=base,
                     trade_currency=r.get("trade_currency") or c)
        out.append(r)
    return out


def base_doc(doc, country="canada"):
    """A book document (a bare row list or {"transactions": [...]}) with
    `base_rows`' rows."""
    if isinstance(doc, list):
        return base_rows(doc, country)
    return dict(doc, transactions=base_rows(doc.get("transactions") or [],
                                            country))


def radar_country(argv, default="canada"):
    """The --country of a radar command line (`argv`: its arguments, the
    last one given winning, as argparse reads them), else `default`."""
    argv = [str(a) for a in argv]
    at = [i for i, a in enumerate(argv[:-1]) if a == "--country"]
    return argv[at[-1] + 1] if at else default
