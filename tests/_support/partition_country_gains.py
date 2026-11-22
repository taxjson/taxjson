"""Shared test helpers: the pipeline's gains under one country, Questrade and spin-off rows, and a US project and an IB futures project.

Shared from: test_fix_1029_r7e_engine
"""
import contextlib
import io


def _gains(book, country, year=2025):
    """pipeline.run_gains under one country: (result, stderr)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), [], [], req=GainsRequest(
            country=country, taxable=True, year=year, phantom_hint=False))
    return res, err.getvalue()


_QT_HEAD = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Activity Type,Account #,Account Type\n')


def _qt(date, settle, action, sym, desc, qty, net, cur='USD'):
    return (f'{date},{settle},{action},{sym},{desc},{qty},'
            f'{abs(net / qty) if qty else 0:.4f},{net},0,{net},{cur},'
            f'Trades,99900001,Margin\n')


def _us_project(tmp, name, year, files, extra=''):
    from _qa_project import Path
    root = Path(tmp) / name
    for rel, text in files.items():
        p = root / 'inputs' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / 'taxjson.toml').write_text(
        f'[settings]\nyear = {year}\ncountry = "usa"\n'
        f'base_currency = "USD"\nsource_currencies = ["USD"]\n{extra}'
        '[accounts.margin]\ntype = "taxable"\n')
    return root


def _spin_rows(on_shares, qty=20.0, alloc=40.0, record='2025-06-02'):
    from taxjson.lib.tx_load import coerce_transaction_row
    from taxjson.lib.corp_actions import CorporateAction, resolve_event
    ev = CorporateAction(
        date='2025-06-16', time='09:30:00', action_type='spinoff',
        source_symbol='ZZP.US', source_isin='ZZP.US',
        target_symbol='ZZS.US', target_isin='ZZS.US',
        ratio_new=qty, ratio_old=on_shares or 1.0, qty_disposed=0.0,
        qty_received=qty, fmv=0.0, currency='USD',
        target_currency='USD', account='margin', record_date=record)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'tax_free_355', 'usa',
                             {'allocated_acb': alloc})
    return [coerce_transaction_row(r, i, 'spin') for i, r in enumerate(rows)]


def _futures_project(tmp, rows_a, rows_b, country='canada'):
    """A project of two IB statements in one folder (margin): SYNZ6
    futures trades `rows_a` and `rows_b` (date, quantity, price, code)."""
    from _qa_project import Path
    cur = 'CAD' if country == 'canada' else 'USD'

    def stmt(acct, rows):
        return (
            'Account Information,Header,Field Name,Field Value\n'
            f'Account Information,Data,Account,{acct}\n'
            'Financial Instrument Information,Header,Asset Category,'
            'Symbol,Description,Conid,Underlying,Listing Exch,Multiplier,'
            'Expiry,Delivery Month,Instrument Type,Issuer\n'
            'Financial Instrument Information,Data,Futures,SYNZ6,SYN '
            'DEC26,999,SYN,CME,1,2026-12-18,2026-12,FUT,\n'
            'Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
            'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n'
            + ''.join(f'Trades,Data,Order,Futures,{cur},SYNZ6,"{d}, '
                      f'10:00:00",{q},{p},{p},{-q * p},0,0,0,0,{c}\n'
                      for d, q, p, c in rows))
    root = Path(tmp) / 'p'
    (root / 'inputs/margin').mkdir(parents=True)
    (root / 'inputs/margin/ib_a.csv').write_text(stmt('U1234567', rows_a))
    (root / 'inputs/margin/ib_b.csv').write_text(stmt('U9990002', rows_b))
    prov = 'province = "ON"\n' if country == 'canada' else ''
    (root / 'taxjson.toml').write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n{prov}'
        f'base_currency = "{cur}"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    return root
