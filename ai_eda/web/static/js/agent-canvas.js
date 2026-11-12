/* --------------------------- Agent canvas render --------------------------- */

function createAgentNode(node) {
  const [glyph, glyphClass] = NODE_GLYPHS[node.type] || ["?", "report-glyph"];
  const element = document.createElement("div");
  element.className = `agent-node type-${node.type}`;
  element.dataset.nodeId = node.id;
  element.style.left = `${node.x}px`;
  element.style.top = `${node.y}px`;
  element.innerHTML = `
    <div class="agent-node-head">
      <span class="node-glyph ${glyphClass}">${glyph}</span>
      <div class="agent-node-heading">
        <span class="agent-node-type">${escapeHtml(node.type.toUpperCase())}</span>
        <span class="agent-node-title"></span>
      </div>
      <span class="node-status-indicator" aria-hidden="true"></span>
    </div>
    <div class="agent-node-preview"></div>
    <div class="agent-node-foot">
      <span class="agent-entry-badge">ENTRY</span>
      <span class="agent-node-meta"></span>
    </div>
    <span class="node-port port-left" data-port-side="left" aria-hidden="true"></span>
    <span class="node-port port-right" data-port-side="right" aria-hidden="true"></span>
    <span class="node-port port-top" data-port-side="top" aria-hidden="true"></span>
    <span class="node-port port-bottom" data-port-side="bottom" aria-hidden="true"></span>
  `;
  $(".agent-node-title", element).textContent = node.title;
  const preview = node.prompt.replaceAll(/\s+/g, " ").trim();
  $(".agent-node-preview", element).textContent = preview;
  $(".agent-entry-badge", element).classList.toggle("hidden", node.id !== agentGraph().entry);
  element.tabIndex = 0;
  element.setAttribute("role", "group");
  element.setAttribute("aria-label", `${node.type} node: ${node.title}`);
  element.addEventListener("click", () => selectAgentNode(node.id));
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectAgentNode(node.id);
    }
  });
  return element;
}

function renderAgentCanvas() {
  const graph = agentGraph();
  if (!graph) return;
  $("#agent-name").textContent = graph.name;
  const layer = $("#agent-nodes");
  layer.replaceChildren();
  $("#canvas-empty").classList.toggle("hidden", graph.nodes.length > 0);
  graph.nodes.forEach((node) => layer.append(createAgentNode(node)));
  renderAgentFixedNodes(layer);
  $("#agent-graph-stats").textContent = `${graph.nodes.length} nodes · ${graph.edges.length} links`;
  const order = new Set(agentChainOrder());
  $$(".agent-node", layer).forEach((element) => {
    element.classList.toggle("unreachable", !order.has(element.dataset.nodeId));
  });
  drawAgentEdges();
  updateNodeStatuses();
  renderAgentFlowList();
}

const EDGE_SIDES = ["left", "right", "top", "bottom"];

function isEdgeSide(side) {
  return EDGE_SIDES.includes(side);
}

function nodePoint(node, side) {
  if (side === "left") return { x: node.x, y: node.y + NODE_H / 2 };
  if (side === "right") return { x: node.x + NODE_W, y: node.y + NODE_H / 2 };
  if (side === "top") return { x: node.x + NODE_W / 2, y: node.y };
  return { x: node.x + NODE_W / 2, y: node.y + NODE_H };
}

function edgeSideVector(side) {
  if (side === "left") return { x: -1, y: 0 };
  if (side === "right") return { x: 1, y: 0 };
  if (side === "top") return { x: 0, y: -1 };
  return { x: 0, y: 1 };
}

function chooseEdgeSides(from, to) {
  const dx = Number(to.x) - Number(from.x);
  const dy = Number(to.y) - Number(from.y);
  // 目标在明显下方/上方且水平距离不大时优先走上下边；
  // 这样测试器到修改器的回环不会横穿整条流水线。
  if (Math.abs(dy) > NODE_H * 0.55 && Math.abs(dx) < NODE_W * 1.35) {
    return dy >= 0
      ? { from: "bottom", to: "top" }
      : { from: "top", to: "bottom" };
  }
  if (Math.abs(dx) >= Math.abs(dy) * 0.65) {
    return dx >= 0
      ? { from: "right", to: "left" }
      : { from: "left", to: "right" };
  }
  return dy >= 0
    ? { from: "bottom", to: "top" }
    : { from: "top", to: "bottom" };
}

