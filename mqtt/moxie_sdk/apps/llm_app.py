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
from ..actions import ACTION_TAG_PROMPT, parse_action_tags
from ..segment import SentenceSegmenter
from ..types import Turn, Reply, ReplyChunk, RobotContext
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


# --- streaming helpers ------------------------------------------------------ #
# The expressive prompt asks for `{"say": …, "mood": …, "gesture": …}`, and the model
# writes that object left to right — so while a reply is still streaming we have the
# spoken words but NOT yet the mood/gesture, which arrive after the closing quote of
# "say". Rather than spend a second model call per chunk (the whole point of streaming is
# to be faster, not more expensive), each in-flight chunk gets a **rule-based** mood +
# gesture from its own punctuation, and the FINAL chunk uses the mood/gesture the model
# actually chose, parsed off the completed JSON. `build_markup` itself is pure local
# string work, so markup costs nothing either way.

def stream_style(text: str) -> tuple:
    """A cheap (mood, gesture) for a mid-stream chunk — no model call."""
    t = (text or "").strip()
    if t.endswith("?"):
        return "neutral", "question"
    if t.endswith("!"):
        return "positive", "talk"
    return "neutral", "talk"


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "\\": "\\", "/": "/"}


class SayStream:
    """Pull the spoken words out of a *streaming* reply, as they arrive.

    In expressive mode the model streams a JSON object, so the spoken line is the value
    of its `"say"` key: this walks the growing raw text, finds that key, and decodes the
    string incrementally (stopping short of a half-arrived `\\uXXXX` escape) so the
    segmenter downstream only ever sees real words. Everything after the closing quote —
    `"mood"`, `"gesture"` — is never spoken.

    A model that ignores the format and just writes prose is handled too: the first
    non-space character decides. `{` (or a ``` fence) means JSON, anything else means the
    whole stream is the spoken line.
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

    # Few-shot lines for the tag rules, measured against graphling-medium (see
    # sim/tests/test_live_action_tags.py). Three findings, all the hard way:
    #   * it writes a tag OR the JSON envelope, and drops the tag when asked for both —
    #     so the examples show the finished object with the tag already inside "say";
    #   * a *trailing* tag is forgotten by the time the sentence ends (0/3 goodbyes),
    #     a *leading* one is decided before any words exist (4/4) — hence "begin with";
    #   * with only one example of each rule it parrots the example verbatim, so there
    #     are two goodbyes, an explicit "never reuse their wording", and one untagged
    #     line so an ordinary turn does not grow a spurious tag.
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

    #: Below this many characters a finished sentence waits for the next one, so the
    #: child never hears a lone "Hi." followed by a gap (moxie_sdk/segment.py).
    stream_min_chars = 24

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
        # Model agency: the tags the brain may write inline (moxie_sdk/actions.py
        # parses them off the line and onto the Reply as real robot actions).
        tags = ("\n\n--- Robot controls (most important rule) ---\n" + ACTION_TAG_PROMPT +
                "\nThese tags are REQUIRED when they apply, not optional:\n"
                "  * Child says goodbye / is done / asks to stop -> your reply MUST "
                "begin with <exit>.\n"
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
                '"mood": "neutral|positive|concerned|oops|surprised", '
                '"gesture": "none|talk|think|question|point|self|big|up|down|celebrate"}\n'
                "Pick the mood and gesture that genuinely fit your line — you are a robot "
                "with a face and arms, so move and emote naturally (e.g. celebrate good news, "
                "think when pondering, question when asking, self when talking about yourself).\n"
                'Any robot-control tag goes inside the "say" string, nowhere else. '
                "Writing JSON never excuses you from the tag."
            )
        return self._persona + who + fmt + tags

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

    def _messages(self, turn: Turn) -> list:
        messages = [{"role": "system", "content": self._system(turn.robot)}]
        messages += turn.history[-self._max_history:]
        messages.append({"role": "user", "content": turn.speech})
        return messages

    # ---- streaming (a sentence at a time) ----
    def respond_stream(self, turn: Turn):
        """Answer as the model writes: one `ReplyChunk` per finished sentence.

        A whole completion costs 18-45 s on our gateway, but its first sentence is done
        after a handful of tokens — so the child hears real words at first-token latency
        instead of waiting for the full answer (docs/architecture/mqtt-and-conversation.md
        §4.5). The persona, the JSON envelope and the leading-tag convention are exactly
        the ones `respond` uses; only the delivery changes.

        If the stream fails **before any words were spoken**, this falls back to the
        ordinary `respond` call and yields its answer as the single closing chunk, so a
        gateway that cannot stream is never worse than before."""
        return self._stream_chunks(turn)

    def _stream_chunks(self, turn: Turn):
        from ..chat import stream_completion
        messages = self._messages(turn)
        seg = SentenceSegmenter(min_chars=self.stream_min_chars)
        says = SayStream(self._expressive)
        carry, spoken = [], 0            # actions with no chunk yet; chunks published
        try:
            for delta in stream_completion(
                    self._client, self._model, messages, max_tokens=self._max_tokens,
                    temperature=self._temperature, pacer=self._pacer):
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
                    spoken += 1
                    mood, gesture = stream_style(text)
                    yield ReplyChunk(
                        text=text, actions=actions,
                        markup=build_markup(text, mood, gesture) if self._expressive else None)
        except GeneratorExit:                    # the runtime cancelled a stale turn
            raise
        except Exception as e:
            if spoken == 0:
                # Nothing has been said yet, so the whole answer is still recoverable:
                # take the ordinary non-streaming path and close the turn with it.
                print(f"[llm] stream unavailable ({type(e).__name__}); "
                      f"falling back to a single reply", flush=True)
                yield ReplyChunk.from_reply(self.respond(turn))
                return
            print(f"[llm] stream died mid-answer ({type(e).__name__}); "
                  f"closing with what we have", flush=True)

        # The last sentence is ALWAYS still in the segmenter (a boundary is only
        # confirmed by following text), so this is the real closing line — and by now the
        # model's own mood/gesture have arrived at the tail of the JSON.
        tail = (seg.flush() or [""])[0]
        say, mood, gesture = self._parse(says.raw)
        if not tail and spoken == 0:
            tail = say                           # model ignored the format entirely
        text, actions = parse_action_tags(tail)
        actions = carry + actions
        if not text and not actions and spoken == 0:
            text, mood, gesture = "Tell me more!", "positive", "question"
        markup = build_markup(text, mood, gesture) if (self._expressive and text) else None
        yield ReplyChunk(text=text, markup=markup, actions=actions, final=True)

    def respond(self, turn: Turn) -> Reply:
        messages = self._messages(turn)
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
        # The model may have written robot-control tags into its line — lift them out
        # as real actions and speak only what is left (moxie_sdk/actions.py).
        text, actions = parse_action_tags(text)
        if not text and not actions:
            text = "Tell me more!"
            mood, gesture = "positive", "question"
        markup = build_markup(text, mood, gesture) if (self._expressive and text) else None
        return Reply(text=text, markup=markup, actions=actions)
