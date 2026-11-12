/* --------------------------- Agent graph helpers --------------------------- */

const NODE_DEFAULTS = {
  model: {
    title: "New Model Step",
    prompt: "Generate compilable SystemVerilog RTL from the frozen spec. Return one RTL module only; no Markdown, explanation, testbench, or extra modules.\n\n{spec}\n\nIf previous verification feedback is provided, fix it first:\n{verify_feedback}",
  },
  verify: {
    title: "New Verify Step",
    prompt: "Verify the generated RTL with the fixed teacher TB.",
  },
};

const NODE_GLYPHS = {
  model: ["✦", "model-glyph"],
  verify: ["✓", "verify-glyph"],
};

const DEFAULT_AGENT_GRAPH = {
  name: "EDA Auto-Design Agent",
  entry: "designer",
  nodes: [
    {
      id: "designer",
      type: "model",
      title: "01 · RTL Designer",
      prompt: "Generate compilable SystemVerilog RTL from the frozen spec. Interfaces, timing, and reset are mandatory. Return one RTL module only; no Markdown, explanation, testbench, or extra modules.\n\n{spec}\n\nGeneration attempt {attempt}. If failure feedback is provided, diagnose and fix each item:\n{verify_feedback}",
      x: 120,
      y: 120,
    },
    {
      id: "verify",
      type: "verify",
      title: "02 · Compile & TB",
      prompt: "Compile and simulate the generated RTL with the teacher testbench; record pass status, checks, and metrics.",
      x: 540,
      y: 120,
      retry: { target: "designer", max: 8 },
    },
  ],
  edges: [{ from: "designer", to: "verify" }],
};

function normalizeAgentGraph(graph) {
  if (!graph || typeof graph !== "object") return graph;
  const rawNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const rawEdges = Array.isArray(graph.edges) ? graph.edges : [];
  const byId = new Map(rawNodes.filter((node) => node && typeof node.id === "string").map((node) => [node.id, node]));
  if (!byId.size) return graph;
  const next = new Map();
  rawEdges.forEach((edge) => {
    if (edge && byId.has(edge.from) && byId.has(edge.to)) next.set(edge.from, edge.to);
  });
  const order = [];
  const seen = new Set();
  let current = byId.has(graph.entry) ? graph.entry : rawNodes[0]?.id;
  while (current && !seen.has(current)) {
    seen.add(current);
    order.push(current);
    current = next.get(current);
  }
  rawNodes.forEach((node) => {
    if (node?.id && !seen.has(node.id)) {
      seen.add(node.id);
      order.push(node.id);
    }
  });
  const kept = order.filter((id) => ["model", "verify"].includes(byId.get(id)?.type));
  if (!kept.length) kept.push("designer");
  const nodes = kept.map((id) => {
    const node = JSON.parse(JSON.stringify(byId.get(id) || {
      id: "designer",
      type: "model",
      title: "01 · RTL Designer",
      prompt: NODE_DEFAULTS.model.prompt,
      x: 120,
      y: 120,
    }));
    delete node.retry;
    if (node.type === "model") {
    }
    return node;
  });
  const validSides = new Set(["left", "right", "top", "bottom"]);
  rawNodes.forEach((raw) => {
    if (!raw || !kept.includes(raw.id) || !raw.retry || !kept.includes(raw.retry.target)) return;
    const node = nodes.find((candidate) => candidate.id === raw.id);
    if (node) {
      node.retry = { target: raw.retry.target, max: Math.max(1, Math.min(10, Number(raw.retry.max) || 8)) };
      if (validSides.has(raw.retry.fromSide)) node.retry.fromSide = raw.retry.fromSide;
      if (validSides.has(raw.retry.toSide)) node.retry.toSide = raw.retry.toSide;
    }
  });
  const rawEdgeByPair = new Map();
  rawEdges.forEach((edge) => {
    if (edge && typeof edge.from === "string" && typeof edge.to === "string") {
      rawEdgeByPair.set(`${edge.from}->${edge.to}`, edge);
    }
  });
  return {
    name: graph.name || "Agent design flow",
    entry: nodes[0].id,
    nodes,
    edges: nodes.slice(0, -1).map((node, index) => {
      const next = nodes[index + 1];
      const source = rawEdgeByPair.get(`${node.id}->${next.id}`) || {};
      const edge = { from: node.id, to: next.id };
      if (validSides.has(source.fromSide)) edge.fromSide = source.fromSide;
      if (validSides.has(source.toSide)) edge.toSide = source.toSide;
      return edge;
    }),
  };
}

