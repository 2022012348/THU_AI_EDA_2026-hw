/* ------------------------------ Prompt tab ------------------------------ */

function setSaveStatus(text, mode = "") {
  const node = $("#save-status");
  if (!node) return;
  node.textContent = text;
  node.className = `save-status ${mode}`;
}

function activePrompt() { return state.prompts.get(state.activePromptId); }

function buildStageList() {
  const list = $("#prompt-list");
  list.replaceChildren();
  [...state.prompts.entries()].forEach(([id, prompt], index) => {
    const button = document.createElement("button");
    button.className = `stage ${id === state.activePromptId ? "active" : ""}`;
    button.innerHTML = `<span class="stage-index">${String(index + 1).padStart(2, "0")}</span><span><strong>${escapeHtml(prompt.title)}</strong><small>${escapeHtml(prompt.description)}</small></span>`;
    button.addEventListener("click", () => selectPrompt(id));
    list.append(button);
  });
  $("#stage-count").textContent = `${state.prompts.size} stages`;
}

function selectPrompt(id) {
  const prompt = state.prompts.get(id);
  if (!prompt) return;
  state.activePromptId = id;
  $("#prompt-editor").value = prompt.content;
  $("#prompt-description").textContent = prompt.description;
  $("#prompt-file").textContent = prompt.file;
  buildStageList();
}

function responseBlock(label, tone, text, fallback) {
  const block = document.createElement("section");
  block.className = `response-block response-${tone}`;
  const heading = document.createElement("p");
  heading.className = "response-block-label";
  heading.textContent = label;
  const body = document.createElement("div");
  body.className = "response-block-body";
  if (text) {
    renderRichContent(body, text, { format: "auto" });
  } else {
    body.classList.add("response-block-note");
    body.textContent = fallback;
  }
  block.append(heading, body);
  return block;
}

function renderResponse() {
  const content = $("#response-content");
  content.replaceChildren();
  $$("[data-response-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.responseTab === state.activeResponseTab);
  });
  if (!state.lastRun) {
    content.className = "response-content empty-copy";
    content.textContent = "Reasoning and final answer appear here after generation.";
    return;
  }
  content.className = "response-content";
  if (state.activeResponseTab === "rtl") {
    const body = document.createElement("pre");
    body.className = "response-block-body response-rtl";
    body.textContent = state.lastRun.rtl || "No RTL extracted from the model answer.";
    content.append(body);
    return;
  }
  content.append(
    responseBlock(
      "Reasoning",
      "reasoning",
      state.lastRun.reasoning,
      "No displayable reasoning. Enable Request model reasoning and rerun; TB remains the source of truth.",
    ),
    responseBlock("Final answer", "answer", state.lastRun.answer, "No final model answer."),
  );
  content.scrollTop = 0;
}

/* ----------------------------- Streaming model output ----------------------------- */

function streamContentElement(target) {
  return target === "homework" ? $("#hw-response-content") : $("#response-content");
}

function setStreamingActiveTab(target) {
  if (target === "homework") {
    state.homework.activeResponseTab = "combined";
    $$("[data-hw-response-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.hwResponseTab === "combined");
    });
  } else {
    state.activeResponseTab = "combined";
    $$("[data-response-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.responseTab === "combined");
    });
  }
}

function createStreamBlock(label, tone) {
  const block = document.createElement("section");
  block.className = `response-block response-${tone}`;
  const heading = document.createElement("p");
  heading.className = "response-block-label";
  heading.textContent = label;
  const body = document.createElement("pre");
  body.className = "response-block-body response-block-note";
  block.append(heading, body);
  return { block, body };
}

function beginStreamDisplay(target) {
  if (state.streaming.frame) cancelAnimationFrame(state.streaming.frame);
  state.streaming = {
    active: true,
    target,
    reasoning: "",
    answer: "",
    frame: null,
    reasoningNode: null,
    answerNode: null,
  };
  setStreamingActiveTab(target);
  const content = streamContentElement(target);
  if (!content) return;
  content.className = "response-content";
  content.replaceChildren();
  const reasoning = createStreamBlock("Reasoning", "reasoning");
  const answer = createStreamBlock("Final answer", "answer");
  reasoning.body.textContent = "Waiting for reasoning…";
  answer.body.textContent = "Waiting for final answer…";
  content.append(reasoning.block, answer.block);
  state.streaming.reasoningNode = reasoning.body;
  state.streaming.answerNode = answer.body;
}

function appendStreamDelta(kind, text) {
  if (!state.streaming.active || !text) return;
  if (kind === "reasoning") state.streaming.reasoning += text;
  else state.streaming.answer += text;
  if (state.streaming.frame) return;
  state.streaming.frame = requestAnimationFrame(flushStreamDisplay);
}

