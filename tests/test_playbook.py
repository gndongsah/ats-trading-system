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


def test_invalid_playbook_fails_with_clear_validation_error(tmp_path):
    playbook = yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    playbook["market_opportunity"]["thresholds"]["active"] = playbook[
        "market_opportunity"
    ]["thresholds"]["watchlist"]
    invalid_path = tmp_path / "invalid_playbook.yaml"
    invalid_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")

    with pytest.raises(PlaybookValidationError, match="active threshold"):
        load_playbook(invalid_path)