function agentGraph() { return state.agent.graph; }
function agentNode(id) { return agentGraph()?.nodes.find((node) => node.id === id); }
function agentNext(id) { return agentGraph()?.edges.find((edge) => edge.from === id)?.to || ""; }

function agentChainOrder() {
  const graph = agentGraph();
  if (!graph) return [];
  const order = [];
  const seen = new Set();
  let current = graph.entry;
  while (current && !seen.has(current)) {
    seen.add(current);
    order.push(current);
    current = agentNext(current);
  }
  return order;
}

function graphSnapshot() {
  return agentGraph() ? JSON.stringify(agentGraph()) : "";
}

function designFingerprint(graph) {
  if (!graph) return "";
  return JSON.stringify({
    name: graph.name,
    entry: graph.entry,
    nodes: (graph.nodes || []).map(({ x, y, ...rest }) => rest),
    edges: graph.edges || [],
  });
}

function isDesignDirty() {
  return Boolean(
    state.agent.designName
    && state.agent.designBaseline
    && designFingerprint(agentGraph()) !== state.agent.designBaseline
  );
}

function updateDesignBadge() {
  const badge = $("#agent-design-badge");
  if (!badge) return;
  if (!state.agent.designName) {
    badge.classList.add("hidden");
    badge.textContent = "";
    return;
  }
  const dirty = isDesignDirty();
  badge.classList.remove("hidden");
  badge.classList.toggle("dirty", dirty);
  badge.textContent = `${state.agent.designName}${dirty ? " · modified" : ""}`;
  badge.title = dirty ? "Graph differs from the saved design; the next run will be marked as modified" : "Run will be linked to this design";
}

function setCurrentDesign(design) {
  state.agent.designId = design?.id || null;
  state.agent.designName = design?.name || null;
  state.agent.designBaseline = design
    ? designFingerprint(design.graph || agentGraph())
    : null;
  updateDesignBadge();
}

function currentRunDesign() {
  if (!state.agent.designName) return { design_id: null, design_name: null };
  const dirty = isDesignDirty();
  return {
    design_id: dirty ? null : state.agent.designId,
    design_name: dirty ? `${state.agent.designName} (modified)` : state.agent.designName,
  };
}

function setAgentSaveStatus(text, mode = "") {
  const node = $("#agent-save-status");
  if (!node) return;
  node.textContent = text;
  node.className = `save-status ${mode}`;
}

function updateUndoRedoButtons() {
  const undo = $("#agent-undo");
  const redo = $("#agent-redo");
  if (undo) undo.disabled = state.agent.history.past.length === 0;
  if (redo) redo.disabled = state.agent.history.future.length === 0;
}

function pushHistorySnapshot(snapshot) {
  if (!snapshot || snapshot === graphSnapshot()) return;
  state.agent.history.past.push(snapshot);
  if (state.agent.history.past.length > HISTORY_LIMIT) state.agent.history.past.shift();
  state.agent.history.future = [];
  updateUndoRedoButtons();
}

function commitGraphMutation(mutate, { render = true, save = true } = {}) {
  const snapshot = graphSnapshot();
  mutate(agentGraph());
  pushHistorySnapshot(snapshot);
  if (render) renderAgentCanvas();
  if (save) scheduleAgentSave();
}

