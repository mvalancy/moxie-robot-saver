"""Content packs (export/import/undo) and content authoring + template rendering."""
from __future__ import annotations
import os, time

from moxie_sdk.content import packs as content_packs
from moxie_sdk.content import render


class ContentMixin:
    # ---- 📦 content packs (backlog/content-packs.md) ----
    # Content stops being a file in our repository and becomes a thing a parent installs
    # and a stranger publishes: one JSON file, reviewed before it changes anything, undoable
    # afterwards. Everything hard is in the pure `moxie_sdk/content/packs.py` — this region
    # is the store, the clock and the live swap, and nothing else.
    #
    # Three properties are load-bearing here, and each is asserted by a test:
    #   * **Review writes nothing.** `content_review` is a pure read; only `content_import`
    #     touches the store, and only after it has taken the one-slot snapshot `undo`
    #     restores (R1: one atomic `write_shared`, so a crash leaves the old set or the new
    #     one, never a mixture).
    #   * **The overlay is written, never the merged view.** Effective content is *shipped
    #     defaults ⊕ overlay*; an import writes only the accepted items into the overlay, so
    #     a future release's improved starter chat is still an upgrade rather than something
    #     the overlay silently shadows.
    #   * **The swap is one attribute.** `reload_content()` reassigns `self.app.module`; a
    #     turn already in flight finishes on the module object it started with and the NEXT
    #     turn uses the new one. There is no lock in the turn loop — the same rule the voice
    #     picker adopted for engine swaps — and that is documented behaviour, not an
    #     oversight. `_push_config` is untouched: nothing a P0 pack carries reaches
    #     `RobotCloudConfig`, which is exactly why face/config packs are P2.

    CONTENT_ITEMS_COLLECTION = "content_items"    # → $MOXIE_DATA_DIR/fleet/content_items.json
    CONTENT_PACKS_COLLECTION = "content_packs"    # the ledger the 📦 card lists
    CONTENT_BACKUP_COLLECTION = "content_backup"  # the ONE-slot pre-import snapshot

    @staticmethod
    def pack_max_bytes() -> int:
        """Largest pack body this appliance will buffer (`MOXIE_PACK_MAX_BYTES`, 1 MiB).

        Read per call rather than at import, so the cap is testable and a deployment can
        raise it without a code change. Upstream has no cap at all and round-trips the
        pack through a hidden form field twice."""
        try:
            value = int(os.environ.get("MOXIE_PACK_MAX_BYTES", "").strip() or 0)
        except ValueError:
            value = 0
        return value if value > 0 else content_packs.DEFAULT_MAX_BYTES

    def _content_apps(self) -> list:
        """Every live app that carries a content module.

        Since 🧠 per-child brains, "the content app" is not necessarily `self.app`: an
        appliance whose default is `llm` can still have one child on `content`, built
        lazily by `app_for` and held in `_brains`. A pack import that swapped only
        `self.app.module` would install content that the child who is actually running it
        never sees — so the swap iterates. De-duplicated by identity, because the
        appliance's own brain is also cached under its own name.
        """
        apps, seen = [], set()
        for app in [getattr(self, "app", None)] + list(self._brains.values()):
            if app is None or id(app) in seen:
                continue
            seen.add(id(app))
            if (getattr(app, "module", None) is not None
                    or getattr(app, "content_defaults", None) is not None):
                apps.append(app)
        return apps

    def _content_defaults(self) -> dict:
        """The SHIPPED baseline the overlay sits on top of.

        `config.build_content_app()` records it on the app (`content_defaults`) *before* it
        applies the overlay, which is the only way an `undo` can put a shipped item back
        after a pack replaced it. Without it — a bare `MoxieApp`, or an app built some other
        way — we fall back to the loaded module itself, which is the same answer on a fresh
        appliance and an honest approximation on one that has already imported (the merge is
        idempotent, and overlay entries win either way)."""
        for app in self._content_apps():
            recorded = getattr(app, "content_defaults", None)
            if isinstance(recorded, dict):
                return recorded
        for app in self._content_apps():
            items = content_packs.items_from_module(getattr(app, "module", None))
            if items:
                return items
        return content_packs.items_from_module(getattr(self.app, "module", None))

    def _content_overlay(self) -> dict:
        """The installed overlay (`fleet/content_items.json`) — `{}` when nothing imported."""
        rec = self.store.read_shared(self.CONTENT_ITEMS_COLLECTION, {}) or {}
        items = rec.get("items") if isinstance(rec, dict) else None
        return items if isinstance(items, dict) else {}

    def _write_content_overlay(self, items: dict) -> bool:
        """One atomic write of the whole overlay (R1) — never a partial merge."""
        return self.store.write_shared(self.CONTENT_ITEMS_COLLECTION,
                                       {"items": items, "updated_at": int(time.time())})

    def _content_packs(self) -> list:
        rec = self.store.read_shared(self.CONTENT_PACKS_COLLECTION, {}) or {}
        rows = rec.get("packs") if isinstance(rec, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def content_items(self) -> dict:
        """**Effective content**: shipped defaults, then the overlay by `kind:key`."""
        return content_packs.merge_items(self._content_defaults(), self._content_overlay())

    def _known_child_names(self) -> list:
        """Names this appliance knows, for the export-time PII flag.

        The child profile the supervisor was started with, every connected robot's, and any
        name-ish string in the fleet config. It catches the names we know and **nothing
        else** — a prompt naming a sibling or a school sails straight through, and the card
        says so."""
        names = []
        for child in ([getattr(self, "child", None)]
                      + [getattr(r, "child", None) for r in self.robots.values()]):
            nick = str(getattr(child, "nickname", "") or "").strip()
            if nick and nick not in names:
                names.append(nick)
        for key, value in (self.fleet_config() or {}).items():
            if isinstance(value, str) and ("name" in key or "nickname" in key):
                value = value.strip()
                if value and value not in names:
                    names.append(value)
        return names

    def reload_content(self) -> dict:
        """Rebuild the live `ContentModule` from defaults ⊕ overlay and swap it in.

        One attribute assignment. The next turn renders the new prompt; a turn already in
        flight finishes on the module it started with, and a conversation session keeps its
        `Conversation` for that session (brief §2.5). No restart, and nothing on the wire —
        a pack is server-side data.
        """
        defaults, overlay = self._content_defaults(), self._content_overlay()
        module = content_packs.build_module(defaults, overlay)
        live = False
        for app in self._content_apps():
            if getattr(app, "module", None) is not None:
                app.module = module              # ← the whole swap, per live content brain
                live = True
        return {"ok": True, "live": live,
                "conversations": len(module.conversations),
                "globals": len(module.globals),
                "schedules": len(module.schedules),
                "overlay": len(overlay), "shipped": len(defaults)}

    def content_view(self) -> dict:
        """The 📦 card's poll: the inventory, the pack ledger, and whether undo is armed."""
        items = self.content_items()
        backup = self.store.read_shared(self.CONTENT_BACKUP_COLLECTION, {}) or {}
        rows = content_packs.inventory(items, known_names=self._known_child_names())
        return {
            "ok": True,
            "items": rows,
            "packs": self._content_packs(),
            "counts": {"total": len(rows),
                       "edited": sum(1 for r in rows if r["local_edited"]),
                       "with_code": sum(1 for r in rows if r["has_code"]),
                       "from_packs": sum(1 for r in rows if r["origin"] == "pack")},
            "undo_available": bool(isinstance(backup, dict) and backup.get("items") is not None),
            "undo_label": str((backup or {}).get("label") or ""),
            "max_bytes": self.pack_max_bytes(),
            "pack_format": content_packs.PACK_FORMAT,
        }

    def content_export(self, keys=None, *, name: str = "", pack_id: str = "",
                       details: str = "", author: str = "", now=None) -> dict:
        """Build a pack from the named installed items (`kind:key`), or from all of them.

        Returns the pack itself — the HTTP layer serializes it and the browser saves it.
        A key that is not installed is an error rather than a quietly smaller file.
        """
        items = self.content_items()
        wanted = [str(k).strip() for k in (keys or []) if str(k or "").strip()]
        if wanted:
            missing = [k for k in wanted if k not in items]
            if missing:
                raise content_packs.PackError(
                    "not installed: " + ", ".join(sorted(missing)))
            items = {k: items[k] for k in wanted}
        if not items:
            raise content_packs.PackError("there is nothing to export")
        label = str(name or "").strip() or "Moxie content"
        return content_packs.export_pack(items, name=label,
                                         pack_id=pack_id or label, details=details,
                                         author=author, now=now)

    def content_review(self, body) -> dict:
        """What WOULD happen if this pack were imported. Writes nothing, reads no clock.

        `expect_digest` in the answer is the digest of the body as reviewed; echoing it back
        on import is what closes the review-one-file-import-another gap that upstream's
        hidden form field leaves open.
        """
        pack, meta = content_packs.parse_pack(body)
        rows = content_packs.review_pack(pack, self.content_items(),
                                         digest=meta["digest"])
        return {
            "ok": True,
            "pack": {k: v for k, v in pack.items() if k != "items"},
            "digest": meta["digest"],
            "expect_digest": meta["computed"],
            "warnings": meta["warnings"],
            "items": rows,
            "accept": [r["id"] for r in rows if r["default"]],
            "counts": {"total": len(rows),
                       "default": sum(1 for r in rows if r["default"]),
                       "conflicts": sum(1 for r in rows
                                        if r["state"] in (content_packs.CONFLICT,
                                                          content_packs.DOWNGRADE_CONFLICT)),
                       "invalid": sum(1 for r in rows
                                      if r["state"] == content_packs.INVALID)},
        }

    def content_import(self, body, accept=None, expect_digest: str = "") -> dict:
        """Apply the accepted items, then make them live. The only verb here that writes.

        Refuses with `conflict: True` (HTTP **409**) when `expect_digest` — the digest the
        reviewer was shown — is not the digest of the body now being imported: the pack is
        re-sent between review and import (the server holds no session state), so the two
        can genuinely be different files.
        """
        pack, meta = content_packs.parse_pack(body)
        if expect_digest and str(expect_digest) != meta["computed"]:
            return {"ok": False, "conflict": True,
                    "error": "this is not the pack that was reviewed",
                    "reason": "The file changed between the review and the import. "
                              "Review it again before installing.",
                    "expect_digest": meta["computed"]}
        with self._content_lock:
            overlay = self._content_overlay()
            merged, summary = content_packs.apply_pack(pack, overlay, accept or [],
                                                       now=int(time.time()))
            if summary["applied"]:
                self.store.write_shared(self.CONTENT_BACKUP_COLLECTION, {
                    "items": overlay, "packs": self._content_packs(),
                    "label": f"before importing {pack.get('name') or pack.get('id')}",
                    "at": int(time.time())})
                if not self._write_content_overlay(merged):
                    return {"ok": False, "error": "could not write the content overlay",
                            "reason": "The appliance could not save the imported items."}
                ledger = [r for r in self._content_packs()
                          if r.get("id") != summary["pack"]["id"]]
                ledger.append(summary["pack"])
                self.store.write_shared(self.CONTENT_PACKS_COLLECTION, {"packs": ledger})
            reload = self.reload_content()
        self._note("content", f"📦 imported {summary['count']} item(s) "
                              f"from {pack.get('id')}")
        return {"ok": True, "digest": meta["digest"], **summary, "reload": reload,
                "undo_available": bool(summary["applied"])}

    def content_undo(self) -> dict:
        """Put the one-slot snapshot back — the overlay AND the ledger, byte for byte."""
        with self._content_lock:
            backup = self.store.read_shared(self.CONTENT_BACKUP_COLLECTION, {}) or {}
            items = backup.get("items") if isinstance(backup, dict) else None
            if not isinstance(items, dict):
                return {"ok": False, "error": "nothing to undo",
                        "reason": "No import has been made since this appliance started "
                                  "keeping a snapshot."}
            self._write_content_overlay(items)
            packs_before = backup.get("packs")
            if isinstance(packs_before, list):
                self.store.write_shared(self.CONTENT_PACKS_COLLECTION,
                                        {"packs": packs_before})
            self.store.delete_shared(self.CONTENT_BACKUP_COLLECTION)   # one slot, used up
            reload = self.reload_content()
        self._note("content", "📦 undo — content restored")
        return {"ok": True, "restored": len(items), "reload": reload,
                "label": str(backup.get("label") or ""), "undo_available": False}

    # ---- ✍️ content authoring (backlog/content-authoring.md) ----
    # Packs made content **shippable**; these two verbs make it **writable**. The design
    # decision they rest on is that an authored item is exactly as untrusted as an
    # imported one *because it enters through the same functions* (brief §6.1) — so there
    # is no "we wrote this one" branch anywhere below, and the only genuinely new safety
    # code in the whole slice is the `validate_item` call in `content_save_item`.
    #
    # Why that one call is the load-bearing line (§6.3): `packs.mark_edited` calls
    # `normalize_data` and **not** `validate_item` — `apply_pack` does that itself before
    # writing. So a save that skipped it would let an authored global with a
    # non-compiling `pattern` reach `Global.from_dict`, which compiles at LOAD, and a
    # throw inside the loader takes down `reload_content()` for every item at once.
    # `sim/tools/authoring_mutation_check.py` deletes the call and requires
    # `test_a_bad_pattern_is_refused_with_validate_items_own_sentence` to go red.
    #
    # What is deliberately absent here: `POST /content/try`. P0 makes **no brain call**
    # (brief §9's "not in P0" list), so there is no budget, no counter and no 429 in this
    # region — `config.AUTHOR_TRY_BUDGET` is declared for P1 and consumed by nobody yet.

    #: Item kinds the editor may write. `schedule` is absent on purpose and refused by
    #: name below: it is the one kind that reaches the robot as `ContentSchedule`, and no
    #: physical Moxie has ever been served a pack-authored one (brief §0), so a
    #: parent-facing button must not put an unobserved wire behaviour behind it.
    AUTHORABLE_KINDS = ("conversation", "global")

    def content_save_item(self, body) -> dict:
        """✍️ Save one authored item — validate, snapshot, write the overlay, reload.

        The verb the 📦 card's ✏️ and ＋ New both call. Everything it enforces is a
        refusal, and each refusal is a sentence rather than a status code:

        * a **schedule** is refused by kind (§0/§4.5), naming the robot as the reason;
        * a change to **`code`** or **`extension`** is refused (§4.5) — both round-trip a
          save untouched and neither is authorable in any phase, so the editor shows them
          and the route makes that structural;
        * `validate_item`'s own sentence is returned verbatim for anything it refuses,
          because a paraphrase here would be a second validator (§6.1);
        * a stale `local_rev` is a **409** with the import conflict's own wording (R7):
          two tabs are *detected*, never merged, and the one undo slot is not a fix for
          that and must not be described as one.

        On success it takes the same one-slot snapshot an import takes, so
        `POST /content/undo` restores an authored save with no new mechanism, and calls
        `reload_content()` so disk and memory never disagree about what Moxie says next
        (§6.5). The answer carries the shadow check for a command (§4.4) — advice, never a
        refusal, and scoped to the phrases the author actually typed.
        """
        if not isinstance(body, dict):
            return {"ok": False, "error": "expected a JSON object",
                    "reason": "The editor sent something that is not an item."}
        kind = str(body.get("kind") or "")
        if kind == "schedule":
            return {"ok": False, "error": "a schedule cannot be authored here",
                    "reason": "A schedule is the one kind of content that is sent to the "
                              "robot itself, and no Moxie has ever been given one that "
                              "came from a pack. Until that has been watched on real "
                              "hardware, this editor will not write one."}
        if kind not in self.AUTHORABLE_KINDS:
            return {"ok": False, "error": f"unknown item kind {kind!r}",
                    "reason": "This editor writes conversations and commands."}
        try:
            data = content_packs.normalize_data(kind, body.get("data"))
        except content_packs.PackError as e:
            return {"ok": False, "error": str(e), "reason": str(e)}

        key = content_packs.item_key(kind, data)
        ident = content_packs.full_key(kind, key)
        asked = str(body.get("key") or "")
        items = self.content_items()
        if asked and content_packs.full_key(kind, asked) != ident \
                and content_packs.full_key(kind, asked) in items:
            return {"ok": False,
                    "error": "this item's identity is locked",
                    "reason": "A conversation is identified by its module and content "
                              "ids, and a command by its name. Changing one makes a NEW "
                              "item rather than editing this one — save it under the new "
                              "name and the old one stays where it is."}

        before = items.get(ident)
        refusal = self._refuse_unwritable_fields(kind, data, before)
        if refusal:
            return refusal

        # §6.3 — the one `if`. `mark_edited` normalizes; it does not validate.
        reasons = content_packs.validate_item(
            {"kind": kind, "key": key, "data": data,
             "source_version": content_packs.source_version_of(before or {})})
        if reasons:
            return {"ok": False, "error": reasons[0], "reason": reasons[0],
                    "reasons": reasons}

        expected = str(body.get("local_rev") or "")
        if expected and before is not None \
                and expected != content_packs.local_rev({"kind": kind, **before}):
            return {"ok": False, "conflict": True,
                    "error": "this is not the version you opened",
                    "reason": "Somebody else saved this item while you were editing it. "
                              "Open it again before saving, or your change would quietly "
                              "replace theirs."}

        shadow = (content_packs.shadow_check(data, items, body.get("phrases"))
                  if kind == "global" else [])

        with self._content_lock:
            overlay = self._content_overlay()
            self.store.write_shared(self.CONTENT_BACKUP_COLLECTION, {
                "items": overlay, "packs": self._content_packs(),
                "label": f"before editing {data.get('name') or key}",
                "at": int(time.time())})
            merged = content_packs.mark_edited(overlay, ident, data)
            if not self._write_content_overlay(merged):
                return {"ok": False, "error": "could not write the content overlay",
                        "reason": "The appliance could not save this item."}
            reload = self.reload_content()
        self._note("content", f"✍️ saved {ident}")
        rows = content_packs.inventory({ident: merged[ident]},
                                       known_names=self._known_child_names())
        return {"ok": True, "id": ident, "key": key, "kind": kind,
                "created": before is None,
                "item": rows[0] if rows else {},
                "local_rev": content_packs.local_rev({"kind": kind, **merged[ident]}),
                "shadow": shadow, "reload": reload, "undo_available": True,
                "undo_slots": 1}

    @staticmethod
    def _refuse_unwritable_fields(kind: str, data: dict, before) -> dict:
        """`code` and `extension` survive a save untouched, or the save does not happen.

        Not a warning: the property that makes a pack safe is structural (§6.5), and an
        editor that could *change* an extension would be the text→AST surface
        `backlog/sandboxed-extensions.md` P1 owns — with a second compiler, which that
        brief already refused in its own §7.4. So the card shows both fields read-only and
        this refuses anything else, for a new item as much as an edited one (a parent
        cannot *create* a `code` block either).
        """
        base = content_packs.normalize_data(kind, (before or {}).get("data")) if before \
            else content_packs.normalize_data(kind, {})
        if content_packs.canonical(data.get("extension") or {}) \
                != content_packs.canonical(base.get("extension") or {}):
            return {"ok": False,
                    "error": "an extension cannot be edited here",
                    "reason": "This item carries a sandboxed program. The editor shows "
                              "what it does and never changes it — see "
                              "docs/architecture/backlog/sandboxed-extensions.md for the "
                              "surface that will."}
        if str(data.get("code") or "") != str(base.get("code") or ""):
            return {"ok": False,
                    "error": "a code block cannot be edited here",
                    "reason": "This appliance never runs a content module's Python `code`, "
                              "so there is nothing here that could write one. It travels "
                              "as inert text and the editor leaves it exactly as it found "
                              "it — see docs/architecture/backlog/sandboxed-extensions.md "
                              "for behaviour this appliance CAN run."}
        return {}

    #: The sample values rung 1 renders against. A prompt is a template over exactly three
    #: top-level names — `volley`, `session` and `presence` (`content_app.py`:312 for the
    #: opener, :371 for the prompt) — and that closed list is what makes the chip list
    #: closeable at all (§4.3).
    RENDER_SAMPLE_FACTS = ("likes drawing dinosaurs",
                           "is learning to whistle",
                           "was nervous about the school play")

    def content_render(self, body) -> dict:
        """👁️ Resolve a draft prompt against a sample context. **No brain, no store.**

        Rung 1 of the loop (§5.1), and the highest-value free feedback we can give: the
        panel is *the actual system prompt the brain would receive*, which is the thing a
        prompt author most needs and today cannot see at all. `render_prompt` is a pure
        function over a plain dict, so this route is one call and a made-up context.

        It renders **twice**, and that is a decision worth defending. The second pass is
        `render._minimal_render` — the dependency-free renderer a bare
        `pip install moxie-cloud-sdk` without the `content` extra lands on. Reporting only
        `render.STRIPPED` around the real call would report **zero** on every appliance we
        ship (the container installs jinja2), i.e. a counter that can never fire where it
        matters. Rendering both answers the question §4.3 actually promises — *does this
        prompt mean the same thing off this box?* — and `portable_identical: false` is the
        signal that an author has typed past the guided grammar.

        `counts_advisory` is `true` and stays true: `render.BLOCKED` / `render.STRIPPED`
        are process-global integers the turn loop also moves, so even the narrow window
        `render._tally` takes is polluted by a concurrent turn. The fix is not a lock
        around the renderer (§5.1 forbids it — the turn loop calls it too); the fix is
        saying so.
        """
        if not isinstance(body, dict):
            return {"ok": False, "error": "expected a JSON object",
                    "reason": "The editor sent something that is not a draft."}
        kind = str(body.get("kind") or "conversation")
        if kind != "conversation":
            return {"ok": False, "error": f"{kind} items have no prompt to resolve",
                    "reason": "Only a conversation has a prompt. A command matches "
                              "phrases, and you can see what it would catch beside it."}
        try:
            data = content_packs.normalize_data("conversation", body.get("data"))
        except content_packs.PackError as e:
            return {"ok": False, "error": str(e), "reason": str(e)}

        ctx = body.get("context") if isinstance(body.get("context"), dict) else {}
        known = self._known_child_names()
        nickname = str(ctx.get("nickname") or (known[0] if known else "Sam"))
        face = bool(ctx.get("face_present", True))
        overflow = bool(ctx.get("overflow", False))
        namespace = str((data.get("memory") or {}).get("namespace") or "") or "memory_chat"
        facts = ctx.get("facts")
        facts = ([str(f) for f in facts] if isinstance(facts, (list, tuple))
                 else list(self.RENDER_SAMPLE_FACTS))
        sample = {"nickname": nickname, "face_present": face, "overflow": overflow,
                  "namespace": namespace, "facts": facts}
        context = self._render_context(sample)

        counts, portable_counts = {}, {}
        prompt = render.render_prompt(data.get("prompt") or "", context, counts=counts)
        portable = render._minimal_render(data.get("prompt") or "", context,
                                          counts=portable_counts)
        opener = render.render_prompt((data.get("opener") or "").split("|")[0], context)
        return {
            "ok": True,
            "prompt": prompt,
            "opener": opener.replace("<opener>", "").strip(),
            "openers": [o for o in (data.get("opener") or "").split("|") if o.strip()],
            "portable": portable,
            "portable_identical": portable == prompt,
            "counts": {"blocked": counts.get("blocked", 0) + portable_counts.get("blocked", 0),
                       "stripped": portable_counts.get("stripped", 0)},
            "counts_advisory": True,
            "context": sample,
        }

    @staticmethod
    def _render_context(sample: dict) -> dict:
        """The three top-level names a prompt template may see, filled with sample values.

        Plain dicts rather than a live `Volley`/`Session`: `render_prompt` walks dotted
        paths over dicts and objects alike, and a made-up turn must not be able to reach
        anything real. `FactList` is used for the facts so `{{ …facts }}` renders as
        bullet lines here exactly as it does on a turn, instead of as a list repr.
        """
        from moxie_sdk.content.memory import FactList
        return {
            "volley": {
                "speech": "Tell me about my day",
                "config": {"child_pii": {"nickname": sample["nickname"],
                                         "pronouns": "they/them", "birthday": "",
                                         "notes": ""}},
                "request": {"input_vars": {}},
                "entities": [],
                "persist_data": {sample["namespace"]: {"facts": FactList(sample["facts"])}},
                "local_data": {},
            },
            "session": {"overflow": sample["overflow"], "total_volleys": 3,
                        "history": [], "module_id": "", "content_id": ""},
            "presence": {"known": True, "face_present": sample["face_present"],
                         "faces_seen": 1, "flickers": 0, "events": 2,
                         "line": ("Someone is in front of Moxie right now."
                                  if sample["face_present"]
                                  else "Nobody is in front of Moxie.")},
        }
