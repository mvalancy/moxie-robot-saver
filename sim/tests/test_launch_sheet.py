"""
🎴 The printable sheet — the paper half of the launch-card feature.

`test_launch_cards.py` proves what a scanned string may do. This file proves the other
direction: that the page a parent prints carries exactly the cards the decoder admits,
and nothing else.

The assertion that carries the weight is not "the caption under the code says
`GO<launch:DRAW>`" — that only proves a string we already had. It is
`test_every_card_reads_back_out_of_its_own_modules_and_decodes`, which walks the rendered
QR **module matrix** the way a scanner does (`helpers_qr_matrix.py`: format information,
un-mask, zig-zag, de-interleave, byte segment) and hands the result to the real
`launch_cards.decode`. Everything between the catalog and the ink is then proven.
**What is not proven is optics**: no physical Moxie has ever read one of these, our
corpus says nothing about its camera, and nobody here can print a sheet and scan it. The
geometry tests below check the arithmetic of the paper — module size, quiet zone, that
the grid fits both A4 and Letter — which is the part a test can see.

Hermetic and pure: no broker, no clock, no model, no network. `segno` is a declared test
dependency (`requirements-hermetic.txt` -> `server/requirements.txt`), so it is a hard
requirement here rather than an `importorskip` that would read as a pass.
"""
from __future__ import annotations

import ast
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

import helpers_qr_matrix as qrm                                         # noqa: E402
from moxie_sdk import launch_cards as cards                             # noqa: E402
from moxie_sdk import launch_sheet as sheet                             # noqa: E402
from moxie_sdk import schedule                                          # noqa: E402
from moxie_sdk.types import ActionType                                  # noqa: E402

SOURCE = os.path.join(REPO, "mqtt", "moxie_sdk", "launch_sheet.py")


@pytest.fixture(scope="module")
def deck():
    return sheet.cards_for()


@pytest.fixture(scope="module")
def version(deck):
    return sheet.deck_version([p for _, _, p in deck])


@pytest.fixture(scope="module")
def html():
    return sheet.render_sheet()


# --------------------------------------------------------------------------- #
# 1. The payload comes from `encode`, and from nowhere else
# --------------------------------------------------------------------------- #
def test_every_card_payload_is_the_one_launch_cards_encode_produces(deck):
    """The design rule the sheet exists under: the printing side never formats a payload.
    Compared against `launch_cards.encode` id by id, so a second formatter appearing here
    would have to disagree with this line to matter."""
    assert [(i, p) for i, _, p in deck] == [(i, cards.encode(i))
                                            for i in sorted(cards.LAUNCHABLE_MODULE_IDS)]


def _code_string_literals(path):
    """Every string literal in the module's *code* — docstrings and comments excluded.

    Playbook rule 17: a guard must assert over code, not over the whole file. This module
    quotes `GO<launch:DM>` in its docstring on purpose, which is the house style
    everywhere else here; what must not exist is a literal in an executable position.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def test_the_sheet_does_not_carry_a_second_copy_of_the_payload_format():
    """No executable string in the generator spells the card grammar. If it did, a sheet
    could drift from the decoder silently — the exact failure `launch_cards.encode`'s
    docstring says it lives outside the sheet generator to prevent."""
    offenders = [s for s in _code_string_literals(SOURCE)
                 if "<launch" in s or s == cards.CARD_PREFIX or "GO<" in s]
    assert not offenders, offenders


def test_the_sheet_does_not_re_derive_the_catalog_from_the_schedule_module():
    """The catalog has exactly one owner (`launch_cards._catalog`) and this is not it.
    A third derivation is the drift `sim/test_qr.mjs` was written to catch."""
    tree = ast.parse(open(SOURCE, encoding="utf-8").read())
    reads = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "ONBOARD_MODULES" not in reads and "DEFAULT_TEMPLATE" not in reads
    assert sheet.catalog_ids() == sorted(cards.LAUNCHABLE_MODULE_IDS)


def test_the_default_sheet_covers_the_whole_closed_catalog(deck):
    assert len(deck) == 24
    assert {i for i, _, _ in deck} == set(cards.LAUNCHABLE_MODULE_IDS)


# --------------------------------------------------------------------------- #
# 2. The refusal path — an id outside the catalog produces no paper at all
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["NOPE", "dm", "DM ", "DM:DRAW", " DM", "launch",
                                 "sleep", "exit", "*", "AB\x00"])
def test_an_id_outside_the_catalog_raises_instead_of_producing_paper(bad):
    """It raises out of `launch_cards.encode`, before a single module is drawn — the
    property `encode`'s docstring names as the reason it sits outside this file."""
    with pytest.raises(ValueError):
        sheet.cards_for([bad])
    with pytest.raises(ValueError):
        sheet.render_sheet([bad])


