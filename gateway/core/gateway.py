import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from gateway.agent.chat_agent import ChatAgent
from gateway.audit.log import AuditLog
from gateway.audit.trace import StepOutcome, TraceRecorder, TraceStepKind, new_trace_id
from gateway.config.provider import CheckpointPipelines
from gateway.core.audit_event import build_audit_event
from gateway.core.conversation import Conversation
from gateway.core.reply import FAILED_CLOSED_TEXT, REFUSAL_TEXT, GatewayReply, ReplyOutcome
from gateway.guards.contract import GuardDecision
from gateway.guards.pipeline import GuardRun, PipelineVerdict
from gateway.identity.resolver import UserIdentity

USER_INPUT_CHECKPOINT_NAME = "user_input"
REQUEST_ID_PREFIX = "req_"
REQUEST_ID_HEX_LENGTH = 12
FAILED_CLOSED_REASON = "failed closed: {error_type}"
AUDIT_WRITE_FAILED_MESSAGE = "audit write failed for %s"
REQUEST_FAILED_MESSAGE = "request %s failed closed"
REPLY_STEP_OUTCOMES = {
    ReplyOutcome.ANSWERED: StepOutcome.PASSED,
    ReplyOutcome.REFUSED: StepOutcome.DENIED,
    ReplyOutcome.FAILED_CLOSED: StepOutcome.FAILED,
}

logger = logging.getLogger(__name__)


class PipelineSource(Protocol):
    def get_current(self) -> CheckpointPipelines: ...


@dataclass(frozen=True)
class ReplyDecision:
    outcome: ReplyOutcome
    text: str
    reason: str


class Gateway:
    def __init__(self, pipeline_source: PipelineSource, chat_agent: ChatAgent, audit_log: AuditLog):
        self._pipeline_source = pipeline_source
        self._chat_agent = chat_agent
        self._audit_log = audit_log

    async def handle(self, conversation: Conversation, user: UserIdentity) -> GatewayReply:
        request_id = f"{REQUEST_ID_PREFIX}{uuid.uuid4().hex[:REQUEST_ID_HEX_LENGTH]}"
        trace_id = new_trace_id()
        started = time.perf_counter()
        recorder = TraceRecorder()
        pipelines = self._pipeline_source.get_current()
        latest_user_message = conversation.get_latest_user_message()
        root_step_id = recorder.add_step(
            TraceStepKind.USER_PROMPT, user.user_id, StepOutcome.INFO,
            detail={"message": latest_user_message.content if latest_user_message else "", "config_version": pipelines.config_version},
        )
        decision = await self._decide_reply(request_id, conversation, pipelines, recorder, root_step_id, trace_id)
        recorder.add_step(
            TraceStepKind.REPLY, decision.outcome.value, REPLY_STEP_OUTCOMES[decision.outcome],
            parent_step_id=root_step_id, reason=decision.reason, detail={"text": decision.text},
        )
        self._write_audit_event(build_audit_event(
            request_id, trace_id, user, pipelines.config_version, decision.outcome, decision.reason,
            recorder.steps, round((time.perf_counter() - started) * 1000, 2),
        ))
        return GatewayReply(request_id, decision.outcome, decision.text)

    async def _decide_reply(
        self,
        request_id: str,
        conversation: Conversation,
        pipelines: CheckpointPipelines,
        recorder: TraceRecorder,
        root_step_id: str,
        trace_id: str,
    ) -> ReplyDecision:
        try:
            verdict = await pipelines.user_input.evaluate(conversation)
            record_checkpoint(recorder, root_step_id, USER_INPUT_CHECKPOINT_NAME, verdict)
            if verdict.refused:
                return ReplyDecision(ReplyOutcome.REFUSED, REFUSAL_TEXT, get_refusal_reason(verdict))
            agent_reply = await self._chat_agent.reply(conversation, recorder, root_step_id, trace_id)
            return ReplyDecision(ReplyOutcome.ANSWERED, agent_reply.answer, "")
        except Exception as error:
            logger.exception(REQUEST_FAILED_MESSAGE, request_id)
            return ReplyDecision(ReplyOutcome.FAILED_CLOSED, FAILED_CLOSED_TEXT, FAILED_CLOSED_REASON.format(error_type=type(error).__name__))

    def _write_audit_event(self, event: dict) -> None:
        try:
            self._audit_log.append(event)
        except Exception:
            # Losing an audit line is bad, but refusing to answer because the disk is full is worse for a demo.
            logger.exception(AUDIT_WRITE_FAILED_MESSAGE, event["request_id"])


def record_checkpoint(recorder: TraceRecorder, root_step_id: str, checkpoint_name: str, verdict: PipelineVerdict) -> None:
    checkpoint_step_id = recorder.add_step(
        TraceStepKind.CHECKPOINT, checkpoint_name, StepOutcome.DENIED if verdict.refused else StepOutcome.PASSED,
        parent_step_id=root_step_id, detail={"decision": verdict.decision.value, "guard_count": len(verdict.runs)},
    )
    for run in verdict.runs:
        recorder.add_step(
            TraceStepKind.GUARD, run.instance_id, get_guard_step_outcome(run), parent_step_id=checkpoint_step_id,
            reason=run.verdict.reason, detail=build_guard_step_detail(run), started_at=run.started_at, duration_ms=run.duration_ms,
        )


def get_guard_step_outcome(run: GuardRun) -> StepOutcome:
    if run.blocks:
        return StepOutcome.DENIED
    if run.errored:
        return StepOutcome.FAILED
    if run.verdict.decision is GuardDecision.REFUSE:
        return StepOutcome.INFO  # monitor mode: would have refused
    return StepOutcome.PASSED


def build_guard_step_detail(run: GuardRun) -> dict:
    return {
        "type": run.type_name,
        "mode": run.mode.value,
        "decision": run.verdict.decision.value,
        "errored": run.errored,
        "findings": [{"label": finding.label, "score": finding.score} for finding in run.verdict.findings],
        **run.verdict.detail,
    }


def get_refusal_reason(verdict: PipelineVerdict) -> str:
    return next(f"{run.instance_id}: {run.verdict.reason}" for run in verdict.runs if run.blocks)
