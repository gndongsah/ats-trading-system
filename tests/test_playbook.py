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
    assert PLAYBOOK.setup_score.max_score == 100
    assert {
        name: factor.weight for name, factor in PLAYBOOK.setup_score.factors.items()
    } == {
        "trend_alignment": 25,
        "relative_strength_vs_spy": 25,
        "structure_quality": 25,
        "liquidity_volume_confirmation": 15,
        "earnings_distance_safety": 10,
    }
    assert PLAYBOOK.setup_score.thresholds == {
        "planned_size": 70,
        "unproven_size": 50,
    }


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


def test_invalid_setup_score_playbook_rejects_changed_weights_and_thresholds(tmp_path):
    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    playbook["setup_score"]["factors"]["trend_alignment"]["weight"] = 24
    playbook["setup_score"]["factors"]["structure_quality"]["weight"] = 26
    invalid_weight_path = tmp_path / "invalid-setup-weight.yaml"
    invalid_weight_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="weights must match"):
        load_playbook(invalid_weight_path)

    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    playbook["setup_score"]["thresholds"]["planned_size"] = 71
    invalid_threshold_path = tmp_path / "invalid-setup-threshold.yaml"
    invalid_threshold_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="thresholds must be 70 and 50"):
        load_playbook(invalid_threshold_path)


def test_invalid_setup_score_playbook_rejects_missing_factor(tmp_path):
    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    del playbook["setup_score"]["factors"]["earnings_distance_safety"]
    playbook["setup_score"]["max_score"] = 90
    invalid_path = tmp_path / "invalid-setup-factor.yaml"
    invalid_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="five Playbook v3 components"):
        load_playbook(invalid_path)


def test_invalid_setup_score_playbook_rejects_changed_swing_screen_limits(tmp_path):
    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    playbook["setup_score"]["swing_long_screen"]["min_avg_daily_volume"] = 499999
    invalid_path = tmp_path / "invalid-setup-screen.yaml"
    invalid_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="500k volume"):
        load_playbook(invalid_path)
