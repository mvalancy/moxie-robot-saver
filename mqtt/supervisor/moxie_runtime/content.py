"""Content packs (export/import/undo) and content authoring + template rendering."""
from __future__ import annotations
import os, time

from moxie_sdk.actions import parse_action_tags
from moxie_sdk.content import packs as content_packs
from moxie_sdk.content import render
from moxie_sdk.content.content_app import opener_alternatives, pick_opener


class ContentMixin:
    # ---- content packs (backlog/content-packs.md) ----
    # The logic is in the pure `moxie_sdk/content/packs/`; this is the store, the clock
    # and the live swap. Review writes nothing; import snapshots first (one-slot undo, one
    # atomic write). Only the overlay is written (effective = shipped defaults + overlay),
    # so a release's improved starter content still upgrades. The swap is one attribute
    # (`reload_content`): a turn in flight finishes on its module, the next uses the new one.

    CONTENT_ITEMS_COLLECTION = "content_items"    # → $MOXIE_DATA_DIR/fleet/content_items.json
    CONTENT_PACKS_COLLECTION = "content_packs"    # the ledger the 📦 card lists
    CONTENT_BACKUP_COLLECTION = "content_backup"  # the ONE-slot pre-import snapshot

    @staticmethod
    def pack_max_bytes() -> int:
        """Largest pack body this appliance will buffer (`MOXIE_PACK_MAX_BYTES`, default
        1 MiB), read per call so tests and deployments can change it."""
        try:
            value = int(os.environ.get("MOXIE_PACK_MAX_BYTES", "").strip() or 0)
        except ValueError:
            value = 0
        return value if value > 0 else content_packs.DEFAULT_MAX_BYTES

    def _content_apps(self) -> list:
        """Every live app that carries a content module — not only `self.app`: a per-child
        `content` brain may be cached in `_brains`. De-duplicated by identity."""
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
        """The SHIPPED baseline under the overlay. `config.build_content_app()` records
        it on the app before applying the overlay (so undo can restore a shipped item);
        otherwise fall back to the loaded module (overlay entries win either way)."""
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
        """The installed overlay (`fleet/content_items.json`); `{}` when nothing imported."""
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
        """Effective content: shipped defaults, then the overlay by `kind:key`."""
        return content_packs.merge_items(self._content_defaults(), self._content_overlay())

    def _known_child_names(self) -> list:
        """Names this appliance knows (startup child, connected robots, name-ish fleet
        config values) for the export-time PII flag. Catches only names we know."""
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
        """Rebuild the live `ContentModule` from defaults + overlay and swap it in (one
        attribute per live content brain; nothing on the wire, no restart)."""
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
        """The pack card's poll: inventory, pack ledger, and whether undo is armed."""
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
        """Build a pack from the named installed items (`kind:key`), or all of them. An
        uninstalled key is an error, not a quietly smaller file."""
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
        """What WOULD happen if this pack were imported. Writes nothing. `expect_digest`
        echoed back on import proves the imported file is the reviewed one."""
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
        """Apply the accepted items and make them live — the only writing verb here.
        Refuses with `conflict` (409) when `expect_digest` does not match the body (the
        server holds no session between review and import)."""
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
        """Restore the one-slot snapshot: the overlay AND the ledger."""
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

    # ---- content authoring (backlog/content-authoring.md) ----
    # An authored item is as untrusted as an imported one because it goes through the same
    # functions (§6.1). The one new safety line is `validate_item` in `content_save_item`:
    # `mark_edited` only normalizes, and an invalid global `pattern` would otherwise crash
    # `reload_content()` for every item (authoring_mutation_check.py deletes it to prove
    # it). No brain call here: the paid try is `tryit.py`, and it tries installed items only.

    #: Item kinds the editor may write. `schedule` is refused by name: it reaches the robot
    #: as `ContentSchedule` and no Moxie has been served a pack-authored one (brief §0).
    AUTHORABLE_KINDS = ("conversation", "global")

    def content_save_item(self, body) -> dict:
        """Save one authored item: validate, snapshot, write the overlay, reload.

        Refusals are sentences: a schedule (by kind); any change to `code`/`extension`
        (never authorable); `validate_item`'s own sentence verbatim; a stale `local_rev`
        -> 409 (two tabs are detected, never merged). Success takes the same one-slot
        snapshot as an import (so undo works) and returns the command shadow check
        (advice only).
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

        # §6.3: `mark_edited` normalizes; it does not validate.
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
        """`code` and `extension` must survive a save untouched, or it does not happen —
        for new items too. Editing an extension belongs to backlog/sandboxed-extensions.md."""
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

    #: Sample facts for rung 1. A prompt template sees exactly three names — `volley`,
    #: `session`, `presence` — which is what makes the chip list closeable (§4.3).
    RENDER_SAMPLE_FACTS = ("likes drawing dinosaurs",
                           "is learning to whistle",
                           "was nervous about the school play")

    def content_render(self, body) -> dict:
        """Resolve a draft prompt against a sample context (rung 1, §5.1). No brain, no store.

        Renders twice: with the installed renderer and with `render._minimal_render` (what
        a bare SDK install without jinja2 uses), so `portable_identical: false` flags a
        prompt that would mean something different off this box. `counts_advisory` stays
        true: the render counters are process-global and a concurrent turn moves them.
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
        # The robot's own split and first pick, its tags lifted: the line a robot hears
        # first (a `|` inside `{{ }}`/`{% %}`/`{# #}` is not a separator).
        openers = data.get("opener") or ""
        opener = parse_action_tags(pick_opener(openers, context) or "")[0]
        return {
            "ok": True,
            "prompt": prompt,
            "opener": opener,
            "openers": [o for o in opener_alternatives(openers) if o.strip()],
            "portable": portable,
            "portable_identical": portable == prompt,
            "counts": {"blocked": counts.get("blocked", 0) + portable_counts.get("blocked", 0),
                       "stripped": portable_counts.get("stripped", 0)},
            "counts_advisory": True,
            "context": sample,
        }

    @staticmethod
    def _render_context(sample: dict) -> dict:
        """The three names a prompt template may see, filled with sample values — plain
        dicts, so a made-up turn cannot reach anything real. `FactList` renders facts as
        bullet lines, as on a real turn."""
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
