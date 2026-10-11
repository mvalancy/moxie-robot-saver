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

Actions may be declared in `actions` or written inline as tags in `text` (the
`moxie_sdk/actions.py` grammar); inline tags are stripped so they are never spoken.
Declared actions come first.

The service's `text` is a brain's own line and is held to the one policy every brain's line
is under (`content/ext_host.py::model_line`): the tags written whole act, and what is spoken
holds no tag of ours however nested and no catalogue mark this appliance could not have
minted however assembled. Its `markup` is a third party's authored markup and gets the gate
a pack's markup gets (`ext_host.pack_markup`): an HTTP brain is no more trusted than a pack.
A system verb or a system tree the service writes is cut and named in the supervisor log
once per robot and name.
"""
from __future__ import annotations
import json
from ..app import MoxieApp
from ..content.ext_host import model_line, pack_markup, tell_brain_commands
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


def _json_to_reply(d: dict, refused: list | None = None) -> Reply:
    """The service's answer as a `Reply`. `refused`, when given, collects the names of the
    commands the gate cut (for `WebhookApp._tell_refused`)."""
    actions = []
    for a in d.get("actions", []) or []:
        try:
            actions.append(Action(type=ActionType(a["type"]),
                                  module_id=a.get("module_id"),
                                  content_id=a.get("content_id"),
                                  function=a.get("function"), args=a.get("args", {})))
        except Exception:
            pass
    # The text is a brain's own line (`ext_host.model_line`): inline tags written whole act
    # and are never spoken, nothing of ours however nested is spoken, and no mark this
    # appliance could not have minted reaches the robot. The markup is a third party's
    # authored markup and gets the gate a pack's markup gets (`ext_host.pack_markup`: our
    # tags lifted once, text only, so actions are not counted twice; a mark this appliance
    # could have minted is untouched; every other catalogue tag dropped, or the markup
    # dropped whole and the runtime's floor performs the text).
    text, tag_actions, cut = model_line(d.get("text", "") or "", what="the webhook's line")
    markup = d.get("markup")
    if markup:
        markup, _, names = pack_markup(markup)
        cut = cut + names
        markup = markup or None
    if refused is not None:
        refused.extend(cut)
    return Reply(text=text, markup=markup, actions=actions + tag_actions,
                 end_turn=bool(d.get("end_turn", False)))


class WebhookApp(MoxieApp):
    name = "webhook"

    def __init__(self, endpoint: str, timeout: float = 15.0, headers: dict | None = None):
        self._endpoint = endpoint
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        #: `{(device_id, name): cuts}`: the commands the gate cut from the service's lines
        #: and markup, per robot (`ext_host.model_line`, `pack_markup`); the supervisor log
        #: names each once per robot and name (`ext_host.tell_brain_commands`).
        self._commands_refused: dict = {}

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
        refused: list = []
        reply = _json_to_reply(d, refused)
        if refused:
            tell_brain_commands(self._commands_refused,
                                getattr(turn.robot, "device_id", "") or "", refused)
        return reply

    def on_event(self, robot: RobotContext, name: str, payload: dict) -> None:
        # Fire-and-forget: let the external app react to vision/module events.
        self._post(f"event/{name}", {"device_id": robot.device_id,
                                     "event": name, "payload": payload})
