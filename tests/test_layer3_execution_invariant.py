import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from src.layer3 import (
    Layer3Decision,
    _require_execution_disabled,
)
from src.persistence.models import Base, RiskReservationEventRecord
from src.portfolio import RiskDecision
from tests.test_layer3 import authorize, create_service_and_db


def make_decision(**overrides) -> Layer3Decision:
    values = {"decision": RiskDecision.ALLOW, "quantity": 5, "reason": "WITHIN_LIMITS"}
    return Layer3Decision(**(values | overrides))


def test_execution_disabled_by_default_and_true_is_rejected_on_construction():
    assert make_decision().execution_enabled is False
    with pytest.raises(ValidationError):
        make_decision(execution_enabled=True)


@pytest.mark.parametrize("value", ["true", "True", 1, None, "yes"])
def test_non_false_values_are_rejected_on_construction(value):
    with pytest.raises(ValidationError):
        make_decision(execution_enabled=value)


def test_validation_and_json_deserialization_reject_true():
    payload = make_decision().model_dump(mode="json")
    assert Layer3Decision.model_validate(payload).execution_enabled is False
    with pytest.raises(ValidationError):
        Layer3Decision.model_validate(payload | {"execution_enabled": True})
    with pytest.raises(ValidationError):
        Layer3Decision.model_validate_json(
            make_decision().model_dump_json().replace("false", "true", 1)
        )


@pytest.mark.parametrize("deep", [False, True])
def test_model_copy_cannot_set_execution_enabled(deep):
    decision = make_decision()
    with pytest.raises(ValueError, match="never enable broker execution"):
        decision.model_copy(update={"execution_enabled": True}, deep=deep)
    with pytest.raises(ValueError, match="never enable broker execution"):
        decision.model_copy(update={"execution_enabled": "true"}, deep=deep)
    assert decision.execution_enabled is False


def test_model_copy_still_supports_replay_marking_and_revalidates_other_fields():
    decision = make_decision()
    replay = decision.model_copy(update={"replayed": True})
    assert replay.replayed is True and replay.execution_enabled is False
    assert replay == decision.model_copy(update={"replayed": True})
    assert decision.model_copy().execution_enabled is False
    with pytest.raises(ValidationError):
        decision.model_copy(update={"quantity": "not-a-number"})


@pytest.mark.parametrize("value", [True, 1, "true"])
def test_model_construct_rejects_non_false_execution_flag(value):
    with pytest.raises(ValueError, match="never enable broker execution"):
        Layer3Decision.model_construct(
            decision=RiskDecision.ALLOW,
            quantity=1,
            reason="x",
            execution_enabled=value,
        )


def test_model_construct_without_the_flag_is_still_allowed():
    built = Layer3Decision.model_construct(
        decision=RiskDecision.ALLOW, quantity=1, reason="x"
    )
    assert built.execution_enabled is False


def test_instances_are_frozen():
    with pytest.raises(ValidationError):
        make_decision().execution_enabled = True


def test_documented_limit_in_process_forgery_is_detected_only_by_the_boundary_check():
    # object.__setattr__ bypasses pydantic entirely; the model is not a security
    # boundary. The service re-checks decisions before persisting them.
    forged = make_decision()
    object.__setattr__(forged, "execution_enabled", True)
    assert forged.execution_enabled is True
    with pytest.raises(ValueError, match="never enable broker execution"):
        _require_execution_disabled(forged)


def test_replay_still_works_and_never_enables_execution(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        first = authorize(factory, service, account_id, plan_id, "request-1")
        replay = authorize(factory, service, account_id, plan_id, "request-1")
        assert replay.replayed is True and first.replayed is False
        assert replay.execution_enabled is False
        assert replay == first.model_copy(update={"replayed": True})
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_replay_of_a_tampered_stored_decision_fails_closed(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        authorize(factory, service, account_id, plan_id, "request-1")
        with factory.begin() as session:
            record = session.scalar(select(RiskReservationEventRecord))
            data = dict(record.event_data)
            data["decision"] = data["decision"] | {"execution_enabled": True}
            record.event_data = data
        with pytest.raises(ValidationError):
            authorize(factory, service, account_id, plan_id, "request-1")
        with factory() as session:
            count = session.scalar(
                select(func.count()).select_from(RiskReservationEventRecord)
            )
        assert count == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize("update", [None, {"execution_enabled": True}])
def test_deprecated_copy_is_rejected_in_favor_of_guarded_model_copy(update):
    with pytest.raises(TypeError, match="use model_copy"):
        make_decision().copy(update=update)
