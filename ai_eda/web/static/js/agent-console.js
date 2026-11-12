/* ------------------------------ Agent console ------------------------------ */

function eventStatus(event) {
  if (event.status === "running") return "running";
  if (event.status) return event.status;
  return event.report ? (event.report.passed ? "passed" : "failed") : "info";
}

function renderAgentEvents() {
  const list = $("#agent-events");
  const events = state.agent.events;
  $("#agent-event-count").textContent = String(events.length);
  list.replaceChildren();
  if (!events.length) {
    list.className = "agent-events empty-copy";
    list.textContent = "Steps appear here in real time.";
    renderAgentEventDetail();
    return;
  }
  list.className = "agent-events";
  events.forEach((event) => {
    const status = eventStatus(event);
    const [label, badge] = statusMeta(status);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `agent-event type-${event.node_type} status-${status} ${event.step === state.agent.selectedStep ? "active" : ""}`;
    button.innerHTML = `
      <span class="event-step">${String(event.step).padStart(2, "0")}</span>
      <span class="event-main">
        <strong></strong>
        <small></small>
      </span>
      <span class="event-result ${badge}">${label}</span>
    `;
    $("strong", button).textContent = String(event.title || "")
      .replace(/（第 (\d+) 轮）/g, " · Round $1")
      .replace(/（第(\d+)轮）/g, " · Round $1");
    const parts = [
      event.node_type.toUpperCase(),
      `attempt ${event.attempt || 1}`,
      formatDuration(event.duration_ms),
      formatOutputTokens(event),
    ].filter(Boolean);
    if (event.retry) {
      parts.push(event.retry.exhausted ? "retry exhausted" : `retry → ${event.retry.attempt}`);
    }
    $("small", button).textContent = parts.join(" · ");
    button.addEventListener("click", () => selectAgentStep(event.step));
    list.append(button);
  });
  if (state.agent.followLive) {
    const active = list.querySelector(".agent-event.active");
    if (active) {
      // 只滚动 Steps 自己的容器，不能调用 scrollIntoView：
      // 那会同时滚动整个文档，导致流式更新期间页面被锁在底部。
      const listRect = list.getBoundingClientRect();
      const activeRect = active.getBoundingClientRect();
      if (activeRect.top < listRect.top) {
        list.scrollTop -= listRect.top - activeRect.top;
      } else if (activeRect.bottom > listRect.bottom) {
        list.scrollTop += activeRect.bottom - listRect.bottom;
      }
    }
  }
  renderAgentEventDetail();
  if (state.agent.followLive && selectedEvent()?.status === "running") {
    const detail = $("#agent-detail-content");
    requestAnimationFrame(() => {
      if (state.agent.followLive && !detail.classList.contains("hidden")) {
        detail.scrollTop = detail.scrollHeight;
      }
    });
  }
}

function updateFollowLiveButton() {
  const button = $("#agent-follow-live");
  if (!button) return;
  button.classList.toggle("active", state.agent.followLive);
  button.textContent = state.agent.followLive ? "Following" : "Follow latest";
}

function agentDetailTabs(event) {
  if (!event) return [];
  const isModelStep = ["parser", "planner", "coder", "modifier", "diagnoser", "patch"].includes(event.node_id);
  if (event.node_id === "tester") {
    return [{ id: "tools", label: "Tool output" }];
  }
  const tabs = [];
  if (isModelStep) {
    tabs.push({ id: "prompt", label: "Sent prompt" });
    tabs.push({ id: "model", label: "Model output" });
    if (event.rtl) tabs.push({ id: "rtl", label: "RTL / TB" });
  }
  if (!tabs.length && (event.response || event.reasoning || event.raw_response)) {
    tabs.push({ id: "model", label: "Model output" });
  }
  return tabs;
}

function renderAgentDetailTabs(event) {
  const container = $("#agent-detail-tabs");
  const tabs = agentDetailTabs(event);
  const available = new Set(tabs.map((tab) => tab.id));
  if (!available.has(state.agent.detailTab)) {
    state.agent.detailTab = tabs[0]?.id || "";
  }
  container.replaceChildren();
  container.classList.toggle("hidden", !tabs.length);
  tabs.forEach((tab) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `agent-detail-tab ${tab.id === state.agent.detailTab ? "active" : ""}`;
    button.dataset.agentDetail = tab.id;
    button.textContent = tab.label;
    button.addEventListener("click", () => {
      rememberAgentDetailScroll();
      state.agent.detailTab = tab.id;
      setFollowForSelection(state.agent.selectedStep, tab.id);
      updateFollowLiveButton();
      renderAgentEventDetail();
    });
    container.append(button);
  });
}

