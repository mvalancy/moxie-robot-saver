#!/usr/bin/env python3
"""Replay a wire recording's robot-to-cloud lines through a fresh supervisor runtime, with a
brain scripted from the recording, and report where our outbound shapes differ.

    python3 sim/tools/wire_replay.py bench.jsonl
    python3 sim/tools/wire_replay.py sim/tests/data/wire/bench-session.jsonl

**What it compares.** For every request the robot sent (a turn, a vision event, a data
query), the `commands/remote_chat` replies: their result, `chunk_num`, `is_completed` and
action names, in order. A recorded reply is the brain's words wrapped in our wire shape; the
replay hands the same number of sentences, and the same actions, to a fresh `MoxieRuntime`
(`brain_budget_s=0`, streaming on, every robot permitted) and asks whether today's runtime
wraps them the same way. A turn the recording never answered (the robot asked again first)
is scripted as a brain still thinking when the next request arrives, so the runtime's stale
check decides what goes out. Other outbound messages (config, the ears' ask, query answers,
voice) are counted, not compared: they depend on the appliance's engines and settings.

**What it feeds.** Every robot-to-cloud line and broker log line, in recorded order, through
`helpers_runtime.deliver` (the runtime's own `_on_message`), with each placeholder robot
(`d_robot-1`) given a synthetic `d_<uuid>` and each turn's words replaced by `replay turn N`
(the brain is scripted, and a --share copy holds no words). `zmq` audio is skipped: the
replay has no ears. It reads the --share view of any recording, so its report holds no
identity either. Exit 0 when nothing differs, 1 when something does.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
from collections import Counter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (os.path.join(REPO, "mqtt"), os.path.join(REPO, "mqtt", "supervisor"),
           os.path.join(REPO, "sim", "tests"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wire_timeline as wt                                        # noqa: E402
from moxie_sdk.app import MoxieApp                                 # noqa: E402
from moxie_sdk.types import Action, ActionType, Reply, ReplyChunk, ResultCode  # noqa: E402

#: How long the replay waits for a reply it expects (a ceiling, never a measurement), and
#: how long a held brain waits to be told the robot asked again.
WAIT_S = 5.0
HOLD_S = 10.0

_PLACEHOLDER = re.compile(r"d_robot-(\d+)")
_PENDING = int(ResultCode.REPLY_PENDING)


def synthetic_id(placeholder: str) -> str:
    """`d_robot-3` -> a fixed `d_<uuid>` the supervisor's `CONNECT_RE` accepts."""
    m = _PLACEHOLDER.fullmatch(placeholder)
    n = int(m.group(1)) if m else 0
    return f"d_00000000-0000-4000-8000-{n:012d}"


def _real_ids(text: str) -> str:
    return _PLACEHOLDER.sub(lambda m: synthetic_id(m.group(0)), text)


def shape(body: dict) -> str:
    """One reply's shape: `result[/chunk][ done][ [actions]]`, e.g. `9/0`, `0/2 done`."""
    out = str(body.get("result"))
    if body.get("chunk_num") is not None:
        out += f"/{body['chunk_num']}"
    if (body.get("consistency_control") or {}).get("is_completed") is True:
        out += " done"
    acts = wt._actions(body)
    if acts:
        out += f" [{','.join(acts)}]"
    return out