def test_one_bad_id_among_good_ones_refuses_the_whole_sheet():
    """Not a partial sheet with a hole in it: the run fails and the parent gets told."""
    with pytest.raises(ValueError):
        sheet.render_sheet(["DRAW", "NOPE", "JOKE"])


def test_a_sheet_of_nothing_is_refused_rather_than_rendered_empty():
    with pytest.raises(ValueError):
        sheet.render_sheet([])


def test_the_refusal_survives_the_whole_render_path(deck):
    """Anti-vacuity for the tests above: the same call shape that refuses `NOPE` really
    does produce a page for a catalog id, so the refusals are the guard and not a
    universally broken function."""
    assert "<svg" in sheet.render_sheet(["DRAW"])


# --------------------------------------------------------------------------- #
# 3. The proof that matters: read the payload back OUT of the printed modules
# --------------------------------------------------------------------------- #
def test_every_card_reads_back_out_of_its_own_modules_and_decodes(deck, version):
    """Walk each rendered symbol the way a scanner does and hand what comes out to the
    real `launch_cards.decode`. This is the only assertion in the file that touches the
    black squares rather than the string that produced them."""
    for module_id, _, payload in deck:
        matrix = sheet.qr_matrix(payload, version)
        read = qrm.read_payload(matrix)
        assert read == payload, module_id
        action = cards.decode(read)
        assert action is not None, f"{module_id}: the sheet drew a card decode refuses"
        assert action.type is ActionType.LAUNCH
        assert action.module_id == module_id
        assert action.content_id is None


def test_every_symbol_declares_error_level_q_and_the_pinned_version(deck, version):
    """Read out of the symbol's own format information, whose BCH is recomputed rather
    than looked up — so a matrix with corrupt format modules fails here."""
    for module_id, _, payload in deck:
        matrix = sheet.qr_matrix(payload, version)
        level, mask = qrm.format_info(matrix)
        assert level == "Q", module_id
        assert 0 <= mask <= 7
        assert qrm.version_of(matrix) == version


def test_no_card_is_ever_drawn_as_a_micro_qr(deck, version):
    """`segno.make` returns a Micro QR for a short payload at a forgiving level — a
    different symbology with a string version and a two-module quiet zone, which we have
    no reason to think the robot's reader accepts. `make_qr` forbids it, so the version is
    always an integer and no symbol is smaller than 21x21."""
    assert isinstance(version, int)
    for _, _, payload in deck:
        assert isinstance(sheet.deck_version([payload]), int), payload
        assert len(sheet.qr_matrix(payload, version)) >= 21


def test_the_whole_deck_is_drawn_at_one_size(deck, version):
    """One version for every card, so the deck is uniform and `module_mm` is a single
    number for the sheet instead of twenty-four."""
    sizes = {len(sheet.qr_matrix(p, version)) for _, _, p in deck}
    assert sizes == {version * 4 + 17}


def test_the_deck_version_is_derived_from_the_payloads_not_typed_in(version):
    """A longer payload moves it by itself — proven with a card carrying a content id,
    which the decoder admits and which no default sheet contains."""
    long_payload = cards.encode("DRAW", "x" * 30)
    assert sheet.deck_version([long_payload]) > version
    bigger = sheet.deck_version([p for _, _, p in sheet.cards_for()] + [long_payload])
    assert bigger == sheet.deck_version([long_payload])