function agentDetailKey(event) {
  if (!event) return "";
  return `${event.run_id || "run"}:${event.step}:${state.agent.detailTab}`;
}

function rememberAgentDetailScroll() {
  const detail = $("#agent-detail-content");
  const key = state.agent.renderedDetailKey;
  if (detail && key) state.agent.detailScrollPositions.set(key, detail.scrollTop);
}

function restoreAgentDetailScroll(event) {
  const detail = $("#agent-detail-content");
  const key = agentDetailKey(event);
  state.agent.renderedDetailKey = key;
  if (!detail || !key) return;
  const saved = state.agent.detailScrollPositions.get(key);
  requestAnimationFrame(() => {
    if (state.agent.renderedDetailKey !== key) return;
    // 跟随最新输出时，Execution Trace 自己的滚动条始终贴底；
    // 不调用 scrollIntoView，避免整个页面被锁到底部。
    if (state.agent.followLive && event?.status === "running") {
      detail.scrollTop = detail.scrollHeight;
      return;
    }
    detail.scrollTop = Number.isFinite(saved) ? saved : 0;
  });
}

function isLatestRunningStep(step) {
  const events = state.agent.events || [];
  const last = events[events.length - 1];
  return Boolean(state.agent.running && last && last.step === step && last.status === "running");
}

function setFollowForSelection(step, tab = state.agent.detailTab) {
  // Follow is opt-in: only the latest running step's Model Output tab locks to
  // the bottom while tokens stream. Selecting any older step/tab releases it.
  state.agent.followLive = Boolean(isLatestRunningStep(step) && tab === "model");
}

function selectAgentStep(step) {
  state.agent.selectedStep = step;
  setFollowForSelection(step);
  updateFollowLiveButton();
  renderAgentEvents();
  applyExecutionState();
}

function parseEventJson(event) {
  const raw = event?.raw_response || event?.response || "";
  if (!raw) return null;
  const stripped = raw.replace(/^```[a-zA-Z0-9_-]*\s*/, "").replace(/\s*```$/, "");
  try {
    const parsed = JSON.parse(stripped);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (_) {
    const start = stripped.indexOf("{");
    const end = stripped.lastIndexOf("}");
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(stripped.slice(start, end + 1));
      } catch (_) {
        return null;
      }
    }
    return null;
  }
}

function detailSection(container, title) {
  const section = document.createElement("section");
  section.className = "trace-detail-section";
  if (title) {
    const heading = document.createElement("h4");
    heading.textContent = title;
    section.append(heading);
  }
  container.append(section);
  return section;
}

function detailPre(section, text, className = "", format = "auto") {
  const body = document.createElement("div");
  body.className = `trace-detail-code ${className}`.trim();
  renderRichContent(body, text || "—", { format });
  section.append(body);
  return body;
}

function detailList(section, items) {
  if (!items?.length) return;
  const list = document.createElement("ul");
  list.className = "trace-detail-list";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = String(item);
    list.append(li);
  });
  section.append(list);
}

function detailPills(section, items) {
  if (!items?.length) return;
  const wrap = document.createElement("div");
  wrap.className = "trace-detail-pills";
  items.forEach((item) => {
    const pill = document.createElement("span");
    pill.textContent = `${item.name} → ${item.file}`;
    wrap.append(pill);
  });
  section.append(wrap);
}

function renderParserDetail(container, event) {
  const parsed = parseEventJson(event) || {};
  const verdict = document.createElement("div");
  verdict.className = `trace-verdict ${parsed.sufficient ? "pass" : "warn"}`;
  verdict.textContent = parsed.sufficient ? "Spec is sufficient" : "Spec needs more detail";
  container.append(verdict);
  const missing = parsed.missing || event.input_request?.missing || [];
  const questions = parsed.questions || event.input_request?.questions || [];
  if (missing.length) {
    const section = detailSection(container, "Missing details");
    detailList(section, missing);
  }
  if (questions.length) {
    const section = detailSection(container, "User questions");
    detailList(section, questions);
  }
  if (event.input_response) {
    const section = detailSection(container, "User supplement");
    detailPre(section, event.input_response, "", "markdown");
  }
  const parserAddendum = parsed.spec_addendum || parsed.refined_spec;
  if (parserAddendum) {
    const section = detailSection(container, "Parser addendum");
    detailPre(section, parserAddendum, "", "markdown");
  }
  if (!missing.length && !questions.length && !event.input_response) {
    const section = detailSection(container, "Conclusion");
    detailPre(section, event.response || "Spec is sufficient.", "", "markdown");
  }
}

