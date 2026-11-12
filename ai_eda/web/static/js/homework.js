/* ------------------------------- Homework tab ------------------------------- */

const HOMEWORK_DRAFT_KEY = "ai-eda-homework-draft";

function setHomeworkSaveStatus(text, mode = "") {
  const node = $("#hw-save-status");
  if (!node) return;
  node.textContent = text;
  node.className = `save-status ${mode}`;
}

function setHomeworkStatus(text, mode = "") {
  const node = $("#hw-run-status");
  if (!node) return;
  node.textContent = text;
  node.className = `run-status ${mode}`;
}

function renderHomeworkResponse() {
  const content = $("#hw-response-content");
  if (!content) return;
  content.replaceChildren();
  $$("[data-hw-response-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.hwResponseTab === state.homework.activeResponseTab);
  });
  const run = state.homework.lastRun;
  if (!run) {
    content.className = "response-content empty-copy";
    content.textContent = "Model response appears here; switch tabs to inspect extracted RTL.";
    return;
  }
  content.className = "response-content";
  if (state.homework.activeResponseTab === "rtl") {
    const body = document.createElement("pre");
    body.className = "response-block-body response-rtl";
    body.textContent = run.rtl || "No RTL extracted from the model answer.";
    content.append(body);
    return;
  }
  content.append(
    responseBlock(
      "Reasoning",
      "reasoning",
      run.reasoning,
      "No displayable reasoning. Enable Request model reasoning and rerun; teacher TB remains authoritative.",
    ),
    responseBlock("Final answer", "answer", run.answer, "No final model answer."),
  );
  content.scrollTop = 0;
}

function renderHomeworkTrace(trace = []) {
  const wrap = $("#hw-trace-wrap");
  if (!wrap) return;
  if (!trace.length) {
    wrap.className = "trace-wrap empty-copy";
    wrap.textContent = "No structured trace; check Raw TB output on compile failure.";
    return;
  }
  const table = document.createElement("table");
  table.className = "trace-table homework-trace";
  const headRow = document.createElement("tr");
  ["Cycle", "Test", "Event", "Result", "Details"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.append(th);
  });
  const thead = document.createElement("thead");
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  trace.forEach((event) => {
    const row = document.createElement("tr");
    const cycle = document.createElement("td");
    cycle.textContent = event.cycle ?? "—";
    const test = document.createElement("td");
    test.textContent = event.test || "—";
    const eventName = document.createElement("td");
    eventName.textContent = event.event || "—";
    const result = document.createElement("td");
    const badge = document.createElement("span");
    const ok = String(event.ok) === "1";
    badge.className = `event-result ${ok ? "pass" : "fail"}`;
    badge.textContent = ok ? "OK" : "FAIL";
    result.append(badge);
    const detail = document.createElement("td");
    detail.textContent = event.detail || "—";
    row.append(cycle, test, eventName, result, detail);
    tbody.append(row);
  });
  table.append(thead, tbody);
  wrap.className = "trace-wrap";
  wrap.replaceChildren(table);
}

function renderHomeworkReport(report = {}) {
  const badge = $("#hw-tb-badge");
  if (!badge) return;
  const passed = report.passed;
  badge.textContent = passed === true ? "PASS" : passed === false ? "FAIL" : "NO RUN";
  badge.className = `result-badge ${passed === true ? "pass" : passed === false ? "fail" : "neutral"}`;
  const checks = report.checks || [];
  const checkText = checks.length ? ` · ${checks.map((item) => item.name).join("、")}` : "";
  $("#hw-tb-summary").textContent = report.stage
    ? `${report.stage === "compile" ? "Compile" : "Simulation"} ${passed ? "PASS" : "FAIL"}${checkText}`
    : "No test result yet.";
  $("#hw-tb-stdout").textContent = [report.stdout, report.stderr].filter(Boolean).join("\n") || "—";
  renderHomeworkTrace(report.trace || []);
  const metrics = report.metrics || {};
  if (metrics.area !== undefined) {
    $("#hw-metric-area").textContent = Number(metrics.area).toFixed(3);
    $("#hw-metric-unit").textContent = metrics.area_unit || "library area units";
    if (metrics.power_status === "ok") {
      $("#hw-metric-power").textContent = `${(Number(metrics.total_power_w) * 1e6).toFixed(3)} µW`;
      $("#hw-metric-status").textContent = "POWER READY";
      $("#hw-metric-note").textContent = `Dynamic ${(Number(metrics.dynamic_power_w) * 1e6).toFixed(3)} µW · Leakage ${(Number(metrics.leakage_power_w) * 1e6).toFixed(3)} µW`;
    } else {
      $("#hw-metric-power").textContent = "—";
      $("#hw-metric-status").textContent = "AREA ONLY";
      $("#hw-metric-note").textContent = metrics.power_reason || "OpenSTA";
    }
  } else {
    $("#hw-metric-area").textContent = "—";
    $("#hw-metric-unit").textContent = metrics.reason || "Shown only after successful post-simulation synthesis";
    $("#hw-metric-power").textContent = "N/A";
    $("#hw-metric-status").textContent = metrics.status ? metrics.status.toUpperCase() : "WAIT";
    $("#hw-metric-note").textContent = "Yosys / OpenSTA";
  }
}

