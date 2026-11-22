"""Shared test helpers: a planning project's taxjson.toml and a date some days back.

Shared from: test_fix_1029_planning
"""
from datetime import date, timedelta


def _toml(country="canada", *, sheltered=False, extra="", year=None):
    zone = "America/New_York" if country == "usa" else "America/Toronto"
    cur = "USD" if country == "usa" else "CAD"
    text = (f'[settings]\nlocal_timezone = "{zone}"\nyear = '
            f'{year or date.today().year}\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
    if sheltered:
        text += '[accounts.rrsp]\ntype = "sheltered"\n'
    return text + extra


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()