function renderPlannerDetail(container, event) {
  const plan = parseEventJson(event) || {};
  if (!plan.modules?.length) {
    const section = detailSection(container, "Plan");
    detailPre(section, event.response || "No Planner output yet.", "", "auto");
    return;
  }
  const meta = detailSection(container, "Project");
  detailPre(meta, { project: plan.project_name || "—", top_module: plan.top_module || "—" }, "", "json");
  if (plan.architecture) {
    const skill = detailSection(container, "Planner Skill · Area/Power architecture");
    detailPre(skill, plan.architecture, "", "json");
  }
  const modules = detailSection(container, "Modules / Files");
  detailPills(modules, plan.modules);
  if (plan.notes) {
    const notes = detailSection(container, "Notes");
    detailPre(notes, plan.notes, "", "markdown");
  }
}

function renderTesterDetail(container, event) {
  const report = event.report || {};
  const verdict = document.createElement("div");
  verdict.className = `trace-verdict ${report.passed ? "pass" : "fail"}`;
  verdict.textContent = report.passed ? `Tester PASS · ${event.title}` : `Tester FAIL · ${event.title}`;
  const actions = document.createElement("div");
  actions.className = "tester-actions";
  const retest = document.createElement("button");
  retest.type = "button";
  retest.className = "primary-button compact-button";
  retest.textContent = "Retest";
  retest.addEventListener("click", () => retestCurrentAgentRun(retest));
  actions.append(retest);
  verdict.append(actions);
  container.append(verdict);
  const summary = detailSection(container, "Result");
  detailPre(summary, event.response || (report.passed ? "Teacher TB passed." : "Teacher TB failed."), "", "markdown");
  const checks = report.checks || [];
  if (checks.length) {
    const section = detailSection(container, "Failed checks");
    checks.slice(0, 8).forEach((check) => {
      const row = document.createElement("div");
      row.className = "trace-check";
      const name = document.createElement("strong");
      name.textContent = check.name || "check";
      const detail = document.createElement("span");
      detail.textContent = check.detail || "";
      row.append(name, detail);
      section.append(row);
    });
  }
  const metrics = report.metrics || {};
  const overview = detailSection(container, "Metrics overview");
  detailPre(
    overview,
    [
      metrics.area !== undefined ? `Area: ${Number(metrics.area).toFixed(3)} ${metrics.area_unit || ""}` : "Area: —",
      metrics.power_status === "ok"
        ? `Total power: ${(Number(metrics.total_power_w) * 1e6).toFixed(3)} µW`
        : "Power: —",
    ].join("\n"),
  );
}

function renderCoderDetail(container, event) {
  const section = detailSection(container, "Generated files");
  detailPre(section, event.response || "No Coder notes yet.", "", "markdown");
  if (event.rtl) {
    const code = detailSection(container, "File RTL");
    detailPre(code, event.rtl, "", "code");
  }
}

function renderModifierDetail(container, event) {
  const parsed = parseEventJson(event) || {};
  const files = parsed.files || [];
  if (files.length) {
    const section = detailSection(container, "Modified files");
    detailList(section, files.map((file) => file.path || "—"));
  }
  if (parsed.notes) {
    const notes = detailSection(container, "Repair notes");
    detailPre(notes, parsed.notes, "", "markdown");
  }
  if (!files.length) {
    const section = detailSection(container, "Repair result");
    detailPre(section, event.response || "No Modifier output yet.", "", "auto");
  }
}

function appendStreamCursor(block) {
  const body = block?.querySelector(".response-block-body");
  if (!body) return;
  const cursor = document.createElement("span");
  cursor.className = "trace-stream-cursor";
  body.append(cursor);
}

function renderAgentModelOutput(container, event) {
  const running = event.status === "running";
  const frame = document.createElement("section");
  frame.className = "trace-model-frame";

  const reasoningText = event.reasoning || "";
  const reasoning = responseBlock(
    "Reasoning",
    "reasoning",
    reasoningText,
    running
      ? "Waiting for streamed reasoning…"
      : "No displayable model reasoning for this step.",
  );
  if (running) appendStreamCursor(reasoning);
  frame.append(reasoning);

  const answer = document.createElement("section");
  answer.className = "response-block response-answer";
  const answerLabel = document.createElement("p");
  answerLabel.className = "response-block-label";
  answerLabel.textContent = "Model answer";
  answer.append(answerLabel);

  const structured = !running && ["parser", "planner", "modifier"].includes(event.node_id);
  if (structured) {
    if (event.node_id === "parser") renderParserDetail(answer, event);
    else if (event.node_id === "planner") renderPlannerDetail(answer, event);
    else renderModifierDetail(answer, event);
  } else {
    const body = document.createElement("div");
    body.className = "response-block-body";
    const text = event.node_id === "coder"
      ? event.response
      : (event.raw_response || event.response);
    if (text) {
      if (running) body.textContent = text;
      else renderRichContent(body, text, { format: event.node_id === "coder" ? "code" : "auto" });
    } else {
      body.classList.add("response-block-note");
      body.textContent = running ? "Streaming model answer…" : "No model answer for this step.";
    }
    if (running) appendStreamCursor(answer);
    answer.append(body);
  }

  if (event.error) {
    const error = document.createElement("div");
    error.className = "trace-verdict fail";
    error.textContent = `Execution failed: ${event.error}`;
    answer.append(error);
  }
  frame.append(answer);
  container.append(frame);
}