function flushStreamDisplay() {
  state.streaming.frame = null;
  if (!state.streaming.active) return;
  const update = (node, text) => {
    if (!node) return;
    node.classList.remove("response-block-note");
    node.textContent = text;
  };
  update(state.streaming.reasoningNode, state.streaming.reasoning);
  update(state.streaming.answerNode, state.streaming.answer);
  const content = streamContentElement(state.streaming.target);
  if (content) content.scrollTop = content.scrollHeight;
}

function endStreamDisplay() {
  if (state.streaming.frame) cancelAnimationFrame(state.streaming.frame);
  state.streaming.frame = null;
  state.streaming.active = false;
}

async function streamApi(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator = buffer.indexOf("\n\n");
    while (separator >= 0) {
      const raw = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      raw.split("\n").forEach((line) => {
        if (!line.startsWith("data:")) return;
        const payload = JSON.parse(line.slice(5).trim());
        onEvent(payload);
      });
      separator = buffer.indexOf("\n\n");
    }
  }
}

function markTeachingRunPending() {
  const badge = $("#tb-badge");
  badge.textContent = "Unverified";
  badge.className = "result-badge neutral";
  $("#tb-summary").textContent = "RTL generated; teacher TB not yet run. Click Run TB; use Evaluate metrics for area/power.";
  $("#tb-stdout").textContent = "—";
  const trace = $("#trace-wrap");
  trace.className = "trace-wrap empty-copy";
  trace.textContent = "Teacher TB trace appears after a run.";
  $("#metric-area").textContent = "—";
  $("#metric-unit").textContent = "Awaiting verification";
  $("#metric-power").textContent = "—";
  $("#metric-status").textContent = "PENDING";
  $("#metric-note").textContent = "Yosys / OpenSTA";
}

function markHomeworkRunPending() {
  const badge = $("#hw-tb-badge");
  badge.textContent = "Unverified";
  badge.className = "result-badge neutral";
  $("#hw-tb-summary").textContent = "RTL generated; teacher AES TB not yet run. Click Run TB; use Evaluate metrics for area/power.";
  $("#hw-tb-stdout").textContent = "—";
  const trace = $("#hw-trace-wrap");
  trace.className = "trace-wrap empty-copy";
  trace.textContent = "Teacher TB trace appears after a run.";
  $("#hw-metric-area").textContent = "—";
  $("#hw-metric-unit").textContent = "Awaiting verification";
  $("#hw-metric-power").textContent = "—";
  $("#hw-metric-status").textContent = "PENDING";
  $("#hw-metric-note").textContent = "Yosys / OpenSTA";
}

function updateTeachingActions() {
  const busy = state.streaming.active || state.verifyingTeaching;
  const runButton = $("#run-button");
  if (runButton) runButton.disabled = busy;
  const hasRun = Boolean(state.lastRun?.run_id);
  ["#run-tb-button", "#run-metric-button"].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = busy || !hasRun;
  });
}

function updateHomeworkActions() {
  const busy = state.streaming.active || state.verifyingHomework;
  const runButton = $("#hw-run-button");
  if (runButton) runButton.disabled = busy;
  const hasRun = Boolean(state.homework.lastRun?.run_id);
  ["#hw-run-tb-button", "#hw-run-metric-button"].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = busy || !hasRun;
  });
}

function signal(value, expected) {
  const mismatch = expected !== undefined && expected !== "" && value !== expected;
  const className = mismatch ? "signal-mismatch" : value === "1" ? "signal-high" : "signal-low";
  return `<span class="${className}">${escapeHtml(value ?? "—")}</span>`;
}

function renderTrace(trace = []) {
  const wrap = $("#trace-wrap");
  if (!trace.length) {
    wrap.className = "trace-wrap empty-copy";
    wrap.textContent = "No visual trace; check Raw TB output on compile failure.";
    return;
  }
  const template = $("#trace-template");
  const table = template.content.firstElementChild.cloneNode(true);
  const body = table.querySelector("tbody");
  trace.forEach((event) => {
    const row = document.createElement("tr");
    const input = `${signal(event.in_valid)} · ${signal(event.in_ready)} · ${signal(event.in_bit)}`;
    const output = `${signal(event.out_valid, event.expected_valid)} · ${signal(event.out_ready)} · ${signal(event.match, event.expected_match)}`;
    const expected = `${signal(event.expected_valid)} · ${signal(event.expected_match)}`;
    row.innerHTML = `<td>${escapeHtml(event.cycle)}</td><td>${escapeHtml(event.case || "—")}<br><small>${escapeHtml(event.event || "sample")}</small></td><td>${input}</td><td>${output}</td><td>${expected}</td>`;
    body.append(row);
  });
  wrap.className = "trace-wrap";
  wrap.replaceChildren(table);
}