function edgePortSides(edge, from, to) {
  const auto = chooseEdgeSides(from, to);
  return {
    from: isEdgeSide(edge.fromSide) ? edge.fromSide : auto.from,
    to: isEdgeSide(edge.toSide) ? edge.toSide : auto.to,
  };
}

function controlPoint(point, side, distance) {
  const vector = edgeSideVector(side);
  return {
    x: point.x + vector.x * distance,
    y: point.y + vector.y * distance,
  };
}

function edgePathFromSides(fromNode, fromSide, toNode, toSide, offset = 0) {
  const from = nodePoint(fromNode, fromSide);
  const to = nodePoint(toNode, toSide);
  if (offset) {
    if (fromSide === "left" || fromSide === "right") from.y += offset;
    else from.x += offset;
    if (toSide === "left" || toSide === "right") to.y += offset;
    else to.x += offset;
  }
  const distance = Math.max(42, Math.min(150, Math.hypot(to.x - from.x, to.y - from.y) * 0.45));
  const controlFrom = controlPoint(from, fromSide, distance);
  const controlTo = controlPoint(to, toSide, distance);
  return `M ${from.x} ${from.y} C ${controlFrom.x} ${controlFrom.y}, ${controlTo.x} ${controlTo.y}, ${to.x} ${to.y}`;
}

function retryEdgePath(fromNode, toNode) {
  const fromSide = fromNode.retry?.fromSide && isEdgeSide(fromNode.retry.fromSide)
    ? fromNode.retry.fromSide : "bottom";
  const toSide = fromNode.retry?.toSide && isEdgeSide(fromNode.retry.toSide)
    ? fromNode.retry.toSide : "bottom";
  const from = nodePoint(fromNode, fromSide);
  const to = nodePoint(toNode, toSide);
  const dip = Math.max(64, Math.abs(to.x - from.x) * 0.18);
  return `M ${from.x} ${from.y} C ${from.x} ${from.y + dip}, ${to.x} ${to.y + dip}, ${to.x} ${to.y}`;
}

function markNodePorts(fromNode, toNode, sides, kind) {
  const fromPort = document.querySelector(`.agent-node[data-node-id="${fromNode.id}"] .node-port[data-port-side="${sides.from}"]`);
  const toPort = document.querySelector(`.agent-node[data-node-id="${toNode.id}"] .node-port[data-port-side="${sides.to}"]`);
  if (fromPort) fromPort.classList.add("active", kind === "retry" ? "retry" : "out");
  if (toPort) toPort.classList.add("active", kind === "retry" ? "retry" : "in");
}

const HANDOFF_LABELS = {
  spec_to_parser: "Input Spec → Parser",
  parser_to_planner: "Parser → Planner",
  planner_to_coder: "Planner → Coder",
  coder_to_tester: "Coder → Tester",
  tester_to_diagnoser: "Tester → Diagnoser",
  diagnoser_to_patch: "Diagnoser → Patch Agent",
  patch_to_tester: "Patch Agent → Tester",
  tester_to_modifier: "Tester → Modifier",
  modifier_to_tester: "Modifier → Tester",
  tester_to_summary: "Tester → Summary",
};

const HANDOFF_FIELD_LABELS = {
  attempt: "Test attempt",
  task: "Task",
  summary: "Handoff summary",
  notes: "Repair notes",
  changed_paths: "Changed files",
};

function handoffFieldValue(value) {
  if (Array.isArray(value)) return value.join("、") || "—";
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value ?? "—");
}

