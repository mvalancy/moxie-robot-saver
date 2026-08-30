"""
Behavior-markup builder — emit <mark name="cmd:..."> tags Moxie's brain understands.

Inline these into TTS text so the robot moves/emotes while speaking. The data:{} object is JSON with
'+' standing in for '"' (the mark lives inside an XML attribute). See
docs/reverse-engineering/behavior-markup.md for the full verb + field catalog.
"""
import json

def _mark(verb, data=None):
    if not data:
        return f'<mark name="cmd:{verb}"/>'
    # JSON, then swap the quotes to '+' the way the shipped content does
    body = json.dumps(data, separators=(",", ":")).replace('"', "+")
    return f'<mark name="cmd:{verb},data:{body}"/>'

def behaviour_tree(behaviour="", *, eventName="Gesture_None", category="BehaviourTree", action=0,
                   Track="", transition=0.5, duration=1.0, repeat=1, blocking=False,
                   layerBlendInTime=0.5, layerBlendOutTime=0.5, variableName="", variableValue="",
                   lifetime=0):
    return _mark("behaviour-tree", {
        "transition": transition, "duration": duration, "repeat": repeat,
        "layerBlendInTime": layerBlendInTime, "layerBlendOutTime": layerBlendOutTime,
        "blocking": blocking, "action": action, "variableName": variableName,
        "variableValue": variableValue, "eventName": eventName, "lifetime": lifetime,
        "category": category, "behaviour": behaviour, "Track": Track})

# playback-mood `mood` ints — inferred from shipped content (behavior-markup.md).
MOOD_NEUTRAL, MOOD_POSITIVE, MOOD_CONCERNED, MOOD_OOPS, MOOD_SURPRISED = 0, 1, 2, 4, 5

def playback_mood(mood=0, intensity=0):
    return _mark("playback-mood", {"mood": mood, "intensity": intensity})

def icons(names=(), *, command=0, index=0, transition=0, volume=0.5, highlight=0):
    """cmd:icons-v2 — show/clear up to 4 icons on the face screen.

    command: 0 = show, 2 = clear. `names` are icon value strings (e.g. "Birthday",
    "School", "Medical", or a "*Heart*" name); empty slots are filled as iconType 0.
    A turn typically emits icons(names, command=0) before the line and
    icons(names, command=2) after. See behavior-markup.md.
    """
    names = list(names)[:4]
    slots = {}
    for i in range(4):
        v = names[i] if i < len(names) and names[i] else None
        slots[f"icon{i}"] = ({"iconType": 1, "value": v, "background": "Null"} if v
                             else {"iconType": 0, "value": "Null", "background": "Null"})
    return _mark("icons-v2", {"command": command, "index": index, "transition": transition,
                              "volume": volume, **slots, "highlight": highlight})

def idlestate(idleState=0):
    return _mark("idlestate", {"idleState": idleState})

def playaudio(SoundToPlay, *, channel=0, LoopSound=False, playInBackground=False,
              ReplaceCurrentSound=False, PlayImmediate=True, ForceQueue=False, Volume=1.0,
              FadeInTime=0.0, FadeOutTime=0.0, AudioTimelineField="none"):
    return _mark("playaudio", {
        "SoundToPlay": SoundToPlay, "LoopSound": LoopSound, "playInBackground": playInBackground,
        "channel": channel, "ReplaceCurrentSound": ReplaceCurrentSound, "PlayImmediate": PlayImmediate,
        "ForceQueue": ForceQueue, "Volume": Volume, "FadeInTime": FadeInTime,
        "FadeOutTime": FadeOutTime, "AudioTimelineField": AudioTimelineField})

def stopaudio(*, scope=1, channel=0, FadeOutTime=1.0, ClearQueue=True):
    return _mark("stopaudio", {"scope": scope, "channel": channel,
                               "FadeOutTime": FadeOutTime, "ClearQueue": ClearQueue})

def notification(message, duration=2.0):
    return _mark("notification", {"message": message, "duration": duration})

def composite(alias):
    return _mark("composite", {"alias": alias})

# The 52 built-in CereProc vocal gestures / "spurts" (VocalGestures.availableGestures, v24.10.803;
# assets named g0001_<id>). See docs/reverse-engineering/runtime/behavior-markup.md.
VOCAL_GESTURES = [
    "tut", "tut tut", "cough", "cough2", "cough3", "clear throat", "breath in", "sharp intake of breath",
    "breath in through teeth", "sigh happy", "sigh sad", "hmm question", "hmm yes", "hmm thinking", "umm",
    "umm2", "err", "err2", "giggle", "giggle2", "laugh", "laugh2", "laugh3", "laugh4", "ah positive",
    "ah negative", "yeah question", "yeah positive", "yeah resigned", "sniff", "sniff2", "argh", "argh2",
    "ugh", "ocht", "yay", "oh positive", "oh negative", "sarcastic noise", "yawn", "yawn2", "snore", "null",
    "snore phew", "zzz", "raspberry", "raspberry2", "brr cold", "snort", "ha ha (sarcastic)", "doh", "gasp"]

def vocal_gesture(gesture):
    """cmd:vocal-gesture — play a non-word vocalization (laugh/sigh/hmm…). See VOCAL_GESTURES."""
    return _mark("vocal-gesture", {"gesture": gesture})

def raw(verb, **data):
    """Escape hatch for any of the 24 verbs: raw('emotion', state=...)."""
    return _mark(verb, data or None)

# All known verbs (docs/reverse-engineering/behavior-markup.md)
VERBS = ["animation","attachment","attachment-animator","attachment-particles","behaviour-tree",
         "blink-control","composite","dynamic-face-texture","emotion","hud","icons-v2","idlestate",
         "notification","playaudio","playback-mood","playback-restore","playback-save","reward-star",
         "scripted","speech-playback","start-systemsuspend","start-systemunpair","stopaudio",
         "vocal-gesture","whiteboard"]

# <usel> voice-style genres seen in shipped content (behavior-markup.md).
GENRES = ("none", "question", "motivational", "intimate", "excited")

def usel(text, variant=0, genre="none"):
    return f'<usel variant="{variant}" genre="{genre}">{text}</usel>'

def brk(seconds):
    return f'<break time="{seconds}s"/>'