function renderHomeworkRun(run) {
  state.homework.lastRun = run;
  const report = run.report || {};
  $("#hw-run-id-label").textContent = run.run_id;
  renderHomeworkResponse();
  renderHomeworkReport(report);
  if (!report.stage) {
    if (report.metrics) {
      const badge = $("#hw-tb-badge");
      badge.textContent = "NO RUN";
      badge.className = "result-badge neutral";
      $("#hw-tb-summary").textContent = "Metrics evaluated; teacher AES TB not yet run. Click Run TB.";
      $("#hw-tb-stdout").textContent = "—";
      const trace = $("#hw-trace-wrap");
      trace.className = "trace-wrap empty-copy";
      trace.textContent = "Teacher AES TB not started.";
      setHomeworkStatus(`Metrics evaluated; TB pending: ${run.run_id}`);
    } else {
      markHomeworkRunPending();
      setHomeworkStatus(`Generated; verification pending: ${run.run_id}`);
    }
  } else {
    setHomeworkStatus(`Complete: ${run.run_id}`);
  }
  updateHomeworkActions();
}

function renderHomeworkRuns(runs = []) {
  state.homework.runs = runs;
  const list = $("#hw-run-list");
  if (!list) return;
  list.replaceChildren();
  if (!runs.length) {
    list.className = "run-list empty-copy";
    list.textContent = "No homework runs yet";
    return;
  }
  list.className = "run-list";
  runs.slice().reverse().forEach((run) => {
    const item = document.createElement("div");
    item.className = "run-item";
    const open = document.createElement("button");
    open.className = "run-open-button";
    const status = run.passed === true ? "PASS" : run.passed === false ? "FAIL" : "Unverified";
    const stage = run.stage ? ` · ${escapeHtml(run.stage)}` : "";
    const preview = run.prompt_preview ? `<span class="run-prompt-preview">${escapeHtml(run.prompt_preview)}</span>` : "";
    open.innerHTML = `${escapeHtml(run.run_id)}<span>${status}${stage}</span>${preview}`;
    open.addEventListener("click", () => openHomeworkRun(run.run_id));
    const remove = document.createElement("button");
    remove.className = "run-delete-button";
    remove.type = "button";
    remove.title = "Delete "+run.run_id;
    remove.setAttribute("aria-label", "Delete homework run "+run.run_id);
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteHomeworkRun(run.run_id));
    item.append(open, remove);
    list.append(item);
  });
}

async function loadHomeworkState() {
  try {
    const payload = await api("/api/homework/state");
    state.homework.task = payload.task;
    state.homework.prompt = payload.prompt || "";
    $("#hw-task-title").textContent = payload.task.title;
  renderRichContent($("#hw-spec-content"), payload.task.spec, { format: "markdown" });
    $("#hw-prompt-file").textContent = payload.task.prompt_file;
    const draft = localStorage.getItem(HOMEWORK_DRAFT_KEY);
    $("#hw-prompt-editor").value = draft ?? state.homework.prompt;
    setHomeworkSaveStatus(draft === null ? "Loaded local file" : "Recovered browser draft", draft === null ? "saved" : "");
    renderHomeworkRuns(payload.runs || []);
  } catch (error) {
    showToast(`Unable to load homework state: ${error.message}`, "error");
    setHomeworkStatus(`Unable to load homework state: ${error.message}`, "error");
  }
}

async function loadHomeworkRuns() {
  const payload = await api("/api/homework/runs");
  renderHomeworkRuns(payload.runs || []);
  updateHomeworkActions();
}

