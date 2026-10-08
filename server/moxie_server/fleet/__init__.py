"""The console's pure card views: supervisor payload in, render-ready shape out.

Dependency-free on purpose (no fastapi, no network, nothing from `mqtt/`) so every view
unit-tests in the hermetic suite; the routes in `routes/` fetch and call these. Every
view tolerates a missing, partial or mistyped payload — a card is never a 500.
"""
from .activity import *  # noqa: F401,F403
from .cards import *  # noqa: F401,F403
from .content import *  # noqa: F401,F403
from .memory import *  # noqa: F401,F403
from .robots import *  # noqa: F401,F403
from .tryit import *  # noqa: F401,F403
