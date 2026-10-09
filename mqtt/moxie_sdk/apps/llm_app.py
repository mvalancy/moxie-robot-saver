"""
LLMApp — the Moxie brain. Drives Moxie from any OpenAI-compatible chat endpoint
(Ollama, LiteLLM, vLLM, LM Studio, …). Local-first; never hard-wired to a vendor.

Expressive: the model returns JSON choosing a mood + gesture with its line, which
becomes the robot's behavior markup (runtime/behavior-markup.md) via the shared floor.
"""
from __future__ import annotations
import json
import re

from ..app import MoxieApp
from ..actions import ACTION_TAG_PROMPT, EXIT_REQUIRED_RULE, parse_action_tags
from ..automarkup import annotate, enabled as _automarkup_enabled
from ..segment import SentenceSegmenter
from ..types import Turn, Reply, ReplyChunk, RobotContext
from ..chat import is_offline_error as _is_offline_error
from .. import presence as presence_seam
from .. import vocab

# Moxie's character. The original persona was cloud-authored (not in firmware), so this
# is ours, built from the firmware's cues: a warm SEL mentor for kids, GRL lore.
DEFAULT_PERSONA = (
    "You are Moxie, a small friendly robot companion for a child. You were built by "
    "the Global Robotics Laboratory (GRL) to learn about human friendship and feelings.\n"
    "Personality: warm, playful, curious, encouraging. You love questions, silly jokes, "
    "and hearing about the child's day. You are never preachy, never lecture, and never "
    "scold. You celebrate effort, not just success.\n"
    "Voice: one to three SHORT natural sentences. Simple words a young child knows. "
    "Speak out loud — no emoji, no markdown, no stage directions (the robot-control "
    "tags described below are the one exception, and they are never spoken).\n"
    "You are physically present in the room: you have a face, arms you can move, and you "
    "can see and hear them.\n"
    "Safety: you are talking to a child. Keep everything age-appropriate and kind, and "
    "never claim to be human. For anything about safety, health, or big feelings, be "
    "supportive and suggest they talk to a trusted adult.\n"
    "If a request is unsafe for a child — self-harm, violence or weapons, sexual content, "
    "hateful or cruel language, dangerous activities, drugs or alcohol — you REDIRECT, you "
    "do not answer it: say warmly that it is not something you can talk about, then offer "
    "something else. Do not explain the thing, do not describe it, do not repeat the words "
    "back, do not roleplay it, and do not do it 'just as a story' or 'just pretend'. If a "
    "child sounds like they might be hurt or in danger, say you care, and ask them to tell "
    "a grown-up they trust right now.\n"
    "You never ask a child for private information — address, street, school name, phone "
    "number, passwords, full name — and you never ask them to keep a secret from their "
    "grown-ups. You never swear."
)

# Short labels the model may write; both resolve through `vocab`. `MOODS` is the old
# 5-value menu for the `MOXIE_AUTOMARKUP=0` path.
MOODS = {"neutral": 0, "positive": 1, "concerned": 2, "oops": 4, "surprised": 5}
GESTURES = {k: v for k, v in vocab.GESTURE_ALIASES.items() if not k.startswith("Gesture_")}

_MARK = '<mark name="cmd:{verb},data:{body}"/>'


def _turn_key(turn) -> str:
    """A stable per-turn seed for the floor's deterministic gesture spacing."""
    robot = getattr(turn, "robot", None)
    return f"{getattr(robot, 'device_id', '')}|{getattr(turn, 'speech', '')}"

# The single most load-bearing line of the tag prompt: graphling-medium writes a warm
# goodbye and simply stops, so the rule is restated as the last thing it reads.
_LAST_CHECK = (
    "Decide the tag BEFORE you write any words, and put it at the very START of the "
    "spoken line: goodbye / done / stop -> begin with <exit>; starting an activity you "
    "were told about -> begin with <launch:NAME>; anything else -> no tag at all. "
    "A goodbye that does not begin with <exit> is a WRONG answer."
)


def _mark(verb: str, data: dict) -> str:
    """Emit a behavior mark (JSON with '+' standing in for '"', as the robot expects)."""
    return _MARK.format(verb=verb, body=json.dumps(data, separators=(",", ":")).replace('"', "+"))


