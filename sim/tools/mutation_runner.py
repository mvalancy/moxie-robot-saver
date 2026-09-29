"""The shared runner behind every `sim/tools/*_mutation_check.py` table.

A table is a list of rows `(name, file, old, new, *args)`: replace `old` (which must occur
EXACTLY once — an ambiguous anchor mutates whichever copy comes first and proves nothing)
with `new`, run the row's command, and require it to go red. Verdicts:

  caught       the command failed (or hung past the timeout, said so)
  NOT CAUGHT   the command stayed green with the guard broken — a hole in the tests
  NO-OP        the anchor is gone or ambiguous; the row proves nothing until repaired
  NO TEST      a pytest `-k` selector matched nothing (exit 5) — the row runs no test
  WRONG CHECK  (node tables) red, but no failing line names the row's selector

Before any row, the unmutated baseline must be GREEN: a suite already red would read every
row as caught. `sim/tests/test_mutation_tables.py` parses the tables with `ast` (never
imports them) to keep every anchor resolving in the fast tier.

Python tables mutate the checkout in place and restore in a `finally`; node tables pass
`scratch=` and run in a throwaway `cp -al` copy, so a killed run leaves nothing behind.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

WT = pathlib.Path(__file__).resolve().parents[2]
_VENV = WT / ".venv/bin/python"
PY = str(_VENV if _VENV.exists() else pathlib.Path(sys.executable))

#: Credentials blanked explicitly (a bare run would find a developer's `mqtt/.env` and
#: spend real gateway calls); no bytecode, so an earlier mutation's `__pycache__` cannot
#: shadow a later one.
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
       "HOME": os.environ.get("HOME", "/tmp"),
       "MOXIE_LLM_API_KEY": "", "MOXIE_LLM_BASE_URL": "",
       "MOXIE_VOICE_BASE_URL": "", "MOXIE_STT_BASE_URL": "",
       "MOXIE_SKIP_DOTENV": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def pytest(tests, selector=None, *extra):
    """A pytest command line for `tests` (a path or a list), optionally `-k selector`."""
    tests = [tests] if isinstance(tests, (str, pathlib.Path)) else list(tests)
    cmd = [PY, "-m", "pytest", *map(str, tests), "-q", "--no-header", "-p", "no:cacheprovider"]
    return cmd + (["-k", selector] if selector else []) + list(extra)


def pytest_verdict(_row, proc):
    summary = [ln for ln in proc.stdout.splitlines() if " in " in ln and ("passed" in ln
               or "failed" in ln or "error" in ln or "deselected" in ln)]
    tail = summary[-1].strip("= ") if summary else ""
    if proc.returncode == 5:
        return "NO TEST", tail
    return ("NOT CAUGHT" if proc.returncode == 0 else "caught"), tail


#: The three failure-line formats the node suites print: `FAIL: <label>`
#: (test_turnstile.mjs), `  - <label>` (finish()), `   · <label>` (browser_harness.mjs).
def _failing_lines(out):
    return [ln for ln in out.splitlines()
            if "FAIL:" in ln or ln.startswith("  - ") or ln.startswith("   · ")]


def node_verdict(row, proc):
    """Caught only when a FAILING line names the row's selector (its last column): a
    mutation that reddened some unrelated check has not proven this row's guard."""
    if proc.returncode == 0:
        return "NOT CAUGHT", ""
    failing, selector = _failing_lines(proc.stdout + proc.stderr), row[-1]
    named = [ln for ln in failing if selector in ln]
    if named:
        return "caught", f"{len(failing)} red, {len(named)} naming {selector!r}"
    first = f"; first red: {failing[0].strip()[:100]}" if failing else ""
    return "WRONG CHECK", f"{len(failing)} red, none naming {selector!r}{first}"


def _scratch_tree(trees, files, root_files=()):
    """Hardlink-copy `trees` (~0.2 s), then replace every file the table mutates with a
    REAL copy — a write through a shared inode would truncate the checkout."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="mutation-"))
    for tree in trees:
        subprocess.run(["cp", "-al", str(WT / tree), str(root / tree)], check=True)
    for name in root_files:
        shutil.copyfile(WT / name, root / name)
    for real in files:
        target = root / real.relative_to(WT)
        data = real.read_bytes()
        target.unlink()
        target.write_bytes(data)
        assert target.stat().st_nlink == 1, f"{real} is still hardlinked to the checkout"
    return root


def run_table(rows, command, *, verdict=pytest_verdict, baseline=(), timeout=None,
              scratch=None, root_files=(), argv=None) -> int:
    """Run `rows` (or those whose name starts with an `argv` entry); 0 iff every row is
    caught. `command(row)` gives the argv to run; `baseline` lists commands that must be
    green unmutated; `scratch` names the subtrees to copy for an out-of-tree run."""
    argv = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("-")]
    if argv:
        rows = [r for r in rows if any(r[0].split()[0] == a or r[0].startswith(a) for a in argv)]
        if not rows:
            print(f"no row matches {argv}")
            return 1
    files = sorted({WT / r[1] for r in rows})
    root = _scratch_tree(scratch, files, root_files) if scratch else WT

    def execute(cmd):
        return subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                              env=ENV, timeout=timeout)

    counts = {}
    try:
        for cmd in baseline:
            proc = execute(cmd)
            if proc.returncode != 0:
                print(f"BASELINE RED — fix before mutating: {' '.join(map(str, cmd[-4:]))}\n"
                      + (proc.stdout + proc.stderr)[-1500:])
                return 1
        for row in rows:
            name, real, old, new = row[:4]
            path = root / (WT / real).relative_to(WT)
            src = path.read_text()
            hits = src.count(old)
            if hits != 1:
                status, detail = "NO-OP", ("anchor not found" if not hits
                                           else f"anchor matches {hits} places")
            else:
                path.write_text(src.replace(old, new, 1))
                try:
                    status, detail = verdict(row, execute(command(row)))
                except subprocess.TimeoutExpired:
                    status, detail = "caught", f"hung — killed after {timeout}s"
                finally:
                    path.write_text(src)
            counts[status] = counts.get(status, 0) + 1
            print(f"  {status:<11} {name}" + (f"  ({detail})" if detail else ""), flush=True)
    finally:
        if scratch:
            shutil.rmtree(root, ignore_errors=True)
    print(f"\nMUTATIONS: {counts.get('caught', 0)}/{len(rows)} caught  "
          + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()) if k != "caught"))
    return 0 if counts.get("caught", 0) == len(rows) else 1