def test_a_content_id_card_also_reads_back_and_decodes():
    """The longer payload takes a bigger symbol, and the same walk still recovers it."""
    payload = cards.encode("DRAW", "x" * 30)
    version = sheet.deck_version([payload])
    read = qrm.read_payload(sheet.qr_matrix(payload, version))
    assert read == payload
    action = cards.decode(read)
    assert action.module_id == "DRAW" and action.content_id == "x" * 30


def test_the_matrix_reader_refuses_a_symbol_it_cannot_honestly_read():
    """Anti-vacuity for the reader: it is not a function that returns something plausible
    for anything. Past version 6 its block table stops, and it says so by name rather than
    walking a symbol whose structure it does not model."""
    payload = cards.encode("DRAW", "x" * 105)
    version = sheet.deck_version([payload])
    assert version >= 7
    with pytest.raises(qrm.QRReadError):
        qrm.read_payload(sheet.qr_matrix(payload, version))


def test_the_matrix_reader_notices_a_flipped_module(deck, version):
    """The second anti-vacuity leg: no Reed-Solomon, deliberately, so damage surfaces
    instead of being repaired rather than a wrong card being read as a right one.

    The bottom-right corner is where symbol character placement starts, so those modules
    carry the mode indicator, the length and the first characters — the bytes a repair
    would have to invent. Each flip must change what comes out, or be refused outright."""
    payload = cards.encode("DRAW")
    clean = sheet.qr_matrix(payload, version)
    size = len(clean)
    flips = [(size - 1, size - 1), (size - 1, size - 2), (size - 2, size - 1),
             (size - 3, size - 1), (size - 4, size - 2)]
    for row, col in flips:
        matrix = sheet.qr_matrix(payload, version)
        matrix[row][col] ^= 1
        try:
            assert qrm.read_payload(matrix) != payload, (row, col)
        except (qrm.QRReadError, UnicodeDecodeError):
            pass                            # refusing outright is also a correct answer


# --------------------------------------------------------------------------- #
# 4. The geometry — the arithmetic of the paper
# --------------------------------------------------------------------------- #
def test_a_printed_module_clears_the_size_floor(version):
    """The scannability number. ~1.5 mm as configured; the floor is practical guidance,
    not a measurement of Moxie's camera, which our corpus does not describe."""
    geo = sheet.geometry(version)
    assert geo["module_mm"] >= sheet.MIN_MODULE_MM
    assert geo["module_mm"] == pytest.approx(1.51, abs=0.02)


def test_the_quiet_zone_is_four_modules_and_is_inside_the_symbol(version, deck):
    """ISO/IEC 18004's minimum, drawn as part of the SVG so no layout can crop it —
    the ordinary way a home-printed code stops scanning."""
    geo = sheet.geometry(version)
    assert sheet.QUIET_MODULES == 4
    assert geo["units"] == geo["modules"] + 2 * sheet.QUIET_MODULES
    svg = sheet.symbol_svg(deck[0][2], version)
    assert f'viewBox="0 0 {int(geo["units"])} {int(geo["units"])}"' in svg


def test_no_dark_module_is_drawn_inside_the_quiet_zone(version, deck):
    """Asserted over the path data itself, not over the constant that was meant to
    produce it."""
    units = int(sheet.geometry(version)["units"])
    quiet = sheet.QUIET_MODULES
    for _, _, payload in deck:
        svg = sheet.symbol_svg(payload, version)
        runs = re.findall(r"M(\d+) (\d+)h(\d+)", svg)
        assert runs
        for x, y, run in ((int(a), int(b), int(c)) for a, b, c in runs):
            assert quiet <= x and x + run <= units - quiet
            assert quiet <= y < units - quiet


