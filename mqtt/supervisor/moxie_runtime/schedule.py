"""The day plan: schedule building and its explanation for the console."""
from __future__ import annotations
from .constants import MENTOR_BEHAVIORS_COLLECTION


class ScheduleMixin:
    # ---- the day plan ----
    SCHEDULE_EXPLAIN_COLLECTION = "schedule_explain"   # robots/<id>/schedule_explain.json

    def plan_schedule_for(self, device_id, *, now=None) -> tuple:
        """Plan this robot's day -> `(ContentSchedule, explanations, inputs)`.

        The recommender (`moxie_sdk/schedule/`) is pure; this gathers its live inputs:
        the content module's `schedules[]`, stored mentor behaviors, the effective config
        (parent requests, bedtime) and buffered telemetry (context only, not a score).
        Planned on the house's clock (`house_now`: the day, the slots, bedtime) unless a
        `now` is given.
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
        child = getattr(self.child_for(device_id), "nickname", "") or ""
        return plan(device_id, content_schedules=schedules,
                    mentor_behaviors=self.mentor_behaviors(device_id),
                    effective_config=config, telemetry_packets=packets,
                    child_name=child,
                    now=self.house_now(device_id, cfg=config) if now is None else now)

    def build_schedule_for(self, device_id) -> dict:
        """The ContentSchedule served as `CloudQueryResponse.schedule`. The "why this
        activity today" lines are stored beside it (never on the wire) for `GET /schedule`."""
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
        """The parent-facing slice of the planner's inputs (already JSON-safe)."""
        keys = ("device_id", "day", "now", "bucket", "slot_minutes", "child_name",
                "bedtime", "slots", "parent_requests", "ftue_skips", "telemetry",
                "planned")
        out = {k: inputs.get(k) for k in keys if k in inputs}
        history = inputs.get("history") or {}
        out["history"] = {k: history[k] for k in sorted(history)}
        return out

    def schedule_view(self, device_id, *, refresh: bool = False) -> dict:
        """`GET /schedule?device_id=…`: the served day, the "why" behind each entry and
        the planner's inputs. Plans on the spot if nothing is stored yet."""
        stored = self.store.read(device_id, self.SCHEDULE_EXPLAIN_COLLECTION, None)
        if refresh or not isinstance(stored, dict) or not stored.get("explanations"):
            if not self._is_known(device_id) and not self.store.read(
                    device_id, MENTOR_BEHAVIORS_COLLECTION, None):
                return {"ok": False, "error": f"unknown device_id {device_id!r}"}
            sched, explanations, inputs = self.plan_schedule_for(device_id)
            stored = {"day": inputs.get("day"), "planned_at": inputs.get("now"),
                      "schedule": sched, "explanations": explanations,
                      "inputs": self._schedule_inputs_summary(inputs), "served": False}
        else:
            stored = dict(stored)
            stored.setdefault("served", True)
        return {"ok": True, "device_id": device_id, **stored}