function restoreGraph(snapshot) {
  try {
    state.agent.graph = normalizeAgentGraph(JSON.parse(snapshot));
  } catch (_) {
    showToast("Undo history is corrupted", "error");
    return;
  }
  if (!agentNode(state.agent.selectedNodeId)) {
    state.agent.selectedNodeId = null;
    clearAgentInspector();
  } else {
    selectAgentNode(state.agent.selectedNodeId, { open: false });
  }
  renderAgentCanvas();
  scheduleAgentSave();
}

function undoAgent() {
  if (state.agent.running) {
    showToast("Agent is running; cannot undo graph changes", "warning");
    return;
  }
  if (!state.agent.history.past.length) return;
  const current = graphSnapshot();
  const previous = state.agent.history.past.pop();
  state.agent.history.future.push(current);
  restoreGraph(previous);
  updateUndoRedoButtons();
}

function redoAgent() {
  if (state.agent.running) {
    showToast("Agent is running; cannot redo graph changes", "warning");
    return;
  }
  if (!state.agent.history.future.length) return;
  const current = graphSnapshot();
  const next = state.agent.history.future.pop();
  state.agent.history.past.push(current);
  restoreGraph(next);
  updateUndoRedoButtons();
}

function setAgentNext(sourceId, targetId) {
  const graph = agentGraph();
  graph.edges = graph.edges.filter((edge) => edge.from !== sourceId && (!targetId || edge.to !== targetId));
  if (targetId) graph.edges.push({ from: sourceId, to: targetId });
}

function wouldCreateCycle(sourceId, targetId) {
  let current = targetId;
  const seen = new Set();
  while (current) {
    if (current === sourceId) return true;
    if (seen.has(current)) return true;
    seen.add(current);
    current = agentNext(current);
  }
  return false;
}

function connectCandidates(sourceId) {
  const graph = agentGraph();
  if (!graph) return [];
  const incoming = new Map(graph.edges.map((edge) => [edge.to, edge.from]));
  return graph.nodes.filter((node) => {
    if (node.id === sourceId) return false;
    if (wouldCreateCycle(sourceId, node.id)) return false;
    const predecessor = incoming.get(node.id);
    return !predecessor || predecessor === sourceId;
  });
}

function sanitizeRetryConfigs() {
  const graph = agentGraph();
  if (!graph) return 0;
  const order = agentChainOrder();
  let removed = 0;
  graph.nodes.forEach((node) => {
    if (!node.retry) return;
    const nodeIndex = order.indexOf(node.id);
    const targetIndex = order.indexOf(node.retry.target);
    if (node.type !== "verify" || nodeIndex < 0 || targetIndex < 0 || targetIndex >= nodeIndex) {
      delete node.retry;
      removed += 1;
    }
  });
  if (removed) {
    const selected = agentNode(state.agent.selectedNodeId);
    if (selected?.type === "verify") renderRetryInspector(selected);
  }
  return removed;
}

function upstreamModelNodes(nodeId) {
  const order = agentChainOrder();
  const index = order.indexOf(nodeId);
  if (index < 0) return [];
  return order.slice(0, index).map(agentNode).filter((node) => node?.type === "model");
}

function updateNodeCard(node) {
  const card = document.querySelector(`.agent-node[data-node-id="${node.id}"]`);
  if (!card) return;
  const title = $(".agent-node-title", card);
  const preview = $(".agent-node-preview", card);
  if (title) title.textContent = node.title;
  if (preview) preview.textContent = node.prompt.replaceAll(/\s+/g, " ").trim();
}

function renderAgentFlowList() {
  const container = $("#agent-flow-list");
  if (!container) return;
  const graph = agentGraph();
  if (!graph) {
    container.textContent = "Flow is not loaded.";
    return;
  }
  container.replaceChildren();
  const addItem = (label, title, detail, onClick) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "agent-flow-item";
    const tag = document.createElement("span");
    tag.className = "agent-flow-tag";
    tag.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = title;
    const small = document.createElement("small");
    small.textContent = detail;
    button.append(tag, strong, small);
    if (onClick) button.addEventListener("click", onClick);
    container.append(button);
  };
  addItem("INPUT", "Task Spec", "Read-only · click to view the full spec", () => openAgentFixedDialog("spec"));
  const chain = agentChainOrder();
  const ids = chain.length ? chain : graph.nodes.map((node) => node.id);
  ids.forEach((id) => {
    const node = agentNode(id);
    if (!node) return;
    let detail = "Fixed step · read-only";
    if (node.id === "tester") detail = "PASS ends; FAIL → Modifier repairs, then retest";
    if (node.id === "diagnoser") detail = "Localize a failure from the current snapshot";
    if (node.id === "patch") detail = "Patch the selected RTL files, then retest";
    if (node.id === "modifier") detail = "Repair RTL, then return to Tester";
    addItem(node.type.toUpperCase(), node.title, detail, () => selectAgentNode(node.id));
  });
}