async function openHomeworkRun(runId) {
  if (state.streaming.active) {
    showToast("Model is streaming; wait before switching runs", "warning");
    return;
  }
  try {
    const run = await api(`/api/homework/runs/${runId}`);
    renderHomeworkRun(run);
    if (run.prompt) {
      $("#hw-prompt-editor").value = run.prompt;
      localStorage.removeItem(HOMEWORK_DRAFT_KEY);
      setHomeworkSaveStatus("Prompt loaded (not saved)");
    }
    setHomeworkStatus(`Run and prompt loaded: ${runId}`);
    showToast("Run and prompt loaded", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function clearHomeworkResultView() {
  state.homework.lastRun = null;
  $("#hw-run-id-label").textContent = "Not run yet";
  renderHomeworkResponse();
  renderHomeworkReport({});
  updateHomeworkActions();
}

async function deleteHomeworkRun(runId) {
  const approved = await confirmAction("Delete homework record", `Delete “${runId}”? This cannot be undone.`, "Delete");
  if (!approved) return;
  try {
    await api(`/api/homework/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
    if (state.homework.lastRun?.run_id === runId) clearHomeworkResultView();
    await loadHomeworkRuns();
    showToast(`Deleted ${runId}`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function clearHomeworkRuns() {
  const approved = await confirmAction("Clear homework records", "Clear all homework runs? This cannot be undone.", "Clear");
  if (!approved) return;
  try {
    await api("/api/homework/runs", { method: "DELETE" });
    clearHomeworkResultView();
    await loadHomeworkRuns();
    showToast("Homework records cleared", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveHomeworkPrompt() {
  const content = $("#hw-prompt-editor").value;
  localStorage.setItem(HOMEWORK_DRAFT_KEY, content);
  setHomeworkSaveStatus("Saving…", "saving");
  try {
    await api("/api/homework/prompt", { method: "PUT", body: JSON.stringify({ content }) });
    state.homework.prompt = content;
    localStorage.removeItem(HOMEWORK_DRAFT_KEY);
    setHomeworkSaveStatus("Saved to local file", "saved");
  } catch (error) {
    setHomeworkSaveStatus("Save failed; draft kept in browser", "error");
    throw error;
  }
}

function scheduleHomeworkSave() {
  localStorage.setItem(HOMEWORK_DRAFT_KEY, $("#hw-prompt-editor").value);
  setHomeworkSaveStatus("Browser draft saved", "saving");
  clearTimeout(state.homework.saveTimer);
  state.homework.saveTimer = setTimeout(() => saveHomeworkPrompt().catch(() => {}), 650);
}

async function generateHomework() {
  const button = $("#hw-run-button");
  const content = $("#hw-prompt-editor").value;
  if (content.trim().length < 20) {
    showToast("Write a complete prompt (at least 20 characters)", "warning");
    $("#hw-prompt-editor").focus();
    return;
  }
  if (state.streaming.active) return;
  button.disabled = true;
  updateHomeworkActions();
  clearTimeout(state.homework.saveTimer);
  beginStreamDisplay("homework");
  $("#hw-run-id-label").textContent = "Generating…";
  const startedAt = Date.now();
  const timer = setInterval(() => {
    if (!state.streaming.active) return;
    setHomeworkStatus(`Generating… ${formatDuration(Date.now() - startedAt)}`, "running");
  }, 500);
  setHomeworkStatus("Requesting model; output streams live…", "running");
  let generated = null;
  try {
    await saveHomeworkPrompt();
    await streamApi("/api/homework/stream", {
      content,
      temperature: Number($("#hw-temperature").value),
      max_tokens: Number($("#hw-max-tokens").value),
      thinking: $("#hw-thinking").checked,
      reasoning_effort: $("#hw-reasoning-effort").value,
    }, (event) => {
      if (event.type === "start") {
        $("#hw-run-id-label").textContent = event.run_id;
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
    renderHomeworkRun(generated);
    setHomeworkStatus("Generation complete. Use Run TB or Evaluate metrics.");
    await loadHomeworkRuns();
  } catch (error) {
    endStreamDisplay();
    setHomeworkStatus(error.message, "error");
    showToast(error.message, "error");
    if (state.homework.lastRun) {
      renderHomeworkResponse();
    } else {
      const content = $("#hw-response-content");
      content.className = "response-content empty-copy";
      content.textContent = "Generation failed; adjust settings and retry.";
      $("#hw-run-id-label").textContent = "Not run yet";
    }
  } finally {
    clearInterval(timer);
    button.disabled = false;
    updateHomeworkActions();
  }
}

async function verifyHomeworkMode(mode) {
  if (!state.homework.lastRun?.run_id) {
    showToast("Generate or load a homework run first", "warning");
    return;
  }
  if (state.verifyingHomework) return;
  const taskId = state.homework.lastRun?.metadata?.task_name || state.homework.task?.id;
  if (!taskId) {
    showToast("Unable to determine homework task id", "error");
    return;
  }
  state.verifyingHomework = true;
  updateHomeworkActions();
  const label = mode === "tb" ? "Teacher AES TB" : "Area/power metrics";
  const startedAt = Date.now();
  const timer = setInterval(() => {
    setHomeworkStatus(`Running ${label}… ${formatDuration(Date.now() - startedAt)}`, "running");
  }, 500);
  setHomeworkStatus(`Running ${label}…`, "running");
  try {
    const run = await api("/api/verify", {
      method: "POST",
      body: JSON.stringify({ task_id: taskId, run_id: state.homework.lastRun.run_id, mode }),
    });
    renderHomeworkRun(run);
    await loadHomeworkRuns();
    showToast(`${label} updated`, "success");
  } catch (error) {
    setHomeworkStatus(error.message, "error");
    showToast(error.message, "error");
  } finally {
    clearInterval(timer);
    state.verifyingHomework = false;
    updateHomeworkActions();
  }
}

async function runHomeworkTB() {
  await verifyHomeworkMode("tb");
}

async function runHomeworkMetric() {
  await verifyHomeworkMode("metrics");
}

