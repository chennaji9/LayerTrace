(function () {
  "use strict";
  const PROV = { observed: "观测", simulated: "模拟", replayed: "回放", inferred: "推断", manual: "人工" };
  const STATES = {
    current: "当前", stale: "采集陈旧", gap: "采集空档", unknown: "未知", unavailable: "不可用",
    healthy: "正常", degraded: "降级", not_configured: "未配置", permission_denied: "权限不足",
    open: "待处理", acknowledged: "已确认", resolved: "已恢复", running: "运行中",
    succeeded: "成功", failed: "失败", timed_out: "超时", interrupted: "中断", stopped: "已停止",
    pending: "等待中", verifying: "验证中", restoring: "恢复中", restored: "恢复完成", restore_failed: "恢复失败",
    trusted: "可信", high: "高支持", medium: "中支持", low: "低支持",
  };
  const KINDS = { project: "项目", host: "宿主机", vm: "虚拟机", container: "容器", service: "服务", service_instance: "服务实例", gpu: "GPU", vgpu: "vGPU" };
  const FIELDS = {
    oom_killed: "内核 OOM 标记", exit_code: "退出码", oom_kill_delta: "OOM 计数增量",
    available_ratio: "可用内存比例", used_bytes: "已用内存（字节）", memory_limit_bytes: "内存限制（字节）",
    error_class: "错误类型", phase: "阶段", signal: "信号", reason: "原因", state: "状态",
    cpu_percent: "CPU 使用率（%）", quota: "CPU 配额", period_us: "调度周期（微秒）",
    throttled_periods: "节流周期数", throttled_ratio: "节流比例", throttled_delta: "节流周期增量",
    http_status: "HTTP 状态码", tool_name: "工具名称", model_name: "模型名称", duration_ms: "耗时（毫秒）",
    p95_ms: "P95 延迟（毫秒）", baseline_p95_ms: "健康基线 P95（毫秒）", sample_count: "样本数",
    container_id: "容器 ID", task_id: "任务 ID", resource_id: "资源 ID", memory_working_set_bytes: "工作集（字节）",
    matching_container: "匹配故障容器", matching_task: "匹配失败任务", clock_skew_seconds: "时钟偏差（秒）",
  };
  function esc(value) { const div = document.createElement("div"); div.textContent = value == null ? "" : String(value); return div.innerHTML; }
  function cookie(name) {
    const pair = document.cookie.split(";").map(v => v.trim()).find(v => v.startsWith(name + "="));
    return pair ? decodeURIComponent(pair.slice(name.length + 1)) : "";
  }
  function safeClass(value, allowed) { return Object.hasOwn(allowed, value) ? value : "unknown"; }
  function displayValue(value) {
    if (Array.isArray(value)) return value.map(displayValue);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /(?:token|secret|password|authorization|api[_-]?key|access[_-]?key|cookie)/i.test(key) ? "[已隐藏]" : displayValue(item)]));
    if (typeof value === "string") return value.replace(/\b(Bearer|OAuth|ZStack)\s+[^\s,;]+/gi, "$1 [已隐藏]");
    return value;
  }
  window.LT = {
    esc,
    displayValue,
    kinds: KINDS,
    label(value) { return STATES[value] || value || "—"; },
    provBadge(value) { return `<span class="prov prov-${safeClass(value, PROV)}">${esc(PROV[value] || "来源未知")}</span>`; },
    dataStateBadge(value) { return `<span class="dstate dstate-${safeClass(value, STATES)}">${esc(STATES[value] || value)}</span>`; },
    stateDot(value) { return `<span class="dot dot-${safeClass(value, STATES)}"></span>${esc(STATES[value] || value)}`; },
    stateBadge(value) { return `<span class="status status-${safeClass(value, STATES)}" title="${esc(value)}">${esc(STATES[value] || value)}</span>`; },
    async api(path, options = {}) {
      const headers = new Headers(options.headers || {}); headers.set("Accept", "application/json");
      if (!["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase())) {
        const csrf = cookie("layertrace_csrf"); if (csrf) headers.set("X-CSRF-Token", csrf);
      }
      const res = await fetch(path, { ...options, headers, credentials: "same-origin", cache: "no-store" });
      if (res.status === 401 && document.body.dataset.page !== "login") window.location.assign("/login?next=" + encodeURIComponent(location.pathname + location.search));
      if (!res.ok) {
        let detail = "";
        try { const body = await res.json(); detail = body.message || body.detail?.message || body.detail || ""; } catch (_) {}
        const error = new Error(detail || `请求失败（${res.status}）`); error.status = res.status; throw error;
      }
      return res.status === 204 ? null : res.json();
    },
    el(tag, className, value) { const node = document.createElement(tag); if (className) node.className = className; if (value != null) node.textContent = value; return node; },
    fmtTime(value) {
      if (!value) return "—"; const date = new Date(value);
      return Number.isNaN(date.getTime()) ? esc(value) : date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
    },
    fmtDuration(value) { return value == null ? "—" : `${Number(value).toLocaleString("zh-CN")} ms`; },
    fmtBytes(value) { return value == null ? "—" : `${(Number(value) / 1048576).toFixed(1)} MiB`; },
    resourceLink(id, label, at) { return id ? `<a href="/pages/topology?resource_id=${encodeURIComponent(id)}${at ? "&amp;at=" + encodeURIComponent(at) : ""}">${esc(label || id)}</a>` : "—"; },
    taskLink(id, label) { return id ? `<a href="/pages/tasks?task_id=${encodeURIComponent(id)}">${esc(label || id)}</a>` : "—"; },
    diagnosisLink(id, label = "查看诊断") { return `<a href="/pages/diagnosis?incident_id=${encodeURIComponent(id)}">${esc(label)}</a>`; },
    values(values) {
      if (!values || !Object.keys(values).length) return '<p class="muted">未提供数值摘要</p>';
      return '<dl class="facts">' + Object.entries(displayValue(values)).map(([key, value]) => {
        let display = value;
        if (value === true) display = "是"; else if (value === false) display = "否";
        else if (value == null) display = "—"; else if (typeof value === "object") display = JSON.stringify(value, null, 2);
        const rendered = key === "resource_id" ? window.LT.resourceLink(value) : key === "task_id" ? window.LT.taskLink(value) : esc(display);
        return `<dt title="${esc(key)}">${esc(FIELDS[key] || key)}</dt><dd class="mono">${rendered}</dd>`;
      }).join("") + "</dl>";
    },
    notice(message, error = false) {
      const node = document.getElementById("toast"); if (!node) return;
      node.textContent = message; node.className = `toast ${error ? "notice-error" : ""}`; node.hidden = false;
      clearTimeout(window.LT.toastTimer); window.LT.toastTimer = setTimeout(() => { node.hidden = true; }, 6000);
    },
    async copy(value) { try { await navigator.clipboard.writeText(value); window.LT.notice("已复制"); } catch (_) { window.LT.notice("当前浏览器无法复制，请选中文本后复制。", true); } },
    installRefresh(load, interval = 10000) {
      let running = false, active = true;
      async function refresh() {
        if (running || !active) return; running = true;
        try { await load(); const node = document.getElementById("last-refreshed"); if (node) node.textContent = `更新于 ${window.LT.fmtTime(new Date())} · 北京时间`; }
        catch (error) { window.LT.notice(error.message, true); } finally { running = false; }
      }
      document.getElementById("refresh-button")?.addEventListener("click", refresh);
      const timer = setInterval(() => { if (!document.hidden && !document.querySelector("details[open], form:focus-within")) refresh(); }, interval);
      window.addEventListener("pagehide", () => { active = false; clearInterval(timer); }, { once: true }); return refresh;
    },
  };
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".mainnav a").forEach(a => { if (a.dataset.page === document.body.dataset.page) { a.classList.add("active"); a.setAttribute("aria-current", "page"); } });
    const logout = document.getElementById("logout-button"), login = document.getElementById("login-link");
    if (cookie("layertrace_csrf") && logout) {
      logout.hidden = false; if (login) login.hidden = true;
      logout.addEventListener("click", async () => {
        const res = await fetch("/logout", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": cookie("layertrace_csrf") } });
        if (res.ok) location.assign("/login"); else window.LT.notice("退出失败，请重新登录。", true);
      });
    }
  });
})();
