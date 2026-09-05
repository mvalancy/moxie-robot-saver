"""
Every mutation table's anchors must resolve against the tree — a ratchet.

The mutation checkers (`sim/tools/*_mutation_check.py`) are the *"proven in both
directions"* half of this repo's testing: a green suite says the guards are **present**,
they say the guards are **load-bearing**. Each one is a table of
`(name, file, old, new, tests, selector)`, and it works by replacing `old` with `new`,
running `tests -k selector`, and requiring a failure.

Two ways that rots silently, and both have happened here:

1. **A stale anchor.** A refactor moves or reformats the code `old` matched, so the row
   becomes a NO-OP and stops proving anything. P1's refactors broke **three** of P0's
   thirty-five rows this way (`append` moving into `_append_path`, `_on_event` calling
   `_device_connect` unconditionally). The checkers print `NO-OP` for this and exit
   non-zero — but only if somebody runs them, and they take twenty minutes.
2. **A captured mutation.** A checker makes the working tree transiently wrong *by
   design*; `git add -A` cannot tell that from an edit. Staging during a run committed
   `while asked < self.lock_timeout_s or True:` — the wait-forever bug T5 exists to catch,
   which hangs the MQTT loop — into two commits on 2026-09-03.

3. **An ambiguous anchor.** `old` matches the tree in more than one place, and
   `str.replace(old, new, 1)` mutates whichever comes FIRST. The row then proves whatever
   that block happens to be — possibly the guard it names, possibly its twin, and nothing
   in the output says which. Found by measurement on 2026-09-05 in three rows across two
   tables (`ext` X1 and X10, `hardening` S4), each anchored on a line that a *deliberate*
   twin guard also carries: a load-time refusal and its runtime belt-and-braces, or
   `_connack_failed` and `_suback_failed`. All three happened to hit the intended block,
   by line order alone; a reordering refactor would have flipped them to proving nothing
   while still printing `caught`. `unit_budget_mutation_check.py` found the same defect
   in its own table first, where the wrong block WAS the one patched.

   Ambiguity also silently disables rot mode 2 above. A captured mutation is detected as
   `new in src` **only when `old` has gone**; if `old` still matches at the *other* site,
   `old in src` stays true and the mutation sits in the tree unremarked. That was exactly
   `hardening` S4's shape.

All three are the same assertion from three sides: **for every row, the tree must contain
`old` EXACTLY ONCE, and not contain `new`.** No `old` is a stale row; several is an
ambiguous one; a present `new` is a captured mutation. It runs in under a second and it is
the fast-tier check the twenty-minute checkers cannot be.

The four tables that already refused a non-unique anchor at run time (`brain`,
`launch_card`, `performance`, `subscribe`) had **zero** ambiguous rows; the five that did
not (`authoring`, `ext`, `hardening`, `hardening_p1`, `turnstile`) held all three. That is
not a coincidence about their authors — an enforced invariant is one you cannot fail to
notice you broke — and it is the argument for putting the check HERE, where it covers every
table including the ones nobody has written yet.
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
    """`[(name, file, old, new)]` for one table, **parsed, never executed**.

    `repo` is the root the row's repo-relative file constant is joined onto; it defaults
    to this checkout and exists only so the self-test below can point a synthetic table
    at a synthetic tree in `tmp_path`. A `client=`-style seam rather than a monkeypatch of
    the module global, per playbook rule 9.

    Importing a mutation checker is not a neutral act: `ext_mutation_check.py` had its
    whole run at module level, so the first draft of this guard *ran twenty-eight
    mutations against the tree from inside pytest* — ten seconds, and a live corruption of
    files other tests were reading. (It has since been given the `__main__` guard the
    other four already had; parsing rather than importing is what makes that a
    belt-and-braces rather than the only thing standing between here and a very confusing
    afternoon.)

    So the table is read with `ast`: module-level `NAME = WT / "some/path"` assignments
    give the file each row points at, and the `old`/`new` elements are string literals,
    which `ast.literal_eval` handles including implicit concatenation across lines.
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
    """A row count written in a doc must be the row count in the table.

    WHY THIS IS A TEST AND NOT A COMMENT. `functions/README.md` told operators to run the
    Turnstile checker and expect **26 rows** while the table held 28, and nothing anywhere
    noticed: this file already checked every row's structure and every anchor, but never
    how many rows there were. The number a reader checks their own run against was
    therefore not load-bearing in either direction — two rows could vanish from a table
    and no doc and no test would say so, which is precisely how a security table quietly
    shrinks.

    It is deliberately driven off the DOC rather than pinned to a constant here: a table
    is *supposed* to grow, and the only thing that must never drift is the pair.
    """
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
    """`(stale, ambiguous, captured)` for a parsed table — the whole judgement, in one place.

    Split out of the test so the SELF-TEST below can feed it tables built to be wrong. A
    guard nobody has ever seen fail is a guard nobody knows works (rule 17), and this one is
    now the only thing standing between the repo and a row that anchors on a twin.
    """
    stale, captured, ambiguous = [], [], []
    sources = {}
    for name, path, old, new in rows:
        src = sources.setdefault(path, open(path).read())
        hits = src.count(old)
        if hits > 1:
            # NOT a pass, and NOT a lesser sin than a stale anchor. `replace(old, new, 1)`
            # takes the FIRST match, so this row is about whichever block sorts earliest in
            # the file rather than the one its name claims — and the checker prints
            # `caught` either way, which is the whole problem: it reads as coverage.
            ambiguous.append(f"{name} (matches {hits}x)")
            continue
        if hits == 1:
            continue
        # The anchor is gone, so this row proves nothing as it stands. Which of the two
        # diseases it is depends on whether the REPLACEMENT is what took its place.
        #
        # (An earlier draft asked `new in src` while the anchor was still present, and
        # every checker lit up: a `new` like `pass`, or one that merely deletes lines from
        # `old`, occurs all over a file that has no mutation applied at all. A mutation can
        # only be *applied* where its anchor is *absent*, which makes this the only
        # question worth asking.)
        (captured if new in src else stale).append(name)
    return stale, ambiguous, captured


#: A miniature target file whose `a()` and `b()` are byte-identical. That is not a contrived
#: shape invented to make a test fail — it is exactly `_connack_failed`/`_suback_failed` in
#: `moxie_runtime.py`, `_var`/`lookup` in `ext.py`, and the budget/window fail-open blocks
#: in `limits.js` that turned this defect up in the first place. Twins are GOOD code here;
#: they are two gates on the same fact, written the same way on purpose. It is the *anchor*
#: that must tell them apart, and nothing but this check makes it.
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
    """Proven in BOTH directions: the audit reds on each disease and stays silent on health.

    Without this, the ambiguity assertion added on 2026-09-05 would itself be an untested
    guard — and an untested guard over untested guards is the emptiest kind of green there
    is. The negative half matters just as much: an audit that flagged a *unique* anchor
    would be a scanner that cries wolf, and this repo has one measured case of exactly that
    teaching people to wave a security check through (see `test_compose.py`'s key scan).
    """
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