function renderAgentDetailContent(event) {
  const container = $("#agent-detail-content");
  if (!container) return;
  container.replaceChildren();
  if (!event) {
    container.textContent = "Select a step to inspect its details.";
    return;
  }
  const tab = state.agent.detailTab;
  if (tab === "prompt") {
    const section = detailSection(container, "Sent prompt");
    detailPre(section, event.prompt, "", "markdown");
    return;
  }
  if (tab === "rtl") {
    if (event.rtl) {
      const section = detailSection(container, "RTL / TB data");
      detailPre(section, event.rtl, "", "code");
    } else if (event.report) {
      const section = detailSection(container, "TB report");
      detailPre(section, JSON.stringify(event.report, null, 2), "", "json");
    } else {
      const section = detailSection(container, "RTL / TB data");
      detailPre(section, "No RTL or TB data for this step.");
    }
    return;
  }
  if (tab === "tools") {
    if (!event.report) {
      const section = detailSection(container, "Tool output");
      detailPre(section, "Tool is running; output will appear below.");
    }
    return;
  }
  if (tab === "model") {
    renderAgentModelOutput(container, event);
    return;
  }
  const section = detailSection(container, "Step");
  detailPre(section, "No output for this step.");
}

function syncExecutionTraceHeight() {
  const grid = document.querySelector(".agent-console-grid");
  const pane = document.querySelector(".agent-console-detail");
  if (!grid || !pane) return;
  const visual = document.querySelector("#agent-verify-visual");
  const content = visual && !visual.classList.contains("hidden")
    ? visual
    : document.querySelector("#agent-detail-content");
  if (!content) return;
  const paneTop = pane.getBoundingClientRect().top;
  const contentTop = content.getBoundingClientRect().top - paneTop;
  const contentHeight = Math.max(0, content.scrollHeight || content.getBoundingClientRect().height);
  const target = clamp(Math.ceil(contentTop + contentHeight + 12), 220, 640);
  grid.style.height = `${target}px`;
}

function renderAgentEventDetail() {
  rememberAgentDetailScroll();
  state.agent.renderedDetailKey = "";
  const event = selectedEvent();
  renderAgentDetailTabs(event);
  renderAgentDetailContent(event);

  const meta = $("#agent-event-meta");
  meta.replaceChildren();
  if (!event) {
    meta.classList.add("hidden");
  } else {
    meta.classList.remove("hidden");
    const status = eventStatus(event);
    const [label, badge] = statusMeta(status);
    const items = [
      ["STEP", `${event.step}`],
      ["TYPE", event.node_type.toUpperCase()],
      ["STATUS", label, badge],
      ["ATTEMPT", `${event.attempt || 1}`],
      ["DURATION", formatDuration(event.duration_ms)],
    ];
    if (eventOutputTokens(event) > 0) items.push(["OUT TOKENS", formatOutputTokens(event)]);
    items.forEach(([key, value, tone]) => {
      const chip = document.createElement("span");
      chip.className = `meta-chip ${tone || ""}`;
      chip.innerHTML = `<b>${escapeHtml(key)}</b><span>${escapeHtml(value)}</span>`;
      meta.append(chip);
    });
    if (event.retry) {
      const chip = document.createElement("span");
      chip.className = `meta-chip retry ${event.retry.exhausted ? "fail" : "running"}`;
      chip.innerHTML = `<b>RETRY</b><span>${event.retry.exhausted ? "retries exhausted" : `Back to model · round ${event.retry.attempt}`}</span>`;
      meta.append(chip);
    }
  }

  const showToolWindow = state.agent.detailTab === "tools" && Boolean(event?.report);
  $("#agent-detail-content").classList.toggle("hidden", showToolWindow);
  const visual = $("#agent-verify-visual");
  if (showToolWindow) {
    visual.classList.remove("hidden");
    renderAgentVerifyVisual(event.report);
  } else {
    visual.classList.add("hidden");
  }
  if (!showToolWindow) restoreAgentDetailScroll(event);
  requestAnimationFrame(() => {
    syncExecutionTraceHeight();
    if (state.agent.followLive && event?.status === "running") {
      const detail = $("#agent-detail-content");
      detail.scrollTop = detail.scrollHeight;
    }
  });
}
