(function () {
  "use strict";
  const COLORS = { observed: "#3fb27f", simulated: "#d9a441", replayed: "#4da3ff", inferred: "#e0607e", manual: "#8a99ac" };
  document.addEventListener("DOMContentLoaded", () => {
    const LT = window.LT, esc = LT.esc, params = new URLSearchParams(location.search), root = params.get("resource_id");
    let selected = root, data = { nodes: [], edges: [] };
    const container = document.getElementById("topology-view");
    container.innerHTML = '<div class="toolbar topology-controls"><label>关系时刻（北京时间）<input type="datetime-local" id="topo-at" step="1"></label><button id="topo-reload" class="secondary">刷新拓扑</button><button id="topo-fit" class="secondary">适合窗口</button><span id="topo-status" role="status"></span></div><div id="topo-cy" style="height:520px;min-height:320px"></div><div id="topo-meta" class="muted small"></div>';
    if (params.get("at")) {
      const date = new Date(params.get("at"));
      if (!Number.isNaN(date.getTime())) document.getElementById("topo-at").value = new Date(date.getTime() + 8 * 3600000).toISOString().slice(0, 19);
    }
    document.getElementById("topo-legend").innerHTML = Object.keys(COLORS).map(k => LT.provBadge(k)).join(" ");
    const cy = cytoscape({ container: document.getElementById("topo-cy"), style: [
      { selector: "node", style: { label: "data(label)", "text-valign": "bottom", "text-margin-y": 8, "text-halign": "center", "background-color": "data(color)", width: 48, height: 48, "font-size": 12, color: "#dce4ee", "border-width": 2, "border-color": "#10151c", "text-wrap": "wrap", "text-max-width": 130 } },
      { selector: "edge", style: { label: "data(label)", "target-arrow-shape": "triangle", "curve-style": "bezier", width: 2, "line-color": "#465365", "target-arrow-color": "#465365", "font-size": 10, color: "#a5b1bf", "text-background-color": "#10151c", "text-background-opacity": .8, "text-background-padding": 3 } },
      { selector: "node:selected", style: { "border-color": "#fff", "border-width": 4 } },
    ] });
    function detail(id) {
      const node = data.nodes.find(n => n.id === id), box = document.getElementById("topo-detail");
      if (!node) { box.innerHTML = '<h2>资源详情</h2><p class="muted">点击拓扑节点查看资源身份与关联。</p>'; return; }
      selected = id;
      const at = document.getElementById("topo-at").value;
      history.replaceState(null, "", `/pages/topology?resource_id=${encodeURIComponent(id)}${at ? "&at=" + encodeURIComponent(new Date(at + "+08:00").toISOString()) : ""}`);
      const relations = data.edges.filter(e => e.from_id === id || e.to_id === id);
      box.innerHTML = `<h2>${esc(node.display_name || node.source_id)} ${LT.provBadge(node.provenance)}</h2><dl class="facts"><dt>资源类型</dt><dd>${esc(LT.kinds[node.kind] || node.kind)}</dd><dt>资源 ID</dt><dd class="mono">${esc(node.id)}</dd><dt>平台身份</dt><dd class="mono">${esc(node.source_id)}</dd><dt>数据来源</dt><dd class="mono">${esc(node.provider)} / ${esc(node.provider_instance)}</dd><dt>最近发现</dt><dd>${LT.fmtTime(node.last_seen)}</dd></dl>${LT.values(node.attributes)}<h3>关联资源</h3>${relations.length ? '<ul>' + relations.map(edge => { const target = data.nodes.find(n => n.id === (edge.from_id === id ? edge.to_id : edge.from_id)); return `<li>${esc(edge.type)} → <button type="button" class="secondary resource-select" data-resource="${esc(target?.id || "")}">${esc(target?.display_name || target?.source_id || "未知")}</button> ${LT.provBadge(edge.provenance)}</li>`; }).join("") + '</ul>' : '<p class="muted">当前时刻无关联记录。</p>'}${node.kind === "service_instance" ? `<p><a class="button secondary" href="/pages/tasks?service_instance_id=${encodeURIComponent(id)}">查看此实例的任务</a></p>` : ""}${node.kind === "container" ? `<p><a class="button secondary" href="/pages/workloads?resource_id=${encodeURIComponent(id)}">查看容器健康</a></p>` : ""}`;
      box.querySelectorAll(".resource-select").forEach(button => button.addEventListener("click", () => select(button.dataset.resource)));
    }
    function select(id) { cy.nodes().unselect(); const node = cy.getElementById(id); if (node.length) { node.select(); cy.animate({ center: { eles: node }, duration: 250 }); } detail(id); }
    cy.on("tap", "node", event => detail(event.target.id()));
    async function refresh() {
      document.getElementById("topo-status").textContent = "加载中…";
      try {
        const query = new URLSearchParams(); const at = document.getElementById("topo-at").value;
        if (at) query.set("at", new Date(at + "+08:00").toISOString());
        data = await LT.api("/api/v1/topology" + (query.size ? "?" + query : ""));
        const labels = { contains: "包含", runs_on: "运行于", depends_on: "依赖", hosts: "承载", belongs_to: "属于", uses: "使用" };
        cy.elements().remove();
        cy.add([...data.nodes.map(n => ({ data: { id: n.id, label: `${(n.display_name || n.source_id).slice(0, 22)}\n${LT.kinds[n.kind] || n.kind}`, color: COLORS[n.provenance] || "#8a99ac" } })), ...data.edges.map(e => ({ data: { id: e.id, source: e.from_id, target: e.to_id, label: labels[e.type] || e.type } }))]);
        cy.layout({ name: "breadthfirst", directed: true, padding: 50, spacingFactor: 1.4 }).run();
        document.getElementById("topo-status").textContent = data.nodes.length ? "" : "暂无拓扑，等待平台与探针发现资源。";
        document.getElementById("topo-meta").textContent = `节点 ${data.nodes.length} / 关系 ${data.edges.length} · ${LT.fmtTime(data.at)}（北京时间）${data.truncated ? " · 拓扑已截断" : ""}`;
        if (selected) select(selected); else detail(null);
      } catch (error) { document.getElementById("topo-status").textContent = error.message; }
    }
    document.getElementById("topo-reload").addEventListener("click", refresh);
    document.getElementById("topo-fit").addEventListener("click", () => cy.fit(undefined, 50));
    refresh();
  });
})();
