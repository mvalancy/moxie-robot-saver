"""Every mutation table's anchors must resolve against the tree — a ratchet.

The mutation checkers (`sim/tools/*_mutation_check.py`) prove guards are load-bearing:
each row `(name, file, old, new, tests, selector)` replaces `old` with `new`, runs
`tests -k selector`, and requires a failure. Three ways a row rots silently, all seen here:

1. **Stale anchor** — a refactor moves the code `old` matched, the row becomes a NO-OP.
   The checkers report it, but only when someone runs them (twenty minutes).
2. **Captured mutation** — a checker makes the tree transiently wrong by design, and a
   `git add -A` during a run commits it (a wait-forever lock bug was committed this way).
3. **Ambiguous anchor** — `old` matches more than once and `str.replace(old, new, 1)`
   mutates whichever comes FIRST, so the row proves whatever that block is, possibly the
   guard's deliberate twin, while still printing `caught`. It also hides mode 2: a
   captured `new` goes unnoticed while `old` still matches at the other site.

One assertion covers all three: **for every row the tree contains `old` EXACTLY ONCE and
does not contain `new`.** It runs in under a second in the fast tier, and it covers every
table, including ones not yet written — the tables that enforced uniqueness themselves
had no ambiguous rows; the ones that did not held all of them.
"""
from __future__ import annotations

import ast
import glob
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLES = sorted(glob.glob(os.path.join(REPO, "sim", "tools", "*mutation_check.py")))


def _rows(path, repo=None):
    """`[(name, file, old, new)]` for one table, **parsed with `ast`, never executed** —
    importing a checker is not neutral (one once ran its whole mutation run at import).

    Module-level `NAME = WT / "path"` (or `NAME = "path.py"`) assignments give each row's
    file; `old`/`new` are string literals for `ast.literal_eval`. `repo` lets the self-test
    point a synthetic table at a synthetic tree.
    """
    repo = REPO if repo is None else repo
    tree = ast.parse(open(path).read())
    paths = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if not isinstance(target, ast.Name):
            continue
        # Two shapes in the tree today, both repo-relative:
        #   `STORE = WT / "mqtt/moxie_sdk/store.py"`   (hardening, hardening_p1, ext)
        #   `B = "mqtt/moxie_sdk/brains.py"`           (brain, performance)
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


def test_there_are_mutation_tables_to_check():
    """A guard over a glob that matched nothing is the emptiest kind of green."""
    assert len(TABLES) >= 4, f"only found {TABLES}"


#: `python3 sim/tools/x_mutation_check.py        # 54 rows; every one must say "caught"`
#: — the shape the docs use to tell a reader what a clean run looks like.
_DOC_ROW_COUNT = re.compile(
    r"(?P<tool>[\w./-]*?(?P<base>\w+_mutation_check\.py))\b[^\n]*?#\s*(?P<n>\d+)\s+rows"
)


def _docs():
    for pattern in ("*.md", "*/*.md", "*/*/*.md", "*/*/*/*.md"):
        for path in glob.glob(os.path.join(REPO, pattern)):
            yield path


def test_documented_row_counts_match_the_tables():
    """A row count written in a doc must be the row count in the table — otherwise rows
    can vanish from a (security) table and neither doc nor test says so. Driven off the
    DOC, not a constant: a table is supposed to grow; only the pair must never drift."""
    counts = {os.path.basename(t): len(_rows(t)) for t in TABLES}
    checked = []
    for doc in _docs():
        text = open(doc, encoding="utf-8").read()
        for m in _DOC_ROW_COUNT.finditer(text):
            base, claimed = m.group("base"), int(m.group("n"))
            if base not in counts:
                continue
            rel = os.path.relpath(doc, REPO)
            assert claimed == counts[base], (
                f"{rel} says {base} has {claimed} rows; it has {counts[base]}. "
                f"Update the doc (or the table) so an operator can tell a table that GREW "
                f"from a selector that silently stopped matching."
            )
            checked.append((rel, base, claimed))
    # A regex that matched nothing would make this test the emptiest kind of green — the
    # exact failure mode its own subject is about.
    assert checked, (
        "no doc states a mutation-table row count in the documented "
        '`<tool>  # N rows` form — this guard is checking nothing'
    )


def _audit(rows):
    """`(stale, ambiguous, captured)` for a parsed table — the whole judgement, split out
    so the self-test below can feed it tables built to be wrong (a guard nobody has seen
    fail is a guard nobody knows works)."""
    stale, captured, ambiguous = [], [], []
    sources = {}
    for name, path, old, new in rows:
        src = sources.setdefault(path, open(path).read())
        hits = src.count(old)
        if hits > 1:
            # `replace(old, new, 1)` takes the FIRST match, so the row proves whichever
            # block sorts earliest — and still prints `caught`, reading as coverage.
            ambiguous.append(f"{name} (matches {hits}x)")
            continue
        if hits == 1:
            continue
        # The anchor is gone: captured if the REPLACEMENT took its place, else stale. Only
        # ask `new in src` once `old` is absent — a `new` like `pass` occurs all over an
        # unmutated file.
        (captured if new in src else stale).append(name)
    return stale, ambiguous, captured


#: A miniature target whose `a()` and `b()` are byte-identical — the real shape of
#: `_connack_failed`/`_suback_failed` and similar deliberate twin guards. Twins are good
#: code; it is the *anchor* that must tell them apart.
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

#: One row per disease, parsed by the real `_rows` and judged by the real `_audit`.
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

#: The same file, anchored on the one line only `c()` carries. Nothing to report.
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
    """Proven in BOTH directions: the audit reds on each disease and stays silent on
    health — a scanner that flagged a *unique* anchor would cry wolf and get waved
    through."""
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
    rows = _rows(table)
    assert rows, f"{table} has no MUTATIONS table"
    stale, ambiguous, captured = _audit(rows)
    assert not stale, (
        f"{os.path.basename(table)}: {len(stale)} row(s) no longer match the tree, so they "
        f"prove nothing — repair the anchor, do not delete the row: {stale}")
    assert not ambiguous, (
        f"{os.path.basename(table)}: {len(ambiguous)} row(s) anchor on a snippet that "
        f"appears more than once, so they mutate whichever copy comes first and prove "
        f"whatever that block happens to be — make the anchor unique (widen it to a "
        f"neighbouring line the twin does not carry), do not delete the row: {ambiguous}")
    assert not captured, (
        f"{os.path.basename(table)}: the tree contains a MUTATION. Either a checker is "
        f"running right now (wait for it), or one was committed by a `git add` during a "
        f"run: {captured}")
