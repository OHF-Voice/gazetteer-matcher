# %%
from pathlib import Path

from gazetteer_matcher import GazetteerMatcher
from gazetteer_matcher.debug import render_interpretation

PROJECT_ROOT = Path(__file__).resolve().parents[1]
matcher = GazetteerMatcher(home_path=PROJECT_ROOT / "tests" / "home.yaml")

# %%
result = matcher.interpret("flick on the kichen lights")
[(frame.intent, frame.combination, frame.slots) for frame in result.frames]

# %%
print(render_interpretation(result))

# %%
result = matcher.interpret("turn on the kitchen and hallway lights")
[(frame.intent, frame.slots) for frame in result.frames]

# %%
result = matcher.interpret("turn off the kitchen lights and open the bedroom blinds")
[(frame.intent, frame.slots) for frame in result.frames]

# %%
result = matcher.interpret("turn on seventeen kitchen lights")
print(result.accepted, result.reason)
print(render_interpretation(result))