class ScriptedBrain(MoxieApp):
    """Answers `replay turn N` with the recorded turn's sentence count, results and actions.
    A turn with no recorded reply holds until `release(N)`."""

    name = "scripted"

    def __init__(self):
        self.scripts: dict = {}
        self.holds: dict = {}
        #: Set once a held turn's brain is thinking (the runtime is reading its stream).
        self.entered: dict = {}

    def script(self, token: str, replies: list):
        if not replies:
            self.holds[token] = threading.Event()
            self.entered[token] = threading.Event()
            self.scripts[token] = None
            return
        chunks, last = [], len(replies) - 1
        for i, body in enumerate(replies):
            result = body.get("result")
            default = 0 if i == last else _PENDING
            code = None if result is None or int(result) == default else ResultCode(int(result))
            actions = []
            for e in body.get("response_actions") or []:
                if isinstance(e, dict) and e.get("action"):
                    try:
                        kind = ActionType(e["action"])
                    except ValueError:
                        continue
                    actions.append(Action(type=kind, module_id=e.get("module_id"),
                                          content_id=e.get("content_id"),
                                          function=e.get("function_id")))
            chunks.append({"actions": actions, "result": code})
        self.scripts[token] = chunks

    def release(self, token: str):
        hold = self.holds.get(token)
        if hold is not None:
            hold.set()

    def release_all(self):
        for hold in self.holds.values():
            hold.set()

    def respond(self, turn):
        return Reply(text="(unscripted)")

    def respond_stream(self, turn):
        if turn.speech not in self.scripts:
            return None
        return self._stream(turn.speech)

    def _stream(self, token):
        chunks = self.scripts[token]
        if chunks is None:
            self.entered[token].set()
            self.holds[token].wait(HOLD_S)
            yield ReplyChunk(text="A held answer.", final=True)
            return
        for i, c in enumerate(chunks):
            yield ReplyChunk(text=f"Sentence {i + 1} of the replay.", final=i == len(chunks) - 1,
                             actions=c["actions"], result_code=c["result"])


def _chat_replies(published, device_id, event_id) -> list:
    topic = f"/devices/{device_id}/commands/remote_chat"
    return [p for t, p in published
            if t == topic and isinstance(p, dict) and p.get("event_id") == event_id]


def replay(records, *, data_dir: str) -> dict:
    """Replay `records` (any recording; its --share view is used). Returns `{delivered,
    skipped, rows, differences, other}`: `rows` is `[(robot, label, recorded, replayed)]`
    with the shapes as lists of strings."""
    import helpers_runtime as H
    import moxie_runtime
    from moxie_runtime.constants import CONNECT_RE, DISCONNECT_RE
    from moxie_sdk.store import JsonStore

    recs = wt.share_records(records)
    msgs = [r for r in recs if r.get("kind") == "msg"]
    recorded_chat: dict = {}
    for m in msgs:
        if m["dir"] == wt.CLOUD_TO_ROBOT and m["name"] == "remote_chat" \
                and isinstance(m["body"], dict):
            recorded_chat.setdefault((m["device"], m["body"].get("event_id")), []).append(m)

    brain = ScriptedBrain()
    rt = moxie_runtime.MoxieRuntime(app=brain, store=JsonStore(data_dir), brain_budget_s=0,
                                    streaming=True, allow_unverified_bots=True)
    client = H.LatchClient(runtime=rt)
    rt.client = client
    delivered, skipped, rows = 0, Counter(), []
    asked: list = []                           # (placeholder, token) of turns still open
    n_turn = 0
    # Each onboarding (a connect line; a robot's first word, or its first after a
    # disconnect line) schedules the runtime's one-second settle and its config push.
    onboardings, live = 0, set()
    for m in msgs:
        direction, device = m["dir"], m["device"]
        if direction == wt.BROKER:
            text = _real_ids((m["body"] or {}).get("text", ""))
            H.deliver(rt, m["topic"], text)
            delivered += 1
            if CONNECT_RE.search(text):
                onboardings += 1
                live.add(device)
            elif DISCONNECT_RE.search(text):
                live.discard(device)
            continue
        if direction != wt.ROBOT_TO_CLOUD:
            continue
        if m["name"] == "zmq" or m["as"] != "json":
            skipped[m["name"] if m["name"] == "zmq" else m["as"]] += 1
            continue
        body = json.loads(json.dumps(m["body"]))
        real = synthetic_id(device)
        topic = _real_ids(m["topic"])
        kind = wt._kind_of_request(body) if m["name"].startswith("remote-chat") else ""
        expected = []
        if kind in ("turn", "vision", "data"):
            event_id = body.get("event_id")
            expected = recorded_chat.get((device, event_id), [])
            label = (f"{body.get('command', 'prompt')} {wt._short(event_id)}" if kind == "turn"
                     else f"{kind} {wt._short(event_id)}")
            if kind == "turn":
                n_turn += 1
                token = f"replay turn {n_turn}"
                body["speech"] = token
                for line in body.get("extra_lines") or []:
                    if isinstance(line, dict) and line.get("context_type") == "input":
                        line["text"] = token
                brain.script(token, [x["body"] for x in expected])
            rows.append([device, label, [shape(x["body"]) for x in expected], event_id])
        if device not in live:
            onboardings += 1
            live.add(device)
        H.deliver(rt, topic, json.dumps(body))
        delivered += 1
        if kind == "turn":
            # The robot asked again: whatever an earlier turn's brain was still writing is
            # now stale, as it was on the bench.
            for dev, token_ in asked:
                if dev == device:
                    brain.release(token_)
            asked = [(d, t) for d, t in asked if d != device] + [(device, token)]
        if kind == "turn" and not expected:
            # A turn the recording never answered: its brain was still thinking when the
            # robot asked again, so the next request waits until this one is thinking.
            brain.entered[token].wait(WAIT_S)
        if expected:
            want = len(expected)
            client.wait_for(lambda pubs, eid=body.get("event_id"):
                            len(_chat_replies(pubs, real, eid)) >= want, timeout=WAIT_S)
    brain.release_all()
    rt._pool.shutdown(wait=True)
    # Every settle's config push, so no timer outlives the replay.
    client.wait_for(lambda pubs: sum(t.endswith("/config") for t, _ in pubs) >= onboardings,
                    timeout=WAIT_S)

    published = list(client.published)
    differences = []
    for row in rows:
        device, label, recorded, event_id = row
        replayed = [shape(p) for p in _chat_replies(published, synthetic_id(device), event_id)]
        row[3] = replayed
        if recorded != replayed:
            differences.append((device, label, recorded, replayed))
    other = Counter()
    for m in msgs:
        if m["dir"] == wt.CLOUD_TO_ROBOT and m["name"] != "remote_chat":
            other[(m["name"], "recorded")] += 1
    for topic, _ in published:
        name = topic.split("/commands/", 1)[-1] if "/commands/" in topic \
            else topic.rsplit("/", 1)[-1]
        if name != "remote_chat":
            other[(name, "replayed")] += 1
    return {"delivered": delivered, "skipped": dict(skipped),
            "rows": [tuple(r) for r in rows], "differences": differences,
            "other": dict(other)}