function selectAgentNode(nodeId, options = {}) {
  const node = agentNode(nodeId);
  if (!node) return;
  state.agent.selectedNodeId = nodeId;
  document.querySelectorAll(".agent-node").forEach((element) => {
    element.classList.toggle("selected", element.dataset.nodeId === nodeId);
  });
  applyExecutionState();
  if (state.agent.running) {
    showToast("Agent is running; cannot edit Run Config", "warning");
    return;
  }
  if (options.open !== false) openAgentRunConfigDialog(nodeId);
}

function openAgentRunConfigDialog(nodeId) {
  const node = agentNode(nodeId);
  const dialog = $("#agent-run-config-dialog");
  if (!node || !dialog) return;
  state.agent.selectedNodeId = nodeId;
  const skill = $("#agent-planner-skill");
  const hint = $("#agent-planner-skill-hint");
  const available = (state.agent.projectId || "stream_filter") === "stream_filter";
  if (skill) {
    skill.disabled = !available;
    if (!available) skill.checked = false;
  }
  if (hint) {
    hint.textContent = available
      ? "Run a Python estimator before planning and select smaller-area FIFO architecture parameters."
      : "This Skill is available only for the synchronous FIFO project.";
  }
  if (!dialog.open) dialog.showModal();
}

function clearAgentInspector() {
  const dialog = $("#agent-run-config-dialog");
  if (dialog?.open) dialog.close();
  document.querySelectorAll(".agent-node").forEach((element) => element.classList.remove("selected"));
}

function renderRetryInspector(node) {
  const field = $("#agent-retry-field");
  if (node.type !== "verify") {
    field.classList.add("hidden");
    return;
  }
  field.classList.remove("hidden");
  const targets = upstreamModelNodes(node.id);
  const targetSelect = $("#agent-retry-target");
  targetSelect.replaceChildren();
  targets.forEach((candidate) => targetSelect.append(new Option(candidate.title, candidate.id)));
  const retry = node.retry && targets.some((candidate) => candidate.id === node.retry.target) ? node.retry : null;
  const enabled = Boolean(retry);
  $("#agent-retry-enabled").checked = enabled;
  $("#agent-retry-enabled").disabled = targets.length === 0;
  targetSelect.disabled = !enabled || targets.length === 0;
  $("#agent-retry-max").disabled = !enabled || targets.length === 0;
  if (retry) {
    targetSelect.value = retry.target;
    $("#agent-retry-max").value = retry.max;
  } else if (targets.length) {
    targetSelect.value = targets[targets.length - 1].id;
    $("#agent-retry-max").value = 8;
  }
}

function renderAgentSpec() {
  // Spec 现在以固定节点显示在画布左上角；保留空函数兼容旧调用。
}

function fixedResultPreview(result) {
  if (!result) return "Agent has not run yet.";
  const metrics = result.metrics || {};
  const parts = [result.summary || (result.passed ? "Final verification passed." : "Final verification failed.")];
  if (metrics.area !== undefined) parts.push(`Area ${Number(metrics.area).toFixed(3)}`);
  if (metrics.power_status === "ok") parts.push(`Power ${(Number(metrics.total_power_w) * 1e6).toFixed(3)} µW`);
  return parts.join(" · ");
}

