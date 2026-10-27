import pytest
import yaml

from src.playbook import PLAYBOOK, PLAYBOOK_PATH, PlaybookValidationError, load_playbook


def test_playbook_loads_version_and_weights_from_yaml():
    assert PLAYBOOK.version == "3.0"
    for section in (PLAYBOOK.market_opportunity, PLAYBOOK.discovery):
        assert (
            sum(factor.weight for factor in section.factors.values())
            == section.max_score
        )
    assert {rule.regime for rule in PLAYBOOK.regime.rules.values()} == {
        1,
        2,
        3,
        4,
        5,
    }
    assert set(PLAYBOOK.regime.precedence) == set(PLAYBOOK.regime.rules)


def test_invalid_playbook_fails_with_clear_validation_error(tmp_path):
    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    playbook["market_opportunity"]["thresholds"]["active"] = playbook[
        "market_opportunity"
    ]["thresholds"]["watchlist"]
    invalid_path = tmp_path / "invalid_playbook.yaml"
    invalid_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="active threshold"):
        load_playbook(invalid_path)


def test_invalid_regime_playbook_rejects_unknown_signal_and_incomplete_precedence(
    tmp_path,
):
    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    playbook["regime"]["rules"]["risk_on"]["requires"].append("mystery_signal")
    invalid_signal_path = tmp_path / "invalid-regime-signal.yaml"
    invalid_signal_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="unknown regime signal"):
        load_playbook(invalid_signal_path)

    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    playbook["regime"]["precedence"].pop()
    invalid_precedence_path = tmp_path / "invalid-regime-precedence.yaml"
    invalid_precedence_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="precedence"):
        load_playbook(invalid_precedence_path)
