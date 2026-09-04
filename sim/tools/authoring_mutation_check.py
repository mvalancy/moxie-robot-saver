"""Remove each guard the ✍️ content editor rests on, and check its test goes red.

"A test for every feature, proven in BOTH directions": a green
`sim/tests/test_content_authoring.py` proves the guards are *present*, and this proves
they are *load-bearing*. Run it by hand after touching the authoring region of
`mqtt/supervisor/moxie_runtime.py`, `packs.shadow_check` or `render.render_prompt`:

    python3 sim/tools/authoring_mutation_check.py

Every row must say "caught". A row that says NOT CAUGHT means the assertion passes with
the guard deleted, which means it is not testing what its name claims.

**The row this file exists for is the first one.** `docs/architecture/backlog/
content-authoring.md` §6.3 names one `if` as the single most important line in the slice:
`packs.mark_edited` calls `normalize_data` and **not** `validate_item` (`apply_pack` does
that itself), so `POST /content/item` has to call it. Without that call an authored global
with a non-compiling `pattern` reaches `Global.from_dict`, which compiles at **load**, and
a throw inside the loader takes down `reload_content()` for every item at once. Deleting
the call must redden `test_a_bad_pattern_is_refused_with_validate_items_own_sentence`.

**One guard the brief's M1 lists is deliberately absent: the try budget.** It belongs to
`POST /content/try`, which is P1 (§9's "not in P0" list) — P0 makes no brain call at all,
so there is nothing here to budget. `config.AUTHOR_TRY_BUDGET` is declared and read by
nobody. A row with an anchor that does not exist would be a NO-OP that reads as coverage,
and `sim/tests/test_mutation_tables.py` fails a table whose anchors do not resolve — so
the row is named here in prose and added the day the route lands.

Nothing here writes to the tree permanently — each mutation is reverted in a `finally`.
`PYTHONDONTWRITEBYTECODE` is not a nicety: without it a `__pycache__` entry from an
earlier mutation can shadow a later one, and a guard reads as un-caught when it is fine.
"""
import pathlib, subprocess

WT = pathlib.Path(__file__).resolve().parents[2]
RT = WT / "mqtt/supervisor/moxie_runtime.py"
PK = WT / "mqtt/moxie_sdk/content/packs.py"
REN = WT / "mqtt/moxie_sdk/content/render.py"

