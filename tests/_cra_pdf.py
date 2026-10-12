"""Synthetic CRA My Account slip PDFs for the slip-audit tests: the page
layout CRA prints ("YYYY T5 slip (original) from ISSUER", then the box
table: number, name, value), written as a one-page PDF with Helvetica
text at fixed positions — no PDF library needed; `pdftotext -layout`
reads it back in columns. Every name, address and amount is made up."""
from pathlib import Path
from typing import List, Sequence, Tuple


def _pdf(items: List[Tuple[int, int, str]]) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = ("BT /F1 9 Tf\n" + "".join(
        f"1 0 0 1 {x} {y} Tm ({esc(t)}) Tj\n" for x, y, t in items)
        + "ET\n")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            f"<< /Length {len(content)} >>\nstream\n{content}endstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += (f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
            + "".join(f"{o:010d} 00000 n \n" for o in offs).encode())
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return out


TITLES = {"T5": "Statement of Investment Income",
          "T3": "Statement of Trust Income Allocations and Designations"}


def slip_lines(typ: str, issuer: Sequence[str],
               rows: Sequence[Tuple[str, Sequence[str], str]],
               other: Sequence[Tuple[str, str, str]] = (),
               year: int = 2025, status: str = "original",
               holder: str = "ZED SYNTHETIC-HOLDER") -> List[Tuple[int, int, str]]:
    """The page: `issuer` lines (a long name wraps), `rows` (box, name
    lines, value; a two-line name puts the number and the value between
    them, as CRA's page does), `other` (the "Other information" table).
    The recipient's name and a made-up address and SIN are on it too:
    the reader must never take them."""
    y = 760
    it = [(30, y, f"10/8/26, 9:00 AM"), (330, y, f"{year} {typ} "
                                                 f"{TITLES[typ]}")]
    y -= 30
    it.append((40, y, "Tax information slips (T4 and more)"))
    y -= 14
    it.append((40, y, f"Signed in as {holder}"))
    y -= 14
    # An address line shaped like a box row: never read as box 12.
    it += [(50, y, "12"), (160, y, "Recipient home"), (500, y, "Somewhere")]
    y -= 14
    it.append((40, y, "Social insurance number: (synthetic, on file)"))
    y -= 24
    it.append((40, y, f"{year} {typ} slip ({status}) from {issuer[0]}"))
    for extra in issuer[1:]:
        y -= 12
        it.append((40, y, extra))
    y -= 24
    it += [(50, y, "Box"), (160, y, "Box name"), (480, y, "Box value")]
    y -= 10
    it.append((50, y, "number"))
    y -= 18
    for box, names, value in rows:
        if len(names) == 1:
            it += [(50, y, box), (160, y, names[0]), (500, y, value)]
            y -= 14
        else:
            it.append((160, y, names[0]))
            y -= 8
            it += [(50, y, box), (500, y, value)]
            y -= 8
            it.append((160, y, names[1]))
            y -= 14
    if other:
        y -= 10
        it.append((40, y, "Other information"))
        y -= 14
        it += [(50, y, "Box number"), (160, y, "Box name"),
               (480, y, "Box value")]
        y -= 14
        for box, name, value in other:
            it += [(50, y, box), (160, y, name), (500, y, value)]
            y -= 14
    y -= 20
    it.append((40, y, "This is the latest information we have on file."))
    return it


def write(path: Path, items) -> Path:
    path = Path(path)
    path.write_bytes(_pdf(items))
    return path


def t5(path: Path, issuer, boxes: dict, currency: str = "CAD",
       other: dict = None, **kw) -> Path:
    rows = [("10", ("Actual amount of dividends other than",
                    "eligible dividends"), boxes.get("10", "0.00")),
            ("11", ("Taxable amount of dividends other than",
                    "eligible dividends"), boxes.get("11", "0.00")),
            ("13", ("Interest from Canadian sources",),
             boxes.get("13", "0.00")),
            ("18", ("Capital gains dividends",), boxes.get("18", "0.00")),
            ("23", ("Recipient type",), "Individual"),
            ("24", ("Actual amount of eligible dividends",),
             boxes.get("24", "0.00")),
            ("25", ("Taxable amount of eligible dividends",),
             boxes.get("25", "0.00")),
            ("27", ("Foreign currency",), currency),
            ("30", ("Equity Linked Notes Interest",), "0.00")]
    oth = [(b, {"15": "Foreign income", "16": "Foreign tax paid"}[b], v)
           for b, v in (other or {}).items()]
    return write(path, slip_lines("T5", issuer, rows, oth, **kw))


def t3(path: Path, issuer, boxes: dict, other: dict = None, **kw) -> Path:
    rows = [("18", ("Beneficiary code",), "Individual"),
            ("21", ("Capital gains",), boxes.get("21", "0.00")),
            ("23", ("Actual amount of dividends other than",
                    "eligible dividends"), boxes.get("23", "0.00")),
            ("26", ("Other income",), boxes.get("26", "0.00")),
            ("49", ("Actual amount of eligible dividends",),
             boxes.get("49", "0.00")),
            ("50", ("Taxable amount of eligible dividends",),
             boxes.get("50", "0.00"))]
    names = {"25": "Foreign non-business income",
             "34": "Foreign non-business income tax paid",
             "42": "Amounts resulting in cost base adjustment"}
    oth = [(b, names[b], v) for b, v in (other or {}).items()]
    return write(path, slip_lines("T3", issuer, rows, oth, **kw))
