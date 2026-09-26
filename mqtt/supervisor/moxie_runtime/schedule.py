"""The day plan: schedule building and its explanation for the console."""
from __future__ import annotations



class ScheduleMixin:
    # ---- the day plan ----
    SCHEDULE_EXPLAIN_COLLECTION = "schedule_explain"   # robots/<id>/schedule_explain.json

    def plan_schedule_for(self, device_id, *, now=None) -> tuple:
        """Plan this robot's day → `(ContentSchedule, explanations, inputs)`.

        The recommender (audit §4.2 BEYOND #7) is a pure function; this method is the
        only place that gathers its signals from live state:
          * the running content module's `schedules[]` (read-only authoring templates),
          * this robot's stored `mentor_behaviors` (what the child finished vs. quit),
          * its effective config — `schedule_preferences.parent_requests[]` and the
            bedtime windows (read-only; the config path itself is untouched),
          * its buffered telemetry Packets (see `moxie_sdk.schedule.telemetry_signals`
            for what those can honestly contribute — no module-scoped event vocabulary
            is recovered, so they are context, not a score).
        See `moxie_sdk/schedule.py` for the shape, the weights and the citations.
        """
        from moxie_sdk.schedule import plan
        schedules = None
        try:
            apps = self._content_apps() or [self.app]
            schedules = getattr(getattr(apps[0], "module", None), "schedules", None)
        except Exception as e:
            print(f"[runtime] schedule template unavailable ({e}); using the default")
        robot = self.robots.get(device_id)
        packets = self._telemetry_buffer(device_id, robot) if robot else []
        try:
            config = self.effective_config(device_id)
        except Exception as e:
            print(f"[runtime] effective config unavailable ({e}); planning without it")
            config = {}
        child = getattr(self.child, "nickname", "") or ""
        return plan(device_id, content_schedules=schedules,
                    mentor_behaviors=self.mentor_behaviors(device_id),
                    effective_config=config, telemetry_packets=packets,
                    child_name=child, now=now)

    def build_schedule_for(self, device_id) -> dict:
        """The ContentSchedule this robot gets for this session — the planner's output,
        and the value of `CloudQueryResponse.schedule`. The parent-readable "why this
        activity today" lines are stored alongside it (never on the wire) so
        `GET /schedule` can show them after the robot has pulled its day."""
        sched, explanations, inputs = self.plan_schedule_for(device_id)
        try:
            self.store.write(device_id, self.SCHEDULE_EXPLAIN_COLLECTION,
                             {"day": inputs.get("day"), "planned_at": inputs.get("now"),
                              "schedule": sched, "explanations": explanations,
                              "inputs": self._schedule_inputs_summary(inputs)})
        except Exception as e:                     # a plan must never fail on its audit
            print(f"[runtime] could not store schedule explanations: {e}", flush=True)
        return sched

    @staticmethod
    def _schedule_inputs_summary(inputs) -> dict:
        """The parent-facing slice of the planner's inputs: what it knew, not the whole
        catalog. Everything here is already JSON-safe (`plan_inputs` guarantees it)."""
        keys = ("device_id", "day", "now", "bucket", "slot_minutes", "child_name",
                "bedtime", "slots", "parent_requests", "ftue_skips", "telemetry",
                "planned")
        out = {k: inputs.get(k) for k in keys if k in inputs}
        history = inputs.get("history") or {}
        out["history"] = {k: history[k] for k in sorted(history)}
        return out

    def schedule_view(self, device_id, *, refresh: bool = False) -> dict:
        """`GET /schedule?device_id=…` — the day this robot was served, the "why this
        activity today" line behind every entry, and a summary of the signals the planner
        had. Read-only: with nothing stored yet (the robot has not pulled a schedule this
        run) it plans one on the spot rather than answering empty."""
        stored = self.store.read(device_id, self.SCHEDULE_EXPLAIN_COLLECTION, None)
        if refresh or not isinstance(stored, dict) or not stored.get("explanations"):
            if device_id not in self.robots and not self.store.read(
                    device_id, "mentor_behaviors", None):
                return {"ok": False, "error": f"unknown device_id {device_id!r}"}
            sched, explanations, inputs = self.plan_schedule_for(device_id)
            stored = {"day": inputs.get("day"), "planned_at": inputs.get("now"),
                      "schedule": sched, "explanations": explanations,
                      "inputs": self._schedule_inputs_summary(inputs), "served": False}
        else:
            stored = dict(stored)
            stored.setdefault("served", True)
        return {"ok": True, "device_id": device_id, **stored}