function renderAgentHandoffContent(container, item) {
  container.replaceChildren();
  const payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  const fields = Object.entries(payload).filter(([key]) => key !== "artifacts");
  if (fields.length) {
    const message = document.createElement("section");
    message.className = "handoff-message-card";
    const label = document.createElement("span");
    label.className = "handoff-card-label";
    label.textContent = "Handoff";
    message.append(label);
    const list = document.createElement("dl");
    fields.forEach(([key, value]) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = HANDOFF_FIELD_LABELS[key] || key.replaceAll("_", " ");
      const detail = document.createElement("dd");
      renderRichContent(detail, handoffFieldValue(value));
      row.append(term, detail);
      list.append(row);
    });
    message.append(list);
    container.append(message);
  }

  const previews = Array.isArray(item.artifact_previews) ? item.artifact_previews : [];
  if (!previews.length) {
    const empty = document.createElement("p");
    empty.className = "handoff-empty";
    empty.textContent = "No displayable text for this handoff yet.";
    container.append(empty);
    return;
  }
  const grid = document.createElement("div");
  grid.className = "handoff-artifact-grid";
  previews.forEach((artifact) => {
    const card = document.createElement("section");
    card.className = "handoff-artifact-card";
    const heading = document.createElement("div");
    heading.className = "handoff-artifact-heading";
    const title = document.createElement("strong");
    title.textContent = artifact.title || "Handoff text";
    const state = document.createElement("small");
    state.textContent = artifact.truncated ? "Truncated" : "Full text";
    heading.append(title, state);
    const text = document.createElement("div");
    text.className = "handoff-artifact-content";
    renderRichContent(text, artifact.content || "Empty text");
    card.append(heading, text);
    grid.append(card);
  });
  container.append(grid);
}

function handoffEdgeId(source, target) {
  return `${source}_to_${target}`;
}

function addHandoffHitArea(svg, pathData, edgeId, visiblePath = null) {
  const hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
  hit.setAttribute("d", pathData);
  hit.setAttribute("class", "agent-edge-hit");
  hit.dataset.handoff = edgeId;
  const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
  title.textContent = `${HANDOFF_LABELS[edgeId] || edgeId} · click to view handoff`;
  hit.append(title);
  hit.addEventListener("mouseenter", () => visiblePath?.classList.add("handoff-hover"));
  hit.addEventListener("mouseleave", () => visiblePath?.classList.remove("handoff-hover"));
  hit.addEventListener("click", (event) => {
    event.stopPropagation();
    openAgentHandoffDialog(edgeId);
  });
  svg.append(hit);
}

function addEdgeBadge(svg, path, edgeId) {
  let point;
  try {
    const length = path.getTotalLength();
    point = path.getPointAtLength(length / 2);
  } catch (_) {
    return;
  }
  const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
  const available = state.agent.handoffEdges.has(edgeId);
  group.setAttribute("class", `agent-edge-badge ${available ? "available" : "disabled"}`);
  group.setAttribute("transform", `translate(${point.x} ${point.y})`);
  group.dataset.handoff = edgeId;
  const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
  title.textContent = available
    ? `${HANDOFF_LABELS[edgeId] || edgeId} · click to view handoff`
    : "This run has not produced this handoff yet";
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", "-17");
  rect.setAttribute("y", "-8");
  rect.setAttribute("width", "34");
  rect.setAttribute("height", "16");
  rect.setAttribute("rx", "8");
  const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
  text.setAttribute("x", "0");
  text.setAttribute("y", "3.5");
  text.setAttribute("text-anchor", "middle");
  text.textContent = "Handoff";
  group.append(title, rect, text);
  group.addEventListener("click", (event) => {
    event.stopPropagation();
    if (!state.agent.handoffEdges.has(edgeId)) return;
    openAgentHandoffDialog(edgeId);
  });
  svg.append(group);
}

function updateAgentHandoffBadges() {
  $$(".agent-edge-badge").forEach((badge) => {
    const edgeId = badge.dataset.handoff;
    const available = state.agent.handoffEdges.has(edgeId);
    badge.classList.toggle("available", available);
    badge.classList.toggle("disabled", !available);
    const title = badge.querySelector("title");
    if (title) {
      title.textContent = available
        ? `${HANDOFF_LABELS[edgeId] || edgeId} · click to view handoff`
        : "This run has not produced this handoff yet";
    }
  });
}

