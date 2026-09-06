"""
🎴 The launch-card **sheet** — the paper `launch_cards.py` was written to read back.

`launch_cards.encode` is the payload; this is the page a parent prints, cuts up and hands
to a child. It is the last piece of P0-c in
`docs/architecture/backlog/qr-launch-cards.md`, and it is deliberately thin: **every
string it puts under a QR comes from `launch_cards.encode`**, so the printing side can
never emit a payload the reading side refuses. There is no second copy of the payload
format in this file, and there must never be one — an id outside the closed catalog
raises out of `encode` before a single module is drawn.

Why an HTML page and not PNGs or a PDF
--------------------------------------
Upstream OpenMoxie ships 24 PNGs (`site/data/qr/extract.py`, MIT, (c) Justin Beghtol —
credited in `ATTRIBUTION.md`; the idea is theirs, the code below is ours). We ship **one
self-contained HTML file with the symbols as inline SVG**, for three reasons that are all
about paper rather than about taste:

1. **A raster QR is resampled by the printer; a vector one is not.** A PNG has a fixed
   pixel grid, and a home printer scales it to its own DPI — module edges land mid-pixel
   and come out as grey fringes, which is the ordinary way a home-printed QR stops
   scanning. Every `<rect>`/`<path>` below is rasterised by the print driver at the
   device's native resolution, so a module edge is exact at any size.
2. **The size is stated in millimetres, not in pixels.** `SYMBOL_MM` and `MODULE_MM` are
   physical quantities a test can check (`sim/tests/test_launch_sheet.py`), rather than a
   scale factor whose real size depends on a DPI nobody wrote down.
3. **One file, no pipeline.** A parent opens it and presses Ctrl-P; "Save as PDF" in the
   same dialog produces a PDF for anyone who wants one. The brief says explicitly: *do
   not build a PDF pipeline.*

The one dependency, and why it is optional
------------------------------------------
`segno` builds the QR matrix. It is **not** re-implemented here — Reed-Solomon and mask
selection are exactly the sort of thing to take from a library that is tested. It is
already a declared dependency of the console (`server/requirements.txt`) and therefore of
the test environment (`sim/tests/requirements-hermetic.txt`), so this file adds **nothing
new to CI**. It is imported *lazily* and declared as the `cards` extra in
`mqtt/pyproject.toml` rather than a base dependency, so the SDK wheel still installs with
`paho-mqtt` alone: importing this module never needs segno, and only `qr_matrix` (and its
callers) do. Absence is reported as one sentence naming the fix, not a traceback.

What the geometry is chosen for
-------------------------------
A card is scanned off paper by a robot's camera, so the number that matters is the
printed width of one module (`MODULE_MM`), and the second one is the quiet zone
(`QUIET_MODULES`, the four all-white modules ISO/IEC 18004 requires around the symbol —
plenty of home-printed codes fail only because a layout cropped them).

  * **Error correction Q (25 %)**, not the M the browser preview uses. A card lives in a
    child's hands: a crease, a thumb over a corner, a lamp reflection. Q survives all
    three, and here it is nearly free — the payloads are 13-26 bytes, so Q costs one
    symbol version (29x29 instead of 25x25) and nothing else.
  * **One version for the whole deck** (`deck_version`): every card is drawn at the
    version the *longest* payload needs, so all 24 symbols are the same size and density
    and the module size is one number for the sheet instead of 24. Derived from the
    payloads, never typed in — a longer id changes it automatically.
  * **56 mm symbols**, which at 29 modules + 8 quiet modules gives ~1.5 mm per module.
    The common field rule of thumb is a 10:1 read distance to symbol width, i.e. ~0.5 m
    for this card.

**Both of those last numbers are rules of thumb, not measurements of this robot.** Our
corpus says nothing about the camera's resolution, focus range or field of view
(`docs/architecture/vision.md` describes only the semantic events it emits), and **no
physical Moxie has ever sent us an `eb-qr-event`**. So the honest claim is: the symbols
are correct, they are large, and their quiet zone is intact — not that a Moxie has read
one. See `sim/tests/test_launch_sheet.py`, which reads the payload back out of the
rendered module matrix and hands it to the real `launch_cards.decode`; that closes
everything except the optics.

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
# The physical page. Every length is millimetres, because every one of them ends
# up as a millimetre on paper.
# --------------------------------------------------------------------------- #

#: Printer margin. Most consumer inkjets and lasers cannot image closer than ~6.4 mm to
#: the edge; 10 mm clears that on every one we could name and keeps the arithmetic round.
PAGE_MARGIN_MM = 10.0

#: The printable box **both** common paper sizes agree on, so one file prints correctly on
#: A4 (210 x 297 mm) and US Letter (215.9 x 279.4 mm) with no size flag and no second
#: build: the narrower width is A4's, the shorter height is Letter's.
SHEET_WIDTH_MM = 210.0 - 2 * PAGE_MARGIN_MM        # 190.0 — A4 is the narrower
SHEET_HEIGHT_MM = 279.4 - 2 * PAGE_MARGIN_MM       # 259.4 — Letter is the shorter

#: The running header on each printed sheet (cut instruction + page number).
HEADER_MM = 7.0

#: One card, and the grid of them. 2 x 3 is a deliberate choice over a denser grid: a card
#: is an object a child picks up and holds toward a robot's face, so postcard-sized beats
#: six-to-a-page, and the extra room is what buys the 1.5 mm module below. 24 ids print on
#: four sheets.
CARD_W_MM = 90.0
CARD_H_MM = 80.0
CARD_GAP_MM = 4.0
COLUMNS = 2
ROWS = 3

#: The printed width of the whole QR symbol, quiet zone included.
SYMBOL_MM = 56.0

#: The all-white border ISO/IEC 18004 requires around a symbol, in modules. Four is the
#: standard's own minimum and it is drawn as part of the SVG, so no layout can crop it.
QUIET_MODULES = 4

#: Error-correction level for a card. See the module docstring — Q (25 %) costs one symbol
#: version at these payload lengths and buys tolerance of a crease, a thumb and a glare.
ERROR_LEVEL = "q"

#: Byte mode, explicitly. segno would otherwise split `GO<launch:DM>` into mixed
#: alphanumeric/byte segments — legal, decodable, and pointless here (the version is
#: pinned by the longest payload either way), while a single byte segment is the most
#: universally handled encoding there is and is what the browser twin (`sim/web/qr.js`)
#: already emits. Determinism is the other half: with the version pinned, the error level
#: un-boosted and the mode fixed, one payload has exactly one matrix.
ENCODE_MODE = "byte"

#: The floor a printed module must clear for a camera to resolve it. Practical guidance,
#: **not** a measurement of Moxie's camera, which our corpus does not describe. The sheet
#: as configured lands at roughly 1.5 mm, so this is a guard against a future geometry
#: edit silently shrinking the paper, not a claim about optics.
MIN_MODULE_MM = 0.6

#: Pure black on pure white. Contrast is a scannability property, so it is named here
#: rather than left to a stylesheet: no brand colour goes near a symbol.
DARK = "#000000"
LIGHT = "#ffffff"


# --------------------------------------------------------------------------- #
# 1. What goes on a card
# --------------------------------------------------------------------------- #
def card_label(module_id: str) -> str:
    """The human-readable name for a card, or the id when there is no honest name.

    `schedule.MODULE_LABELS` maps only the ids that are unambiguously an English word or
    phrase; it deliberately does not invent Embodied product names for the rest (`AB`,
    `FF`, `RDL` today). A card for one of those shows its id, and `unlabelled_ids` below
    lets the sheet say so in as many words rather than leaving a parent to guess.
    """
    labels = getattr(schedule_seam, "MODULE_LABELS", {})
    label = labels.get(module_id) if isinstance(labels, dict) else None
    return label if isinstance(label, str) and label else module_id


def catalog_ids() -> List[str]:
    """The ids a sheet covers by default — `launch_cards`' closed catalog, sorted.

    Read through `launch_cards` rather than re-derived from `schedule.py`, because the
    catalog has exactly one owner and this is not it. `launch_cards._catalog()` is a live
    function of `schedule.py`; a third derivation here would be the very drift
    `sim/test_qr.mjs` exists to catch.
    """
    return sorted(cards_seam.LAUNCHABLE_MODULE_IDS)


def cards_for(ids: Optional[Iterable[str]] = None) -> List[Tuple[str, str, str]]:
    """`(module_id, label, payload)` for each requested id, in the order given.

    **The payload comes from `launch_cards.encode` and from nowhere else.** That is the
    whole safety story of this module: an id outside the catalog raises here, so a sheet
    cannot exist that carries a card the runtime would refuse. Requesting nothing means
    the whole catalog.
    """
    wanted = catalog_ids() if ids is None else [str(i) for i in ids]
    if not wanted:
        raise ValueError("a sheet needs at least one module id")
    return [(i, card_label(i), cards_seam.encode(i)) for i in wanted]


def unlabelled_ids(ids: Optional[Iterable[str]] = None) -> List[str]:
    """The requested ids `schedule.MODULE_LABELS` has no plain-English name for.

    Derived, so the sheet's footnote can name them without anyone transcribing a list that
    would rot the first time a label is added.
    """
    return [i for i, label, _ in cards_for(ids) if label == i]


# --------------------------------------------------------------------------- #
# 2. The symbol
# --------------------------------------------------------------------------- #
def _segno():
    """`segno`, or one sentence saying how to get it. See the module docstring.

    Imported here rather than at module scope so the SDK wheel keeps installing with
    `paho-mqtt` alone and so importing this module — which every test of the pure parts
    above does — never needs it.
    """
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

    The maximum of what each payload needs on its own, so the whole deck is the same size
    and density and `module_mm` is a single number for the sheet. Derived from the
    payloads: a longer module id, or a card carrying a `content_id`, moves it by itself.

    `make_qr`, never `make`: `segno.make` returns a **Micro QR** when a short payload fits
    one (`M4-M` holds 15 bytes and `GO<launch:AB>` is 13), and that is a different
    symbology — its own version numbering (`"M4"`, a string, not an int), a two-module
    quiet zone, and no reason whatever to believe the robot's reader accepts it. Forcing
    the ordinary kind keeps the version an integer and the deck one symbology. Found by
    mutating `ERROR_LEVEL` to `m` and watching this function crash.
    """
    segno = _segno()
    if not payloads:
        raise ValueError("a deck needs at least one payload")
    return max(segno.make_qr(p, error=ERROR_LEVEL, mode=ENCODE_MODE,
                             boost_error=False).version
               for p in payloads)


def qr_matrix(payload: str, version: int) -> List[List[int]]:
    """The QR module matrix for one payload — rows of 0/1, quiet zone **not** included.

    `boost_error=False` matters: segno raises the error level on its own when a shorter
    payload leaves room, which would give the deck a mix of Q and H symbols. One stated
    level for every card beats a slightly better one on some of them. `make_qr` rather
    than `make` for the reason `deck_version` gives: never a Micro QR.
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

    The `viewBox` is in *modules*, the `width`/`height` in mm, so the browser's print
    rasteriser draws every module edge on the device's own pixel grid at whatever DPI it
    has — the property a PNG cannot have. Horizontal runs of dark modules are merged into
    one path segment, which keeps a 24-card file small enough to open instantly.
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
        f'<svg class="qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {units} {units}"'
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

    The point of returning this rather than measuring the HTML: the guard in
    `sim/tests/test_launch_sheet.py` checks the *arithmetic of the paper* — that the grid
    fits inside the printable box of both paper sizes, that the symbol fits inside a card,
    and that a module clears `MIN_MODULE_MM` — instead of regexing a stylesheet.
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

    No external stylesheet, script or font — a file bound for a printer must not depend on
    a CDN being up, and a parent may well open it with the appliance switched off.
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
