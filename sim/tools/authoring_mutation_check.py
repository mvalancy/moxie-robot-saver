"""Break each guard the content editor rests on; `test_content_authoring.py` must go red.

Row A1 is the one this exists for: `POST /content/item` must call `validate_item`, or an
authored global with a non-compiling `pattern` crashes `reload_content()` for every item
(content-authoring.md §6.3). Runner and verdicts: `mutation_runner.py`.

    python3 sim/tools/authoring_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

RT_CONTENT = WT / "mqtt/supervisor/moxie_runtime/content.py"
PK = WT / "mqtt/moxie_sdk/content/packs/authoring.py"
REN = WT / "mqtt/moxie_sdk/content/render.py"
TESTS = "sim/tests/test_content_authoring.py"

MUTATIONS = [
 # ---- §6.3: the one `if` --------------------------------------------------------
 ("A1  drop the `validate_item` call from the writing route", RT_CONTENT,
  "        reasons = content_packs.validate_item(",
  "        reasons = [] and content_packs.validate_item(",
  "a_bad_pattern_is_refused"),
 ("A1  accept an item `validate_item` refused", RT_CONTENT,
  "        if reasons:\n            return {\"ok\": False, \"error\": reasons[0], \"reason\": reasons[0],",
  "        if False:\n            return {\"ok\": False, \"error\": reasons[0], \"reason\": reasons[0],",
  "a_bad_pattern_is_refused"),

 # ---- §0/§4.5: the kind refusal --------------------------------------------------
 ("A2  let the editor author a schedule", RT_CONTENT,
  '        if kind == "schedule":',
  '        if False:',
  "schedule_is_refused"),
 # The kind check below `if kind == "schedule"` is a second, redundant fence, so widening
 # `AUTHORABLE_KINDS` alone changes nothing and would be a row that only looks like
 # coverage. What IS worth pinning is that a schedule gets the *named* refusal — the one
 # that says why — rather than the generic "unknown kind" a mis-cased compare would fall
 # through to, because "no" without a reason is what sends a parent to the issue tracker.
 ("A2  let a schedule fall through to the generic kind refusal", RT_CONTENT,
  '        if kind == "schedule":',
  '        if kind == "SCHEDULE":',
  "schedule_is_refused"),

 # ---- §4.5: `code` and `extension` are shown, never written ----------------------
 ("A3  drop the unwritable-field refusal", RT_CONTENT,
  "        refusal = self._refuse_unwritable_fields(kind, data, before)\n        if refusal:",
  "        refusal = self._refuse_unwritable_fields(kind, data, before)\n        if False:",
  "extension_and_code_are_not_writable"),
 ("A3  let an extension be rewritten", RT_CONTENT,
  "        if content_packs.canonical(data.get(\"extension\") or {}) \\\n                != content_packs.canonical(base.get(\"extension\") or {}):",
  "        if False:",
  "extension_and_code_are_not_writable"),
 ("A3  let a `code` block be rewritten", RT_CONTENT,
  '        if str(data.get("code") or "") != str(base.get("code") or ""):',
  "        if False:",
  "extension_and_code_are_not_writable"),

 # ---- §6.5: a write path that skips the live swap --------------------------------
 ("A4  save the overlay and never reload the live module", RT_CONTENT,
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
 ("A5  save without snapshotting what it replaced", RT_CONTENT,
  "            self.store.write_shared(self.CONTENT_BACKUP_COLLECTION, {\n"
  "                \"items\": overlay, \"packs\": self._content_packs(),\n"
  "                \"label\": f\"before editing {data.get('name') or key}\",\n"
  "                \"at\": int(time.time())})",
  "            pass",
  "undo_restores_an_authored_save or the_undo_slot_holds_one_save"),

 # ---- R7: two tabs are detected, never merged ------------------------------------
 ("A6  drop the `local_rev` conflict check", RT_CONTENT,
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
 ("A7  write the store entry by hand instead of through `mark_edited`", RT_CONTENT,
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
 ("A10 report the real render's counts instead of the portable render's", RT_CONTENT,
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


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS, r[4]), baseline=[pytest(TESTS)]))
