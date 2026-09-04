import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "codex-with-autoroute"
CATALOG = ROOT / "tests" / "catalog.json"


def test_wrapper_launches_codex_with_routed_model(tmp_path):
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    environment = dict(
        os.environ,
        AUTOROUTE_CODEX_BIN=str(fake_codex),
        AUTOROUTE_MODELS_FILE=str(CATALOG),
        AUTOROUTE_SKIP_PROBE="1",
    )

    completed = subprocess.run(
        [str(WRAPPER), "Add a loading state to the Button component"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    argv = json.loads(completed.stdout)
    assert argv[:4] == ["-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="low"']
    assert argv[-1] == "Add a loading state to the Button component"


def test_wrapper_passthrough_for_codex_subcommand(tmp_path):
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    environment = dict(
        os.environ,
        AUTOROUTE_CODEX_BIN=str(fake_codex),
        AUTOROUTE_MODELS_FILE=str(CATALOG),
        AUTOROUTE_SKIP_PROBE="1",
    )

    completed = subprocess.run(
        [str(WRAPPER), "resume", "--last"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert json.loads(completed.stdout) == ["resume", "--last"]
