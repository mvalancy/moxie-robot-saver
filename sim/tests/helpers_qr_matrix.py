"""
🔍 Read a payload back **out of a QR module matrix** — the half of a printed card no
encoder test can see.

Why this exists. Every other assertion about the sheet checks a string we already had:
`launch_sheet` asks `launch_cards.encode` for `GO<launch:DRAW>` and we check that
`GO<launch:DRAW>` appears under the code. That proves the *caption*, not the *code*. The
question a parent actually has is whether the black squares carry the card, and answering
it means walking the modules the way a scanner does.

So this is a **decoder, not a second encoder**. It takes the finished matrix — from
`segno` on the Python side, or from the browser's `vendor/qrcode.js` handed across by
`sim/test_qr.mjs` — reads the format information, un-applies the mask, walks the standard
zig-zag, de-interleaves the blocks and parses the byte-mode segment back to a string. That
string then goes to the **real `launch_cards.decode`**, which is the only authority on
what a card may do. What is left unproven after that is optics and nothing else.

What it deliberately does NOT do
--------------------------------
**No Reed-Solomon.** A matrix straight out of an encoder has no errors, so error
correction would only hide a bug: if the data codewords are wrong, this must say so
rather than quietly repair them. That also keeps the file short enough to audit.

It handles error level **Q**, versions **1-6**, **byte mode**, which is exactly what
`moxie_sdk.launch_sheet` emits (`ERROR_LEVEL`, `ENCODE_MODE`, and a `deck_version` that
is 3 for every id in today's catalog). Anything else raises by name — an unsupported
symbol is a red test, never a silent skip.

Reference: ISO/IEC 18004 (mask patterns §7.8.2, format information §7.9, symbol character
placement §7.7.3, block interleaving §7.6). The tables below are the standard's, not
anyone's code.
"""
from __future__ import annotations

from typing import Callable, List, Sequence, Tuple

#: ISO/IEC 18004 §7.8.2 — the eight data-mask patterns. `True` at (row, col) means that
#: module was inverted when the symbol was made, so XOR-ing the same condition undoes it.
MASKS: Tuple[Callable[[int, int], bool], ...] = (
    lambda i, j: (i + j) % 2 == 0,
    lambda i, j: i % 2 == 0,
    lambda i, j: j % 3 == 0,
    lambda i, j: (i + j) % 3 == 0,
    lambda i, j: (i // 2 + j // 3) % 2 == 0,
    lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0,
)

#: Alignment-pattern centre coordinates per version (ISO/IEC 18004 Annex E), versions 1-6.
ALIGNMENT_CENTRES = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34)}

#: Error-correction **level Q** block structure, versions 1-6: the data-codeword count of
#: each block, in the order the standard interleaves them (shorter group first). Only the
#: data counts are needed — this decoder never touches the EC codewords.
Q_BLOCK_DATA = {
    1: (13,),
    2: (22,),
    3: (17, 17),
    4: (24, 24),
    5: (15, 15, 16, 16),
    6: (19, 19, 19, 19),
}

#: The two format-information bit patterns encode the level in two bits; Q is 0b11.
EC_LEVEL_BITS = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}

MODE_TERMINATOR = 0b0000
MODE_BYTE = 0b0100


class QRReadError(ValueError):
    """The matrix is not a symbol this reader can read, and says which part failed."""


def _size(matrix: Sequence[Sequence[int]]) -> int:
    n = len(matrix)
    if n < 21 or (n - 17) % 4 or any(len(r) != n for r in matrix):
        raise QRReadError(f"not a square QR matrix of a legal size: {n}x{n}")
    return n


def version_of(matrix: Sequence[Sequence[int]]) -> int:
    """Symbol version implied by the matrix size (21x21 is version 1, +4 per version)."""
    return (_size(matrix) - 17) // 4