async function openAgentHandoffDialog(edgeId) {
  const runId = state.agent.run?.run_id || state.agent.job?.run_id;
  if (!runId) {
    showToast("Run the agent before viewing a sub-agent handoff", "warning");
    return;
  }
  if (!state.agent.handoffEdges.has(edgeId)) {
    showToast("This run has not produced this handoff yet", "warning");
    return;
  }
  try {
    const payload = await api(
      `/api/agent/runs/${encodeURIComponent(runId)}/handoffs/${encodeURIComponent(edgeId)}`
    );
    renderAgentHandoffDialog(payload);
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderAgentHandoffDialog(payload) {
  const dialog = $("#agent-handoff-dialog");
  const items = payload.items || [];
  if (!dialog || !items.length) return;
  $("#agent-handoff-title").textContent =
    HANDOFF_LABELS[payload.edge_id] || items[items.length - 1].label || payload.edge_id;
  const latest = items[items.length - 1];
  $("#agent-handoff-meta").textContent = `${payload.run_id} · ${items.length} handoff versions · actual handoff text`;
  const tabs = $("#agent-handoff-tabs");
  tabs.replaceChildren();
  const content = $("#agent-handoff-content");
  const showItem = (index) => {
    const item = items[index];
    renderAgentHandoffContent(content, item);
    [...tabs.querySelectorAll(".handoff-tab")].forEach((button, position) => {
      button.classList.toggle("active", position === index);
    });
  };
  items.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "handoff-tab";
    button.textContent = [
      item.attempt ? `Attempt ${item.attempt}` : "latest",
      item.created_at ? String(item.created_at).slice(11, 19) : "",
    ].filter(Boolean).join(" · ");
    button.addEventListener("click", () => showItem(index));
    tabs.append(button);
  });
  showItem(items.length - 1);
  if (!dialog.open) dialog.showModal();
}

function drawAgentEdges() {
  const graph = agentGraph();
  const svg = $("#agent-edges");
  if (!graph || !svg) return;
  svg.setAttribute("viewBox", "0 0 4000 3000");
  $$("path.agent-edge, path.agent-edge-hit, .agent-edge-badge", svg).forEach((element) => element.remove());
  $$(".agent-node .node-port").forEach((port) => port.classList.remove("active", "in", "out", "retry"));
  const pairedRetryKeys = new Set();
  const hasReverseRetry = (edge) => {
    const target = agentNode(edge.to);
    return Boolean(target?.retry && target.retry.target === edge.from);
  };
  graph.edges.forEach((edge) => {
    const from = agentNode(edge.from);
    const to = agentNode(edge.to);
    if (!from || !to) return;
    const paired = hasReverseRetry(edge);
    const sides = edgePortSides(edge, from, to);
    const pathData = edgePathFromSides(from, sides.from, to, sides.to, paired ? 26 : 0);
    markNodePorts(from, to, sides, paired ? "retry" : "edge");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathData);
    path.setAttribute("class", paired ? "agent-edge paired-forward-edge" : "agent-edge");
    path.setAttribute("marker-end", "url(#arrow-head)");
    path.dataset.edge = `${edge.from}->${edge.to}`;
    svg.append(path);
    addHandoffHitArea(svg, pathData, handoffEdgeId(edge.from, edge.to), path);
    addEdgeBadge(svg, path, handoffEdgeId(edge.from, edge.to));
    if (paired) pairedRetryKeys.add(`${edge.to}->${edge.from}`);
  });
  graph.nodes.forEach((node) => {
    if (!node.retry) return;
    const target = agentNode(node.retry.target);
    if (!target) return;
    const paired = pairedRetryKeys.has(`${node.id}->${node.retry.target}`);
    let pathData;
    let sides;
    if (paired) {
      sides = edgePortSides(
        { from: node.id, to: node.retry.target },
        node,
        target,
      );
      // 反向箭头与正向箭头错开，形成两条独立的有向箭头。
      pathData = edgePathFromSides(node, sides.from, target, sides.to, -26);
    } else {
      sides = {
        from: node.retry?.fromSide && isEdgeSide(node.retry.fromSide) ? node.retry.fromSide : "bottom",
        to: node.retry?.toSide && isEdgeSide(node.retry.toSide) ? node.retry.toSide : "bottom",
      };
      pathData = retryEdgePath(node, target);
    }
    markNodePorts(node, target, sides, "retry");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathData);
    path.setAttribute("class", "agent-edge retry-edge");
    path.setAttribute("marker-end", "url(#arrow-retry)");
    path.dataset.edge = `${node.id}->${node.retry.target}`;
    svg.append(path);
    addHandoffHitArea(svg, pathData, handoffEdgeId(node.id, node.retry.target), path);
    addEdgeBadge(svg, path, handoffEdgeId(node.id, node.retry.target));
  });
  applyExecutionState();
  $$("path.agent-fixed-edge", svg).forEach((path) => path.remove());
  const positions = fixedNodePositions();
  const entry = agentNode(graph.entry);
  if (positions && entry) {
    const spec = positions.spec;
    const edgePath = `M ${spec.x + FIXED_NODE_W / 2} ${spec.y + FIXED_NODE_H} C ${spec.x + FIXED_NODE_W / 2} ${spec.y + FIXED_NODE_H + 36}, ${entry.x + NODE_W / 2} ${entry.y - 36}, ${entry.x + NODE_W / 2} ${entry.y}`;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", edgePath);
    path.setAttribute("class", "agent-fixed-edge");
    svg.append(path);
    addHandoffHitArea(svg, edgePath, "spec_to_parser", path);
    addEdgeBadge(svg, path, "spec_to_parser");
  }

}