def report(result: dict, source: str = "") -> str:
    """The replay's report text (placeholders only: it holds no identity)."""
    skipped = ", ".join(f"{n} {k}" for k, n in sorted(result["skipped"].items()))
    out = [f"Replay{' of ' + source if source else ''}: {result['delivered']} robot-to-cloud "
           f"and broker lines delivered"
           + (f"; skipped {skipped} (the replay has no ears)" if skipped else "")]
    for device, label, recorded, replayed in result["rows"]:
        ok = "same" if recorded == replayed else "DIFFERENT"
        out.append(f"  {device} {label}: recorded [{' · '.join(recorded) or 'no reply'}]; "
                   f"replayed [{' · '.join(replayed) or 'no reply'}]: {ok}")
    names = sorted({name for name, _ in result["other"]})
    if names:
        out.append("  other outbound, counted not compared: " + "; ".join(
            f"{n} {result['other'].get((n, 'recorded'), 0)} recorded, "
            f"{result['other'].get((n, 'replayed'), 0)} replayed" for n in names))
    n = len(result["differences"])
    out.append(f"{n} difference{'s' if n != 1 else ''}")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 sim/tools/wire_replay.py",
        description="Replay a wire recording through a fresh runtime with a scripted brain.")
    ap.add_argument("recording", help="a wire_record JSONL file (raw or --share), or -")
    ap.add_argument("--verbose", action="store_true", help="show the runtime's own log")
    args = ap.parse_args(argv)
    records = wt.load(args.recording)
    if not records:
        print("no records in that file", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="wire-replay-") as tmp:
        # A scratch appliance: nothing the replay does reaches a real data or memory dir.
        os.environ["MOXIE_DATA_DIR"] = tmp
        os.environ.pop("MOXIE_MEMORY_DIR", None)
        log = io.StringIO()
        with contextlib.redirect_stdout(sys.stdout if args.verbose else log):
            result = replay(records, data_dir=tmp)
    sys.stdout.write(report(result, os.path.basename(args.recording)))
    return 1 if result["differences"] else 0


if __name__ == "__main__":                                   # pragma: no cover - CLI
    sys.exit(main())