function updateFixedSummaryNode(result) {
  const node = document.querySelector('.agent-node.fixed-summary');
  if (!node) return;
  const badge = node.querySelector("[data-fixed-badge]");
  const preview = node.querySelector("[data-fixed-preview]");
  if (badge) {
    if (!result) {
      badge.textContent = "NO RUN";
      badge.className = "fixed-badge neutral";
    } else {
      badge.textContent = result.passed ? "PASS" : "FAIL";
      badge.className = `fixed-badge ${result.passed ? "pass" : "fail"}`;
    }
  }
  if (preview) preview.textContent = fixedResultPreview(result);
  node.classList.toggle("pass", result?.passed === true);
  node.classList.toggle("fail", result?.passed === false);
}

function renderAgentResult(result) {
  state.agent.result = result || null;
  updateFixedSummaryNode(state.agent.result);
}

function fixedNodePositions() {
  const graph = agentGraph();
  if (!graph || !graph.nodes.length) return null;
  const xs = graph.nodes.map((node) => Number(node.x) || 0);
  const ys = graph.nodes.map((node) => Number(node.y) || 0);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const maxRight = Math.max(...xs.map((x) => x + NODE_W));
  const fixedY = Math.max(8, minY - FIXED_NODE_H - 24);
  const specX = Math.max(8, minX);
  return {
    spec: { x: specX, y: fixedY },
  };
}

function firstSpecPreview() {
  return state.agent.spec ? "Open the task specification" : "Task Spec is not loaded.";
}

function createFixedAgentNode(kind, position) {
  const isSpec = kind === "spec";
  const element = document.createElement("div");
  element.className = `agent-node fixed-node fixed-${kind}`;
  element.dataset.fixedNode = kind;
  element.style.left = `${position.x}px`;
  element.style.top = `${position.y}px`;
  element.tabIndex = 0;
  element.setAttribute("role", "button");
  element.setAttribute("aria-label", isSpec ? "View input Spec" : "View output Summary");
  element.innerHTML = `
    <div class="agent-node-head">
      <span class="node-glyph ${isSpec ? "spec-glyph" : "report-glyph"}">${isSpec ? "▤" : "≡"}</span>
      <div class="agent-node-heading">
        <span class="agent-node-type">${isSpec ? "INPUT" : "OUTPUT"}</span>
        <span class="agent-node-title">${isSpec ? "Task Spec" : "Result Summary"}</span>
      </div>
      <span data-fixed-badge class="fixed-badge neutral">${isSpec ? "READ ONLY" : "NO RUN"}</span>
    </div>
    <div class="agent-node-preview" data-fixed-preview></div>
    <div class="agent-node-foot"><span class="muted">Read-only · click to view</span></div>
  `;
  const preview = $("[data-fixed-preview]", element);
  if (isSpec) {
    preview.textContent = firstSpecPreview();
  } else {
    const result = state.agent.result;
    const badge = $("[data-fixed-badge]", element);
    if (result) {
      badge.textContent = result.passed ? "PASS" : "FAIL";
      badge.className = `fixed-badge ${result.passed ? "pass" : "fail"}`;
      element.classList.toggle("pass", result.passed === true);
      element.classList.toggle("fail", result.passed === false);
    }
    preview.textContent = fixedResultPreview(result);
  }
  element.addEventListener("click", () => openAgentFixedDialog(kind));
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openAgentFixedDialog(kind);
    }
  });
  return element;
}

function renderAgentFixedNodes(layer) {
  const positions = fixedNodePositions();
  if (!positions) return;
  layer.append(createFixedAgentNode("spec", positions.spec));
}