function updateNodeStatuses() {
  const states = new Map();
  state.agent.events.forEach((event) => {
    states.set(event.node_id, {
      status: event.status || (event.report ? (event.report.passed ? "passed" : "failed") : "info"),
      attempt: event.attempt || 1,
      duration: event.duration_ms,
    });
  });
  $$(".agent-node").forEach((element) => {
    const info = states.get(element.dataset.nodeId);
    element.classList.remove("status-running", "status-passed", "status-failed", "status-info");
    if (info) element.classList.add(`status-${info.status}`);
    const indicator = $(".node-status-indicator", element);
    if (indicator) {
      indicator.className = "node-status-indicator";
      indicator.textContent = "";
      if (info?.status === "running") indicator.classList.add("running");
      else if (info?.status === "passed") { indicator.classList.add("passed"); indicator.textContent = "✓"; }
      else if (info?.status === "failed") { indicator.classList.add("failed"); indicator.textContent = "×"; }
      else if (info?.status === "info") { indicator.classList.add("info"); indicator.textContent = "•"; }
    }
    const meta = $(".agent-node-meta", element);
    if (meta) {
      meta.textContent = info
        ? `Round ${info.attempt}${info.duration ? ` · ${formatDuration(info.duration)}` : ""}`
        : "";
    }
  });
}

function updateNodeSelectionClasses() {
  $$(".agent-node").forEach((element) => {
    element.classList.toggle("selected", element.dataset.nodeId === state.agent.selectedNodeId);
  });
}

function selectedEvent() {
  return state.agent.events.find((event) => event.step === state.agent.selectedStep) || null;
}

function applyExecutionState() {
  updateNodeSelectionClasses();
  const events = state.agent.events;
  const selected = selectedEvent();
  const selectedIndex = selected ? events.findIndex((event) => event.step === selected.step) : -1;

  $$(".agent-node").forEach((element) => {
    element.classList.toggle("trace-node", selected?.node_id === element.dataset.nodeId);
  });

  const traversed = new Set();
  const retryTraversed = new Set();
  events.forEach((event) => {
    if (event.next) traversed.add(`${event.node_id}->${event.next}`);
    if (event.retry?.target) {
      retryTraversed.add(`${event.node_id}->${event.retry.target}`);
      traversed.add(`${event.node_id}->${event.retry.target}`);
    }
  });
  if (selectedIndex > 0) {
    const previous = events[selectedIndex - 1];
    traversed.add(`${previous.node_id}->${selected.node_id}`);
  }

  $$("path.agent-edge").forEach((path) => {
    const keys = (path.dataset.edge || "").split("|").filter(Boolean);
    const isTraversed = keys.some((key) => traversed.has(key));
    const isRetry = keys.some((key) => retryTraversed.has(key));
    const isActive = Boolean(
      selectedIndex > 0
      && keys.includes(`${events[selectedIndex - 1].node_id}->${selected.node_id}`)
    );
    path.classList.toggle("traversed", isTraversed);
    path.classList.toggle("retry-traversed", isRetry);
    path.classList.toggle("active", isActive);
  });
}

