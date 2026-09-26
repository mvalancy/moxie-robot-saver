"""Read the SIM's split classic-script groups (`sim/web/bridge/`, `sim/web/voice/`) the
way the page runs them: the `<script src="{group}/*.js">` parts `sim.html` loads, in its
order, concatenated. The pytest twin of `sim/bridge_harness.mjs::scriptGroup` — a guard
that reads one part would silently miss a table or handler that moved to its sibling."""
import os
import re

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


def script_group(group: str) -> str:
    with open(os.path.join(WEB, "sim.html"), encoding="utf-8") as fh:
        files = re.findall(r'<script src="(%s/[\w-]+\.js)' % re.escape(group), fh.read())
    if not files:
        raise AssertionError(f"sim.html loads no {group}/*.js")
    parts = []
    for f in files:
        with open(os.path.join(WEB, f), encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)