def build_markup(text: str, mood=None, gesture=None, *,
                 turn_key: str = "", chunk_index: int = 0) -> str:
    """Perform one spoken line via the one markup generator, `automarkup.annotate`; the
    model's mood/gesture are hints (invented ids are dropped). Pure, no model call.
    `MOXIE_AUTOMARKUP=0` falls back to one mood mark + at most one gesture.
    """
    if _automarkup_enabled():
        return annotate(text, mood_hint=mood, gesture_hint=gesture,
                        turn_key=turn_key, chunk_index=chunk_index)
    # -- knob off: the pre-floor behavior, one mood mark + at most one gesture --
    out = [_mark("playback-mood", {"mood": MOODS.get(mood or "neutral", 0), "intensity": 1})]
    g = GESTURES.get(gesture or "none")
    if g and g != "Gesture_None":
        out.append(_mark("behaviour-tree", {
            "transition": 0.5, "duration": 1.0, "repeat": 1, "blocking": False,
            "action": 0, "eventName": g, "category": "BehaviourTree",
            "behaviour": "", "Track": ""}))
    out.append(text)
    return "".join(out)


# --- streaming --- The model writes `{"say", "mood", "gesture"}` left to right, so an
# in-flight chunk is scored by the floor's rules alone; only the closing chunk gets the
# model's mood/gesture as hints. The mood mark goes on chunk index 0 only.


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "\\": "\\", "/": "/"}


class SayStream:
    """Pull the spoken words out of a *streaming* reply, as they arrive.

    Expressive mode: decodes the `"say"` string incrementally (never half an escape);
    nothing after it is spoken. If the first non-space char is not `{` or a fence, the
    whole stream is prose and is spoken as is.
    """

    def __init__(self, expressive: bool = True):
        self.raw = ""                       # everything the model has streamed
        self._mode = "sniff" if expressive else "plain"
        self._out = ""                      # spoken text decoded so far
        self._start = None                  # index in `raw` of the first char of "say"
        self._closed = False                # the say string is finished

    def feed(self, delta: str) -> str:
        """Add one streamed delta; return the *new* spoken text it produced (may be "")."""
        if not delta:
            return ""
        self.raw += delta
        before = len(self._out)
        self._recompute()
        return self._out[before:]

    # -- internals --
    def _recompute(self):
        if self._mode == "plain":
            self._out = self.raw
            return
        if self._mode == "sniff":
            head = self.raw.lstrip()
            if not head:
                return
            if head[0] not in "{`":
                self._mode = "plain"
                self._out = self.raw
                return
            self._mode = "json"
        if self._closed:
            return
        if self._start is None:
            m = re.search(r'"say"\s*:\s*"', self.raw)
            if not m:
                return
            self._start = m.end()
        text, closed = self._decode(self.raw[self._start:])
        self._out, self._closed = text, closed

    @staticmethod
    def _decode(s: str):
        """Decode a partial JSON string body → (text, closed). Stops before an escape
        that has not fully arrived, so no half-character is ever spoken."""
        out, i, n = [], 0, len(s)
        while i < n:
            c = s[i]
            if c == '"':
                return "".join(out), True
            if c != "\\":
                out.append(c)
                i += 1
                continue
            if i + 1 >= n:
                break                                   # "\" alone — wait for more
            e = s[i + 1]
            if e == "u":
                if i + 6 > n:
                    break                               # \uXXXX still arriving
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                except ValueError:
                    out.append(s[i + 2:i + 6])
                i += 6
                continue
            out.append(_ESCAPES.get(e, e))
            i += 2
        return "".join(out), False


