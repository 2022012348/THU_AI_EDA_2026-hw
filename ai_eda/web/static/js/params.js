/* ------------------------------ API parameter lab ------------------------------ */

function paramDemo(demoId) {
  return state.params.lab?.demos.find((demo) => demo.id === demoId) || null;
}

function paramCompletionTokens(result) {
  return result?.usage?.completion_tokens ?? null;
}

function formatParamMeta(result) {
  const parts = [];
  const completion = paramCompletionTokens(result);
  if (completion !== null) parts.push(`completion ${completion} tok`);
  parts.push(formatDuration(result?.duration_ms));
  if (result?.finish_reason) parts.push(`finish_reason=${result.finish_reason}`);
  return parts.join(" · ");
}

function numberClose(actual, expected) {
  if (!Number.isFinite(actual) || !Number.isFinite(expected)) return false;
  return Math.abs(actual - expected) <= Math.max(0.02, Math.abs(expected) * 0.001);
}

function evaluateReasoningAnswer(demo, answer) {
  const expectedValues = (demo.expected_values?.length ? demo.expected_values : [Number(demo.expected_answer)])
    .map(Number)
    .filter((value) => Number.isFinite(value));
  const labels = demo.expected_labels?.length
    ? demo.expected_labels
    : expectedValues.map((_, index) => `Item ${index + 1}`);
  const lines = (answer || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const answerLine = [...lines].reverse().find((line) => line.includes("Answer"));
  let hits = null;
  let formatted = false;
  if (answerLine) {
    const numbers = (answerLine.match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
    if (numbers.length >= expectedValues.length) {
      formatted = true;
      hits = expectedValues.map((value, index) => ({
        label: labels[index],
        expected: value,
        actual: numbers[index],
        hit: numberClose(numbers[index], value),
      }));
    }
  }
  if (!hits) {
    const allNumbers = ((answer || "").match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
    hits = expectedValues.map((value, index) => ({
      label: labels[index],
      expected: value,
      actual: null,
      hit: allNumbers.some((number) => numberClose(number, value)),
    }));
  }
  const hitCount = hits.filter((item) => item.hit).length;
  return { hits, hitCount, total: expectedValues.length, complete: hitCount === expectedValues.length, formatted };
}

function textSimilarity(left, right) {
  const normalize = (text) => (text || "")
    .replace(/\s+/g, "")
    .replace(/[，。；：、！？,.;:!?"'“”‘’()（）\[\]【】{}<>《》]/g, "");
  const collect = (text) => {
    const grams = new Map();
    for (let index = 0; index < text.length - 1; index += 1) {
      const gram = text.slice(index, index + 2);
      grams.set(gram, (grams.get(gram) || 0) + 1);
    }
    return grams;
  };
  const a = normalize(left);
  const b = normalize(right);
  if (!a || !b) return 0;
  const leftGrams = collect(a);
  const rightGrams = collect(b);
  let overlap = 0;
  let total = 0;
  leftGrams.forEach((count, gram) => {
    overlap += Math.min(count, rightGrams.get(gram) || 0);
    total += count;
  });
  rightGrams.forEach((count) => { total += count; });
  return total ? (2 * overlap) / total : 0;
}

function createParamEmpty(text) {
  const paragraph = document.createElement("p");
  paragraph.className = "param-empty";
  paragraph.textContent = text;
  return paragraph;
}

function createParamResultCard({ label, badge, badgeTone = "neutral", meta, verdict, verdictTone = "", answer, reasoning }) {
  const card = document.createElement("article");
  card.className = "param-result";
  const head = document.createElement("header");
  head.className = "param-result-head";
  const heading = document.createElement("span");
  heading.className = "param-result-label";
  heading.textContent = label;
  head.append(heading);
  if (badge) {
    const tag = document.createElement("span");
    tag.className = `param-badge ${badgeTone}`;
    tag.textContent = badge;
    head.append(tag);
  }
  if (meta) {
    const metaNode = document.createElement("span");
    metaNode.className = "param-result-meta";
    metaNode.textContent = meta;
    head.append(metaNode);
  }
  card.append(head);
  if (verdict) {
    const verdictNode = document.createElement("p");
    verdictNode.className = `param-verdict${verdictTone ? ` ${verdictTone}` : ""}`;
    verdictNode.textContent = verdict;
    card.append(verdictNode);
  }
  const answerNode = document.createElement("div");
  answerNode.className = "param-answer";
  renderRichContent(answerNode, answer || "No displayable model content.", { format: "auto" });
  card.append(answerNode);
  if (reasoning) {
    const details = document.createElement("details");
    details.className = "param-reasoning";
    const summary = document.createElement("summary");
    summary.textContent = `Reasoning (${reasoning.length} chars)`;
    const reasoningNode = document.createElement("div");
    renderRichContent(reasoningNode, reasoning, { format: "markdown" });
    details.append(summary, reasoningNode);
    card.append(details);
  }
  return card;
}

function maxTokenVerdict(result) {
  const answer = result?.answer || "";
  if (result?.finish_reason === "length") {
    return {
      badge: "TRUNCATED",
      verdict: `✗ finish_reason=length: answer hit max_tokens at ${answer.length} chars; RTL is incomplete.`,
    };
  }
  if (!/\bendmodule\b/.test(answer)) {
    return {
      badge: "INCOMPLETE",
      verdict: "⚠ No endmodule found; the module is still incomplete.",
    };
  }
  return {
    badge: "COMPLETE",
    verdict: `✓ finish_reason=${result?.finish_reason || "stop"}; answer contains endmodule and the FIFO module is complete.`,
  };
}

function renderMaxTokenResults(demo, container, results) {
  const grid = document.createElement("div");
  grid.className = "param-result-grid";
  demo.presets.forEach((preset) => {
    const runs = results[preset.key];
    if (!runs?.length) return;
    const result = runs[0];
    const check = maxTokenVerdict(result);
    const complete = check.badge === "COMPLETE";
    grid.append(createParamResultCard({
      label: preset.label,
      badge: check.badge,
      badgeTone: complete ? "pass" : "fail",
      meta: formatParamMeta(result),
      verdict: check.verdict,
      verdictTone: complete ? "pass" : "fail",
      answer: result.answer,
      reasoning: result.reasoning,
    }));
  });
  if (!grid.childElementCount) return;
  container.append(grid);
}

function renderTemperatureResults(demo, container, results) {
  demo.presets.forEach((preset) => {
    const runs = results[preset.key];
    if (!runs?.length) return;
    const block = document.createElement("section");
    block.className = "param-temp-block";
    const head = document.createElement("header");
    head.className = "param-temp-head";
    head.textContent = preset.label;
    block.append(head);
    if (runs.length >= 2) {
      const similarity = Math.round(textSimilarity(runs[0].answer, runs[1].answer) * 100);
      const row = document.createElement("div");
      row.className = "param-similarity-row";
      const chip = document.createElement("span");
      chip.className = "param-badge neutral";
      chip.textContent = similarity >= 80 ? "Low temperature · responses are highly consistent" : "High temperature · responses differ significantly";
      const value = document.createElement("span");
      value.className = "param-similarity-value";
      value.textContent = `Similarity ${similarity}%`;
      const track = document.createElement("div");
      track.className = "param-similarity-track";
      const fill = document.createElement("span");
      fill.className = "param-similarity-fill";
      fill.style.width = `${similarity}%`;
      track.append(fill);
      row.append(chip, value, track);
      block.append(row);
    }
    const grid = document.createElement("div");
    grid.className = "param-result-grid";
    runs.forEach((result, index) => {
      grid.append(createParamResultCard({
        label: `Generation ${index + 1}`,
        badge: `T = ${result.settings?.temperature ?? preset.settings.temperature}`,
        meta: formatParamMeta(result),
        answer: result.answer,
        reasoning: result.reasoning,
      }));
    });
    block.append(grid);
    container.append(block);
  });
}

function renderReasoningResults(demo, container, results) {
  const grid = document.createElement("div");
  grid.className = "param-result-grid";
  demo.presets.forEach((preset) => {
    const runs = results[preset.key];
    if (!runs?.length) return;
    const result = runs[0];
    const check = evaluateReasoningAnswer(demo, result.answer);
    const reasoningChars = (result.reasoning || "").length;
    let badge;
    let tone;
    let verdict;
    if (check.complete && check.formatted) {
      badge = `All matched ${check.total}/${check.total}`;
      tone = "pass";
      verdict = `✓ All ${check.total} values match. Reference: ${demo.expected_answer}`;
    } else if (check.complete) {
      badge = "Values correct · format missing";
      tone = "warn";
      verdict = `⚠ Values match ${check.total} references, but no final answer line was found. Reference: ${demo.expected_answer}`;
    } else if (check.hitCount > 0) {
      badge = `Matched ${check.hitCount}/${check.total}`;
      tone = "warn";
      const missed = check.hits
        .filter((item) => !item.hit)
        .map((item) => `${item.label} expected ${item.expected.toFixed(2)}, actual ${item.actual === null ? "not formatted" : item.actual.toFixed(2)}`)
        .join("；");
      verdict = `✗ ${check.formatted ? "Final answer line" : "Extracted values"} matched ${check.hitCount}/${check.total}: ${missed}. Reference: ${demo.expected_answer}`;
    } else {
      badge = "No match";
      tone = "fail";
      verdict = `✗ Final answer did not match. Reference: ${demo.expected_answer}`;
    }
    grid.append(createParamResultCard({
      label: preset.label,
      badge,
      badgeTone: tone,
      meta: `${formatParamMeta(result)}${reasoningChars ? ` · reasoning ${reasoningChars} chars` : " · no reasoning returned"}`,
      verdict,
      verdictTone: tone,
      answer: result.answer,
      reasoning: result.reasoning,
    }));
  });
  if (!grid.childElementCount) return;
  container.append(grid);
}

function renderParamResults(demo) {
  const card = document.querySelector(`[data-param-demo="${demo.id}"]`);
  const container = card?.querySelector("[data-param-results]");
  if (!container) return;
  container.replaceChildren();
  const results = state.params.results[demo.id] || {};
  if (!Object.keys(results).length) {
    container.append(createParamEmpty("Not run yet. Click a button above to call the model and show answer, reasoning, and token usage."));
    return;
  }
  if (demo.check === "similarity") renderTemperatureResults(demo, container, results);
  else if (demo.check === "expected_answer") renderReasoningResults(demo, container, results);
  else renderMaxTokenResults(demo, container, results);
}

function createParamDemoCard(demo) {
  const card = document.createElement("article");
  card.className = "panel param-demo";
  card.dataset.paramDemo = demo.id;

  const head = document.createElement("header");
  head.className = "param-demo-head";
  head.innerHTML = `
    <div>
      <p class="eyebrow">EXPERIMENT ${String(demo.index).padStart(2, "0")}</p>
      <h2>${escapeHtml(demo.title)}</h2>
    </div>
    <span class="param-chip">${escapeHtml(demo.parameter)}</span>
  `;
  card.append(head);

  const summary = document.createElement("p");
  summary.className = "param-summary";
  summary.textContent = demo.summary;
  card.append(summary);

  const questionWrap = document.createElement("div");
  questionWrap.className = "param-question-wrap";
  const questionHead = document.createElement("div");
  questionHead.className = "param-question-head";
  questionHead.textContent = "Task · fixed prompt";
  const question = document.createElement("pre");
  question.className = "param-question";
  question.textContent = demo.question;
  questionWrap.append(questionHead, question);
  card.append(questionWrap);

  const hint = document.createElement("p");
  hint.className = "param-hint";
  hint.textContent = demo.hint;
  card.append(hint);

  const actions = document.createElement("div");
  actions.className = "param-actions";
  demo.presets.forEach((preset) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "param-run-button";
    button.dataset.paramRun = preset.key;
    button.textContent = preset.label;
    actions.append(button);
  });
  card.append(actions);

  const status = document.createElement("p");
  status.className = "param-run-status hidden";
  status.dataset.paramStatus = "";
  card.append(status);

  const results = document.createElement("div");
  results.className = "param-results";
  results.dataset.paramResults = "";
  card.append(results);
  return card;
}

function renderParamSubTabs() {
  const bar = $("#param-sub-tabs");
  const lab = state.params.lab;
  if (!bar || !lab) return;
  bar.replaceChildren();
  lab.demos.forEach((demo) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sub-tab";
    button.dataset.paramTab = demo.id;
    button.textContent = `${String(demo.index).padStart(2, "0")} · ${String(demo.parameter).replaceAll("_", " ").toUpperCase()}`;
    bar.append(button);
  });
}

function activateParamTab(name) {
  const lab = state.params.lab;
  if (!lab || !lab.demos.length) return;
  const target = lab.demos.some((demo) => demo.id === name) ? name : lab.demos[0].id;
  state.params.activeTab = target;
  $$("[data-param-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.paramTab === target);
  });
  $$("[data-param-page]").forEach((page) => {
    page.classList.toggle("hidden", page.dataset.paramPage !== target);
  });
}

function renderApiKeyStatus(status) {
  const node = $("#params-api-key-status");
  if (!node) return;
  const source = status?.source;
  const masked = status?.masked || "";
  if (source === "file" && status.configured) {
    node.textContent = `Local file · ${masked}`;
    node.title = "Using local .api_key; environment key ignored";
  } else if (source === "file") {
    node.textContent = "Local file is empty";
    node.title = "Delete .api_key or save again; environment fallback is disabled";
  } else if (source === "environment" && status.configured) {
    node.textContent = `Environment · ${masked}`;
    node.title = "A saved local key takes precedence over .api_key";
  } else {
    node.textContent = "Not configured";
    node.title = "Enter a key to save into local .api_key";
  }
}

async function loadApiKeyStatus() {
  try {
    renderApiKeyStatus(await api("/api/config/api-key"));
  } catch (error) {
    const node = $("#params-api-key-status");
    if (node) node.textContent = "Status read failed";
  }
}

async function saveApiKeyFromForm(event) {
  event.preventDefault();
  const input = $("#api-key-input");
  const value = input?.value?.trim() || "";
  if (!value) {
    showToast("Enter an API key first", "warning");
    input?.focus();
    return;
  }
  try {
    const status = await api("/api/config/api-key", {
      method: "POST",
      body: JSON.stringify({ api_key: value }),
    });
    input.value = "";
    renderApiKeyStatus(status);
    showToast("API key saved to local .api_key", "success");
  } catch (error) {
    showToast(`Failed to save API key: ${error.message}`, "error");
  }
}

async function clearApiKey() {
  try {
    const status = await api("/api/config/api-key", { method: "DELETE" });
    renderApiKeyStatus(status);
    showToast(status.source === "environment" ? "Local key removed; using environment key" : "Local API key removed", "success");
  } catch (error) {
    showToast(`Failed to clear API key: ${error.message}`, "error");
  }
}

function bindApiKeyControls() {
  const form = $("#api-key-form");
  if (!form) return;
  form.addEventListener("submit", saveApiKeyFromForm);
  $("#api-key-clear")?.addEventListener("click", clearApiKey);
}

function renderParamLab() {
  const lab = state.params.lab;
  if (!lab) return;
  $("#params-model").textContent = lab.model;
  $("#params-base-url").textContent = lab.base_url;
  const wrap = $("#params-demos");
  wrap.replaceChildren();
  lab.demos.forEach((demo) => {
    const page = document.createElement("section");
    page.className = "param-page";
    page.dataset.paramPage = demo.id;
    page.append(createParamDemoCard(demo));
    wrap.append(page);
  });
  lab.demos.forEach((demo) => renderParamResults(demo));
  renderParamSubTabs();
  activateParamTab(state.params.activeTab);
}

async function loadParamLab() {
  try {
    state.params.lab = await api("/api/param-lab");
    renderParamLab();
  } catch (error) {
    const wrap = $("#params-demos");
    wrap.replaceChildren();
    const message = document.createElement("p");
    message.className = "panel params-loading";
    message.textContent = `Unable to load parameter lab: ${error.message}`;
    wrap.append(message);
    showToast(error.message, "error");
  }
}

function setParamStatus(demoId, message, tone = "") {
  const card = document.querySelector(`[data-param-demo="${demoId}"]`);
  const status = card?.querySelector("[data-param-status]");
  if (!status) return;
  status.className = `param-run-status${tone ? ` ${tone}` : ""}${message ? "" : " hidden"}`;
  status.textContent = message;
}

function updateParamButtons(demoId) {
  const card = document.querySelector(`[data-param-demo="${demoId}"]`);
  if (!card) return;
  const running = Boolean(state.params.running[demoId]);
  card.querySelectorAll("[data-param-run]").forEach((button) => { button.disabled = running; });
}

async function runParamExperiment(demoId, presetKey) {
  const demo = paramDemo(demoId);
  const preset = demo?.presets.find((item) => item.key === presetKey);
  if (!demo || !preset || state.params.running[demoId]) return;
  const runCount = preset.runs || 1;
  const startedAt = Date.now();
  let currentRun = 0;
  const statusText = () => {
    const runHint = runCount > 1 && currentRun ? " · run ${currentRun}/${runCount}" : "";
    return `Calling model${runCount > 1 ? ` (${runCount} trials)` : ""}… ${formatDuration(Date.now() - startedAt)}${runHint}`;
  };
  state.params.running[demoId] = true;
  updateParamButtons(demoId);
  setParamStatus(demoId, statusText());
  const timer = setInterval(() => {
    if (!state.params.running[demoId]) return;
    setParamStatus(demoId, statusText());
  }, 1000);
  try {
    const runs = [];
    for (let index = 0; index < runCount; index += 1) {
      currentRun = index + 1;
      setParamStatus(demoId, statusText());
      runs.push(await api("/api/param-lab/run", {
        method: "POST",
        body: JSON.stringify({ demo_id: demoId, ...preset.settings }),
      }));
    }
    state.params.results[demoId] = { ...(state.params.results[demoId] || {}), [presetKey]: runs };
    renderParamResults(demo);
    setParamStatus(demoId, "");
    showToast(`${demo.parameter} experiment complete: ${preset.label}`, "success");
  } catch (error) {
    setParamStatus(demoId, error.message, "error");
    showToast(error.message, "error");
  } finally {
    clearInterval(timer);
    state.params.running[demoId] = false;
    updateParamButtons(demoId);
  }
}

