"""US7 集成测试：安装/卸载语义（可自动化部分，T128）。

完整干净环境安装/卸载在真实 Linux 执行（见 docs/reproduction-report.md）；
本测试验证脚本语义：幂等、默认保留数据、数据删除独立。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_uninstall_keeps_data_by_default() -> None:
    """卸载脚本不删除数据卷；purge-data 是独立显式操作。"""
    uninstall = (REPO_ROOT / "deploy/uninstall.sh").read_text(encoding="utf-8")
    assert "docker compose" in uninstall and "down" in uninstall
    assert "volume rm" not in uninstall, "卸载不得删除数据卷"
    assert "purge-data" in uninstall, "卸载必须指向独立的数据删除流程"


def test_purge_requires_confirmation() -> None:
    purge = (REPO_ROOT / "deploy/purge-data.sh").read_text(encoding="utf-8")
    assert "YES" in purge, "数据删除必须二次确认"
    assert "docker volume rm" in purge


def test_install_idempotent_and_generates_token() -> None:
    install = (REPO_ROOT / "deploy/install.sh").read_text(encoding="utf-8")
    assert "if [[ ! -f .env ]]" in install, "配置安装必须幂等"
    assert "PROBE_REGISTRATION_TOKEN" in install


def test_scripts_executable() -> None:
    for name in ("install.sh", "uninstall.sh", "purge-data.sh", "preflight.sh"):
        path = REPO_ROOT / "deploy" / name
        assert path.exists()
        mode = path.stat().st_mode
        assert mode & 0o111, f"{name} 应可执行"