function openAgentFixedDialog(kind) {
  const title = $("#agent-fixed-title");
  const label = $("#agent-fixed-kind");
  const content = $("#agent-fixed-content");
  if (!title || !label || !content) return;
  if (kind === "spec") {
    label.textContent = "INPUT · TASK SPEC";
    title.textContent = "Task Spec (read-only)";
    renderRichContent(content, state.agent.spec || "Task Spec is not loaded.", { format: "markdown" });
  } else {
    label.textContent = "OUTPUT · FINAL RESULT";
    title.textContent = "Final Summary (read-only)";
    const result = state.agent.result;
    if (!result) {
      renderRichContent(content, "Agent has not run yet. After a run, the final teacher TB, area, and power results appear here.", { format: "markdown" });
    } else {
      const metrics = result.metrics || {};
      const lines = [
        `Result: ${result.passed ? "PASS" : "FAIL"}`,
        `Stage: ${result.stage || "—"}`,
        `Summary: ${result.summary || "—"}`,
      ];
      if (metrics.area !== undefined) lines.push(`Area: ${Number(metrics.area).toFixed(3)} ${metrics.area_unit || ""}`.trim());
      if (metrics.power_status === "ok") {
        lines.push(`Total power: ${(Number(metrics.total_power_w) * 1e6).toFixed(3)} µW`);
        if (metrics.internal_power_w !== undefined) lines.push(`Internal: ${(Number(metrics.internal_power_w) * 1e6).toFixed(3)} µW`);
        if (metrics.switching_power_w !== undefined) lines.push(`Switching: ${(Number(metrics.switching_power_w) * 1e6).toFixed(3)} µW`);
        if (metrics.leakage_power_w !== undefined) lines.push(`Leakage: ${(Number(metrics.leakage_power_w) * 1e6).toFixed(3)} µW`);
      }
      if (result.plan?.modules?.length) {
        lines.push("", "Plan:");
        result.plan.modules.forEach((module) => lines.push(`- ${module.name} → ${module.file}`));
      }
      if (Array.isArray(result.files) && result.files.length) {
        lines.push("", "Generated files:");
        result.files.forEach((file) => lines.push(`- ${file.path} (${file.bytes} bytes)`));
      }
      if (result.parser_rounds) lines.push(`Parser rounds: ${result.parser_rounds}`);
      if (result.tester_attempts) lines.push(`Tester attempts: ${result.tester_attempts}`);
      if (Array.isArray(result.supplements) && result.supplements.length) {
        lines.push("", "User supplements:");
        result.supplements.forEach((item) => lines.push(`- ${item}`));
      }
      const report = state.agent.run?.report;
      const checks = report?.checks || [];
      if (checks.length) {
        lines.push("", "Failed checks:");
        checks.slice(0, 8).forEach((check) => lines.push(`- ${check.name}: ${check.detail || ""}`));
      }
      lines.push("", "Metrics:", JSON.stringify(metrics, null, 2));
      renderRichContent(content, lines.join("\n"), { format: "markdown" });
    }
  }
  $("#agent-fixed-dialog").showModal();
}

function updateSelectedAgentNodeDraft(renderCanvas = false) {
  const node = agentNode(state.agent.selectedNodeId);
  if (!node) return;
  node.title = $("#agent-node-title").value.trim() || NODE_DEFAULTS[node.type].title;
  node.prompt = $("#agent-node-prompt").value;
  updateNodeCard(node);
  if (renderCanvas) {
    renderAgentCanvas();
  } else {
    drawAgentEdges();
  }
  updateDesignBadge();
  scheduleAgentSave();
}

function updateRetryFromInspector(previousSnapshot) {
  const node = agentNode(state.agent.selectedNodeId);
  if (!node || node.type !== "verify") return;
  const enabled = $("#agent-retry-enabled").checked;
  if (enabled) {
    const target = $("#agent-retry-target").value;
    const max = clamp(Number($("#agent-retry-max").value) || 8, 1, 10);
    if (!target) {
      $("#agent-retry-enabled").checked = false;
      showToast("Add a Model node before the Verify node", "warning");
      return;
    }
    node.retry = { target, max };
  } else {
    delete node.retry;
  }
  pushHistorySnapshot(previousSnapshot);
  renderAgentCanvas();
  scheduleAgentSave();
}