function renderReport(report = {}) {
  const badge = $("#tb-badge");
  const passed = report.passed;
  badge.textContent = passed === true ? "PASS" : passed === false ? "FAIL" : "NO RUN";
  badge.className = `result-badge ${passed === true ? "pass" : passed === false ? "fail" : "neutral"}`;
  const checks = report.checks || [];
  const checkText = checks.length ? ` · ${checks.map((item) => item.name).join("、")}` : "";
  $("#tb-summary").textContent = report.stage
    ? `${report.stage === "compile" ? "Compile" : "Simulation"}${passed ? "PASS" : "FAIL"}${checkText}`
    : "No test result yet.";
  $("#tb-stdout").textContent = [report.stdout, report.stderr].filter(Boolean).join("\n") || "—";
  renderTrace(report.trace || []);
  const metrics = report.metrics || {};
  if (metrics.area !== undefined) {
    $("#metric-area").textContent = Number(metrics.area).toFixed(3);
    $("#metric-unit").textContent = metrics.area_unit || "library area units";
    if (metrics.power_status === "ok") {
      $("#metric-power").textContent = `${(Number(metrics.total_power_w) * 1e6).toFixed(3)} µW`;
      $("#metric-status").textContent = "POWER READY";
      $("#metric-note").textContent = `Dynamic ${(Number(metrics.dynamic_power_w) * 1e6).toFixed(3)} µW · Leakage ${(Number(metrics.leakage_power_w) * 1e6).toFixed(3)} µW`;
    } else {
      $("#metric-power").textContent = "—";
      $("#metric-status").textContent = "AREA ONLY";
      $("#metric-note").textContent = metrics.power_reason || "OpenSTA";
    }
  } else {
    $("#metric-area").textContent = "—";
    $("#metric-unit").textContent = metrics.reason || "Shown only after successful post-simulation synthesis";
    $("#metric-power").textContent = "N/A";
    $("#metric-status").textContent = metrics.status ? metrics.status.toUpperCase() : "WAIT";
    $("#metric-note").textContent = "Yosys / OpenSTA";
  }
}

function renderRun(run) {
  state.lastRun = run;
  const report = run.report || {};
  $("#run-id-label").textContent = run.run_id;
  renderResponse();
  renderReport(report);
  if (!report.stage) {
    if (report.metrics) {
      const badge = $("#tb-badge");
      badge.textContent = "NO RUN";
      badge.className = "result-badge neutral";
      $("#tb-summary").textContent = "Metrics evaluated; teacher TB not yet run. Click Run TB.";
      $("#tb-stdout").textContent = "—";
      const trace = $("#trace-wrap");
      trace.className = "trace-wrap empty-copy";
      trace.textContent = "Teacher TB not started.";
      $("#run-status").textContent = `Metrics evaluated; TB pending: ${run.run_id}`;
    } else {
      markTeachingRunPending();
      $("#run-status").textContent = `Generated; verification pending: ${run.run_id}`;
    }
  } else {
    $("#run-status").textContent = `Complete: ${run.run_id}`;
  }
  $("#run-status").className = "run-status";
  updateTeachingActions();
}

async function generatePrompt() {
  const button = $("#run-button");
  const prompt = activePrompt();
  if (!prompt || state.streaming.active) return;
  button.disabled = true;
  updateTeachingActions();
  $("#run-status").textContent = "Requesting model; output streams live…";
  $("#run-status").className = "run-status running";
  $("#run-id-label").textContent = "Generating…";
  beginStreamDisplay("teaching");
  const startedAt = Date.now();
  const timer = setInterval(() => {
    if (!state.streaming.active) return;
    $("#run-status").textContent = `Generating… ${formatDuration(Date.now() - startedAt)}`;
  }, 500);
  let generated = null;
  try {
    await streamApi("/api/runs/stream", {
      prompt_id: prompt.id,
      content: $("#prompt-editor").value,
      temperature: Number($("#temperature").value),
      max_tokens: Number($("#max-tokens").value),
      thinking: $("#thinking").checked,
      reasoning_effort: $("#reasoning-effort").value,
    }, (event) => {
      if (event.type === "start") {
        $("#run-id-label").textContent = event.run_id;
      } else if (event.type === "reasoning") {
        appendStreamDelta("reasoning", event.text);
      } else if (event.type === "answer") {
        appendStreamDelta("answer", event.text);
      } else if (event.type === "done") {
        generated = event.run;
      } else if (event.type === "error") {
        throw new Error(event.error);
      }
    });
    endStreamDisplay();
    if (!generated) throw new Error("Streamed generation returned no run.");
    renderRun(generated);
    $("#run-status").textContent = "Generation complete. Use Run TB or Evaluate metrics.";
    await loadRuns();
  } catch (error) {
    endStreamDisplay();
    $("#run-status").textContent = error.message;
    $("#run-status").className = "run-status error";
    if (state.lastRun) renderResponse();
    else {
      const content = $("#response-content");
      content.className = "response-content empty-copy";
      content.textContent = "Generation failed; adjust settings and retry.";
      $("#run-id-label").textContent = "Not run yet";
    }
  } finally {
    clearInterval(timer);
    button.disabled = false;
    updateTeachingActions();
  }
}

