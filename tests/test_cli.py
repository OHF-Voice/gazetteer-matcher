from pathlib import Path

import pytest

from gazetteer_matcher.cli import main


def test_match_requires_an_explicit_home_choice(capsys):
    with pytest.raises(SystemExit, match="2"):
        main(["match", "are any lights on"])

    assert (
        "one of the arguments --home --empty-home is required"
        in capsys.readouterr().err
    )


def test_match_accepts_an_explicit_empty_home(capsys):
    exit_code = main(["match", "are any lights on", "--empty-home"])

    assert exit_code == 0
    assert '"intent": "HassGetState"' in capsys.readouterr().out


def test_match_loads_a_home_file(capsys):
    home_path = Path(__file__).with_name("home.yaml")

    exit_code = main(
        [
            "match",
            "turn on the kitchen lights",
            "--home",
            str(home_path),
        ]
    )

    assert exit_code == 0
    assert '"area": "kitchen"' in capsys.readouterr().out


def test_spans_remains_usable_without_a_home(capsys):
    exit_code = main(["spans", "turn on the lights"])

    assert exit_code == 0
    assert "SPANS" in capsys.readouterr().out


def test_support_does_not_accept_a_home_option(capsys):
    home_path = Path(__file__).with_name("home.yaml")

    with pytest.raises(SystemExit, match="2"):
        main(["support", "--home", str(home_path)])

    assert "unrecognized arguments: --home" in capsys.readouterr().err


def test_support_remains_usable_without_a_home(capsys):
    exit_code = main(["support"])

    assert exit_code == 0
    assert '"total_combinations"' in capsys.readouterr().out
