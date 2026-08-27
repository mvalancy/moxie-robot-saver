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

def playback_mood(mood=0, intensity=0):
    return _mark("playback-mood", {"mood": mood, "intensity": intensity})

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

def raw(verb, **data):
    """Escape hatch for any of the 24 verbs: raw('emotion', state=...)."""
    return _mark(verb, data or None)

# All known verbs (docs/reverse-engineering/behavior-markup.md)
VERBS = ["animation","attachment","attachment-animator","attachment-particles","behaviour-tree",
         "blink-control","composite","dynamic-face-texture","emotion","hud","idlestate","notification",
         "playaudio","playback-mood","playback-restore","playback-save","reward-star","scripted",
         "speech-playback","start-systemsuspend","start-systemunpair","stopaudio","vocal-gesture","whiteboard"]

def usel(text, variant=0, genre="neutral"):
    return f'<usel variant="{variant}" genre="{genre}">{text}</usel>'

def brk(seconds):
    return f'<break time="{seconds}s"/>'