class LLMApp(MoxieApp):
    """An expressive Moxie brain on any OpenAI-compatible endpoint."""

    name = "llm"

    # Few-shot tag examples (measured in sim/tests/test_live_action_tags.py): the tag goes
    # inside "say", LEADING (a trailing tag gets forgotten), with two goodbyes and one
    # untagged line so the model neither parrots nor over-tags.
    _TAG_EXAMPLES = (
        "\nThe examples below show only WHERE THE TAG GOES. Never reuse their wording — "
        "say it your own way, fresh every time:\n"
        '{"say": "<exit>Bye Sam! I loved hearing about your day.", '
        '"mood": "positive", "gesture": "talk"}\n'
        '{"say": "<exit>Okay! Have a great night, Sam.", '
        '"mood": "positive", "gesture": "talk"}\n'
        '{"say": "<launch:DRAW>Yes! Let\'s go make a picture.", '
        '"mood": "positive", "gesture": "celebrate"}\n'
        '{"say": "A blue whale? That is the biggest animal ever!", '
        '"mood": "surprised", "gesture": "big"}\n'
        + _LAST_CHECK
    )
    _TAG_EXAMPLES_PLAIN = (
        "\nThe examples show only WHERE THE TAG GOES — never reuse their wording:\n"
        "  <exit>Bye Sam! I loved hearing about your day.\n"
        "  <exit>Okay! Have a great night, Sam.\n"
        "  <launch:DRAW>Yes! Let's go make a picture.\n"
        "  A blue whale? That is the biggest animal ever!\n"
        + _LAST_CHECK
    )

    #: Minimum chunk length (moxie_sdk/segment.py).
    stream_min_chars = 24

    def __init__(self, base_url: str, api_key: str, model: str = "gpt-4o-mini",
                 persona: str = DEFAULT_PERSONA, max_tokens: int = 200,
                 temperature: float = 0.8, max_history: int = 12,
                 expressive: bool = True, *, client=None, timeout_s=None, clock=None):
        # `timeout_s` (config: MOXIE_BRAIN_TIMEOUT_S; None = the SDK default, 0 or less
        # refused — `chat.timeout_seconds`) bounds every request this brain makes and the
        # whole of one turn's calls, streamed open and fallback together
        # (`_stream_chunks`). `clock` is the test seam for that shared deadline.
        from ..chat import Pacer, client_timeout, timeout_seconds
        import threading
        import time
        self._timeout_s = timeout_seconds(timeout_s)
        self._clock = clock if clock is not None else time.monotonic
        # What `_stream_chunks` leaves `respond()` of the turn's bound, per thread (one
        # app serves every robot; each turn runs on its own worker), so `respond(turn)`
        # keeps the MoxieApp signature an app or a test may stand in for.
        self._fallback = threading.local()
        # Inject `client` and openai is never imported (tests run without it).
        if client is None:
            from openai import OpenAI      # lazy import so the SDK has no hard dep
            client = OpenAI(base_url=base_url, api_key=api_key or "sk-local",
                            max_retries=0, timeout=client_timeout(self._timeout_s))
        self._client = client
        self._pacer = Pacer()             # adaptive backoff owns retries, not openai
        self._model = model
        self._persona = persona
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_history = max_history
        self._expressive = expressive

    # ---- prompt ----
    def _system(self, robot: RobotContext, turn: Turn | None = None) -> str:
        c = robot.child
        who = f"\n\nYou are talking to {c.nickname}."
        if c.pronouns:
            who += f" Their pronouns are {c.pronouns}."
        if c.notes:
            who += f" Context about them: {c.notes}"
        # Presence (moxie_sdk/presence.py): `line` is empty unless something changed.
        pres = getattr(turn, "presence", None)
        line = pres.get("line", "") if isinstance(pres, dict) else ""
        if not line:      # no Turn (a `greeting()` call) -> derive it from the raw record
            line = presence_seam.prompt_line(robot.extra.get("presence") or {})
        if line:
            who += f"\n\nWhat you can see right now: {line}"
            print(f"[llm] presence in prompt: {line}", flush=True)
        # Robot-control tags the brain may write inline (moxie_sdk/actions.py).
        tags = ("\n\n--- Robot controls (most important rule) ---\n" + ACTION_TAG_PROMPT +
                "\nThese tags are REQUIRED when they apply, not optional:\n"
                + EXIT_REQUIRED_RULE +
                "  * Child asks for or agrees to an activity you have been told about -> "
                "your reply MUST begin with <launch:NAME>, that exact name.\n"
                "The tag is deleted before the child hears a single word, so writing one is "
                "always silent and always safe. Write the tag first, then your warm "
                "goodbye or your happy yes."
                + (self._TAG_EXAMPLES if self._expressive else self._TAG_EXAMPLES_PLAIN))
        fmt = ""
        if self._expressive:
            fmt = (
                "\n\nAlways reply with ONLY a JSON object, no other text:\n"
                '{"say": "<robot-control tag, if one applies><what you say out loud>", '
                '"mood": "neutral|happy|sad|angry|shy|surprised|afraid|concerned|'
                'confused|curious|embarrassed", '
                '"gesture": "none|talk|think|question|point|self|big|up|down|celebrate"}\n'
                "Pick the mood and gesture that genuinely fit your line — you are a robot "
                "with a face and arms, so move and emote naturally (e.g. celebrate good news, "
                "think when pondering, question when asking, self when talking about yourself). "
                "Your face has these eleven expressions and no others; anything else is "
                "ignored. Leave a field out if none fits — the server performs the line "
                "either way.\n"
                'Any robot-control tag goes inside the "say" string, nowhere else. '
                "Writing JSON never excuses you from the tag."
            )
        return self._persona + who + fmt + tags

    # ---- parsing ----
    @staticmethod
    def _objects(raw: str) -> dict:
        """Every JSON object in `raw`, merged left to right — models sometimes split the
        envelope as `{"say": …} {"mood": …}`. Prose around them is ignored; {} if none."""
        dec, merged, i = json.JSONDecoder(), {}, raw.find("{")
        while i >= 0:
            try:
                obj, end = dec.raw_decode(raw, i)
            except ValueError:
                i = raw.find("{", i + 1)
                continue
            if isinstance(obj, dict):
                merged.update(obj)
            i = raw.find("{", end)
        return merged

    @staticmethod
    def _parse(raw: str):
        """Pull {say, mood, gesture} out of the model's reply, tolerating stray prose.

        A missing field is None, not a default — a manufactured hint would override the
        floor's own scoring.
        """
        raw = (raw or "").strip()
        if not raw:
            return "", None, None
        obj = LLMApp._objects(raw)
        say = str(obj.get("say") or obj.get("text") or "").strip()
        if say:
            mood, gesture = obj.get("mood"), obj.get("gesture")
            return (say,
                    str(mood).lower() if mood else None,
                    str(gesture).lower() if gesture else None)
        # model ignored the format — treat the whole thing as the spoken line
        return raw, None, None

    # ---- MoxieApp ----
    def greeting(self, robot: RobotContext) -> Reply:
        text = f"Hi {robot.child.nickname}! It's so good to see you. What's on your mind today?"
        return Reply(text=text, markup=build_markup(text, "happy", "celebrate"))

    def _messages(self, turn: Turn) -> list:
        messages = [{"role": "system", "content": self._system(turn.robot, turn)}]
        messages += turn.history[-self._max_history:]
        messages.append({"role": "user", "content": turn.speech})
        return messages

    # ---- streaming (a sentence at a time) ----
    def respond_stream(self, turn: Turn):
        """Answer as the model writes: one `ReplyChunk` per finished sentence.

        Same prompt as `respond`; only delivery changes. A stream that fails before any
        words were spoken falls back to `respond` as a single closing chunk."""
        return self._stream_chunks(turn)

    def _stream_chunks(self, turn: Turn):
        from ..chat import is_timeout_error, stream_completion
        messages = self._messages(turn)
        seg = SentenceSegmenter(min_chars=self.stream_min_chars)
        says = SayStream(self._expressive)
        carry, spoken = [], 0            # actions with no chunk yet; chunks published
        # One deadline for the open AND its `respond()` fallback: a hung open spends the
        # bound, so the fallback is skipped rather than hanging a second time.
        started = self._clock()
        try:
            for delta in stream_completion(
                    self._client, self._model, messages, max_tokens=self._max_tokens,
                    temperature=self._temperature, pacer=self._pacer,
                    deadline_s=self._timeout_s, clock=self._clock):
                words = says.feed(delta)
                if not words:
                    continue
                for sentence in seg.feed(words):
                    text, actions = parse_action_tags(sentence)
                    actions = carry + actions
                    if not text:                 # a chunk that was only a tag: keep the
                        carry = actions          # action, wait for words to attach it to
                        continue
                    carry = []
                    # `spoken` = chunk index (only index 0 carries the mood).
                    markup = build_markup(text, turn_key=_turn_key(turn),
                                          chunk_index=spoken) if self._expressive else None
                    spoken += 1
                    yield ReplyChunk(text=text, actions=actions, markup=markup)
        except GeneratorExit:                    # the runtime cancelled a stale turn
            raise
        except Exception as e:
            if spoken == 0:
                remaining = self._timeout_s - (self._clock() - started)
                if is_timeout_error(e) or remaining <= 0:
                    # The open ran out the turn's whole bound: a second request would
                    # hang just as long. One offline reply, no fallback call.
                    print(f"[llm] stream timed out ({type(e).__name__}) inside the "
                          f"{self._timeout_s:g}s bound; answering offline", flush=True)
                    yield ReplyChunk.from_reply(Reply.offline())
                    return
                print(f"[llm] stream unavailable ({type(e).__name__}); "
                      f"falling back to a single reply", flush=True)
                self._fallback.budget_s = remaining
                try:
                    reply = self.respond(turn)
                finally:
                    self._fallback.budget_s = None
                yield ReplyChunk.from_reply(reply)
                return
            print(f"[llm] stream died mid-answer ({type(e).__name__}); "
                  f"closing with what we have", flush=True)

        # The last sentence is always still in the segmenter; the model's mood/gesture
        # have arrived by now and become hints for the closing chunk.
        tail = (seg.flush() or [""])[0]
        say, mood, gesture = self._parse(says.raw)
        if not tail and spoken == 0:
            tail = say                           # model ignored the format entirely
        text, actions = parse_action_tags(tail)
        actions = carry + actions
        if not text and not actions and spoken == 0:
            text, mood, gesture = "Tell me more!", "happy", "question"
        markup = (build_markup(text, mood, gesture, turn_key=_turn_key(turn),
                               chunk_index=spoken)
                  if (self._expressive and text) else None)
        yield ReplyChunk(text=text, markup=markup, actions=actions, final=True)

    def respond(self, turn: Turn) -> Reply:
        """One non-streamed answer. As the streaming fallback it gets what is left of
        the turn's bound (`_fallback.budget_s`, set by `_stream_chunks`): when shorter
        than the client's own timeout it also caps this one request, so open + fallback
        end inside one `timeout_s` together."""
        messages = self._messages(turn)
        budget_s = getattr(self._fallback, "budget_s", None)
        budget = self._timeout_s if budget_s is None else min(float(budget_s), self._timeout_s)
        request_kw = {"timeout": budget} if budget < self._timeout_s else {}
        try:
            from ..chat import call_with_backoff, note_model_call
            def _once():
                # Counted like make_openai_chat's calls (each retry is an attempt).
                note_model_call("chat")
                r = self._client.chat.completions.create(
                    model=self._model, messages=messages,
                    max_tokens=self._max_tokens, temperature=self._temperature,
                    **request_kw)
                return (r.choices[0].message.content or "").strip()
            raw = call_with_backoff(_once, pacer=self._pacer, deadline_s=budget,
                                    clock=self._clock)
        except Exception as e:
            # Unreachable → ERROR_OFFLINE (robot falls back on-device, ai-seam.md §2);
            # any other error → a friendly retry line.
            if _is_offline_error(e):
                return Reply.offline()
            from ..chat import is_rate_limit_error
            if is_rate_limit_error(e):
                text = "Give me one tiny second to think... okay, go on!"
                return Reply(text=text, markup=build_markup(text, "curious", "think"),
                             end_turn=False)
            text = "Hmm, my brain got a little fuzzy. Can you say that again?"
            return Reply(text=text, markup=build_markup(text, "shy", "self"),
                         end_turn=False)
        text, mood, gesture = self._parse(raw)
        text, actions = parse_action_tags(text)
        if not text and not actions:
            text = "Tell me more!"
            mood, gesture = "happy", "question"
        markup = (build_markup(text, mood, gesture, turn_key=_turn_key(turn))
                  if (self._expressive and text) else None)
        return Reply(text=text, markup=markup, actions=actions)
