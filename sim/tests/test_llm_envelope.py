"""LLMApp._parse: the {say, mood, gesture} envelope, including the split form models emit."""
import pytest

from moxie_sdk.apps.llm_app import LLMApp


@pytest.mark.parametrize("raw, want", [
    ('{"say": "Hi!", "mood": "Happy", "gesture": "wave"}', ("Hi!", "happy", "wave")),
    # Seen live 2026-09-29: the envelope split into two objects was spoken as raw JSON.
    ('{"say": "Rainy days are cosy."} {"mood": "happy", "gesture": "celebrate"}',
     ("Rainy days are cosy.", "happy", "celebrate")),
    ('Sure! {"say": "Okay."} trailing', ("Okay.", None, None)),
    ('{"say": "a {brace} inside"}', ("a {brace} inside", None, None)),
    ("plain prose", ("plain prose", None, None)),
    ('{"mood": "happy"}', ('{"mood": "happy"}', None, None)),
    ("{broken", ("{broken", None, None)),
    ("", ("", None, None)),
])
def test_parse(raw, want):
    assert LLMApp._parse(raw) == want
