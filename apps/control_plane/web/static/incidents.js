(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", () => {
    const LT = window.LT, esc = LT.esc;
    async function load() {
      const query = new URLSearchParams({ limit: "200" });
      const state = document.getElementById("incident-state").value, severity = document.getElementById("incident-severity").value;
      if (state) query.set("state", state); if (severity) query.set("severity", severity);
      const data = await LT.api("/api/v1/incidents?" + query);
      document.getElementById("noise-panel").innerHTML = `<h2>降噪效果</h2><table class="data"><tbody><tr><th>原始告警（Alert）</th><td>${data.raw_alert_count}</td></tr><tr><th>聚合事件单</th><td>${data.incident_count}</td></tr><tr><th>实际通知</th><td>${data.notification_count}</td></tr></tbody></table><p class="muted small">告警、事件单和通知是不同计数。静默期间告警与事件单继续更新。</p>`;
      const body = document.querySelector("#incident-table tbody");
      body.innerHTML = (data.items || []).map(inc => `<tr><td><div class="mono">${esc(inc.id.slice(0, 8))}</div><div class="small mono text-wrap">${esc(inc.fingerprint)}</div></td><td><span class="sev sev-${esc(inc.severity)}">${esc(inc.severity)}</span></td><td>${LT.stateBadge(inc.state)}</td><td>${inc.alert_count}</td><td>${inc.silenced_until ? `至 ${LT.fmtTime(inc.silenced_until)}` : "未静默"}</td><td>${LT.fmtTime(inc.last_seen)}</td><td>${inc.state === "open" ? `<button type="button" data-ack="${esc(inc.id)}">确认</button> ` : ""}${LT.diagnosisLink(inc.id, "诊断")} <button type="button" class="secondary" data-silence="${esc(inc.fingerprint)}">静默通知</button></td></tr>`).join("") || '<tr><td colspan="7">当前筛选下暂无事件单</td></tr>';
      body.querySelectorAll("button[data-ack]").forEach(button => button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await LT.api(`/api/v1/incidents/${encodeURIComponent(button.dataset.ack)}:acknowledge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ actor: "web-ui", note: "已查看并开始处置" }) });
          LT.notice("事件单已确认，诊断事实持续更新。"); await load();
        } catch (error) { LT.notice(error.message, true); button.disabled = false; }
      }));
      body.querySelectorAll("button[data-silence]").forEach(button => button.addEventListener("click", () => {
        document.getElementById("silence-fingerprint").value = button.dataset.silence;
        document.getElementById("silence-panel").scrollIntoView({ behavior: "smooth", block: "start" });
        document.getElementById("silence-reason").focus();
      }));
    }
    const refresh = LT.installRefresh(load);
    document.getElementById("incident-state").addEventListener("change", refresh);
    document.getElementById("incident-severity").addEventListener("change", refresh);
    document.getElementById("silence-form").addEventListener("submit", async event => {
      event.preventDefault();
      const form = new FormData(event.target), now = new Date(), minutes = Number(form.get("minutes"));
      const result = document.getElementById("silence-result"), button = event.target.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        await LT.api("/api/v1/silences", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ matcher: { fingerprint: form.get("fingerprint") }, reason: form.get("reason"), created_by: form.get("created_by"), starts_at: now.toISOString(), ends_at: new Date(now.getTime() + minutes * 60000).toISOString() }) });
        result.className = "notice"; result.textContent = `通知静默已创建，${minutes} 分钟内事实继续更新。`;
        await load();
      } catch (error) { result.className = "notice notice-error"; result.textContent = error.message; }
      finally { button.disabled = false; }
    });
    refresh();
  });
})();
