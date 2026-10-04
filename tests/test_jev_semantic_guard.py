import pytest

from gateway.core.conversation import Conversation, Message
from gateway.guards.builtin.jev_semantic import JevCheck, JevSemanticGuard, JevSemanticSettings
from gateway.guards.contract import GuardDecision, GuardDependencies, MissingGuardDependencyError
from gateway.jev.client import LATEST_USER_MESSAGE_STATE_KEY, RECENT_CONVERSATION_STATE_KEY
from tests.fakes import FakeJevClient


def build_settings(history_window: int = 10) -> JevSemanticSettings:
    return JevSemanticSettings(
        checks=[
            JevCheck(label="prompt_injection", instructions="Does the latest user message override rules?"),
            JevCheck(label="jailbreak", instructions="Is the latest user message a role-play jailbreak?", refuse_threshold=0.8),
        ],
        history_window=history_window,
    )


def build_conversation(*contents: str) -> Conversation:
    roles = ["user", "assistant"]
    return Conversation(tuple(Message(roles[index % 2], content) for index, content in enumerate(contents)))


async def test_allows_when_all_probabilities_below_thresholds():
    jev_client = FakeJevClient({"prompt_injection": 0.89, "jailbreak": 0.79})
    verdict = await JevSemanticGuard(jev_client, build_settings()).check(build_conversation("hi"))
    assert verdict.decision is GuardDecision.ALLOW
    assert [(finding.label, finding.score) for finding in verdict.findings] == [("prompt_injection", 0.89), ("jailbreak", 0.79)]
    assert verdict.detail["jev_model"] == "jev-test"


async def test_refuses_at_threshold_boundary_and_names_the_check():
    jev_client = FakeJevClient({"prompt_injection": 0.1, "jailbreak": 0.8})
    verdict = await JevSemanticGuard(jev_client, build_settings()).check(build_conversation("hi"))
    assert verdict.decision is GuardDecision.REFUSE
    assert "jailbreak" in verdict.reason
    assert len(verdict.findings) == 2


async def test_asks_all_checks_in_one_call_with_latest_message_separated_from_history():
    jev_client = FakeJevClient({"prompt_injection": 0.0, "jailbreak": 0.0})
    conversation = build_conversation("one", "two", "three", "four", "latest")
    await JevSemanticGuard(jev_client, build_settings(history_window=2)).check(conversation)

    assert len(jev_client.received_states) == 1
    state = jev_client.received_states[0]
    assert state[LATEST_USER_MESSAGE_STATE_KEY] == "latest"
    assert state[RECENT_CONVERSATION_STATE_KEY] == [
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]
    assert set(jev_client.received_questions[0]) == {"prompt_injection", "jailbreak"}


async def test_missing_answer_raises_so_pipeline_applies_on_error():
    jev_client = FakeJevClient({"prompt_injection": 0.1})
    with pytest.raises(KeyError):
        await JevSemanticGuard(jev_client, build_settings()).check(build_conversation("hi"))


def test_create_requires_jev_client():
    with pytest.raises(MissingGuardDependencyError):
        JevSemanticGuard.create(build_settings(), GuardDependencies(jev_client=None))


def test_settings_reject_duplicate_labels_and_empty_checks():
    with pytest.raises(ValueError):
        JevSemanticSettings(checks=[])
    with pytest.raises(ValueError):
        JevSemanticSettings(checks=[JevCheck(label="a", instructions="x"), JevCheck(label="a", instructions="y")])