/* ---------------------------- Canvas interaction ---------------------------- */

function applyView() {
  const canvas = $("#agent-canvas");
  if (!canvas) return;
  const { x, y, scale } = state.agent.view;
  canvas.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  const zoomLabel = $("#agent-zoom-label");
  if (zoomLabel) zoomLabel.textContent = `${Math.round(scale * 100)}%`;
}

function viewportCenterPoint() {
  const scroll = $("#agent-canvas-scroll");
  if (!scroll) return { x: 400, y: 240 };
  const rect = scroll.getBoundingClientRect();
  const { x, y, scale } = state.agent.view;
  return { x: (rect.width / 2 - x) / scale, y: (rect.height / 2 - y) / scale };
}

function clientToStage(clientX, clientY) {
  const canvas = $("#agent-canvas");
  const rect = canvas.getBoundingClientRect();
  const { scale } = state.agent.view;
  return { x: (clientX - rect.left) / scale, y: (clientY - rect.top) / scale };
}

function zoomTo(clientX, clientY, targetScale) {
  const view = state.agent.view;
  const canvas = $("#agent-canvas");
  const scrollRect = canvas.parentElement.getBoundingClientRect();
  const rect = canvas.getBoundingClientRect();
  const stageX = (clientX - rect.left) / view.scale;
  const stageY = (clientY - rect.top) / view.scale;
  const nextScale = clamp(targetScale, MIN_SCALE, MAX_SCALE);
  if (Math.abs(nextScale - view.scale) < 0.0001) return;
  view.x = clientX - scrollRect.left - stageX * nextScale;
  view.y = clientY - scrollRect.top - stageY * nextScale;
  view.scale = nextScale;
  state.agent.needsFit = false;
  applyView();
}

function zoomAt(clientX, clientY, factor) {
  zoomTo(clientX, clientY, state.agent.view.scale * factor);
}

function zoomBy(factor) {
  const scroll = $("#agent-canvas-scroll");
  const rect = scroll.getBoundingClientRect();
  zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
}

function resetZoom() {
  const scroll = $("#agent-canvas-scroll");
  const rect = scroll.getBoundingClientRect();
  zoomTo(rect.left + rect.width / 2, rect.top + rect.height / 2, 1);
}

function fitAgentView() {
  const graph = agentGraph();
  const scroll = $("#agent-canvas-scroll");
  if (!graph || !scroll) return;
  if (!graph.nodes.length) {
    state.agent.view = { x: 48, y: 48, scale: 1 };
    applyView();
    return;
  }
  const positions = fixedNodePositions();
  const xs = graph.nodes.map((node) => node.x);
  const ys = graph.nodes.map((node) => node.y);
  const rights = graph.nodes.map((node) => node.x + NODE_W);
  const bottoms = graph.nodes.map((node) => node.y + NODE_H);
  if (positions) {
    xs.push(positions.spec.x);
    ys.push(positions.spec.y);
    rights.push(positions.spec.x + FIXED_NODE_W);
    bottoms.push(positions.spec.y + FIXED_NODE_H);
  }
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const maxX = Math.max(...rights);
  const maxY = Math.max(...bottoms);
  const width = Math.max(1, maxX - minX);
  const height = Math.max(1, maxY - minY);
  const padding = 24;
  const availableWidth = Math.max(200, scroll.clientWidth - padding * 2);
  const availableHeight = Math.max(180, scroll.clientHeight - padding * 2);
  const scale = clamp(Math.min(availableWidth / width, availableHeight / height), MIN_SCALE, 1.5);
  // 画布高度按实际流程图内容收缩，避免上下出现大块空白；
  // 保留最小高度用于节点可读性，最大高度则交给画布滚动。
  const targetHeight = Math.round(clamp(height * scale + 72, 360, 680));
  if (Math.abs(scroll.clientHeight - targetHeight) > 2) {
    scroll.style.height = `${targetHeight}px`;
  }
  state.agent.view = {
    scale,
    x: (scroll.clientWidth - width * scale) / 2 - minX * scale,
    y: (scroll.clientHeight - height * scale) / 2 - minY * scale,
  };
  state.agent.needsFit = false;
  applyView();
}

