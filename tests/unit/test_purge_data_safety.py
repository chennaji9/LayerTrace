"""Data deletion is constrained to exact volumes with verified project labels."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_docker(tmp_path):
    binary = tmp_path / "docker"
    log = tmp_path / "operations.jsonl"
    binary.write_text("""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
with Path(os.environ['PURGE_TEST_LOG']).open('a') as output:
    output.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:3] == ['volume', 'inspect']:
    print(sys.argv[-1] if sys.argv[4] == '{{.Name}}' else os.environ['PURGE_TEST_PROJECT'])
elif sys.argv[1:3] != ['volume', 'rm']:
    sys.exit(2)
""")
    binary.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"], PURGE_TEST_LOG=str(log), PURGE_TEST_PROJECT="layertrace-vm")

    def invoke(arguments, answer="YES\n", project="layertrace-vm"):
        env["PURGE_TEST_PROJECT"] = project
        result = subprocess.run(["bash", str(ROOT / "deploy/purge-data.sh"), *arguments], env=env, input=answer, capture_output=True, text=True)
        operations = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, operations

    return invoke


def test_purge_requires_explicit_exact_names(fake_docker):
    result, calls = fake_docker([])
    assert result.returncode == 2 and calls == []


def test_purge_does_not_touch_other_project_or_cancelled_selection(fake_docker):
    result, calls = fake_docker(["layertrace-vm_probe-state"], project="other-project")
    assert result.returncode != 0
    assert not any(call[:2] == ["volume", "rm"] for call in calls)


def test_purge_cancel_retains_verified_volume(fake_docker):
    result, calls = fake_docker(["layertrace-vm_probe-state"], answer="NO\n")
    assert result.returncode != 0
    assert not any(call[:2] == ["volume", "rm"] for call in calls)


def test_purge_removes_only_explicit_verified_volumes(fake_docker):
    result, calls = fake_docker(["layertrace-vm_probe-state", "layertrace-vm_client-results"])
    assert result.returncode == 0
    assert [call for call in calls if call[:2] == ["volume", "rm"]] == [
        ["volume", "rm", "--", "layertrace-vm_probe-state"],
        ["volume", "rm", "--", "layertrace-vm_client-results"],
    ]
    assert not any(call[:2] == ["volume", "ls"] for call in calls)