function deleteSelectedAgentNode() {
  const graph = agentGraph();
  const id = state.agent.selectedNodeId;
  if (!graph || !id) return;
  if (state.agent.running) {
    showToast("Agent is running; cannot edit the flow", "warning");
    return;
  }
  if (id === graph.entry) {
    showToast("The entry node cannot be deleted; connect a new entry node first", "warning");
    return;
  }
  commitGraphMutation((target) => {
    target.nodes = target.nodes.filter((node) => node.id !== id);
    target.edges = target.edges.filter((edge) => edge.from !== id && edge.to !== id);
    target.nodes.forEach((node) => {
      if (node.retry?.target === id) delete node.retry;
    });
  });
  state.agent.selectedNodeId = null;
  clearAgentInspector();
  renderAgentCanvas();
  showToast("Node deleted; use ⌘Z to undo", "success");
}

function duplicateSelectedAgentNode() {
  const node = agentNode(state.agent.selectedNodeId);
  const graph = agentGraph();
  if (!node || !graph) return;
  if (state.agent.running) {
    showToast("Agent is running; cannot edit the flow", "warning");
    return;
  }
  const base = `${node.id}_copy`;
  let id = base;
  let index = 2;
  while (agentNode(id)) {
    id = `${base}${index}`;
    index += 1;
  }
  commitGraphMutation((target) => {
    const clone = {
      id,
      type: node.type,
      title: `${node.title} copy`,
      prompt: node.prompt,
      x: node.x + 40,
      y: node.y + 40,
    };
    target.nodes.push(clone);
  });
  selectAgentNode(id);
  renderAgentCanvas();
  showToast("Node copied", "success");
}

function addAgentNode(type, x, y) {
  const graph = agentGraph();
  if (!graph || !NODE_DEFAULTS[type]) return;
  if (state.agent.running) {
    showToast("Agent is running; cannot edit the flow", "warning");
    return;
  }
  if (graph.nodes.length >= 16) {
    showToast("At most 16 nodes", "warning");
    return;
  }
  const viewCenter = viewportCenterPoint();
  const baseX = x ?? viewCenter.x - NODE_W / 2 + (graph.nodes.length % 5) * 24;
  const baseY = y ?? viewCenter.y - NODE_H / 2 + (graph.nodes.length % 4) * 24;
  let id = `node_${Date.now().toString(36)}`;
  while (agentNode(id)) id = `${id}x`;
  commitGraphMutation((target) => {
    const newNode = {
      id,
      type,
      title: NODE_DEFAULTS[type].title,
      prompt: NODE_DEFAULTS[type].prompt,
      x: Math.max(0, Math.round(baseX / GRID) * GRID),
      y: Math.max(0, Math.round(baseY / GRID) * GRID),
    };
    target.nodes.push(newNode);
    if (!target.entry) target.entry = id;
  });
  selectAgentNode(id);
  renderAgentCanvas();
  applyView();
}

function autoLayoutGraph() {
  if (!agentGraph() || state.agent.running) return;
  const order = agentChainOrder();
  const remaining = agentGraph().nodes.map((node) => node.id).filter((id) => !order.includes(id));
  const ids = [...order, ...remaining];
  commitGraphMutation((graph) => {
    ids.forEach((id, index) => {
      const node = graph.nodes.find((candidate) => candidate.id === id);
      if (!node) return;
      node.x = 48 + index * (NODE_W + 72);
      node.y = 120;
    });
  }, { render: true });
  fitAgentView();
  showToast("Nodes arranged in execution order", "success");
}

async function resetAgentGraph() {
  if (state.agent.running) {
    showToast("Agent is running; cannot reset the flow", "warning");
    return;
  }
  const approved = await confirmAction("Restore built-in example", "The current flow will be replaced by the built-in example; old runs remain in history.", "Restore");
  if (!approved) return;
  const snapshot = graphSnapshot();
  state.agent.graph = JSON.parse(JSON.stringify(DEFAULT_AGENT_GRAPH));
  setCurrentDesign(null);
  pushHistorySnapshot(snapshot);
  state.agent.selectedNodeId = null;
  clearAgentInspector();
  renderAgentCanvas();
  fitAgentView();
  scheduleAgentSave();
  showToast("Built-in example restored", "success");
}

