"""The model and real tool response both affect the business result."""

import httpx

from workloads.demo_agent import agent


def prepare(monkeypatch):
    events = []
    monkeypatch.setattr(
        agent.ev,
        "service_identity",
        lambda service, **kwargs: {"instance_id": service + "@generation"},
    )
    monkeypatch.setattr(
        agent.ev,
        "emit_task_event",
        lambda event, task, attrs, **kwargs: events.append((event, task, attrs, kwargs)),
    )
    return events


def test_tool_503_and_task_failure_have_same_task_and_dependency_identity(monkeypatch):
    events = prepare(monkeypatch)
    monkeypatch.setattr(agent, "_completion", lambda *args: "CPU is constrained.")

    def unavailable(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "503", request=httpx.Request("POST", "http://tool"), response=httpx.Response(503)
        )

    monkeypatch.setattr(agent, "call_tool", unavailable)
    result = agent.run_task("task-real")
    assert result["status"] == "failed"
    tool_event = next(item for item in events if item[0] == "tool.failed")
    task_event = next(item for item in events if item[0] == "task.failed")
    assert tool_event[1] == task_event[1] == "task-real"
    assert tool_event[2]["http_status"] == 503
    assert tool_event[3]["instance_id"] == "demo-tool@generation"
    assert task_event[3]["instance_id"] == "demo-agent@generation"


def test_synthesis_uses_first_model_answer_and_real_tool_result(monkeypatch):
    prepare(monkeypatch)
    prompts = []

    def inference(prompt, *_args):
        prompts.append(prompt)
        return "First observation." if len(prompts) == 1 else "Action using runbook."

    monkeypatch.setattr(agent, "_completion", inference)
    monkeypatch.setattr(
        agent, "call_tool", lambda *args, **kwargs: {"runbook": {"checks": ["Verify quota"]}}
    )
    result = agent.run_task("task-success")
    assert result["status"] == "succeeded"
    assert "First observation." in prompts[1]
    assert "Verify quota" in prompts[1]
    assert result["summary"] == "Action using runbook."
