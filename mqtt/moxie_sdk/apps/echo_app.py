"""EchoApp — the simplest possible Moxie app, for testing the loop end to end."""
from ..app import MoxieApp
from ..types import Turn, Reply, RobotContext


class EchoApp(MoxieApp):
    name = "echo"

    def greeting(self, robot: RobotContext) -> Reply:
        return Reply(text=f"Hi {robot.child.nickname}! Say something and I'll echo it.")

    def respond(self, turn: Turn) -> Reply:
        if not turn.speech.strip():
            return Reply(text="I didn't catch that. Try again?")
        return Reply(text=f"You said: {turn.speech}")
