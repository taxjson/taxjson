"""Shared test helpers: synthetic FIGIs and securities, a master of them, a project with ticker.map and tobase.map, scripts/build_interlisted.py with a cache folder, an .xlsx writer and OpenFIGI rows.

Shared from: test_tobase_map
"""
import importlib.util
import json
import zipfile
from pathlib import Path

from taxjson.lib import tobase_map as TB
import _paths


REPO = _paths.REPO
F1 = "BBG000000Q01"
F2 = "BBG000000Q02"
F3 = "BBG000000Q03"


def _build_module():
    spec = importlib.util.spec_from_file_location(
        "build_interlisted", REPO / "scripts" / "build_interlisted.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _master(security=None, distinct=None, generated="2026-10-01"):
    return TB.Master({"schema_version": 1, "generated": generated,
                      "sources": [{"name": "synthetic", "as_of": generated}]},
                     dict(security or {}), dict(distinct or {}))


SEC = {
    F1: {"name": "QZALPHA", "kind": "share", "share_class_figi": F1,
         "ca": ["QZA.TO"], "us": ["QZAB.US"], "us_exchange": ["NYSE"]},
    F2: {"name": "QZBETA", "kind": "share", "share_class_figi": F2,
         "ca": ["QZB.TO"], "us": [], "us_exchange": [],
         "us_otc": ["QZBBF.US"]},
    F3: {"name": "QZGAMMA", "kind": "share", "share_class_figi": F3,
         "ca": ["QZG.TO"], "us": [], "us_exchange": [],
         "until": "2025-06-30",
         "history": [{"listing": "QZG.US", "kind": "us",
                      "until": "2025-06-30"}]},
}


def _project(td, country="canada", ticker="", tobase=None):
    root = Path(td) / "p"
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\ncountry = "{country}"\nyear = 2025\n')
    (root / "ticker.map").write_text(ticker)
    if tobase is not None:
        (root / "tobase.map").write_text(tobase)
    return root


def _xlsx(path, sheets):
    """A minimal .xlsx: {sheet name: rows of str} as inline strings."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = ("http://schemas.openxmlformats.org/officeDocument/2006/"
           "relationships")
    with zipfile.ZipFile(path, "w") as z:
        wb = [f'<workbook xmlns="{ns}" xmlns:r="{rns}"><sheets>']
        rel = ['<Relationships xmlns="http://schemas.openxmlformats.org/'
               'package/2006/relationships">']
        for i, (name, rows) in enumerate(sheets.items(), 1):
            wb.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rel.append(f'<Relationship Id="rId{i}" Target="worksheets/'
                       f'sheet{i}.xml" Type="x"/>')
            cells = []
            for r, row in enumerate(rows, 1):
                cs = "".join(
                    f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{v}</t>'
                    f'</is></c>' for c, v in enumerate(row))
                cells.append(f'<row r="{r}">{cs}</row>')
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       f'<worksheet xmlns="{ns}"><sheetData>'
                       + "".join(cells) + "</sheetData></worksheet>")
        z.writestr("xl/workbook.xml", "".join(wb) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   "".join(rel) + "</Relationships>")


def _eq(ticker, exch, sc, typ="Common Stock", name="QZ CO"):
    return {"ticker": ticker, "exchCode": exch, "shareClassFIGI": sc,
            "securityType": typ, "marketSector": "Equity", "name": name}


def _build_cache(td):
    """scripts/build_interlisted.py, loaded, and a cache folder in TD of
    the TMX, Nasdaq and other-listed files, a TMX issuers workbook and
    OpenFIGI answers: (the module, the folder)."""
    B = _build_module()
    cache = Path(td) / "cache"
    cache.mkdir()
    (cache / B.TMX_TXT).write_text(
        "As of January 1, 2026\n\n"
        "Symbol\tName\tUS Symbol\tSector\tInternational Market\n"
        "QZA:TSX\tQZ Alpha\tQZAB\tMining\tNYSE\n"
        "QZM:TSX\tQZ Mismatch\tQZM\tMining\tNYSE\n"
        "QZW.WT:TSX\tQZ Warrant\tQZWW\tMining\tNYSE\n")
    (cache / B.NASDAQ).write_text(
        "Symbol|Security Name|Market Category|Test Issue|Financial "
        "Status|Round Lot Size|ETF|NextShares\n"
        "QZD|QZ Delta US Inc|Q|N|N|100|N|N\n"
        "File Creation Time: 0101202612:00|||||||\n")
    (cache / B.OTHER).write_text(
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot "
        "Size|Test Issue|NASDAQ Symbol\n"
        "QZAB|QZ Alpha|N|QZAB|N|100|N|QZAB\n"
        "QZM|QZ Other Co|N|QZM|N|100|N|QZM\n"
        "File Creation Time: 0101202612:00||||||\n")
    hdr = ["Co_ID", "Root Ticker", "Name", "SP_Type"]
    _xlsx(cache / f"{B.TMX_XLSX_PREFIX}2026-01-01.xlsx", {
        "TSX Issuers": [hdr, ["1", "QZA", "QZ Alpha", ""],
                        ["2", "QZD", "QZ Delta CDR", "CDR"]],
        "TSXV Issuers": [hdr, ["3", "QZV", "QZ Venture", ""]]})
    K = B.Figi.key
    figi = {
        K(B.ticker_job("QZA", "CN")): [_eq("QZA", "CN", "BBG000000A01")],
        K(B.ticker_job("QZAB", "US")): [_eq("QZAB", "US", "BBG000000A01")],
        K(B.class_job("BBG000000A01", "US")): [
            _eq("QZAB", "US", "BBG000000A01"),
            _eq("QZAAF", "US", "BBG000000A01"),
            _eq("QZAAY", "US", "BBG000000A01", typ="ADR")],
        K(B.class_job("BBG000000A01", "CN")): [
            _eq("QZA", "CN", "BBG000000A01"),
            _eq("QZA/U", "CN", "BBG000000A01")],
        K(B.ticker_job("QZM", "CN")): [_eq("QZM", "CN", "BBG000000M01")],
        K(B.ticker_job("QZM", "US")): [_eq("QZM", "US", "BBG000000M02")],
        K(B.class_job("BBG000000M01", "US")): [],
        K(B.ticker_job("QZV", "CN")): [_eq("QZV", "CN", "BBG000000V01")],
        K(B.class_job("BBG000000V01", "US")): [
            _eq("QZVVF", "US", "BBG000000V01")],
        K(B.ticker_job("QZD", "CN")): [_eq("QZD", "CN", "BBG000000D01",
                                           typ="Canadian DR")],
        K(B.ticker_job("QZD", "US")): [_eq("QZD", "US", "BBG000000D02")],
        K({"idType": "ID_ISIN", "idValue": "US0000000001"}): [
            _eq("QZA", "US", "BBG000000A01")],
    }
    (cache / B.FIGI_CACHE).write_text(json.dumps(figi))
    return B, cache
