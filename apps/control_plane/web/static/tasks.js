(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", () => {
    const LT = window.LT, esc = LT.esc, initial = new URLSearchParams(location.search);
    let selected = initial.get("task_id"), items = [];
    document.getElementById("task-id").value = selected || "";
    document.getElementById("task-status").value = initial.get("status") || "";
    async function showDetail(task, updateUrl = true) {
      selected = task.task_id;
      if (updateUrl) history.replaceState(null, "", `/pages/tasks?task_id=${encodeURIComponent(selected)}`);
      const node = document.getElementById("task-detail"); node.hidden = false;
      let detail = { task, events: [], incident_ids: task.incident_ids || [] };
      try { detail = await LT.api(`/api/v1/tasks/${encodeURIComponent(task.task_id)}`); }
      catch (error) { if (error.status !== 404) LT.notice(error.message, true); }
      const t = detail.task || task, incidents = detail.incident_ids || t.incident_ids || [];
      node.innerHTML = `<h2>任务 <span class="mono text-wrap">${esc(t.task_id)}</span> ${LT.stateBadge(t.status)} ${LT.provBadge(t.status_provenance)}</h2><dl class="facts"><dt>总耗时</dt><dd>${LT.fmtDuration(t.duration_ms)}</dd><dt>模型耗时</dt><dd>${LT.fmtDuration(t.model_duration_ms)}</dd><dt>工具耗时</dt><dd>${LT.fmtDuration(t.tool_duration_ms)}</dd><dt>开始时间</dt><dd>${LT.fmtTime(t.started_at)}</dd><dt>结束时间</dt><dd>${LT.fmtTime(t.ended_at)}</dd><dt>错误类型</dt><dd class="mono">${esc(t.error_class || "—")}</dd><dt>服务实例</dt><dd class="mono">${LT.resourceLink(t.service_instance_id)}</dd><dt>Trace ID</dt><dd class="mono">${esc(t.trace_id || "—")}</dd></dl><h3>关联诊断</h3>${incidents.length ? `<ul>${incidents.map(id => `<li>${LT.diagnosisLink(typeof id === "object" ? id.id : id, `事件单 ${(typeof id === "object" ? id.id : id).slice(0, 8)}`)}</li>`).join("")}</ul>` : '<p class="muted">此任务尚未关联事件单。成功任务无需故障诊断；失败任务请检查采集和诊断状态。</p>'}`;
      if ((detail.events || []).length) node.innerHTML += '<h3>任务事件</h3><table class="data"><thead><tr><th>时间</th><th>事件</th><th>来源</th><th>摘要</th></tr></thead><tbody>' + detail.events.map(event => `<tr><td>${LT.fmtTime(event.occurred_at || event.at)}</td><td class="mono">${esc(event.type || event.event_type)}</td><td>${LT.provBadge(event.provenance)}</td><td>${LT.values(event.summary || event.attributes || event.payload || {})}</td></tr>`).join("") + '</tbody></table>';
      document.querySelectorAll("#task-table tr[data-task]").forEach(row => row.classList.toggle("task-selected", row.dataset.task === selected));
    }
    async function load() {
      const query = new URLSearchParams({ limit: "200" }), taskId = document.getElementById("task-id").value.trim(), status = document.getElementById("task-status").value;
      if (taskId) query.set("task_id", taskId); if (status) query.set("status", status);
      if (initial.get("service_instance_id")) query.set("service_instance_id", initial.get("service_instance_id"));
      const data = await LT.api("/api/v1/tasks?" + query);
      items = (data.items || []).filter(t => !taskId || t.task_id === taskId);
      const body = document.querySelector("#task-table tbody");
      body.innerHTML = items.map(t => `<tr data-task="${esc(t.task_id)}" class="${selected === t.task_id ? "task-selected" : ""}"><td class="mono text-wrap">${esc(t.task_id)}</td><td>${LT.stateBadge(t.status)} ${LT.provBadge(t.status_provenance)}</td><td class="mono">${esc(t.error_class || "—")}</td><td>${LT.fmtDuration(t.duration_ms)}</td><td>${LT.fmtDuration(t.model_duration_ms)}</td><td>${LT.fmtDuration(t.tool_duration_ms)}</td><td>${LT.fmtTime(t.started_at)}</td><td><button type="button" class="secondary" data-task="${esc(t.task_id)}">详情</button></td></tr>`).join("") || '<tr><td colspan="8">当前筛选下暂无任务；请检查任务 ID、筛选条件或数据源状态。</td></tr>';
      body.querySelectorAll("button[data-task]").forEach(button => button.addEventListener("click", () => showDetail(items.find(t => t.task_id === button.dataset.task))));
      const match = items.find(t => t.task_id === selected); if (match) await showDetail(match, false);
    }
    const refresh = LT.installRefresh(load);
    document.getElementById("task-filter").addEventListener("submit", event => { event.preventDefault(); selected = document.getElementById("task-id").value.trim() || null; refresh(); });
    document.getElementById("task-reset").addEventListener("click", () => { document.getElementById("task-id").value = ""; document.getElementById("task-status").value = ""; selected = null; history.replaceState(null, "", "/pages/tasks"); document.getElementById("task-detail").hidden = true; refresh(); });
    refresh();
  });
})();