def test_the_grid_fits_the_printable_box_of_both_a4_and_us_letter(version):
    """One file, no paper-size flag: the width budget is A4's and the height budget is
    Letter's, so whichever a parent has, the cards land inside the printable area."""
    geo = sheet.geometry(version)
    assert geo["grid_w_mm"] <= geo["page_w_mm"]
    assert geo["used_h_mm"] <= geo["page_h_mm"]
    assert geo["page_w_mm"] + 2 * sheet.PAGE_MARGIN_MM <= 210.0          # A4 width
    assert geo["page_h_mm"] + 2 * sheet.PAGE_MARGIN_MM <= 279.4          # Letter height


def test_the_symbol_fits_inside_a_card_with_room_for_its_labels(version):
    geo = sheet.geometry(version)
    assert geo["symbol_mm"] < geo["card_w_mm"] - 6.0
    assert geo["symbol_mm"] < geo["card_h_mm"] - 12.0


def test_the_symbol_is_pure_black_on_pure_white(version, deck):
    """Contrast is a scannability property, so no brand colour goes near a symbol."""
    svg = sheet.symbol_svg(deck[0][2], version)
    assert f'fill="{sheet.LIGHT}"' in svg and f'fill="{sheet.DARK}"' in svg
    assert sheet.DARK == "#000000" and sheet.LIGHT == "#ffffff"
    assert len(set(re.findall(r'fill="(#[0-9a-fA-F]{6})"', svg))) == 2


def test_the_symbol_is_vector_and_sized_in_millimetres(version, deck):
    """The whole reason the sheet is SVG rather than PNG: a printer rasterises it at its
    own DPI, so a module edge is exact at any size."""
    svg = sheet.symbol_svg(deck[0][2], version)
    assert f'width="{sheet.SYMBOL_MM}mm"' in svg and f'height="{sheet.SYMBOL_MM}mm"' in svg
    assert 'shape-rendering="crispEdges"' in svg
    assert "<image" not in svg and "data:image" not in svg


# --------------------------------------------------------------------------- #
# 5. The page a parent actually opens
# --------------------------------------------------------------------------- #
def test_the_page_lays_twenty_four_cards_onto_four_sheets(html, deck):
    assert html.count('<section class="sheet">') == 4
    assert html.count('<div class="card">') == len(deck)
    assert "Sheet 1 of 4" in html and "Sheet 4 of 4" in html


def test_every_payload_appears_under_its_own_code_exactly_once(html, deck):
    """The card is auditable by eye: a parent can read what a card carries without
    scanning it, which is a real property for an unauthenticated input."""
    for _, _, payload in deck:
        shown = payload.replace("<", "&lt;").replace(">", "&gt;")
        assert html.count(f'<div class="payload">{shown}</div>') == 1


def test_every_card_is_named_for_a_human(html, deck):
    for _, label, _ in deck:
        assert f'<div class="name">{label}</div>' in html


def test_a_card_with_no_recorded_name_shows_its_id_and_the_page_says_so(html):
    """`schedule.MODULE_LABELS` deliberately maps only ids that are unambiguously an
    English phrase; we do not invent Embodied product names. The footnote naming the
    unlabelled ids is derived, so it cannot rot when a label is added."""
    unnamed = sheet.unlabelled_ids()
    assert unnamed == [i for i in sheet.catalog_ids() if i not in schedule.MODULE_LABELS]
    assert unnamed, "anti-vacuity: today three ids have no label"
    assert "No plain-English name is recorded for" in html
    for module_id in unnamed:
        assert f"<code>{module_id}</code>" in html
        assert sheet.card_label(module_id) == module_id
    assert sheet.card_label("DRAW") == "Drawing"


def test_the_page_is_self_contained_and_reaches_no_network(html):
    """A file bound for a printer must not need a CDN to be up, and a parent may open it
    with the appliance switched off."""
    assert "<script" not in html and "@import" not in html
    assert "url(" not in html
    for attr in ("src=", 'href="http', "<link"):
        assert attr not in html
    assert html.count("http") == html.count("http://www.w3.org/2000/svg") * 1 + \
        html.count("https://") * 0
    assert "https://" not in html


