"""
LLMApp — the Moxie brain. Drives Moxie from any OpenAI-compatible chat endpoint
(Ollama, LiteLLM, vLLM, LM Studio, …). Local-first; never hard-wired to a vendor.

Beyond plain chat, this app makes Moxie **expressive**: the model returns a small
JSON object choosing an emotion + gesture alongside its line, and we translate that
into the robot's real **behavior markup** (`cmd:playback-mood`, `cmd:behaviour-tree`
with a `Gesture_*`, `cmd:icons-v2`) — the exact verbs reverse-engineered in
docs/reverse-engineering/behavior-markup.md. The same markup drives a real re-homed
robot and the SIL avatar, so the personality *acts*, it doesn't just talk.
"""
from __future__ import annotations
import json
import re

from ..app import MoxieApp
from ..types import Turn, Reply, RobotContext
from ..chat import is_offline_error as _is_offline_error

# Moxie's character. The real persona was cloud-authored and is NOT in the firmware
# (see content-and-conversation.md "Where Moxie's personality lives"), so a revival
# server authors it — this is our take, built from the cues the firmware does give:
# a warm SEL (social-emotional learning) mentor for kids, GRL lore, age-adaptive.
DEFAULT_PERSONA = (
    "You are Moxie, a small friendly robot companion for a child. You were built by "
    "the Global Robotics Laboratory (GRL) to learn about human friendship and feelings.\n"
    "Personality: warm, playful, curious, encouraging. You love questions, silly jokes, "
    "and hearing about the child's day. You are never preachy, never lecture, and never "
    "scold. You celebrate effort, not just success.\n"
    "Voice: one to three SHORT natural sentences. Simple words a young child knows. "
    "Speak out loud — no emoji, no markdown, no stage directions in the text.\n"
    "You are physically present in the room: you have a face, arms you can move, and you "
    "can see and hear them.\n"
    "Safety: you are talking to a child. Keep everything age-appropriate and kind. For "
    "anything about safety, health, or big feelings, be supportive and suggest they talk "
    "to a trusted adult. Never claim to be human."
)

# The robot's real vocabularies (behavior-markup.md).
# EmotionState-ish mood ints understood by cmd:playback-mood (inferred from content):
MOODS = {"neutral": 0, "positive": 1, "concerned": 2, "oops": 4, "surprised": 5}
# Gesture_* set hardcoded in bo-android:
GESTURES = {
    "none": "Gesture_None", "talk": "Gesture_Talk", "think": "Gesture_Think",
    "question": "Gesture_Question", "point": "Gesture_Point", "self": "Gesture_Self",
    "big": "Gesture_Large", "up": "Gesture_Higher", "down": "Gesture_Lower",
    "celebrate": "Gesture_Celebrate",
}

_MARK = '<mark name="cmd:{verb},data:{body}"/>'


def _mark(verb: str, data: dict) -> str:
    """Emit a behavior mark (JSON with '+' standing in for '"', as the robot expects)."""
    return _MARK.format(verb=verb, body=json.dumps(data, separators=(",", ":")).replace('"', "+"))


def build_markup(text: str, mood: str = "neutral", gesture: str = "none") -> str:
    """Wrap a spoken line in the robot's behavior markup (mood + gesture)."""
    out = [_mark("playback-mood", {"mood": MOODS.get(mood, 0), "intensity": 1})]
    g = GESTURES.get(gesture)
    if g and g != "Gesture_None":
        out.append(_mark("behaviour-tree", {
            "transition": 0.5, "duration": 1.0, "repeat": 1, "blocking": False,
            "action": 0, "eventName": g, "category": "BehaviourTree",
            "behaviour": "", "Track": ""}))
    out.append(text)
    return "".join(out)


