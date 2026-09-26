"""
🎴 The launch-card **sheet** — the paper `launch_cards.py` was written to read back.

The page a parent prints and cuts up (P0-c of backlog/qr-launch-cards.md). Every payload
comes from `launch_cards.encode` — there is no second copy of the format here, and an id
outside the catalog raises before anything is drawn.

**One self-contained HTML file with inline-SVG symbols** (OpenMoxie ships PNGs; the idea is
theirs, the code ours — ATTRIBUTION.md): vector modules are rasterised at the printer's
native resolution (no grey fringes), sizes are physical millimetres a test can check, and
Ctrl-P / "Save as PDF" is the whole pipeline.

**segno** builds the matrix (imported lazily; the `cards` extra in `mqtt/pyproject.toml`),
so the SDK wheel still installs with paho-mqtt alone.

**Geometry.** Error correction Q (a crease, a thumb, a glare), one symbol version for the
whole deck (`deck_version`, derived from the payloads), 56 mm symbols with the ISO/IEC
18004 four-module quiet zone ≈ 1.5 mm/module. These are rules of thumb, not measurements
of Moxie's camera, and no Moxie has scanned one; `sim/tests/test_launch_sheet.py` decodes
the rendered matrix with the real `launch_cards.decode`, which covers all but the optics.

Usage
-----
    python3 -m moxie_sdk.launch_sheet -o cards.html      # then open it and print
    python3 -m moxie_sdk.launch_sheet --ids DRAW,JOKE    # a two-card sheet
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import launch_cards as cards_seam
from . import schedule as schedule_seam

# --------------------------------------------------------------------------- #
# The physical page, in millimetres.
# --------------------------------------------------------------------------- #

#: Printer margin (consumer printers cannot image closer than ~6.4 mm).
PAGE_MARGIN_MM = 10.0

#: The printable box both A4 and US Letter agree on (A4's width, Letter's height).
SHEET_WIDTH_MM = 210.0 - 2 * PAGE_MARGIN_MM        # 190.0 — A4 is the narrower
SHEET_HEIGHT_MM = 279.4 - 2 * PAGE_MARGIN_MM       # 259.4 — Letter is the shorter

#: The running header on each printed sheet (cut instruction + page number).
HEADER_MM = 7.0

#: One card and the grid: postcard-sized 2 x 3, which buys the 1.5 mm module.
CARD_W_MM = 90.0
CARD_H_MM = 80.0
CARD_GAP_MM = 4.0
COLUMNS = 2
ROWS = 3

#: The printed width of the whole QR symbol, quiet zone included.
SYMBOL_MM = 56.0

#: ISO/IEC 18004's minimum quiet zone, drawn inside the SVG so no layout can crop it.
QUIET_MODULES = 4

#: Error-correction level Q (25 %).
ERROR_LEVEL = "q"

#: Byte mode, explicitly: the most widely handled encoding, what `sim/web/qr.js` emits,
#: and (with version and level pinned) one payload → exactly one matrix.
ENCODE_MODE = "byte"

#: Guard against a geometry edit shrinking modules (practical guidance, not optics data).
MIN_MODULE_MM = 0.6

#: Pure black on white — contrast is a scannability property.
DARK = "#000000"
LIGHT = "#ffffff"


# --------------------------------------------------------------------------- #
# 1. What goes on a card
# --------------------------------------------------------------------------- #
def card_label(module_id: str) -> str:
    """The card's name from `schedule.MODULE_LABELS`, or the id when there is no honest
    name (we invent none)."""
    labels = getattr(schedule_seam, "MODULE_LABELS", {})
    label = labels.get(module_id) if isinstance(labels, dict) else None
    return label if isinstance(label, str) and label else module_id


def catalog_ids() -> List[str]:
    """The default ids — `launch_cards`' closed catalog (its one owner), sorted."""
    return sorted(cards_seam.LAUNCHABLE_MODULE_IDS)


def cards_for(ids: Optional[Iterable[str]] = None) -> List[Tuple[str, str, str]]:
    """`(module_id, label, payload)` for each requested id, in the order given.

    The payload comes only from `launch_cards.encode`, so an id outside the catalog raises
    here. `None` means the whole catalog.
    """
    wanted = catalog_ids() if ids is None else [str(i) for i in ids]
    if not wanted:
        raise ValueError("a sheet needs at least one module id")
    return [(i, card_label(i), cards_seam.encode(i)) for i in wanted]


def unlabelled_ids(ids: Optional[Iterable[str]] = None) -> List[str]:
    """The requested ids with no plain-English name (for the sheet's footnote)."""
    return [i for i, label, _ in cards_for(ids) if label == i]


# --------------------------------------------------------------------------- #
# 2. The symbol
# --------------------------------------------------------------------------- #
def _segno():
    """`segno` (lazily), or one sentence saying how to get it."""
    try:
        import segno                                       # noqa: PLC0415
    except ImportError as exc:                             # pragma: no cover - env-shaped
        raise RuntimeError(
            "printing launch cards needs the `segno` QR encoder — "
            "pip install 'moxie-cloud-sdk[cards]' (or: pip install segno)"
        ) from exc
    return segno