def function_modules(version: int) -> List[List[bool]]:
    """The modules that carry structure rather than data, so the walk can skip them.

    Finders and their separators, both timing lines, the alignment patterns, both copies
    of the format information and the always-dark module. Version information (versions
    >= 7) is not marked because this reader stops at 6 and says so.
    """
    n = version * 4 + 17
    fn = [[False] * n for _ in range(n)]

    def mark(r0: int, r1: int, c0: int, c1: int) -> None:
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if 0 <= r < n and 0 <= c < n:
                    fn[r][c] = True

    mark(0, 7, 0, 7)                      # top-left finder + separator
    mark(0, 7, n - 8, n - 1)              # top-right
    mark(n - 8, n - 1, 0, 7)              # bottom-left
    mark(6, 6, 0, n - 1)                  # horizontal timing
    mark(0, n - 1, 6, 6)                  # vertical timing
    centres = ALIGNMENT_CENTRES[version]
    last = centres[-1] if centres else 0
    for a in centres:
        for b in centres:
            if (a, b) in ((6, 6), (6, last), (last, 6)):
                continue                  # these three sit under the finders
            mark(a - 2, a + 2, b - 2, b + 2)
    mark(8, 8, 0, 8)                      # format info, copy 1
    mark(0, 8, 8, 8)
    mark(8, 8, n - 8, n - 1)              # format info, copy 2 (+ the dark module)
    mark(n - 8, n - 1, 8, 8)
    return fn


def _bch_format(bits5: int) -> int:
    """The 15-bit format information for a 5-bit (level, mask) value — ISO/IEC 18004 §7.9.

    Recomputed rather than looked up so that reading the format back is a real check: a
    matrix whose format modules are not a valid BCH codeword fails instead of yielding a
    plausible-looking mask.
    """
    value = bits5 << 10
    while value.bit_length() - 1 >= 10:
        value ^= 0x537 << (value.bit_length() - 1 - 10)
    return ((bits5 << 10) | value) ^ 0x5412


def format_info(matrix: Sequence[Sequence[int]]) -> Tuple[str, int]:
    """`(error level, mask index)` read from the symbol's own format modules."""
    n = _size(matrix)
    read = [matrix[8][c] for c in (0, 1, 2, 3, 4, 5, 7, 8)]
    read += [matrix[7][8]] + [matrix[r][8] for r in (5, 4, 3, 2, 1, 0)]
    raw = 0
    for bit in read:
        raw = (raw << 1) | (1 if bit else 0)
    for candidate in range(32):
        if _bch_format(candidate) == raw:
            level = EC_LEVEL_BITS[(candidate >> 3) & 0b11]
            return level, candidate & 0b111
    raise QRReadError(f"format information is not a valid BCH codeword ({raw:#07x}) "
                      f"in a {n}x{n} symbol")


def codewords(matrix: Sequence[Sequence[int]]) -> List[int]:
    """Every codeword in placement order, un-masked — ISO/IEC 18004 §7.7.3.

    Two columns at a time from the bottom-right, alternating upward and downward, skipping
    the vertical timing column and every function module.
    """
    n = _size(matrix)
    version = version_of(matrix)
    fn = function_modules(version)
    _, mask = format_info(matrix)
    inverted = MASKS[mask]

    bits: List[int] = []
    col = n - 1
    upward = True
    while col > 0:
        if col == 6:                       # the vertical timing line is not a data column
            col -= 1
        for i in range(n):
            row = (n - 1 - i) if upward else i
            for c in (col, col - 1):
                if not fn[row][c]:
                    bits.append((1 if matrix[row][c] else 0)
                                ^ (1 if inverted(row, c) else 0))
        upward = not upward
        col -= 2

    out: List[int] = []
    for i in range(0, len(bits) - 7, 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        out.append(value)
    return out


def data_codewords(matrix: Sequence[Sequence[int]]) -> List[int]:
    """The data codewords, de-interleaved back into block order (level Q, versions 1-6)."""
    version = version_of(matrix)
    level, _ = format_info(matrix)
    if level != "Q":
        raise QRReadError(f"this reader handles error level Q only, symbol says {level}")
    if version not in Q_BLOCK_DATA:
        raise QRReadError(
            f"this reader handles versions {min(Q_BLOCK_DATA)}-{max(Q_BLOCK_DATA)}, "
            f"symbol is version {version}; add its row to Q_BLOCK_DATA to extend it")
    counts = Q_BLOCK_DATA[version]
    flat = codewords(matrix)
    total = sum(counts)
    if len(flat) < total:
        raise QRReadError(f"symbol carries {len(flat)} codewords, needs {total}")
    blocks: List[List[int]] = [[] for _ in counts]
    index = 0
    for i in range(max(counts)):
        for b, count in enumerate(counts):
            if i < count:
                blocks[b].append(flat[index])
                index += 1
    return [cw for block in blocks for cw in block]


def read_payload(matrix: Sequence[Sequence[int]]) -> str:
    """The string a scanner would hand our runtime, read out of the modules themselves.

    Byte mode only, which is what `launch_sheet.ENCODE_MODE` pins. A different mode
    indicator is raised by name rather than skipped, because the only way one can appear
    is a change to how the sheet is generated.
    """
    data = data_codewords(matrix)
    bits: List[int] = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)

    def take(count: int) -> int:
        nonlocal cursor
        if cursor + count > len(bits):
            raise QRReadError("ran off the end of the data codewords")
        value = 0
        for bit in bits[cursor:cursor + count]:
            value = (value << 1) | bit
        cursor += count
        return value

    cursor = 0
    version = version_of(matrix)
    out = bytearray()
    while cursor + 4 <= len(bits):
        mode = take(4)
        if mode == MODE_TERMINATOR:
            break
        if mode != MODE_BYTE:
            raise QRReadError(f"unexpected mode indicator {mode:#06b}; "
                              "this reader handles byte mode only")
        length_bits = 8 if version <= 9 else 16
        length = take(length_bits)
        for _ in range(length):
            out.append(take(8))
    return out.decode("utf-8")