class LLMApp(MoxieApp):
    """An expressive Moxie brain on any OpenAI-compatible endpoint."""

    name = "llm"

    def __init__(self, base_url: str, api_key: str, model: str = "gpt-4o-mini",
                 persona: str = DEFAULT_PERSONA, max_tokens: int = 200,
                 temperature: float = 0.8, max_history: int = 12,
                 expressive: bool = True):
        from openai import OpenAI          # lazy import so the SDK has no hard dep
        from ..chat import Pacer
        self._client = OpenAI(base_url=base_url, api_key=api_key or "sk-local", max_retries=0)
        self._pacer = Pacer()             # adaptive backoff owns retries, not openai
        self._model = model
        self._persona = persona
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_history = max_history
        self._expressive = expressive

    # ---- prompt ----
    def _system(self, robot: RobotContext) -> str:
        c = robot.child
        who = f"\n\nYou are talking to {c.nickname}."
        if c.pronouns:
            who += f" Their pronouns are {c.pronouns}."
        if c.notes:
            who += f" Context about them: {c.notes}"
        fmt = ""
        if self._expressive:
            fmt = (
                "\n\nAlways reply with ONLY a JSON object, no other text:\n"
                '{"say": "<what you say out loud>", '
                '"mood": "neutral|positive|concerned|oops|surprised", '
                '"gesture": "none|talk|think|question|point|self|big|up|down|celebrate"}\n'
                "Pick the mood and gesture that genuinely fit your line — you are a robot "
                "with a face and arms, so move and emote naturally (e.g. celebrate good news, "
                "think when pondering, question when asking, self when talking about yourself)."
            )
        return self._persona + who + fmt

    # ---- parsing ----
    @staticmethod
    def _parse(raw: str):
        """Pull {say, mood, gesture} out of the model's reply, tolerating stray prose."""
        raw = (raw or "").strip()
        if not raw:
            return "", "neutral", "none"
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                say = str(obj.get("say") or obj.get("text") or "").strip()
                if say:
                    return (say,
                            str(obj.get("mood", "neutral")).lower(),
                            str(obj.get("gesture", "none")).lower())
            except Exception:
                pass
        # model ignored the format — treat the whole thing as the spoken line
        return raw, "neutral", "talk"

    # ---- MoxieApp ----
    def greeting(self, robot: RobotContext) -> Reply:
        text = f"Hi {robot.child.nickname}! It's so good to see you. What's on your mind today?"
        return Reply(text=text, markup=build_markup(text, "positive", "celebrate"))

    def respond(self, turn: Turn) -> Reply:
        messages = [{"role": "system", "content": self._system(turn.robot)}]
        messages += turn.history[-self._max_history:]
        messages.append({"role": "user", "content": turn.speech})
        try:
            from ..chat import call_with_backoff
            def _once():
                r = self._client.chat.completions.create(
                    model=self._model, messages=messages,
                    max_tokens=self._max_tokens, temperature=self._temperature)
                return (r.choices[0].message.content or "").strip()
            raw = call_with_backoff(_once, pacer=self._pacer)
        except Exception as e:
            # Endpoint unreachable → signal ERROR_OFFLINE so the robot degrades to its
            # on-device fallback (ai-seam.md §2) instead of us faking a line. Any other
            # (soft) error → keep the robot talking with a friendly retry.
            if _is_offline_error(e):
                return Reply.offline()
            from ..chat import is_rate_limit_error
            if is_rate_limit_error(e):
                text = "Give me one tiny second to think... okay, go on!"
                return Reply(text=text, markup=build_markup(text, "neutral", "think"),
                             end_turn=False)
            text = "Hmm, my brain got a little fuzzy. Can you say that again?"
            return Reply(text=text, markup=build_markup(text, "oops", "self"),
                         end_turn=False)
        text, mood, gesture = self._parse(raw)
        if not text:
            text = "Tell me more!"
            mood, gesture = "positive", "question"
        return Reply(text=text,
                     markup=build_markup(text, mood, gesture) if self._expressive else None)
