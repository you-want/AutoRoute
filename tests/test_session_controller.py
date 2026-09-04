from scripts import session_controller
from scripts.session_controller import current_thread_id, switch_command


def test_switch_command_targets_current_thread(monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-123")
    command = switch_command("gpt-5.6-sol", "xhigh", "继续当前任务")
    assert command == [
        "codex", "queue", "--thread", "thread-123", "--message", "继续当前任务",
        "-m", "gpt-5.6-sol", "-c", 'model_reasoning_effort="xhigh"',
    ]


def test_switch_command_requires_thread(monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    assert current_thread_id() is None
    assert switch_command("gpt-5.6-sol", "high", "继续") is None


def test_queue_model_switch_runs_codex_queue(monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-123")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(session_controller.subprocess, "run", fake_run)
    ok, command, error = session_controller.queue_model_switch("gpt-5.6-sol", "high", "继续")
    assert ok is True
    assert error is None
    assert command == seen["command"]
    assert command[1:4] == ["queue", "--thread", "thread-123"]