function bindNodeDrag(element, node) {
  let drag = null;
  element.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".node-port") || state.agent.running) return;
    event.preventDefault();
    clearTimeout(state.agent.saveTimer);
    element.setPointerCapture(event.pointerId);
    drag = {
      startX: event.clientX,
      startY: event.clientY,
      nodeX: Number(node.x),
      nodeY: Number(node.y),
      moved: false,
      snapshot: graphSnapshot(),
    };
    element.classList.add("dragging");
  });
  element.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const scale = state.agent.view.scale;
    const dx = (event.clientX - drag.startX) / scale;
    const dy = (event.clientY - drag.startY) / scale;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    node.x = Math.max(0, Math.round((drag.nodeX + dx) / GRID) * GRID);
    node.y = Math.max(0, Math.round((drag.nodeY + dy) / GRID) * GRID);
    element.style.left = `${node.x}px`;
    element.style.top = `${node.y}px`;
    drawAgentEdges();
  });
  const release = (event) => {
    if (!drag) return;
    element.classList.remove("dragging");
    if (drag.moved) {
      pushHistorySnapshot(drag.snapshot);
      scheduleAgentSave();
      state.agent.needsFit = false;
    } else {
      selectAgentNode(node.id);
    }
    drag = null;
    try { element.releasePointerCapture(event.pointerId); } catch (_) { /* already released */ }
  };
  element.addEventListener("pointerup", release);
  element.addEventListener("pointercancel", release);
}

function bindPortConnection(port, node) {
  port.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || state.agent.running) return;
    event.preventDefault();
    event.stopPropagation();
    const from = nodePoint(node, "right");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", "agent-edge connection-preview");
    path.setAttribute("d", `M ${from.x} ${from.y} L ${from.x} ${from.y}`);
    $("#agent-edges").append(path);

    const move = (moveEvent) => {
      const point = clientToStage(moveEvent.clientX, moveEvent.clientY);
      const bend = Math.max(36, Math.abs(point.x - from.x) * 0.4);
      path.setAttribute("d", `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${point.x - bend} ${point.y}, ${point.x} ${point.y}`);
    };
    const up = (upEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      path.remove();
      const targetElement = document.elementFromPoint(upEvent.clientX, upEvent.clientY)?.closest(".agent-node");
      const targetId = targetElement?.dataset.nodeId;
      if (!targetId || targetId === node.id) {
        drawAgentEdges();
        return;
      }
      if (wouldCreateCycle(node.id, targetId)) {
        showToast("This edge would create a cycle; use Verify retry settings instead", "warning");
        drawAgentEdges();
        return;
      }
      const predecessor = agentGraph().edges.find((edge) => edge.to === targetId && edge.from !== node.id);
      if (predecessor) {
        showToast("Target already has a predecessor; remove the old edge or use Verify retry", "warning");
        drawAgentEdges();
        return;
      }
      commitGraphMutation((graph) => {
        graph.edges = graph.edges.filter((edge) => edge.from !== node.id);
        graph.edges.push({ from: node.id, to: targetId });
      });
      if (sanitizeRetryConfigs()) {
        renderAgentCanvas();
        scheduleAgentSave();
        showToast("Edge change invalidated the previous retry settings; removed", "warning");
      }
      if (state.agent.selectedNodeId === node.id) selectAgentNode(node.id);
      showToast(`Connected ${node.title} → ${agentNode(targetId)?.title || targetId}`, "success");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}

