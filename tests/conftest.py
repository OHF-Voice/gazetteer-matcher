from pathlib import Path

import pytest

from gazetteer_matcher import GazetteerMatcher

HOME_PATH = Path(__file__).parent / "home.yaml"


@pytest.fixture(scope="session", name="home_path")
def home_path_fixture() -> Path:
    return HOME_PATH


@pytest.fixture(scope="module")
def matcher(home_path: Path) -> GazetteerMatcher:
    return GazetteerMatcher(home_path=home_path)
