"""layertrace CLI。

CLI 与页面一样只通过 /api/v1/* 版本化接口读写数据（FR-009），禁止直连数据库或读取剧本。
"""

from __future__ import annotations

import os

import click


@click.group()
@click.option(
    "--api-base",
    default=lambda: os.environ.get("LAYERTRACE_API", "http://127.0.0.1:8000"),
    show_default="http://127.0.0.1:8000",
    help="控制面 API 基址（跨主机必须使用 HTTPS）。",
)
@click.pass_context
def main(ctx: click.Context, api_base: str) -> None:
    """层迹 LayerTrace 命令行工具。"""
    ctx.ensure_object(dict)
    ctx.obj["api_base"] = api_base.rstrip("/")


@main.command("serve")
@click.option("--host", default=None, help="绑定地址，默认取 WEB_HOST（仅 loopback 允许 HTTP）。")
@click.option("--port", default=None, type=int, help="绑定端口，默认取 WEB_PORT。")
def serve(host: str | None, port: int | None) -> None:
    """启动控制面 API 服务。"""
    import uvicorn

    from apps.control_plane.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "apps.control_plane.main:app",
        host=host or settings.web_host,
        port=port or settings.web_port,
    )


@main.command("migrate")
@click.option("--revision", default="head", show_default=True)
def migrate(revision: str) -> None:
    """执行 Alembic 数据库迁移。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "apps/control_plane/migrations")
    command.upgrade(cfg, revision)


# ---------------------------------------------------------------- 场景


@main.group("scenario")
def scenario() -> None:
    """回放 / 实验场景运行、验证与恢复（全部经正式 API）。"""


def _api_call(ctx: click.Context, method: str, path: str, json_body: dict | None = None) -> dict:
    import httpx

    base = ctx.obj["api_base"]
    token = os.environ.get("LAYERTRACE_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    res = httpx.request(method, f"{base}{path}", json=json_body, headers=headers, timeout=60)
    if res.status_code >= 400:
        raise click.ClickException(f"API {res.status_code}: {res.text}")
    return res.json()


@scenario.command("run")
@click.argument("name")
@click.option("--mode", type=click.Choice(["replay", "lab"]), default="replay", show_default=True)
@click.option("--timeout", type=int, default=None, help="超时秒数（≤900）")
@click.pass_context
def scenario_run(ctx: click.Context, name: str, mode: str, timeout: int | None) -> None:
    """运行剧本（如 replay:story-oom 中的 story-oom）。"""
    body: dict = {"mode": mode}
    if timeout:
        body["timeout_seconds"] = timeout
    result = _api_call(ctx, "POST", f"/api/v1/scenarios/{name}:run", body)
    click.echo(
        f"run={result['id']} state={result['state']} deadline={result['deadline_at']}"
    )


@scenario.command("verify")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="输出完整 JSON")
@click.pass_context
def scenario_verify(ctx: click.Context, run_id: str, as_json: bool) -> None:
    """使用剧本期望断言验证一次运行。"""
    result = _api_call(ctx, "POST", f"/api/v1/scenario-runs/{run_id}:verify")
    if as_json:
        import json as jsonlib

        click.echo(jsonlib.dumps(result, ensure_ascii=False, indent=2))
        return
    verdict = result.get("verification_result") or {}
    icon = "✓" if verdict.get("passed") else "✗"
    click.echo(f"{icon} run={run_id} passed={verdict.get('passed')}")
    for item in verdict.get("assertions", []):
        mark = "✓" if item.get("passed") else "✗"
        click.echo(f"  {mark} {item.get('path')} {item.get('operator')} — {item.get('detail')}")


@scenario.command("restore")
@click.argument("run_id")
@click.pass_context
def scenario_restore(ctx: click.Context, run_id: str) -> None:
    """恢复场景目标到注入前状态。"""
    result = _api_call(ctx, "POST", f"/api/v1/scenario-runs/{run_id}:restore")
    click.echo(f"run={result['id']} state={result['state']}")


# ---------------------------------------------------------------- 事件单


@main.group("incidents")
def incidents() -> None:
    """事件单查询与解释（全部经正式 API）。"""


@incidents.command("list")
@click.option("--json", "as_json", is_flag=True, help="输出完整 JSON")
@click.option("--state", default=None, help="过滤状态 open/acknowledged/resolved")
@click.pass_context
def incidents_list(ctx: click.Context, as_json: bool, state: str | None) -> None:
    """列出事件单。"""
    query = f"?state={state}" if state else ""
    data = _api_call(ctx, "GET", f"/api/v1/incidents{query}")
    if as_json:
        import json as jsonlib

        click.echo(jsonlib.dumps(data, ensure_ascii=False, indent=2))
        return
    click.echo(
        f"原始告警 {data['raw_alert_count']} / 事件单 {data['incident_count']} / 通知 {data['notification_count']}"
    )
    for item in data["items"]:
        click.echo(
            f"  {item['id'][:8]}  {item['severity']}  {item['state']:<12} "
            f"告警 {item['alert_count']:<4} 最近 {item['last_seen']}"
        )


@incidents.command("explain")
@click.argument("incident_id")
@click.option("--json", "as_json", is_flag=True, help="输出完整 JSON")
@click.pass_context
def incidents_explain(ctx: click.Context, incident_id: str, as_json: bool) -> None:
    """输出事件单的告警、时间线与全部诊断修订。"""
    detail = _api_call(ctx, "GET", f"/api/v1/incidents/{incident_id}")
    diagnoses = _api_call(ctx, "GET", f"/api/v1/incidents/{incident_id}/diagnoses")
    if as_json:
        import json as jsonlib

        click.echo(jsonlib.dumps({"incident": detail, "diagnoses": diagnoses}, ensure_ascii=False, indent=2))
        return
    click.echo(f"事件单 {detail['id']} {detail['severity']} {detail['state']}")
    for diag in diagnoses:
        click.echo(f"  修订 {diag['revision']}（{diag['created_at']}）规则版本 {diag['rule_versions']}")
        for cand in diag["candidates"]:
            click.echo(
                f"    [{cand['support_level']}] {cand['title']} ({cand['rule_id']})"
                f" 支持 {len(cand['supporting'])} 反证 {len(cand['contradicting'])} 缺失 {len(cand['missing'])}"
            )
            for rec in cand["recommendations"]:
                click.echo(f"      建议：{rec}")


if __name__ == "__main__":
    main()