function bindCanvasPanAndZoom() {
  const scroll = $("#agent-canvas-scroll");
  const canvas = $("#agent-canvas");
  if (!scroll || !canvas) return;
  let pan = null;

  canvas.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".agent-node")) return;
    if (event.button !== 0 && event.button !== 1) return;
    event.preventDefault();
    pan = {
      startX: event.clientX,
      startY: event.clientY,
      viewX: state.agent.view.x,
      viewY: state.agent.view.y,
      moved: false,
    };
    scroll.classList.add("panning");
    canvas.setPointerCapture?.(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!pan) return;
    const dx = event.clientX - pan.startX;
    const dy = event.clientY - pan.startY;
    if (Math.abs(dx) + Math.abs(dy) > 3) pan.moved = true;
    state.agent.view.x = pan.viewX + dx;
    state.agent.view.y = pan.viewY + dy;
    applyView();
  });
  const release = () => {
    if (!pan) return;
    scroll.classList.remove("panning");
    if (!pan.moved) {
      state.agent.selectedNodeId = null;
      clearAgentInspector();
      applyExecutionState();
    }
    state.agent.needsFit = false;
    pan = null;
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);

  scroll.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.12 : 0.89);
    } else {
      state.agent.view.x -= event.deltaX;
      state.agent.view.y -= event.deltaY;
      state.agent.needsFit = false;
      applyView();
    }
  }, { passive: false });

  canvas.addEventListener("dblclick", (event) => {
    if (event.target.closest(".agent-node")) return;
    fitAgentView();
  });
}

/* ----------------------------- Agent inspector ----------------------------- */

function insertPromptVariable(variable) {
  const textarea = $("#agent-node-prompt");
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? start;
  textarea.setRangeText(variable, start, end, "end");
  textarea.focus();
  updateSelectedAgentNodeDraft(false);
}

function bindInspector() {
  const form = $("#agent-node-form");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const snapshot = state.agent.history.editSnapshot;
    updateSelectedAgentNodeDraft(true);
    pushHistorySnapshot(snapshot);
    state.agent.history.editSnapshot = null;
    renderAgentCanvas();
    saveAgentGraph().then(() => showToast("Node saved", "success")).catch(() => {});
  });
  $("#agent-delete-node").addEventListener("click", deleteSelectedAgentNode);
  $("#agent-duplicate-node").addEventListener("click", duplicateSelectedAgentNode);
  $("#agent-node-title").addEventListener("input", () => updateSelectedAgentNodeDraft(false));
  $("#agent-node-prompt").addEventListener("input", () => updateSelectedAgentNodeDraft(false));
  ["#agent-node-title", "#agent-node-prompt"].forEach((selector) => {
    const input = $(selector);
    input.addEventListener("focusin", () => {
      if (!state.agent.history.editSnapshot) state.agent.history.editSnapshot = graphSnapshot();
    });
    input.addEventListener("focusout", () => {
      pushHistorySnapshot(state.agent.history.editSnapshot);
      state.agent.history.editSnapshot = null;
    });
  });
  $("#agent-node-next").addEventListener("change", (event) => {
    const node = agentNode(state.agent.selectedNodeId);
    if (!node) return;
    const snapshot = graphSnapshot();
    setAgentNext(node.id, event.target.value);
    pushHistorySnapshot(snapshot);
    renderAgentCanvas();
    if (sanitizeRetryConfigs()) {
      renderAgentCanvas();
      showToast("Edge change invalidated the previous retry settings; removed", "warning");
    }
    scheduleAgentSave();
  });
  $("#agent-retry-enabled").addEventListener("change", () => {
    const snapshot = graphSnapshot();
    const targets = upstreamModelNodes(state.agent.selectedNodeId);
    $("#agent-retry-target").disabled = !$("#agent-retry-enabled").checked || targets.length === 0;
    $("#agent-retry-max").disabled = !$("#agent-retry-enabled").checked || targets.length === 0;
    updateRetryFromInspector(snapshot);
  });
  $("#agent-retry-target").addEventListener("change", () => {
    const snapshot = graphSnapshot();
    updateRetryFromInspector(snapshot);
  });
  $("#agent-retry-max").addEventListener("change", () => {
    const snapshot = graphSnapshot();
    updateRetryFromInspector(snapshot);
  });
  $$("#agent-variable-bar [data-var]").forEach((button) => {
    button.addEventListener("click", () => insertPromptVariable(button.dataset.var));
  });
}