async function verifyPromptMode(mode) {
  if (!state.lastRun?.run_id) {
    showToast("Generate or load a run first", "warning");
    return;
  }
  if (state.verifyingTeaching) return;
  state.verifyingTeaching = true;
  updateTeachingActions();
  const label = mode === "tb" ? "Teacher TB" : "Area/power metrics";
  $("#run-status").textContent = `Running ${label}…`;
  $("#run-status").className = "run-status running";
  const startedAt = Date.now();
  const timer = setInterval(() => {
    $("#run-status").textContent = `Running ${label}… ${formatDuration(Date.now() - startedAt)}`;
  }, 500);
  try {
    const run = await api("/api/verify", {
      method: "POST",
      body: JSON.stringify({ task_id: state.task.id, run_id: state.lastRun.run_id, mode }),
    });
    renderRun(run);
    await loadRuns();
    showToast(`${label} updated`, "success");
  } catch (error) {
    $("#run-status").textContent = error.message;
    $("#run-status").className = "run-status error";
    showToast(error.message, "error");
  } finally {
    clearInterval(timer);
    state.verifyingTeaching = false;
    updateTeachingActions();
  }
}

async function runPromptTB() {
  await verifyPromptMode("tb");
}

async function runPromptMetric() {
  await verifyPromptMode("metrics");
}

async function loadRun(runId) {
  if (state.streaming.active) {
    showToast("Model is streaming; wait for completion before switching runs", "warning");
    return;
  }
  try { renderRun(await api(`/api/runs/${state.task.id}/${runId}`)); }
  catch (error) {
    $("#run-status").textContent = error.message;
    $("#run-status").className = "run-status error";
  }
}

async function loadRuns() {
  if (!state.task) return;
  const payload = await api(`/api/tasks/${state.task.id}/runs`);
  const list = $("#run-list");
  list.replaceChildren();
  if (!payload.runs.length) {
    list.className = "run-list empty-copy";
    list.textContent = "No runs for this task yet";
    return;
  }
  list.className = "run-list";
  payload.runs.slice().reverse().slice(0, 8).forEach((run) => {
    const item = document.createElement("div");
    item.className = "run-item";
    const open = document.createElement("button");
    open.className = "run-open-button";
    open.innerHTML = `${escapeHtml(run.run_id)}<span>${run.passed === true ? "PASS" : run.passed === false ? "FAIL" : "Unverified"} · ${escapeHtml(run.prompt_source || "")}</span>`;
    open.addEventListener("click", () => loadRun(run.run_id));
    const remove = document.createElement("button");
    remove.className = "run-delete-button";
    remove.type = "button";
    remove.title = `Delete ${run.run_id}`;
    remove.setAttribute("aria-label", `Delete run ${run.run_id}`);
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteRun(run.run_id));
    item.append(open, remove);
    list.append(item);
  });
  updateTeachingActions();
}

async function deleteRun(runId) {
  const approved = await confirmAction("Delete run", `Delete “${runId}”? This cannot be undone.`, "Delete");
  if (!approved) return;
  try {
    await api(`/api/runs/${state.task.id}/${encodeURIComponent(runId)}`, { method: "DELETE" });
    if (state.lastRun?.run_id === runId) state.lastRun = null;
    await loadRuns();
    $("#run-status").textContent = `Deleted: ${runId}`;
    $("#run-status").className = "run-status";
    showToast(`Deleted ${runId}`, "success");
  } catch (error) {
    $("#run-status").textContent = error.message;
    $("#run-status").className = "run-status error";
  }
}

async function clearRuns() {
  const approved = await confirmAction("Clear runs", "Clear all runs for this task? This cannot be undone.", "Clear");
  if (!approved) return;
  try {
    await api(`/api/runs/${state.task.id}`, { method: "DELETE" });
    state.lastRun = null;
    renderResponse();
    renderReport({});
    $("#run-id-label").textContent = "Not run yet";
    await loadRuns();
    $("#run-status").textContent = "All runs cleared.";
    $("#run-status").className = "run-status";
    showToast("Prompt runs cleared", "success");
  } catch (error) {
    $("#run-status").textContent = error.message;
    $("#run-status").className = "run-status error";
  }
}

