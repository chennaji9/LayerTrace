(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", () => {
    const LT = window.LT, esc = LT.esc;
    const initial = new URLSearchParams(location.search);
    let selected = initial.get("incident_id");
    let selectedRevision = initial.get("revision");
    let detailRequest = 0;
    function evidenceCard(id, evidence, role) {
      const item = evidence.find(e => e.id === id);
      if (!item) return `<div class="evidence ${role}"><span class="mono">${esc(id)}</span><p class="muted">该引用没有可读取的证据摘要，结论需结合缺失项复核。</p></div>`;
      const type = item.query_parameters?.event_type || (item.kind === "snapshot" ? "资源配置快照" : item.kind);
      const summary = Object.entries(LT.displayValue(item.summary || {})).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join(" · ");
      return `<details class="evidence ${role}" data-evidence-id="${esc(item.id)}">
        <summary><strong>${esc(type)}</strong> ${LT.provBadge(item.provenance)}<br><span class="mono small">${esc(summary || "展开查看证据来源与时间")}</span></summary>
        ${LT.values(item.summary)}
        <dl class="facts"><dt>发生时间</dt><dd>${LT.fmtTime(item.range_start)}</dd><dt>资源</dt><dd class="mono">${LT.resourceLink(item.resource_id, null, item.range_start)}</dd>
        <dt>来源引用</dt><dd class="mono">${esc(item.source_ref)}</dd><dt>证据 ID</dt><dd class="mono">${esc(item.id)}</dd></dl>
        ${item.source_expired ? '<p class="notice">原始数据已超过保留期，当前仅保留采集时生成的摘要。</p>' : ""}
      </details>`;
    }
    function list(values, empty) { return values?.length ? `<ul>${values.map(v => `<li>${esc(v)}</li>`).join("")}</ul>` : `<p class="muted">${empty}</p>`; }
    function tasks(values) { return values?.length ? `<ul>${values.map(id => `<li class="mono">${LT.taskLink(typeof id === "object" ? id.task_id : id)}</li>`).join("")}</ul>` : '<p class="muted">当前证据未确认受影响任务。</p>'; }
    async function loadDetail(id, revision = null, updateUrl = true) {
      const request = ++detailRequest;
      selected = id;
      selectedRevision = revision;
      try {
        const endpoint = `/api/v1/incidents/${encodeURIComponent(id)}`;
        const [detail, records] = await Promise.all([LT.api(endpoint), LT.api(endpoint + "/diagnoses")]);
        if (request !== detailRequest) return;
        const diagnoses = [...records];
        if (detail.diagnosis && !diagnoses.some(item => item.revision === detail.diagnosis.revision)) diagnoses.push(detail.diagnosis);
        diagnoses.sort((a, b) => b.revision - a.revision);
        const latest = diagnoses[0] || detail.diagnosis;
        const pinned = revision !== null;
        const diag = pinned ? diagnoses.find(item => String(item.revision) === String(revision)) : latest;
        if (pinned && !diag) throw new Error(`诊断记录 ${revision} 不存在，请选择可用记录。`);
        const cited = new Set(diag ? [
          ...(diag.supporting_evidence_ids || []), ...(diag.contradicting_evidence_ids || []),
          ...diag.candidates.flatMap(candidate => [...(candidate.supporting || []), ...(candidate.contradicting || [])]),
        ] : []);
        const available = new Map((detail.evidence || []).filter(item => cited.has(item.id)).map(item => [item.id, item]));
        const fetched = await Promise.all([...cited].filter(id => !available.has(id)).map(async evidenceId => {
          try { return await LT.api(`/api/v1/evidence/${encodeURIComponent(evidenceId)}`); }
          catch (error) { if (error.status === 404) return null; throw error; }
        }));
        if (request !== detailRequest) return;
        fetched.filter(Boolean).forEach(item => available.set(item.id, item));
        const evidence = [...available.values()].sort((a, b) => String(a.range_start).localeCompare(String(b.range_start)));
        const historical = diag && latest && diag.revision !== latest.revision;
        if (updateUrl) history.replaceState(null, "", `/pages/diagnosis?incident_id=${encodeURIComponent(id)}${pinned ? `&revision=${encodeURIComponent(diag.revision)}` : ""}`);
        document.getElementById("diagnosis-detail").hidden = false;
        document.querySelectorAll("#incident-table tr[data-incident]").forEach(row => row.classList.toggle("incident-selected", row.dataset.incident === id));
        document.getElementById("d-heading").innerHTML = `<h2>事件单 <span class="mono">${esc(id.slice(0, 8))}</span> <span class="sev sev-${esc(detail.severity)}">${esc(detail.severity)}</span> <span class="small">当前状态</span> ${LT.stateBadge(detail.state)}</h2><div class="muted">首次发生 ${LT.fmtTime(detail.first_seen)} · 最近发生 ${LT.fmtTime(detail.last_seen)}</div><p class="mono small text-wrap">指纹：${esc(detail.fingerprint)}</p>${diag ? `<div class="toolbar diagnosis-record-toolbar"><label for="diagnosis-revision">诊断记录<select id="diagnosis-revision" aria-describedby="diagnosis-record-notice"><option value="latest"${!pinned ? " selected" : ""}>最新记录（${esc(latest.revision)}）</option>${diagnoses.map(item => `<option value="${esc(item.revision)}"${pinned && item.revision === diag.revision ? " selected" : ""}>记录 ${esc(item.revision)} · ${LT.fmtTime(item.created_at)}${item.revision === latest.revision ? " · 当前最新" : ""}</option>`).join("")}</select></label></div><p id="diagnosis-record-notice" class="${historical ? "notice" : "muted small"}">${historical ? `正在查看历史诊断记录 ${esc(diag.revision)}，生成于 ${LT.fmtTime(diag.created_at)}；候选根因与证据对应当时记录。事件单状态为当前状态。` : `正在查看${pinned ? "固定的" : "最新"}诊断记录 ${esc(diag.revision)}，生成于 ${LT.fmtTime(diag.created_at)}。`}</p>` : ""}${detail.silenced_until ? `<p class="notice">通知静默至 ${LT.fmtTime(detail.silenced_until)}；故障事实持续更新。</p>` : ""}`;
        document.getElementById("diagnosis-revision")?.addEventListener("change", event => loadDetail(id, event.target.value === "latest" ? null : event.target.value));
        document.getElementById("d-candidates").dataset.revision = diag ? String(diag.revision) : "";
        document.getElementById("d-candidates").innerHTML = `<h2>候选根因${diag ? ` · 诊断记录 ${diag.revision}${historical ? "（历史）" : ""}` : ""}</h2>` + (!diag ? '<p class="muted">正在等待诊断生成，请稍后刷新。</p>' : diag.candidates.length ? diag.candidates.map(c => `<article class="candidate">
          <div><strong>${esc(c.title)}</strong> <span class="support support-${esc(c.support_level)}">${esc(LT.label(c.support_level))} <span class="small">(${esc(c.support_level)})</span></span> <span class="mono">${esc(c.rule_id)}</span></div>
          <p class="small muted">诊断记录 ${esc(diag.revision)}${historical ? " · 历史记录" : ""} · 生成于 ${LT.fmtTime(diag.created_at)}</p>
          <p class="small muted">支持等级表示规则的证据充分程度。</p>
          ${Object.keys(c.detail || {}).length ? `<h3>判定值与条件</h3>${LT.values(c.detail)}` : ""}
          <h3>支持证据 · ${(c.supporting || []).length} 项</h3>${(c.supporting || []).map(id => evidenceCard(id, evidence, "supporting")).join("") || '<p class="muted">暂无直接支持证据。</p>'}
          <h3>反证 · ${(c.contradicting || []).length} 项</h3>${(c.contradicting || []).map(id => evidenceCard(id, evidence, "contradicting")).join("") || '<p class="muted">没有记录反证；这不代表其他层已被排除。</p>'}
          <h3>缺失证据</h3>${list(c.missing, "当前候选未列出缺失项。")}
          <div class="grid-2"><div><h3>处置建议</h3>${list(c.recommendations, "暂无建议")}</div><div><h3>恢复验证</h3>${list(c.verification, "暂无验证方法")}</div></div>
        </article>`).join("") : '<p class="notice">现有证据不足以支撑候选根因。</p>');
        const observed = diag?.observed_impact || {}, potential = diag?.potential_impact || {};
        document.getElementById("d-impact").innerHTML = `<h2>任务影响</h2><h3>已确认失败任务 · ${Number(observed.failed_task_count || 0)}</h3>${tasks(observed.task_ids)}<p>症状资源：<span class="mono">${LT.resourceLink(observed.symptom_resource_id, null, detail.first_seen)}</span></p><h3>潜在影响</h3><p class="muted">${esc(potential.note || "潜在影响需要进一步验证，不能当作已确认失败。")}</p>${tasks(potential.running_task_ids)}${(potential.vm_ids || []).length ? `<div>相关虚拟机：${potential.vm_ids.map(id => LT.resourceLink(id, id.slice(0, 8), detail.first_seen)).join(" · ")}</div>` : ""}`;
        document.getElementById("d-meta").innerHTML = `<h2>规则与时间可信度</h2>` + (diag ? `<dl class="facts"><dt>规则版本</dt><dd class="mono">${Object.entries(diag.rule_versions || {}).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ")}</dd><dt>时间可信度</dt><dd>${esc(LT.label(diag.time_confidence))}</dd><dt>上下文截断</dt><dd>${diag.context_truncated ? "是，可能存在遗漏" : "否"}</dd><dt>生成时间</dt><dd>${LT.fmtTime(diag.created_at)}</dd></dl><h3>全局缺失项</h3>${list(diag.missing_evidence, "当前诊断未列出全局缺失项。")}` : '<p class="muted">等待诊断生成</p>');
        document.getElementById("d-timeline").innerHTML = '<h2>故障时间线</h2><p class="muted small">时间线展示事件单当前累计事实；候选根因与证据索引对应所选诊断记录。</p><table class="data"><thead><tr><th>时间</th><th>事件</th><th>资源 / 任务</th><th>来源</th></tr></thead><tbody>' + (detail.timeline || []).map(t => `<tr><td>${LT.fmtTime(t.at)}</td><td class="mono">${esc(t.type || t.rule_id || t.kind)} ${esc(t.state ? LT.label(t.state) : "")}</td><td>${t.resource_id ? LT.resourceLink(t.resource_id, t.resource_id.slice(0, 8)) : ""} ${t.task_id ? LT.taskLink(t.task_id) : ""}</td><td>${esc(t.source || "")} ${LT.provBadge(t.provenance)}</td></tr>`).join("") + '</tbody></table>';
        document.getElementById("d-evidence").innerHTML = `<h2>证据索引 · ${evidence.length} 项${diag ? ` · 诊断记录 ${diag.revision}` : ""}</h2><p class="muted small">摘要保留原始来源、发生时间与资源身份，便于复核支持和反证。</p>` + (evidence.length ? evidence.map(e => evidenceCard(e.id, evidence, "")).join("") : '<p class="muted">尚无可读取的证据摘要。</p>');
      } catch (error) {
        if (request !== detailRequest) return;
        document.getElementById("diagnosis-detail").hidden = false;
        document.getElementById("d-heading").innerHTML = `<p class="notice notice-error">${esc(error.message)}</p><button type="button" id="diagnosis-latest">查看最新诊断</button>`;
        document.getElementById("diagnosis-latest").addEventListener("click", () => loadDetail(id));
        ["d-candidates", "d-impact", "d-meta", "d-timeline", "d-evidence"].forEach(panel => { document.getElementById(panel).replaceChildren(); });
        document.getElementById("d-candidates").dataset.revision = "";
      }
    }
    async function load() {
      const data = await LT.api("/api/v1/incidents?limit=200");
      const body = document.querySelector("#incident-table tbody");
      body.innerHTML = (data.items || []).map(inc => `<tr data-incident="${esc(inc.id)}" class="${selected === inc.id ? "incident-selected" : ""}"><td class="mono">${esc(inc.id.slice(0, 8))}</td><td><span class="sev sev-${esc(inc.severity)}">${esc(inc.severity)}</span></td><td>${LT.stateBadge(inc.state)}</td><td>${inc.alert_count}</td><td>${LT.fmtTime(inc.last_seen)}</td><td><button type="button" data-incident="${esc(inc.id)}">查看诊断</button></td></tr>`).join("") || '<tr><td colspan="6">暂无事件单</td></tr>';
      body.querySelectorAll("button[data-incident]").forEach(button => button.addEventListener("click", () => loadDetail(button.dataset.incident)));
      if (selected) await loadDetail(selected, selectedRevision, false);
    }
    LT.installRefresh(load)();
  });
})();