def test_the_print_stylesheet_sets_the_margin_and_breaks_between_sheets(html):
    assert f"@page {{ margin: {sheet.PAGE_MARGIN_MM}mm; }}" in html
    assert "break-before: page" in html and "page-break-before: always" in html
    assert ".intro { display: none; }" in html            # screen-only notes stay off paper


def test_the_page_tells_a_parent_not_to_shrink_it(html):
    """A "fit to page" tick is the one printer setting that silently ruins the geometry
    every other test here defends."""
    assert "100% scale" in html and "fit to page" in html
    assert f"{sheet.ERROR_LEVEL.upper()}" in html


def test_rendering_twice_produces_the_same_bytes():
    """Version pinned, error level un-boosted, mode fixed: one payload, one matrix. A
    sheet a parent re-prints is the sheet they printed."""
    assert sheet.render_sheet() == sheet.render_sheet()


# --------------------------------------------------------------------------- #
# 6. The dependency, and how its absence reads
# --------------------------------------------------------------------------- #
def test_the_module_imports_with_no_qr_library_at_all(monkeypatch):
    """`segno` is the `cards` extra, not a base dependency: the SDK wheel installs with
    `paho-mqtt` alone, so importing this module — and every pure function above — must
    work without it. Only drawing needs it."""
    monkeypatch.setitem(sys.modules, "segno", None)
    assert sheet.cards_for(["DRAW"]) == [("DRAW", "Drawing", "GO<launch:DRAW>")]
    assert sheet.catalog_ids() == sorted(cards.LAUNCHABLE_MODULE_IDS)
    with pytest.raises(RuntimeError) as exc:
        sheet.qr_matrix("GO<launch:DRAW>", 3)
    assert "segno" in str(exc.value) and "cards" in str(exc.value)


def test_the_wheel_declares_the_qr_library_as_an_extra_and_not_a_base_dependency():
    """Read out of `pyproject.toml`, so the promise above is checked where it is made."""
    text = open(os.path.join(REPO, "mqtt", "pyproject.toml"), encoding="utf-8").read()
    base = text.split("[project.optional-dependencies]")[0]
    assert "segno" not in base
    assert re.search(r"^cards = \[\"segno>=[\d.]+\"\]", text, re.M)


# --------------------------------------------------------------------------- #
# 7. The command a parent runs
# --------------------------------------------------------------------------- #
def test_the_cli_writes_a_sheet_and_reports_the_printed_geometry(tmp_path, capsys):
    out = tmp_path / "cards.html"
    assert sheet.main(["-o", str(out)]) == 0
    written = out.read_text(encoding="utf-8")
    assert written.startswith("<!doctype html>") and written.count("<svg") == 24
    note = capsys.readouterr().err
    assert "mm/module" in note and "EC Q" in note and "100% scale" in note


