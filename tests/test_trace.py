import re
from datetime import UTC, datetime

from gateway.audit.trace import (
    StepOutcome,
    TraceRecorder,
    TraceStep,
    TraceStepKind,
    build_traceparent,
    new_span_id,
    new_trace_id,
    order_steps_as_tree,
)


def test_recorder_assigns_sequential_ids_and_parents():
    recorder = TraceRecorder()
    root = recorder.add_step(TraceStepKind.USER_PROMPT, "alice", StepOutcome.INFO)
    child = recorder.add_step(TraceStepKind.CHECKPOINT, "user_input", StepOutcome.PASSED, parent_step_id=root)
    assert (root, child) == ("step_1", "step_2")
    assert recorder.steps[1].parent_step_id == "step_1"
    assert recorder.steps[1].detail == {}


def test_step_round_trips_through_dict():
    step = TraceStep("step_1", None, TraceStepKind.GUARD, "g", StepOutcome.DENIED, "r", {"k": 1}, datetime(2026, 10, 4, tzinfo=UTC), 1.5)
    assert TraceStep.from_dict(step.to_dict()) == step
    assert step.to_dict()["kind"] == "guard"


def test_traceparent_is_w3c_formatted():
    traceparent = build_traceparent(new_trace_id(), new_span_id())
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", traceparent)


def test_order_steps_as_tree_is_depth_first():
    recorder = TraceRecorder()
    root = recorder.add_step(TraceStepKind.USER_PROMPT, "u", StepOutcome.INFO)
    turn = recorder.add_step(TraceStepKind.AGENT_TURN, "t1", StepOutcome.INFO, parent_step_id=root)
    recorder.add_step(TraceStepKind.REPLY, "r", StepOutcome.PASSED, parent_step_id=root)
    recorder.add_step(TraceStepKind.DATA_FETCH, "f", StepOutcome.PASSED, parent_step_id=turn)
    names = [step.name for step in order_steps_as_tree(list(reversed(recorder.steps)))]
    assert names == ["u", "t1", "f", "r"]
