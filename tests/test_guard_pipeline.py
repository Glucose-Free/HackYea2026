import time

from gateway.guards.contract import GuardDecision
from gateway.guards.pipeline import GuardMode, GuardPipeline
from tests.fakes import REFUSE_VERDICT, ScriptedGuard, build_configured_guard_for_test


async def test_empty_pipeline_allows():
    verdict = await GuardPipeline([]).evaluate("subject")
    assert verdict.decision is GuardDecision.ALLOW
    assert verdict.runs == ()


async def test_enforce_mode_refusal_refuses():
    pipeline = GuardPipeline([
        build_configured_guard_for_test(ScriptedGuard(), "ok"),
        build_configured_guard_for_test(ScriptedGuard(REFUSE_VERDICT), "bad"),
    ])
    verdict = await pipeline.evaluate("subject")
    assert verdict.refused
    assert [run.instance_id for run in verdict.runs] == ["ok", "bad"]
    assert [run.blocks for run in verdict.runs] == [False, True]


async def test_monitor_mode_refusal_is_recorded_but_does_not_refuse():
    pipeline = GuardPipeline([build_configured_guard_for_test(ScriptedGuard(REFUSE_VERDICT), mode=GuardMode.MONITOR)])
    verdict = await pipeline.evaluate("subject")
    assert not verdict.refused
    assert verdict.runs[0].verdict.decision is GuardDecision.REFUSE
    assert verdict.runs[0].mode is GuardMode.MONITOR


async def test_timeout_applies_on_error_decision():
    slow_guard = ScriptedGuard(delay_seconds=0.5)
    refusing = GuardPipeline([build_configured_guard_for_test(slow_guard, timeout_seconds=0.05)])
    allowing = GuardPipeline([build_configured_guard_for_test(slow_guard, timeout_seconds=0.05, on_error=GuardDecision.ALLOW)])

    refused_verdict = await refusing.evaluate("s")
    allowed_verdict = await allowing.evaluate("s")

    assert refused_verdict.refused and refused_verdict.runs[0].errored
    assert "timed out" in refused_verdict.runs[0].verdict.reason
    assert not allowed_verdict.refused and allowed_verdict.runs[0].errored


async def test_exception_applies_on_error_decision():
    pipeline = GuardPipeline([build_configured_guard_for_test(ScriptedGuard(error=ConnectionError("down")))])
    verdict = await pipeline.evaluate("s")
    assert verdict.refused
    assert verdict.runs[0].errored
    assert "ConnectionError" in verdict.runs[0].verdict.reason


async def test_guards_run_concurrently():
    pipeline = GuardPipeline([
        build_configured_guard_for_test(ScriptedGuard(delay_seconds=0.2), "a"),
        build_configured_guard_for_test(ScriptedGuard(delay_seconds=0.2), "b"),
    ])
    started = time.perf_counter()
    await pipeline.evaluate("s")
    assert time.perf_counter() - started < 0.35


async def test_every_guard_receives_the_subject_and_type_name_is_recorded():
    guard = ScriptedGuard()
    verdict = await GuardPipeline([build_configured_guard_for_test(guard)]).evaluate("the subject")
    assert guard.checked_subjects == ["the subject"]
    assert verdict.runs[0].type_name == "scripted"