def test_the_cli_refuses_an_id_outside_the_catalog_without_a_traceback(capsys):
    """A parent gets one sentence and a non-zero exit, not a stack trace."""
    assert sheet.main(["--ids", "NOPE"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "NOPE" in err and "Traceback" not in err


def test_the_cli_can_list_what_it_would_print(capsys):
    assert sheet.main(["--list"]) == 0
    listing = capsys.readouterr().out
    for module_id in sheet.catalog_ids():
        assert cards.encode(module_id) in listing
    assert "Drawing" in listing


def test_the_cli_writes_to_stdout_by_default(capsys):
    assert sheet.main(["--ids", "DRAW"]) == 0
    assert capsys.readouterr().out.count("<svg") == 1


# --------------------------------------------------------------------------- #
# 8. A real browser, a real rasteriser — the closest thing to a scan we can run
# --------------------------------------------------------------------------- #
# Everything above this line reasons about the sheet. These two put it through the engine
# a parent will actually print with. They use the suite's shared `browser` fixture, which
# falls back to a system Chrome and skips cleanly where there is none.

def test_a_real_browser_paginates_the_sheet_onto_four_pages_of_a4_and_letter(browser):
    """The print stylesheet, checked by a paginator instead of by arithmetic.

    Every other geometry test here computes whether the cards fit. This one prints the
    page to PDF at both paper sizes and counts what came out: four sheets, not five and
    not a card orphaned onto a fifth. It is the one assertion that would notice a browser
    disagreeing with our millimetres."""
    page = browser.new_page()
    try:
        page.set_content(sheet.render_sheet())
        # The millimetres survive the stylesheet. A flex container can shrink an item, and
        # an SVG with a viewBox shrinks proportionally — so a symbol can silently come out
        # smaller than `SYMBOL_MM` while every arithmetic test above still passes.
        for selector, expected in ((".card", (sheet.CARD_W_MM, sheet.CARD_H_MM)),
                                   (".card .qr", (sheet.SYMBOL_MM, sheet.SYMBOL_MM))):
            box = page.locator(selector).first.bounding_box()
            got = (box["width"] / 96 * 25.4, box["height"] / 96 * 25.4)   # CSS px -> mm
            assert got == pytest.approx(expected, abs=0.3), (selector, got, expected)
        for paper in ("A4", "Letter"):
            pdf = page.pdf(format=paper, print_background=True)
            counts = [int(n) for n in re.findall(rb"/Count (\d+)", pdf)]
            assert counts and max(counts) == 4, (paper, counts)
    finally:
        page.close()


def test_a_printed_symbol_still_reads_back_after_a_browser_rasterises_it(browser, deck,
                                                                         version):
    """**The closest thing to a scan anyone here can perform.**

    The SVG goes through a real rasteriser at 300 dpi — the resolution a home printer
    actually lays down — and comes back as pixels. Those pixels are thresholded, the
    module centres sampled, and the recovered matrix handed to the same reader and the
    same `launch_cards.decode` as everywhere else. Antialiasing, the fractional
    millimetre-to-pixel mapping and the browser's own path filling are all inside the loop.

    What it still does not prove is **optics**: lens blur, glare, angle, motion, and
    Moxie's camera, which our corpus does not describe. No physical robot has read one of
    these cards, and this test does not change that."""
    scale = 300 / 96.0                       # CSS px are 1/96 inch; this makes them 1/300
    units = int(sheet.geometry(version)["units"])
    context = browser.new_context(device_scale_factor=scale)
    page = context.new_page()
    try:
        for module_id, _, payload in deck:
            page.set_content(f'<body style="margin:0">'
                             f'{sheet.symbol_svg(payload, version)}</body>')
            shot = page.locator("svg").screenshot(type="png")
            width, height, grey = qrm.read_png_gray(shot)
            assert width >= units * 8, (module_id, width)     # >= 8 px per module at 300dpi
            matrix = qrm.sample_matrix(width, height, grey, units, sheet.QUIET_MODULES)
            read = qrm.read_payload(matrix)
            assert read == payload, module_id
            action = cards.decode(read)
            assert action is not None and action.module_id == module_id, module_id
    finally:
        page.close()
        context.close()


def test_the_raster_reader_is_not_a_function_that_returns_the_right_answer_anyway(browser,
                                                                                  version):
    """Anti-vacuity for the test above: sample a symbol drawn for a DIFFERENT card and the
    payload must come back different, so the pipeline is reading the pixels rather than
    the string it was handed."""
    units = int(sheet.geometry(version)["units"])
    context = browser.new_context(device_scale_factor=300 / 96.0)
    page = context.new_page()
    try:
        page.set_content(f'<body style="margin:0">'
                         f'{sheet.symbol_svg(cards.encode("JOKE"), version)}</body>')
        shot = page.locator("svg").screenshot(type="png")
        width, height, grey = qrm.read_png_gray(shot)
        matrix = qrm.sample_matrix(width, height, grey, units, sheet.QUIET_MODULES)
        assert qrm.read_payload(matrix) == cards.encode("JOKE")
        assert qrm.read_payload(matrix) != cards.encode("DRAW")
    finally:
        page.close()
        context.close()
