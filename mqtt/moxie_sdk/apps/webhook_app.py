"""
WebhookApp — bridge Moxie to an EXTERNAL AI/service over HTTP.

This is how a game engine, an agent, or any external system drives Moxie as an
avatar WITHOUT its code living in this repo. The runtime turns each Moxie turn into
a small JSON POST; your service returns the reply. Clean interface, language-agnostic.

Request  POST <endpoint>  (JSON):
    { "device_id","speech","command","child":{...},"history":[...],"input_vars":{...} }
Response (JSON):
    { "text": "...", "markup": "...?", "end_turn": false,
      "actions": [ {"type":"launch","module_id":"..."} ] }

Point `endpoint` at your service (e.g. a game server) and it *becomes* Moxie's brain.
"""
from __future__ import annotations
import json
from ..app import MoxieApp
from ..types import Turn, Reply, Action, ActionType, RobotContext


def _turn_to_json(turn: Turn) -> dict:
    r = turn.robot
    return {
        "device_id": r.device_id, "speech": turn.speech, "command": turn.command,
        "module_id": r.module_id, "content_id": r.content_id,
        "input_vars": turn.input_vars, "history": turn.history,
        "child": {"nickname": r.child.nickname, "pronouns": r.child.pronouns,
                  "birthday_iso": r.child.birthday_iso, "notes": r.child.notes},
    }


def _json_to_reply(d: dict) -> Reply:
    actions = []
    for a in d.get("actions", []) or []:
        try:
            actions.append(Action(type=ActionType(a["type"]),
                                  module_id=a.get("module_id"),
                                  content_id=a.get("content_id"),
                                  function=a.get("function"), args=a.get("args", {})))
        except Exception:
            pass
    return Reply(text=d.get("text", ""), markup=d.get("markup"),
                 actions=actions, end_turn=bool(d.get("end_turn", False)))


class WebhookApp(MoxieApp):
    name = "webhook"

    def __init__(self, endpoint: str, timeout: float = 15.0, headers: dict | None = None):
        self._endpoint = endpoint
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json", **(headers or {})}

    def _post(self, path_hint: str, body: dict) -> dict | None:
        import urllib.request
        req = urllib.request.Request(
            self._endpoint, data=json.dumps(body).encode(),
            headers={**self._headers, "X-Moxie-Event": path_hint}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    def respond(self, turn: Turn) -> Reply:
        d = self._post("turn", _turn_to_json(turn))
        if not d:
            return Reply(text="One moment… I'm having trouble reaching my imagination.")
        return _json_to_reply(d)

    def on_event(self, robot: RobotContext, name: str, payload: dict) -> None:
        # Fire-and-forget: let the external app react to vision/module events.
        self._post(f"event/{name}", {"device_id": robot.device_id,
                                     "event": name, "payload": payload})
