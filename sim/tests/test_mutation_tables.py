"""Every mutation table's anchors must resolve against the tree — a ratchet.

Each row of `sim/tools/*_mutation_check.py` replaces `old` with `new` and requires a test to
go red. A row rots three ways, all seen here: a refactor moves `old` (the row is a silent
NO-OP until someone runs the 20-minute checker); a `git add -A` during a run commits the
mutation; or `old` matches twice and `replace(old, new, 1)` mutates whichever copy comes
first (possibly the guard's deliberate twin). One sub-second assertion covers all three:
the tree contains `old` EXACTLY ONCE and does not contain `new`.
"""
from __future__ import annotations

import ast
import glob
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLES = sorted(glob.glob(os.path.join(REPO, "sim", "tools", "*mutation_check.py")))


def _rows(path, repo=None):
    """`[(name, file, old, new)]` for one table, parsed with `ast`, never imported (one
    checker once ran its whole mutation run at import). Row files are module-level
    `NAME = WT / "path"` or `NAME = "path.py"`; `repo` lets the self-test use a fake tree."""
    repo = REPO if repo is None else repo
    tree = ast.parse(open(path).read())
    paths = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if not isinstance(target, ast.Name):
            continue
        if (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div)
                and isinstance(value.left, ast.Name) and value.left.id in ("WT", "ROOT")
                and isinstance(value.right, ast.Constant)):
            paths[target.id] = os.path.join(repo, value.right.value)
        elif (isinstance(value, ast.Constant) and isinstance(value.value, str)
                and value.value.endswith(".py") and "/" in value.value):
            paths[target.id] = os.path.join(repo, value.value)
        elif target.id == "MUTATIONS" and isinstance(value, ast.List):
            table = value
    out = []
    for element in table.elts:
        assert isinstance(element, ast.Tuple), ast.dump(element)
        name, where, old, new = element.elts[0], element.elts[1], element.elts[2], element.elts[3]
        assert isinstance(where, ast.Name), f"row {ast.literal_eval(name)} has no named file"
        out.append((ast.literal_eval(name), paths[where.id],
                    ast.literal_eval(old), ast.literal_eval(new)))
    return out


def _audit(rows):
    """`(stale, ambiguous, captured)` for a parsed table."""
    stale, captured, ambiguous = [], [], []
    sources = {}
    for name, path, old, new in rows:
        src = sources.setdefault(path, open(path).read())
        hits = src.count(old)
        if hits > 1:
            ambiguous.append(f"{name} (matches {hits}x)")
            continue
        if hits == 1:
            continue
        # gone: captured if the replacement took its place (only asked once `old` is
        # absent — a `new` like `pass` occurs all over an unmutated file), else stale
        (captured if new in src else stale).append(name)
    return stale, ambiguous, captured


#: A target whose `a()` and `b()` are deliberate twins (cf. `_connack_failed` /
#: `_suback_failed`): it is the anchor that must tell them apart.
_TWIN_SRC = (
    "def a(rc):\n"
    "    failed = rc.bad\n"
    "    return bool(failed)\n"
    "\n"
    "def b(rc):\n"
    "    failed = rc.bad\n"
    "    return bool(failed)\n"
    "\n"
    "def c(rc):\n"
    "    return DELETED\n"
)

_SICK_TABLE = (
    "import pathlib\n"
    "WT = pathlib.Path(__file__).resolve().parents[1]\n"
    'T = WT / "twin.py"\n'
    "MUTATIONS = [\n"
    '    ("AMB the anchor a twin also carries", T,\n'
    '     "    failed = rc.bad", "    failed = None"),\n'
    '    ("STALE an anchor nobody wrote", T, "    zzz = 1", "    zzz = 2"),\n'
    '    ("CAPTURED its mutation is sitting in the tree", T,\n'
    '     "    return NOTHING", "    return DELETED"),\n'
    "]\n"
)

_WELL_TABLE = (
    "import pathlib\n"
    "WT = pathlib.Path(__file__).resolve().parents[1]\n"
    'T = WT / "twin.py"\n'
    "MUTATIONS = [\n"
    '    ("OK an anchor that occurs exactly once", T,\n'
    '     "def c(rc):\\n    return DELETED", "def c(rc):\\n    return None"),\n'
    "]\n"
)


def test_the_ambiguity_check_actually_fails_on_an_ambiguous_anchor(tmp_path):
    """Both directions: each disease is reported, a healthy anchor is not."""
    (tmp_path / "twin.py").write_text(_TWIN_SRC)
    tools = tmp_path / "tools"
    tools.mkdir()

    sick = tools / "sick_mutation_check.py"
    sick.write_text(_SICK_TABLE)
    stale, ambiguous, captured = _audit(_rows(str(sick), repo=str(tmp_path)))
    assert len(ambiguous) == 1 and "matches 2x" in ambiguous[0], ambiguous
    assert stale == ["STALE an anchor nobody wrote"], stale
    assert captured == ["CAPTURED its mutation is sitting in the tree"], captured

    well = tools / "well_mutation_check.py"
    well.write_text(_WELL_TABLE)
    assert _audit(_rows(str(well), repo=str(tmp_path))) == ([], [], []), _audit(_rows(str(well), repo=str(tmp_path)))


@pytest.mark.parametrize("table", TABLES, ids=lambda p: os.path.basename(p))
def test_every_anchor_resolves_and_no_mutation_is_committed(table):
    assert len(TABLES) >= 10
    rows = _rows(table)
    assert rows, f"{table} has no MUTATIONS table"
    stale, ambiguous, captured = _audit(rows)
    assert not stale, f"anchors gone — repair them, do not delete the rows: {stale}"
    assert not ambiguous, f"anchors match >1 place — widen them to a unique line: {ambiguous}"
    assert not captured, f"a MUTATION is in the tree (a checker running, or committed): {captured}"
