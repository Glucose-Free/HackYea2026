import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from gateway.agent.chat_model import ChatModel, ChatModelReply, ToolCallRequest
from gateway.agent.tools import (
    DENIED_TOOL_RESULT_PREFIX,
    FAILED_TOOL_RESULT_PREFIX,
    ToolCallContext,
    ToolCaller,
    ToolCallOutcome,
    ToolCallResult,
    ToolDefinition,
    ToolProvider,
    ToolSession,
)
from gateway.audit.trace import StepOutcome, TraceRecorder, TraceStepKind, build_traceparent, new_span_id
from gateway.core.conversation import Conversation

SYSTEM_PROMPT = (
    "You are the organization's internal data assistant. Use the available tools to fetch data "
    "when the user asks for it. If a tool result says the request was denied, tell the user plainly "
    "that the data service refused it and do not try to work around the refusal."
)
FORWARDED_CONVERSATION_ROLES = ("user", "assistant")
MODEL_TURN_NAME = "model call {turn_number}"
TOOL_ROUND_LIMIT_ERROR = "model still requested tools after {max_tool_rounds} tool rounds"
CHECKPOINT_STEP_OUTCOMES = {"passed": StepOutcome.PASSED, "denied": StepOutcome.DENIED}
FETCH_STEP_OUTCOMES = {
    ToolCallOutcome.PASSED: StepOutcome.PASSED,
    ToolCallOutcome.DENIED: StepOutcome.DENIED,
    ToolCallOutcome.FAILED: StepOutcome.FAILED,
}
TOOL_RESULT_PREFIXES = {
    ToolCallOutcome.PASSED: "",
    ToolCallOutcome.DENIED: DENIED_TOOL_RESULT_PREFIX,
    ToolCallOutcome.FAILED: FAILED_TOOL_RESULT_PREFIX,
}


@dataclass(frozen=True)
class AgentReply:
    answer: str
    model: str


class ToolRoundLimitExceededError(RuntimeError):
    pass


class ChatAgent:
    def __init__(self, chat_model: ChatModel, tool_provider: ToolProvider, max_tool_rounds: int):
        self._chat_model = chat_model
        self._tool_provider = tool_provider
        self._max_tool_rounds = max_tool_rounds

    async def reply(
        self,
        conversation: Conversation,
        recorder: TraceRecorder,
        root_step_id: str,
        trace_id: str,
        caller: ToolCaller,
    ) -> AgentReply:
        messages = build_model_messages(conversation)
        async with self._tool_provider.open_session() as session:
            tools = await session.list_tools()
            for turn_number in range(1, self._max_tool_rounds + 2):
                model_reply, turn_step_id = await self._run_model_turn(messages, tools, recorder, root_step_id, turn_number)
                if not model_reply.tool_calls:
                    return AgentReply(model_reply.content, model_reply.model)
                if turn_number > self._max_tool_rounds:
                    break
                messages.append(build_assistant_tool_call_message(model_reply))
                for tool_call in model_reply.tool_calls:
                    call_context = ToolCallContext(build_traceparent(trace_id, new_span_id()), caller)
                    result = await self._run_fetch(session, tool_call, recorder, turn_step_id, call_context)
                    messages.append(build_tool_result_message(tool_call, result))
        raise ToolRoundLimitExceededError(TOOL_ROUND_LIMIT_ERROR.format(max_tool_rounds=self._max_tool_rounds))

    async def _run_model_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        recorder: TraceRecorder,
        root_step_id: str,
        turn_number: int,
    ) -> tuple[ChatModelReply, str]:
        started_at, started = datetime.now(UTC), time.perf_counter()
        turn_name = MODEL_TURN_NAME.format(turn_number=turn_number)
        try:
            model_reply = await self._chat_model.complete(messages, tools)
        except Exception as error:
            recorder.add_step(
                TraceStepKind.AGENT_TURN, turn_name, StepOutcome.FAILED, parent_step_id=root_step_id,
                reason=f"{type(error).__name__}: {error}", started_at=started_at, duration_ms=get_elapsed_ms(started),
            )
            raise
        turn_step_id = recorder.add_step(
            TraceStepKind.AGENT_TURN, turn_name, StepOutcome.INFO, parent_step_id=root_step_id,
            detail={"model": model_reply.model, "requested_tools": [call.name for call in model_reply.tool_calls]},
            started_at=started_at, duration_ms=get_elapsed_ms(started),
        )
        return model_reply, turn_step_id

    async def _run_fetch(
        self,
        session: ToolSession,
        tool_call: ToolCallRequest,
        recorder: TraceRecorder,
        turn_step_id: str,
        call_context: ToolCallContext,
    ) -> ToolCallResult:
        detail = {"arguments": tool_call.arguments, "traceparent": call_context.traceparent}
        started_at, started = datetime.now(UTC), time.perf_counter()
        try:
            result = await session.call_tool(tool_call.name, tool_call.arguments, call_context)
        except Exception as error:
            recorder.add_step(
                TraceStepKind.DATA_FETCH, tool_call.name, StepOutcome.FAILED, parent_step_id=turn_step_id,
                reason=f"{type(error).__name__}: {error}", detail=detail, started_at=started_at, duration_ms=get_elapsed_ms(started),
            )
            raise
        fetch_step_id = recorder.add_step(
            TraceStepKind.DATA_FETCH, tool_call.name, FETCH_STEP_OUTCOMES[result.outcome],
            parent_step_id=turn_step_id, reason="" if result.outcome is ToolCallOutcome.PASSED else result.content,
            detail={**detail, "result_chars": len(result.content)}, started_at=started_at, duration_ms=get_elapsed_ms(started),
        )
        for checkpoint_step in result.checkpoint_steps:
            recorder.add_step(
                TraceStepKind.FETCH_STEP, checkpoint_step.middleware,
                CHECKPOINT_STEP_OUTCOMES.get(checkpoint_step.outcome, StepOutcome.INFO),
                parent_step_id=fetch_step_id, reason=checkpoint_step.reason, started_at=started_at,
            )
        return result


def build_model_messages(conversation: Conversation) -> list[dict[str, Any]]:
    # Client-supplied system messages are dropped so a client cannot replace the gateway's instructions.
    forwarded = [
        {"role": message.role, "content": message.content}
        for message in conversation.messages
        if message.role in FORWARDED_CONVERSATION_ROLES
    ]
    return [{"role": "system", "content": SYSTEM_PROMPT}, *forwarded]


def build_assistant_tool_call_message(model_reply: ChatModelReply) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": model_reply.content or None,
        "tool_calls": [
            {"id": call.call_id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments)}}
            for call in model_reply.tool_calls
        ],
    }


def build_tool_result_message(tool_call: ToolCallRequest, result: ToolCallResult) -> dict[str, Any]:
    content = TOOL_RESULT_PREFIXES[result.outcome] + result.content
    return {"role": "tool", "tool_call_id": tool_call.call_id, "content": content}


def get_elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
