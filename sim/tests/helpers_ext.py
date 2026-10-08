"""Shared builders for the sandboxed-extension suites (`test_ext*.py`)."""
from moxie_sdk.content.content_app import ContentApp
from moxie_sdk.content.module import load_modules
from moxie_sdk.types import RobotContext, ChildProfile

#: The smallest module a ContentApp can run: one conversation, no globals.
CHAT_MODULE = {"conversations": [{"name": "Chat", "module_id": "CHAT", "content_id": "default",
                                  "prompt": "You are Moxie."}]}


def robot(device_id="robot-1", *, nickname="Sam", module_id="", content_id=""):
    return RobotContext(device_id=device_id, module_id=module_id, content_id=content_id,
                        child=ChildProfile(nickname=nickname))


def app_with(module_json, chat=None, **kw):
    """A ContentApp with memory and the safety classifier off (unless `kw` says otherwise),
    so a test sees only the extension path; the brain answers a fixed line unless `chat`
    is given."""
    kw.setdefault("memory", False)
    kw.setdefault("safety_classifier", False)
    return ContentApp(load_modules(module_json), chat or (lambda m: "the model answered"),
                      default_module_id="CHAT", **kw)