def deck_version(payloads: Sequence[str]) -> int:
    """The one symbol version every card in this deck is drawn at.

    The max any payload needs, so every symbol has one size. `make_qr`, never `make`:
    `segno.make` may return a Micro QR, a different symbology the robot may not read.
    """
    segno = _segno()
    if not payloads:
        raise ValueError("a deck needs at least one payload")
    return max(segno.make_qr(p, error=ERROR_LEVEL, mode=ENCODE_MODE,
                             boost_error=False).version
               for p in payloads)


def qr_matrix(payload: str, version: int) -> List[List[int]]:
    """The QR module matrix for one payload — rows of 0/1, quiet zone **not** included.

    `boost_error=False` keeps one stated level for every card.
    """
    segno = _segno()
    qr = segno.make_qr(payload, error=ERROR_LEVEL, mode=ENCODE_MODE,
                       version=version, boost_error=False)
    return [list(row) for row in qr.matrix]


def module_mm(version: int, symbol_mm: float = SYMBOL_MM) -> float:
    """The printed width of one module, in millimetres. The scannability number."""
    return symbol_mm / float(version * 4 + 17 + 2 * QUIET_MODULES)


def symbol_svg(payload: str, version: int, symbol_mm: float = SYMBOL_MM,
               label: str = "") -> str:
    """One QR symbol as inline SVG, sized in millimetres.

    `viewBox` in modules, size in mm; horizontal dark runs merge into one path segment.
    No `xmlns` (redundant inside HTML), so the page contains no URL at all.
    """
    matrix = qr_matrix(payload, version)
    units = len(matrix) + 2 * QUIET_MODULES
    parts: List[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            run = 0
            while x + run < len(row) and row[x + run]:
                run += 1
            parts.append(f"M{x + QUIET_MODULES} {y + QUIET_MODULES}h{run}v1h-{run}z")
            x += run
    alt = _escape(label or payload)
    return (
        f'<svg class="qr" viewBox="0 0 {units} {units}"'
        f' width="{symbol_mm}mm" height="{symbol_mm}mm" shape-rendering="crispEdges"'
        f' role="img" aria-label="QR code for {alt}">'
        f'<rect width="{units}" height="{units}" fill="{LIGHT}"/>'
        f'<path fill="{DARK}" d="{"".join(parts)}"/>'
        "</svg>"
    )


# --------------------------------------------------------------------------- #
# 3. The page
# --------------------------------------------------------------------------- #
def geometry(version: int, symbol_mm: float = SYMBOL_MM) -> Dict[str, float]:
    """Every physical fact about the sheet, as numbers a test can assert on.

    (`test_launch_sheet.py` checks the paper arithmetic, not the stylesheet.)
    """
    grid_w = COLUMNS * CARD_W_MM + (COLUMNS - 1) * CARD_GAP_MM
    grid_h = ROWS * CARD_H_MM + (ROWS - 1) * CARD_GAP_MM
    return {
        "version": float(version),
        "modules": float(version * 4 + 17),
        "units": float(version * 4 + 17 + 2 * QUIET_MODULES),
        "module_mm": module_mm(version, symbol_mm),
        "symbol_mm": symbol_mm,
        "quiet_mm": QUIET_MODULES * module_mm(version, symbol_mm),
        "card_w_mm": CARD_W_MM,
        "card_h_mm": CARD_H_MM,
        "grid_w_mm": grid_w,
        "grid_h_mm": grid_h,
        "page_w_mm": SHEET_WIDTH_MM,
        "page_h_mm": SHEET_HEIGHT_MM,
        "used_h_mm": HEADER_MM + grid_h,
        "per_page": float(COLUMNS * ROWS),
    }


def _escape(text: str) -> str:
    """Minimal HTML escaping. A payload carries `<` and `>` and is shown verbatim."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_CSS = """
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #eceff1; color: #12181d;
       font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
.intro {{ max-width: 210mm; margin: 0 auto; padding: 18px 12mm 6px; }}
.intro h1 {{ font-size: 20px; margin: 0 0 6px; }}
.intro p {{ margin: 6px 0; color: #33424e; }}
.intro code {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
              background: #dde3e8; padding: 1px 4px; border-radius: 3px; }}
.sheet {{ width: {page_w}mm; min-height: {page_h}mm; margin: 12px auto; padding: 0;
         background: {light}; }}
.head {{ height: {header}mm; display: flex; align-items: center;
        justify-content: space-between; font-size: 8pt; color: #55636e; }}
.grid {{ display: grid; grid-template-columns: repeat({cols}, {card_w}mm);
        grid-auto-rows: {card_h}mm; gap: {gap}mm; }}
.card {{ border: 0.3mm dashed #9aa7b1; border-radius: 2mm; display: flex;
        flex-direction: column; align-items: center; justify-content: center;
        padding: 3mm; break-inside: avoid; page-break-inside: avoid; }}
.name {{ font-size: 12pt; font-weight: 600; text-align: center; line-height: 1.15;
        margin-bottom: 1.5mm; }}
.qr {{ display: block; }}
.payload {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
           font-size: 7pt; color: #3d4a55; margin-top: 1.5mm; letter-spacing: 0.2px; }}
@media print {{
  @page {{ margin: {margin}mm; }}
  body {{ background: {light}; }}
  .intro {{ display: none; }}
  .sheet {{ width: auto; min-height: 0; margin: 0; }}
  .sheet + .sheet {{ break-before: page; page-break-before: always; }}
  .card {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
}}
"""


def render_sheet(ids: Optional[Iterable[str]] = None,
                 symbol_mm: float = SYMBOL_MM,
                 title: str = "Moxie launch cards") -> str:
    """The whole printable page: one self-contained HTML document, no network at all.

    No external stylesheet, script or font — it must print with nothing online.
    """
    deck = cards_for(ids)
    version = deck_version([p for _, _, p in deck])
    geo = geometry(version, symbol_mm)
    per_page = COLUMNS * ROWS
    pages = [deck[i:i + per_page] for i in range(0, len(deck), per_page)]
    unnamed = [i for i, label, _ in deck if label == i]

    css = _CSS.format(page_w=SHEET_WIDTH_MM, page_h=SHEET_HEIGHT_MM, header=HEADER_MM,
                      cols=COLUMNS, card_w=CARD_W_MM, card_h=CARD_H_MM, gap=CARD_GAP_MM,
                      margin=PAGE_MARGIN_MM, light=LIGHT)

    out: List[str] = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_escape(title)}</title>", f"<style>{css}</style>",
        "</head><body>",
        '<div class="intro">', f"<h1>{_escape(title)}</h1>",
        "<p>Print this page, cut along the dashed lines, and keep the cards where your "
        "child can reach them. Holding one up to Moxie's face starts that activity — a "
        "card can start an activity and do nothing else.</p>",
        f"<p>{len(deck)} cards on {len(pages)} sheet{'' if len(pages) == 1 else 's'}. "
        f"Prints correctly on A4 and US Letter at 100% scale (do not tick "
        f"&ldquo;fit to page&rdquo;, which shrinks the codes). Each symbol is "
        f"{symbol_mm:g}&nbsp;mm wide, error-correction level "
        f"{ERROR_LEVEL.upper()}, {geo['module_mm']:.2f}&nbsp;mm per module.</p>",
    ]
    if unnamed:
        out.append(
            "<p>No plain-English name is recorded for "
            + ", ".join(f"<code>{_escape(i)}</code>" for i in unnamed)
            + ", so those cards show the activity id instead. We do not invent names.</p>")
    out.append(
        "<p>Moxie only reads a card while an activity has asked for one; a card shown at "
        "any other time does nothing. The text under each code is exactly what the code "
        "carries, so you can check a card without scanning it.</p>")
    out.append("</div>")

    for n, page in enumerate(pages, 1):
        out.append('<section class="sheet">')
        out.append('<div class="head"><span>Moxie launch cards &mdash; cut along the '
                   'dashed lines</span>'
                   f"<span>Sheet {n} of {len(pages)}</span></div>")
        out.append('<div class="grid">')
        for module_id, label, payload in page:
            out.append(
                '<div class="card">'
                f'<div class="name">{_escape(label)}</div>'
                f"{symbol_svg(payload, version, symbol_mm, label=label)}"
                f'<div class="payload">{_escape(payload)}</div>'
                "</div>")
        out.append("</div></section>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# 4. CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python3 -m moxie_sdk.launch_sheet -o cards.html`, in the shape `broker_acl` uses."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="python3 -m moxie_sdk.launch_sheet",
        description="Print a sheet of Moxie launch cards (HTML; open it and press Ctrl-P).")
    ap.add_argument("-o", "--out", default="-",
                    help="output file, or - for stdout (default)")
    ap.add_argument("--ids", default="",
                    help="comma-separated module ids (default: the whole catalog)")
    ap.add_argument("--symbol-mm", type=float, default=SYMBOL_MM,
                    help=f"printed width of each QR symbol in mm (default {SYMBOL_MM:g})")
    ap.add_argument("--list", action="store_true",
                    help="list the launchable ids and their payloads, and exit")
    args = ap.parse_args(argv)

    ids = [i.strip() for i in args.ids.split(",") if i.strip()] or None
    try:
        if args.list:
            for module_id, label, payload in cards_for(ids):
                print(f"{module_id:<16} {payload:<32} {label}")
            return 0
        html = render_sheet(ids, symbol_mm=args.symbol_mm)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.out == "-":
        sys.stdout.write(html)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(html)
        version = deck_version([p for _, _, p in cards_for(ids)])
        geo = geometry(version, args.symbol_mm)
        print(f"wrote {args.out}: {len(cards_for(ids))} cards, "
              f"{int(geo['modules'])}x{int(geo['modules'])} symbols at "
              f"{geo['symbol_mm']:g} mm ({geo['module_mm']:.2f} mm/module, "
              f"EC {ERROR_LEVEL.upper()}). Open it and print at 100% scale.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":                                 # pragma: no cover - CLI
    raise SystemExit(main())