MUTATIONS = [
 # ---- §6.3: the one `if` --------------------------------------------------------
 ("A1  drop the `validate_item` call from the writing route", RT,
  "        reasons = content_packs.validate_item(",
  "        reasons = [] and content_packs.validate_item(",
  "a_bad_pattern_is_refused"),
 ("A1  accept an item `validate_item` refused", RT,
  "        if reasons:\n            return {\"ok\": False, \"error\": reasons[0], \"reason\": reasons[0],",
  "        if False:\n            return {\"ok\": False, \"error\": reasons[0], \"reason\": reasons[0],",
  "a_bad_pattern_is_refused"),

 # ---- §0/§4.5: the kind refusal --------------------------------------------------
 ("A2  let the editor author a schedule", RT,
  '        if kind == "schedule":',
  '        if False:',
  "schedule_is_refused"),
 # The kind check below `if kind == "schedule"` is a second, redundant fence, so widening
 # `AUTHORABLE_KINDS` alone changes nothing and would be a row that only looks like
 # coverage. What IS worth pinning is that a schedule gets the *named* refusal — the one
 # that says why — rather than the generic "unknown kind" a mis-cased compare would fall
 # through to, because "no" without a reason is what sends a parent to the issue tracker.
 ("A2  let a schedule fall through to the generic kind refusal", RT,
  '        if kind == "schedule":',
  '        if kind == "SCHEDULE":',
  "schedule_is_refused"),

 # ---- §4.5: `code` and `extension` are shown, never written ----------------------
 ("A3  drop the unwritable-field refusal", RT,
  "        refusal = self._refuse_unwritable_fields(kind, data, before)\n        if refusal:",
  "        refusal = self._refuse_unwritable_fields(kind, data, before)\n        if False:",
  "extension_and_code_are_not_writable"),
 ("A3  let an extension be rewritten", RT,
  "        if content_packs.canonical(data.get(\"extension\") or {}) \\\n                != content_packs.canonical(base.get(\"extension\") or {}):",
  "        if False:",
  "extension_and_code_are_not_writable"),
 ("A3  let a `code` block be rewritten", RT,
  '        if str(data.get("code") or "") != str(base.get("code") or ""):',
  "        if False:",
  "extension_and_code_are_not_writable"),

 # ---- §6.5: a write path that skips the live swap --------------------------------
 ("A4  save the overlay and never reload the live module", RT,
  "            merged = content_packs.mark_edited(overlay, ident, data)\n"
  "            if not self._write_content_overlay(merged):\n"
  "                return {\"ok\": False, \"error\": \"could not write the content overlay\",\n"
  "                        \"reason\": \"The appliance could not save this item.\"}\n"
  "            reload = self.reload_content()",
  "            merged = content_packs.mark_edited(overlay, ident, data)\n"
  "            if not self._write_content_overlay(merged):\n"
  "                return {\"ok\": False, \"error\": \"could not write the content overlay\",\n"
  "                        \"reason\": \"The appliance could not save this item.\"}\n"
  "            reload = {}",
  "authored_item_round_trips"),

 # ---- §6.4: the same one-slot undo an import takes -------------------------------
 ("A5  save without snapshotting what it replaced", RT,
  "            self.store.write_shared(self.CONTENT_BACKUP_COLLECTION, {\n"
  "                \"items\": overlay, \"packs\": self._content_packs(),\n"
  "                \"label\": f\"before editing {data.get('name') or key}\",\n"
  "                \"at\": int(time.time())})",
  "            pass",
  "undo_restores_an_authored_save or the_undo_slot_holds_one_save"),

 # ---- R7: two tabs are detected, never merged ------------------------------------
 ("A6  drop the `local_rev` conflict check", RT,
  "        if expected and before is not None \\\n                and expected != content_packs.local_rev({\"kind\": kind, **before}):",
  "        if False:",
  "a_second_tab_cannot_silently_discard"),

 # ---- G1/A1: the allowlist, and the one supported way to change an item -----------
 # `normalize_data` runs TWICE on this path — once in the route and once inside
 # `mark_edited` — so deleting either one alone is unobservable, which is a good property
 # and a bad mutation. The guard actually worth pinning is that the write goes through
 # `mark_edited` at all (assumption A1: it is the only supported way to change an
 # installed item's content). A route that assembled the store entry by hand is the real
 # regression, and it takes the allowlist with it.
 ("A7  write the store entry by hand instead of through `mark_edited`", RT,
  "            merged = content_packs.mark_edited(overlay, ident, data)",
  "            merged = dict(overlay)\n"
  "            merged[ident] = {\"kind\": kind, \"key\": key,\n"
  "                             \"data\": dict(body.get(\"data\") or {}),\n"
  "                             \"provenance\": {\"kind\": kind, \"origin\": \"local\",\n"
  "                                             \"source_version\": 1}}",
  "a_field_outside_the_allowlist_never_lands"),

 # ---- §4.4: the shadow rule, and its honest bound --------------------------------
 ("A8  report a LATER-sorting command as a shadow too", PK,
  '        if not full.startswith("global:") or full >= mine:',
  '        if not full.startswith("global:"):',
  "shadow_check_never_reports_the_item_against_itself"),
 ("A9  warn about every installed command, not the phrases typed", PK,
  "            if rx.search(phrase):",
  "            if True:",
  "no_shadow_warning_when_nothing_shadows"),

 # ---- §4.3/R2: the portability probe rung 1 reports -------------------------------
 ("A10 report the real render's counts instead of the portable render's", RT,
  '        portable = render._minimal_render(data.get("prompt") or "", context,\n'
  "                                          counts=portable_counts)",
  '        portable = render.render_prompt(data.get("prompt") or "", context,\n'
  "                                        counts=portable_counts)",
  "render_reports_stripped"),
 ("A11 stop counting what the dependency-free renderer removed", REN,
  "        counts[\"stripped\"] = counts.get(\"stripped\", 0) + STRIPPED - before[1]",
  "        counts[\"stripped\"] = counts.get(\"stripped\", 0)",
  "render_reports_stripped or render_prompt_hands_a_caller"),
]

TESTS = "sim/tests/test_content_authoring.py"


# The runner lives in `main()` behind a `__main__` guard, like the other five checkers:
# importing a mutation table must never run it (see sim/tests/test_mutation_tables.py,
# which parses these files with `ast` rather than importing them for exactly that reason).
def main() -> int:
    caught = missed = noop = 0
    for name, path, old, new, sel in MUTATIONS:
        src = path.read_text()
        if old not in src:
            print(f"  NO-OP       {name}  (anchor not found)"); noop += 1; continue
        backup = src
        path.write_text(src.replace(old, new, 1))
        try:
            r = subprocess.run([str(WT / ".venv/bin/python"), "-m", "pytest", TESTS,
                                "-q", "-k", sel, "-p", "no:cacheprovider"],
                               cwd=WT, capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "MOXIE_LLM_API_KEY": "",
                                    "HOME": "/home/scubasonar", "PYTHONDONTWRITEBYTECODE": "1"})
            # A selector that matched NOTHING exits 5 ("no tests ran"), which is not a
            # red test — it is a row pointing at a test that does not exist, and reading
            # it as "caught" is exactly the formality this file is written against.
            if r.returncode == 5:
                print(f"  NO TEST     {name}  (-k {sel!r} matched nothing)"); noop += 1
            elif r.returncode == 0:
                print(f"  NOT CAUGHT  {name}"); missed += 1
            else:
                print(f"  caught      {name}"); caught += 1
        finally:
            path.write_text(backup)
    print(f"\nMUTATIONS: {caught} caught, {missed} missed, {noop} no-op")
    return 1 if (missed or noop) else 0


if __name__ == "__main__":
    raise SystemExit(main())
