"""Child-safety gate on input (ai-seam §2) and the parent review journal."""
from __future__ import annotations
import time

from moxie_sdk.types import ResultCode
from moxie_sdk import safety as safety_seam
from moxie_sdk.cloud_config import LoggingPolicy
from .constants import SAFETY_JOURNAL_POLICY


class SafetyMixin:
    # ---- child safety (AI seam §2 — InputSafety) ----
    def safety_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy governing this robot's safety journal — the parent's explicit
        `logging_policy` override if there is one, else `SAFETY_JOURNAL_POLICY`."""
        raw = (self._config_overrides.get(device_id) or {}).get("logging_policy")
        if raw is None:
            return SAFETY_JOURNAL_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return SAFETY_JOURNAL_POLICY

    def _safety_keeps_rows(self, device_id) -> bool:
        """False under `NO_DATA`: the journal then keeps counts and nothing else — no
        excerpt, no per-event row, so none of the child's words are stored at all."""
        return self.safety_policy(device_id) != LoggingPolicy.NO_DATA

    def _assess(self, text, role):
        """Run the classifier, or None when it is off / the text is empty. A classifier
        that raises is treated as "allow": a broken safety stage must never silence Moxie
        (it is a layer under the model's own alignment, not the only one)."""
        if self.safety is None or not (text or "").strip():
            return None
        try:
            return self.safety.assess(text, role=role)
        except Exception as e:
            print(f"[runtime] safety classifier failed (allowing): {e}", flush=True)
            return None

    def _record_safety(self, device_id, verdict) -> dict | None:
        """Put one verdict in the parent review queue. Returns the stored row (or None
        when the policy keeps counts only)."""
        row = None
        try:
            counts = self.store.read(device_id, safety_seam.COUNTS_COLLECTION, {})
            self.store.write(device_id, safety_seam.COUNTS_COLLECTION,
                             safety_seam.roll_up(counts if isinstance(counts, dict) else {},
                                                 verdict))
            if self._safety_keeps_rows(device_id):
                row = safety_seam.event_from(verdict)
                self.store.append(device_id, safety_seam.EVENTS_COLLECTION, row,
                                  cap=safety_seam.MAX_EVENTS)
        except Exception as e:
            print(f"[runtime] safety journal write failed: {e}", flush=True)
        side = "Moxie" if verdict.role == safety_seam.MOXIE else "child"
        cats = ", ".join(verdict.categories) or "?"
        icon = "🛑" if verdict.action == safety_seam.BLOCK else "⚠️"
        self._note("safety", f"{icon} {verdict.action} ({side}): {cats}")
        print(f"[runtime] {icon} safety {verdict.action} on {device_id} "
              f"[{side}]: {cats}", flush=True)
        return row

    def _safety_redirect(self, device_id, verdict):
        """The line Moxie says instead of blocked text: pick it, stamp its id onto the
        verdict as `InputSafety.phrase_id`, and record the block for a parent."""
        red = safety_seam.redirect_for(verdict,
                                       last=self._last_redirect.get(device_id, ""),
                                       classifier=self.safety)
        verdict.phrase_id = red.phrase_id
        self._last_redirect[device_id] = red.text
        self._record_safety(device_id, verdict)
        return red

    def _safety_gate_input(self, device_id, event_id, speech, seq) -> bool:
        """Pre-inference gate: assess what the CHILD said before any brain call.

        Hard-blocked → the brain is never called; Moxie speaks a gentle, kid-appropriate
        redirect as a spec-conformant `RemoteChatResponse` carrying
        `input.safety` (`RemoteChatInput.InputSafety`, RemoteChat.proto:180-186/:198/:335).
        Flagged → allowed through to the brain and recorded for a parent.
        Returns True when the turn was answered here and the caller must stop.
        """
        verdict = self._assess(speech, safety_seam.CHILD)
        if not verdict:
            return False
        if verdict.action != safety_seam.BLOCK:
            self._record_safety(device_id, verdict)
            return False
        red = self._safety_redirect(device_id, verdict)
        if self._is_stale(device_id, seq):
            return True
        # Deliberately remember only OUR line: putting the blocked utterance in the
        # history would feed it to the brain as context on the very next turn.
        self._remember(device_id, "", red.text)
        _, red_scored = self._stage(red.text, red, turn_key=event_id,
                                    markup=red.markup)
        self._publish_chat(device_id, event_id, "router", red.text, red.markup,
                           result=ResultCode.SUCCESS, safety=verdict,
                           scored=red_scored)
        self._maybe_synthesize(device_id, red.markup, event_id, chunk_num=0)
        return True

    def safety_view(self, device_id, limit: int = 20) -> dict:
        """The parent console's review queue for one robot: counts by category plus the
        newest events, newest first. Unknown device with nothing stored → ok:false."""
        counts = self.store.read(device_id, safety_seam.COUNTS_COLLECTION, None)
        if device_id not in self.robots and counts is None:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}"}
        rows = self.store.read(device_id, safety_seam.EVENTS_COLLECTION, []) or []
        if not isinstance(rows, list):
            rows = []
        newest = list(reversed(rows))[:max(0, int(limit))]
        return {
            "ok": True, "device_id": device_id,
            "policy": self.safety_policy(device_id).name,
            "detail": self._safety_keeps_rows(device_id),
            "enabled": self.safety is not None,
            "classifier": getattr(self.safety, "name", None),
            "counts": counts if isinstance(counts, dict) else {},
            "unreviewed": sum(1 for r in rows if not r.get("reviewed")),
            "labels": safety_seam.category_labels(self.safety) if self.safety else {},
            "events": newest,
        }

    def acknowledge_safety(self, device_id, event_id=None, limit: int = 20) -> dict:
        """Mark one queued event reviewed (or every one when `event_id` is None/"all") —
        the parent's "I have seen this". Returns the refreshed view."""
        rows = self.store.read(device_id, safety_seam.EVENTS_COLLECTION, []) or []
        if not isinstance(rows, list):
            rows = []
        want_all = event_id in (None, "", "all", "*")
        hit = 0
        for r in rows:
            if want_all or r.get("id") == event_id:
                if not r.get("reviewed"):
                    r["reviewed"] = True
                    r["reviewed_at"] = time.time()
                hit += 1
        if not hit and not want_all:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown safety event {event_id!r}"}
        self.store.write(device_id, safety_seam.EVENTS_COLLECTION, rows)
        self._note("safety", f"✅ reviewed {hit} safety event(s)")
        out = self.safety_view(device_id, limit=limit)
        out["acknowledged"] = hit
        return out