# --------------------------------------------------------------------------- #
# Reading a symbol back off a RASTER, which is what a printer and a camera make
# --------------------------------------------------------------------------- #
# Everything above works on the matrix an encoder produced. That still trusts the step in
# between: the SVG the sheet writes, rasterised by a real engine at a real print
# resolution. `read_png_gray` + `sample_matrix` close that gap — screenshot a symbol with
# a browser at 300 dpi, threshold it, sample the module centres, and hand the result to
# `read_payload`. It is the nearest thing to a scan anyone here can perform, and what it
# still cannot see is optics: lens blur, glare, angle, motion, and Moxie's own camera,
# which our corpus does not describe.

def read_png_gray(data: bytes):
    """`(width, height, grey_bytes)` from an 8-bit non-interlaced RGB/RGBA PNG.

    Written out rather than pulled in: the suite has no imaging library and does not need
    one for this, and adding a required dependency to read one screenshot would cost more
    than the forty lines below. Only the shapes a browser screenshot actually produces are
    supported; anything else raises rather than guessing.
    """
    import struct
    import zlib

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise QRReadError("not a PNG")
    pos, idat, ihdr = 8, [], None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if ihdr is None:
        raise QRReadError("PNG has no IHDR")
    width, height, depth, colour, _comp, _filt, interlace = ihdr
    if depth != 8 or interlace != 0 or colour not in (2, 6):
        raise QRReadError(f"unsupported PNG: depth={depth} colour={colour} "
                          f"interlace={interlace}")
    channels = 3 if colour == 2 else 4
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    grey = bytearray(width * height)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        for i in range(stride):                              # PNG filters, RFC 2083 §6
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif kind == 4:
                estimate = left + up - up_left
                da, db, dc = (abs(estimate - left), abs(estimate - up),
                              abs(estimate - up_left))
                nearest = left if (da <= db and da <= dc) else (up if db <= dc else up_left)
                line[i] = (line[i] + nearest) & 0xFF
            elif kind != 0:
                raise QRReadError(f"unknown PNG filter {kind} on row {y}")
        for x in range(width):
            r, g, b = line[x * channels], line[x * channels + 1], line[x * channels + 2]
            grey[y * width + x] = (r * 299 + g * 587 + b * 114) // 1000
        previous = line
    return width, height, grey


def sample_matrix(width: int, height: int, grey: Sequence[int], units: int,
                  quiet: int, threshold: int = 128) -> List[List[int]]:
    """A module matrix sampled from an image that is exactly `units` modules across.

    Centres, not edges: a rasteriser antialiases module boundaries, and the middle of a
    module is the one pixel that is unambiguous at any sane resolution. The quiet zone is
    dropped again here, because `read_payload` works on the symbol proper.
    """
    if units <= 2 * quiet:
        raise QRReadError(f"{units} units is not a symbol with a {quiet}-module quiet zone")
    step_x, step_y = width / units, height / units
    rows: List[List[int]] = []
    for r in range(quiet, units - quiet):
        y = int((r + 0.5) * step_y)
        rows.append([1 if grey[y * width + int((c + 0.5) * step_x)] < threshold else 0
                     for c in range(quiet, units - quiet)])
    return rows
