"""Break each guard that keeps Moxie on the house's clock (the family's `timezone_id`, never
the container's): bedtime, "what time is it", the day plan, the Insights days, the whitelist
and its fallbacks. The row's `-k` selector in `test_house_clock.py` / `test_ext.py` must go
red. The console's half (the Time zone field and the one-click offer) has its own teeth in
`sim/test_console_settings.mjs`. Runner and verdicts: `mutation_runner.py`.

    python3 sim/tools/house_clock_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

CC = "mqtt/moxie_sdk/cloud_config.py"
EH = "mqtt/moxie_sdk/content/ext_host.py"
CA = "mqtt/moxie_sdk/content/content_app.py"
SIG = "mqtt/moxie_sdk/schedule/signals.py"
T = "mqtt/moxie_sdk/telemetry.py"
R_PRESENCE = "mqtt/supervisor/moxie_runtime/presence.py"
R_SCHEDULE = "mqtt/supervisor/moxie_runtime/schedule.py"
R_FLEET = "mqtt/supervisor/moxie_runtime/fleet.py"
R_TRYIT = "mqtt/supervisor/moxie_runtime/tryit.py"
R_TELEMETRY = "mqtt/supervisor/moxie_runtime/telemetry.py"
TESTS = ["sim/tests/test_house_clock.py", "sim/tests/test_ext.py"]

MUTATIONS = [
    ("M1  bedtime is judged on the container's clock again", R_PRESENCE,
     "        return in_bedtime(cfg, self.house_now(device_id, now, cfg=cfg))",
     "        import datetime\n"
     "        return in_bedtime(cfg, datetime.datetime.fromtimestamp(now) if now is not None\n"
     "                          else datetime.datetime.now())",
     "bedtime_is_judged or bedtime_holds or hello_and_the_telehealth"),
    ("M2  clock.local ignores the zone (the container's clock)", EH,
     "    t = datetime.datetime.fromtimestamp(\n"
     "        now, resolve_zone(timezone_id or default_timezone_id()).tz)",
     "    t = datetime.datetime.fromtimestamp(now)",
     "what_time_is_it or shipped_clock"),
    ("M3  a typo is stored and pushed, as before", CC,
     "        out[\"timezone_id\"] = check_timezone(raw[\"timezone_id\"])",
     "        out[\"timezone_id\"] = str(raw[\"timezone_id\"])",
     "typo_in_the_zone"),
    ("M4  the day plan is planned on the container's clock", R_SCHEDULE,
     "                    now=self.house_now(device_id, cfg=config) if now is None else now)",
     "                    now=now)",
     "day_plan_is_keyed or plan_keeps_bedtime"),
    ("M5  a push no longer tells the content app the zone it named", R_FLEET,
     "            robot.extra[\"timezone_id\"] = cfg.get(\"timezone_id\")",
     "            pass",
     "tells_the_zone_the_robot_was_told or sleeping_robot"),
    ("M6  the content app reads no zone off the robot", CA,
     "        zone = (getattr(turn.robot, \"extra\", None) or {}).get(\"timezone_id\")  # its push's",
     "        zone = None",
     "tells_the_zone_the_robot_was_told or shipped_clock"),
    ("M7  Try it tells the default zone's time, not the house's", R_TRYIT,
     "                             extra={\"timezone_id\": self.house_zone(device_id).name})",
     "                             extra={})",
     "try_it_card"),
    ("M8  an Insights day is the container's day", T,
     "    if tz is not None:\n        import datetime\n"
     "        return datetime.datetime.fromtimestamp(ts, tz).strftime(\"%Y-%m-%d\")",
     "    if False:\n        import datetime\n"
     "        return datetime.datetime.fromtimestamp(ts, tz).strftime(\"%Y-%m-%d\")",
     "insights_files"),
    ("M9  a stored packet is rolled up on the container's day", R_TELEMETRY,
     "telemetry_seam.reconcile_rollup(stored, ring, tz=tz), row, tz=tz))",
     "telemetry_seam.reconcile_rollup(stored, ring, tz=tz), row))",
     "insights_files"),
    ("M10 a lost roll-up is repaired on the container's day", R_TELEMETRY,
     "        rollup = telemetry_seam.reconcile_rollup(stored, ring,\n"
     "                                                 tz=self.house_zone(device_id).tz)",
     "        rollup = telemetry_seam.reconcile_rollup(stored, ring)",
     "insights_files"),
    ("M11 the Insights history ends on the container's today", R_TELEMETRY,
     "        return self.house_now(device_id).date().isoformat()",
     "        return None",
     "insights_files"),
    ("M12 a zone this server cannot read is said on every read", R_PRESENCE,
     "            if (device_id, str(name)) not in noted:",
     "            if True:",
     "labelled_utc_once"),
    ("M13 a zone this server cannot read raises instead of running on UTC", CC,
     "        except Exception:                    # noqa: BLE001 — unknown key, no database, …\n"
     "            pass\n",
     "        except Exception:                    # noqa: BLE001 — unknown key, no database, …\n"
     "            raise\n",
     "labelled_utc_once or no_tz_database"),
    ("M14 MOXIE_TIMEZONE no longer reaches the layers", R_FLEET,
     "        return merge_config_layers(env_timezone_layer(), self.fleet_config(),",
     "        return merge_config_layers({}, self.fleet_config(),",
     "moxie_timezone_is_the_house_zone"),
    ("M15 a parent's request is read on the container's clock", SIG,
     "            when = datetime.datetime.fromtimestamp(float(raw), now.tzinfo)",
     "            when = datetime.datetime.fromtimestamp(float(raw))",
     "plan_keeps_bedtime"),
    ("M16 a zone typed in the wrong case is refused", CC,
     "        if _known_folded().get(name.lower()):",
     "        if False:",
     "stored_and_pushed_as_given"),
    ("M17 with no tz database any text passes for a zone", CC,
     "    elif _ZONE_NAME.match(name):",
     "    elif name:",
     "no_tz_database"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS, r[4], "-x"),
                               baseline=[pytest(TESTS)]))
