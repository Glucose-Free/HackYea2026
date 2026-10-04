import pytest

from gateway.agent.chat_agent import SYSTEM_PROMPT, ChatAgent, ToolRoundLimitExceededError
from gateway.agent.chat_model import ChatModelError
from gateway.agent.tools import DENIED_TOOL_RESULT_PREFIX, CheckpointStep, ToolCallResult, ToolDefinition
from gateway.audit.trace import StepOutcome, TraceRecorder, TraceStepKind
from gateway.core.conversation import Conversation, Message
from tests.fakes import FakeChatModel, FakeToolProvider, build_answer_reply, build_tool_call_reply

TRACE_ID = "a" * 32


def build_conversation() -> Conversation:
    return Conversation((
        Message("system", "client system prompt that must be ignored"),
        Message("user", "show transactions"),
    ))


def start_trace() -> tuple[TraceRecorder, str]:
    recorder = TraceRecorder()
    return recorder, recorder.add_step(TraceStepKind.USER_PROMPT, "alice", StepOutcome.INFO)


async def test_answers_without_tools():
    chat_model = FakeChatModel([build_answer_reply("hello")])
    recorder, root = start_trace()
    reply = await ChatAgent(chat_model, FakeToolProvider(), 8).reply(build_conversation(), recorder, root, TRACE_ID)

    assert reply.answer == "hello"
    sent = chat_model.received_messages[0]
    assert sent[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert [message["role"] for message in sent] == ["system", "user"]
    assert [step.kind for step in recorder.steps] == [TraceStepKind.USER_PROMPT, TraceStepKind.AGENT_TURN]


async def test_runs_tool_then_answers_and_records_fetch_with_traceparent():
    chat_model = FakeChatModel([build_tool_call_reply("list_transactions"), build_answer_reply("done")])
    tool_provider = FakeToolProvider()
    recorder, root = start_trace()
    reply = await ChatAgent(chat_model, tool_provider, 8).reply(build_conversation(), recorder, root, TRACE_ID)

    assert reply.answer == "done"
    name, arguments, traceparent = tool_provider.calls[0]
    assert (name, arguments) == ("list_transactions", {"n": 0})
    assert traceparent.startswith(f"00-{TRACE_ID}-")
    second_call_messages = chat_model.received_messages[1]
    assert second_call_messages[-2]["tool_calls"][0]["function"]["name"] == "list_transactions"
    assert second_call_messages[-1] == {"role": "tool", "tool_call_id": "call_0", "content": "[{\"id\": 1}]"}
    fetch_steps = [step for step in recorder.steps if step.kind is TraceStepKind.DATA_FETCH]
    assert fetch_steps[0].outcome is StepOutcome.PASSED
    assert fetch_steps[0].detail["traceparent"] == traceparent
    turn_step = recorder.steps[1]
    assert fetch_steps[0].parent_step_id == turn_step.step_id


async def test_denied_fetch_is_sent_to_model_with_prefix_and_checkpoint_steps_recorded():
    denied = ToolCallResult("inference risk", True, (
        CheckpointStep("allowlist", "passed", "ok"),
        CheckpointStep("inference_guard", "denied", "dates + phones"),
    ))
    tool_provider = FakeToolProvider(
        tools=[ToolDefinition("get_phones", "p", {"type": "object", "properties": {}})],
        results_by_name={"get_phones": denied},
    )
    chat_model = FakeChatModel([build_tool_call_reply("get_phones"), build_answer_reply("I can't share that")])
    recorder, root = start_trace()
    await ChatAgent(chat_model, tool_provider, 8).reply(build_conversation(), recorder, root, TRACE_ID)

    assert chat_model.received_messages[1][-1]["content"] == DENIED_TOOL_RESULT_PREFIX + "inference risk"
    fetch_step = next(step for step in recorder.steps if step.kind is TraceStepKind.DATA_FETCH)
    assert fetch_step.outcome is StepOutcome.DENIED and fetch_step.reason == "inference risk"
    middleware_steps = [step for step in recorder.steps if step.kind is TraceStepKind.FETCH_STEP]
    assert [(step.name, step.outcome) for step in middleware_steps] == [
        ("allowlist", StepOutcome.PASSED),
        ("inference_guard", StepOutcome.DENIED),
    ]
    assert all(step.parent_step_id == fetch_step.step_id for step in middleware_steps)


async def test_tool_round_cap_raises():
    chat_model = FakeChatModel([build_tool_call_reply("list_transactions")] * 3)
    recorder, root = start_trace()
    with pytest.raises(ToolRoundLimitExceededError):
        await ChatAgent(chat_model, FakeToolProvider(), 2).reply(build_conversation(), recorder, root, TRACE_ID)
    assert len([step for step in recorder.steps if step.kind is TraceStepKind.DATA_FETCH]) == 2


async def test_model_error_is_recorded_as_failed_turn_and_propagates():
    chat_model = FakeChatModel([ChatModelError("bad tool arguments")])
    recorder, root = start_trace()
    with pytest.raises(ChatModelError):
        await ChatAgent(chat_model, FakeToolProvider(), 8).reply(build_conversation(), recorder, root, TRACE_ID)
    assert recorder.steps[-1].kind is TraceStepKind.AGENT_TURN
    assert recorder.steps[-1].outcome is StepOutcome.FAILED


async def test_tool_transport_error_is_recorded_as_failed_fetch_and_propagates():
    chat_model = FakeChatModel([build_tool_call_reply("list_transactions")])
    recorder, root = start_trace()
    with pytest.raises(ConnectionError):
        await ChatAgent(chat_model, FakeToolProvider(error=ConnectionError("mcp down")), 8).reply(build_conversation(), recorder, root, TRACE_ID)
    assert recorder.steps[-1].kind is TraceStepKind.DATA_FETCH
    assert recorder.steps[-1].outcome is StepOutcome.FAILED
