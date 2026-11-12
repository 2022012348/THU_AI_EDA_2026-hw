/* Agent report rendering: metrics, CPU summary, DFT, trace. */

function metricCard(label, value, note, tone = "") {
  const card = document.createElement("div");
  card.className = `metric ${tone}`;
  const title = document.createElement("span");
  title.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = value;
  const small = document.createElement("small");
  small.textContent = note;
  card.append(title, strong, small);
  return card;
}

const CPU_REPORT_LABELS = {
  expected_level: "Expected level",
  declared_level: "Declared level",
  verified_level: "Verified level",
  observed_level: "Observed level",
  level: "Level",
  features: "Features",
  cache: "I/D Cache",
  predictor: "Branch prediction",
  ooo: "Out-of-order",
  branch_predictor: "Branch prediction",
  out_of_order: "Out-of-order",
  checks: "Checks",
  name: "Name",
};

function cpuReportValue(value) {
  if (Array.isArray(value)) return value.join("、");
  if (value && typeof value === "object") return Object.entries(value).map(([key, item]) => `${key}: ${item}`).join(" · ");
  return String(value ?? "—");
}

function cpuReportSection(title) {
  const section = document.createElement("section");
  section.className = "cpu-report-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  section.append(heading);
  return section;
}

function renderAgentCpuSummary(report) {
  const panel = $("#agent-cpu-summary");
  if (!panel) return;
  panel.replaceChildren();
  const capability = report.cpu_capability && typeof report.cpu_capability === "object"
    ? report.cpu_capability : {};
  const suites = Array.isArray(report.test_suites) ? report.test_suites : [];
  const benchmarks = Array.isArray(report.performance?.benchmarks) ? report.performance.benchmarks : [];
  if (!Object.keys(capability).length && !suites.length && !benchmarks.length) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");

  const heading = document.createElement("div");
  heading.className = "cpu-report-head";
  const title = document.createElement("strong");
  title.textContent = "CPU capability & performance";
  const detail = document.createElement("span");
  const level = capability.verified_level ?? capability.observed_level ?? capability.declared_level ?? capability.level;
  detail.textContent = level === undefined ? "Tier verification" : `Detected L${level}`;
  heading.append(title, detail);
  panel.append(heading);

  if (Object.keys(capability).length) {
    const section = cpuReportSection("Capability");
    const grid = document.createElement("div");
    grid.className = "cpu-capability-grid";
    const preferred = ["expected_level", "declared_level", "verified_level", "observed_level", "features", "name"];
    const entries = Object.entries(capability).filter(([key]) => preferred.includes(key));
    if (!entries.length && capability.level !== undefined) entries.push(["level", capability.level]);
    entries.forEach(([key, value]) => {
      const item = document.createElement("div");
      const label = document.createElement("span");
      label.textContent = CPU_REPORT_LABELS[key] || key.replaceAll("_", " ");
      const result = document.createElement("b");
      result.textContent = key.endsWith("level") || key === "level" ? `L${cpuReportValue(value)}` : cpuReportValue(value);
      item.append(label, result);
      grid.append(item);
    });
    section.append(grid);
    panel.append(section);
  }

  if (suites.length) {
    const section = cpuReportSection("Verification suites");
    const list = document.createElement("div");
    list.className = "cpu-suite-list";
    suites.forEach((suite) => {
      const row = document.createElement("div");
      const skipped = Boolean(suite.skipped);
      row.className = `cpu-suite ${skipped ? "skip" : suite.passed === false ? "fail" : "pass"}`;
      const name = document.createElement("strong");
      name.textContent = suite.name || suite.suite || "Suite";
      const status = document.createElement("span");
      status.textContent = skipped ? "SKIP" : suite.passed === false ? "FAIL" : "PASS";
      const note = document.createElement("small");
      note.textContent = suite.checks ?? suite.detail ?? "";
      row.append(name, status, note);
      list.append(row);
    });
    section.append(list);
    panel.append(section);
  }

  if (benchmarks.length) {
    const section = cpuReportSection("Performance benchmarks");
    const table = document.createElement("table");
    table.className = "cpu-performance-table";
    const head = document.createElement("thead");
    const row = document.createElement("tr");
    ["Benchmark", "Cycles", "Retired", "Branches", "Mispredicts", "OoO bypass", "CPI", "IPC"].forEach((text) => {
      const cell = document.createElement("th");
      cell.textContent = text;
      row.append(cell);
    });
    head.append(row);
    const body = document.createElement("tbody");
    benchmarks.forEach((benchmark) => {
      const line = document.createElement("tr");
      [
        benchmark.benchmark || benchmark.name || "benchmark",
        benchmark.cycles ?? "—",
        benchmark.instructions ?? benchmark.retired ?? "—",
        benchmark.branches ?? "—",
        benchmark.mispredicts ?? "—",
        benchmark.ooo_bypass ?? "—",
        Number.isFinite(Number(benchmark.cpi)) ? Number(benchmark.cpi).toFixed(3) : "—",
        Number.isFinite(Number(benchmark.ipc)) ? Number(benchmark.ipc).toFixed(3) : "—",
      ].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        line.append(cell);
      });
      body.append(line);
    });
    table.append(head, body);
    section.append(table);
    panel.append(section);
  }
}

function renderAgentVerifyVisual(report) {
  const metrics = report.metrics || {};
  const container = $("#agent-metrics");
  container.replaceChildren();
  const area = metrics.area;
  if (area !== undefined && area !== null && Number.isFinite(Number(area))) {
    container.append(metricCard("Area", Number(area).toFixed(3), metrics.area_unit || "library area units", "accent"));
  } else {
    container.append(metricCard("Area", "—", metrics.reason || "Synthesis incomplete"));
  }
  if (metrics.power_status === "ok") {
    container.append(metricCard("Total power", `${(Number(metrics.total_power_w) * 1e6).toFixed(3)} µW`, `Dynamic ${(Number(metrics.dynamic_power_w) * 1e6).toFixed(3)} µW`));
  } else {
    container.append(metricCard("Power", "—", metrics.power_reason || "Activity data required"));
  }
  const stage = report.stage === "compile" ? "COMPILE" : "SIMULATION";
  container.append(metricCard("Result", report.passed ? "PASS" : "FAIL", stage, report.passed ? "success" : "failure"));
  renderAgentCpuSummary(report);
  renderAgentDftSpectrum(report);
  renderAgentTrace(report);
  $("#agent-tb-stdout").textContent = [report.stdout, report.stderr].filter(Boolean).join("\n") || "—";
}

function traceColumns(trace) {
  const first = trace[0] || {};
  if ("signal" in first || "pulse" in first || "expected_pulse" in first) {
    return [
      { key: "cycle", label: "Cycle" },
      { key: "event", label: "Scenario / Event" },
      { key: "signal", label: "signal" },
      { key: "pulse", label: "pulse" },
      { key: "expected_pulse", label: "Expected" },
    ];
  }
  const keys = [];
  Object.keys(first).forEach((key) => {
    if (!key.startsWith("expected_") && keys.length < 7) keys.push(key);
  });
  return keys.map((key) => ({ key, label: key }));
}

function traceRowMismatch(row) {
  return Object.entries(row).some(([key, value]) => {
    if (!key.startsWith("expected_")) return false;
    const actual = row[key.slice("expected_".length)];
    return actual !== undefined && String(actual) !== String(value);
  });
}

function renderAgentDftSpectrum(report) {
  const chart = $("#agent-dft-chart");
  const plot = $("#agent-dft-plot");
  const stats = $("#agent-dft-stats");
  const source = $("#agent-dft-source");
  if (!chart || !plot || !stats || !source) return;
  const trace = report.trace || [];
  const binRows = trace.filter(
    (row) => row.test === "t13_dft" && row.event === "bin",
  );
  if (!binRows.length) {
    chart.classList.add("hidden");
    plot.replaceChildren();
    stats.replaceChildren();
    source.replaceChildren();
    return;
  }
  chart.classList.remove("hidden");
  plot.replaceChildren();
  stats.replaceChildren();
  source.replaceChildren();

  const sourceRows = trace
    .filter((row) => row.test === "t13_dft" && row.event === "source")
    .slice(0, 32);
  source.classList.remove("empty-copy");
  if (sourceRows.length) {
    const ns = "http://www.w3.org/2000/svg";
    const make = (tag, attrs = {}) => {
      const element = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
      return element;
    };
    const values = sourceRows.map((row) => Number(row.x) || 0);
    const width = 440;
    const height = 144;
    const left = 30;
    const right = 10;
    const top = 10;
    const bottom = 24;
    const innerWidth = width - left - right;
    const innerHeight = height - top - bottom;
    const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 1);
    const zeroY = top + innerHeight / 2;
    const yAt = (value) => zeroY - (value / maxAbs) * (innerHeight * 0.42);
    const svg = make("svg", {
      class: "dft-source-plot",
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": "Square-wave input signal",
    });
    [1, 0.5, 0, -0.5, -1].forEach((fraction) => {
      const y = yAt(maxAbs * fraction);
      svg.append(make("line", {
        x1: left,
        y1: y,
        x2: width - right,
        y2: y,
        class: "dft-source-grid",
      }));
    });
    svg.append(make("line", {
      x1: left,
      y1: zeroY,
      x2: width - right,
      y2: zeroY,
      class: "dft-source-zero",
    }));
    // 16 个真实采样点。曲线用采样保持方式连接：每个采样点之间先水平保持，
    // 再在采样边界竖直跳变；不额外生成虚构的采样点。
    const sampleCount = Math.max(1, values.length);
    const points = values.map((value, sampleIndex) => ({
      x: left + ((sampleIndex + 0.5) / sampleCount) * innerWidth,
      y: yAt(value),
      value,
    }));
    let linePath = `M ${left} ${yAt(values[0])}`;
    points.forEach((point, index) => {
      if (index > 0) linePath += ` L ${point.x} ${points[index - 1].y}`;
      linePath += ` L ${point.x} ${point.y}`;
    });
    linePath += ` L ${left + innerWidth} ${yAt(values[values.length - 1])}`;
    svg.append(make("path", { d: linePath, class: "dft-source-line" }));
    values.forEach((value, index) => {
      const x = left + ((index + 0.5) / sampleCount) * innerWidth;
      const label = make("text", {
        x,
        y: height - 7,
        "text-anchor": "middle",
        class: "dft-source-label",
      });
      label.textContent = `n${index}`;
      svg.append(label);
      const circle = make("circle", {
        cx: x,
        cy: yAt(value),
        r: 2.6,
        fill: value < 0 ? "#4b8fe8" : "#d47a18",
      });
      const title = make("title");
      title.textContent = `n=${index}  x=${value}`;
      circle.append(title);
      svg.append(circle);
    });
    [-maxAbs, 0, maxAbs].forEach((value) => {
      const label = make("text", {
        x: left - 4,
        y: yAt(value) + 3,
        "text-anchor": "end",
        class: "dft-source-value",
      });
      label.textContent = String(value);
      svg.append(label);
    });
    source.append(svg);
  } else {
    source.classList.add("empty-copy");
    source.textContent = "No source-signal trace for this run.";
  }

  const binCount = binRows.length;
  const halfBins = Math.max(1, binCount / 2);
  const signedRows = binRows
    .map((row) => {
      const k = Number(row.k) || 0;
      return { ...row, signedK: k >= halfBins ? k - binCount : k };
    })
    .sort((left, right) => left.signedK - right.signedK);
  const magnitudes = signedRows.map((row) => Math.abs(Number(row.mag) || 0));
  const maxMagnitude = Math.max(...magnitudes, 1);
  const peak = signedRows.reduce(
    (best, row) => (Math.abs(Number(row.mag) || 0) > Math.abs(Number(best.mag) || 0) ? row : best),
    signedRows[0],
  );
  const dcRow = signedRows.find((row) => row.signedK === 0);
  const average = magnitudes.reduce((sum, value) => sum + value, 0) / Math.max(1, magnitudes.length);
  const addStat = (label, value) => {
    const chip = document.createElement("span");
    chip.className = "dft-stat";
    const key = document.createElement("b");
    key.textContent = label;
    chip.append(key, document.createTextNode(String(value)));
    stats.append(chip);
  };
  addStat("PEAK", `k${peak.signedK >= 0 ? "+" : ""}${peak.signedK}`);
  addStat("|X|", String(Math.abs(Number(peak.mag) || 0)));
  addStat("DC", String(Math.abs(Number(dcRow?.mag) || 0)));
  addStat("MEAN", average.toFixed(0));
  addStat("BINS", String(signedRows.length));

  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs = {}) => {
    const element = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  };
  const width = 520;
  const height = 150;
  const left = 38;
  const right = 14;
  const top = 12;
  const bottom = 27;
  const innerWidth = width - left - right;
  const innerHeight = height - top - bottom;
  const xAt = (index) => left + (signedRows.length === 1 ? innerWidth / 2 : (index / (signedRows.length - 1)) * innerWidth);
  const yAt = (magnitude) => top + innerHeight - (Math.abs(magnitude) / maxMagnitude) * innerHeight;
  [0, 0.5, 1].forEach((fraction) => {
    const y = top + innerHeight * (1 - fraction);
    plot.append(make("line", { x1: left, y1: y, x2: width - right, y2: y, class: "dft-grid" }));
    const label = make("text", { x: left - 5, y: y + 3, "text-anchor": "end", class: "dft-axis-label" });
    label.textContent = String(Math.round(maxMagnitude * fraction));
    plot.append(label);
  });
  plot.append(make("line", { x1: left, y1: top + innerHeight, x2: width - right, y2: top + innerHeight, class: "dft-axis" }));
  const points = signedRows.map((row, index) => ({
    x: xAt(index),
    y: yAt(Math.abs(Number(row.mag) || 0)),
    row,
  }));
  const linePath = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
  const baseY = top + innerHeight;
  const areaPath = `${linePath} L ${points[points.length - 1].x} ${baseY} L ${points[0].x} ${baseY} Z`;
  plot.append(make("path", { d: areaPath, class: "dft-area" }));
  plot.append(make("path", { d: linePath, class: "dft-line" }));
  points.forEach((point) => {
    const circle = make("circle", {
      cx: point.x,
      cy: point.y,
      r: point.row === peak ? 4 : 2.8,
      class: `dft-point ${point.row === peak ? "peak" : ""}`,
    });
    const title = make("title");
    title.textContent = `k=${point.row.signedK} · Re=${point.row.re} · Im=${point.row.im} · |X|=${point.row.mag}`;
    circle.append(title);
    plot.append(circle);
    const label = make("text", { x: point.x, y: height - 8, "text-anchor": "middle", class: "dft-axis-label" });
    label.textContent = `k${point.row.signedK >= 0 ? "+" : ""}${point.row.signedK}`;
    plot.append(label);
  });
  const peakValue = make("text", {
    x: Math.min(width - right - 8, Math.max(left + 8, points[points.indexOf(points.find((point) => point.row === peak))].x)),
    y: Math.max(top + 10, points.find((point) => point.row === peak).y - 8),
    "text-anchor": "middle",
    class: "dft-value-label",
  });
  peakValue.textContent = String(Math.abs(Number(peak.mag) || 0));
  plot.append(peakValue);
}

function renderAgentTrace(report) {
  const wrap = $("#agent-trace-wrap");
  const trace = report.trace || [];
  if (!trace.length) {
    wrap.className = "trace-wrap empty-copy";
    wrap.textContent = "No visual trace for this verification; check Raw TB output on compile failure.";
    return;
  }
  const columns = traceColumns(trace);
  const table = document.createElement("table");
  table.className = "trace-table agent-trace-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  columns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column.label;
    headRow.append(th);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  trace.forEach((row) => {
    const tr = document.createElement("tr");
    if (traceRowMismatch(row)) tr.className = "mismatch";
    columns.forEach((column) => {
      const td = document.createElement("td");
      const value = row[column.key];
      if (column.key === "pulse" && row.expected_pulse !== undefined) {
        const span = document.createElement("span");
        span.className = String(value) === String(row.expected_pulse) ? "signal-high" : "signal-mismatch";
        span.textContent = value ?? "—";
        td.append(span);
      } else {
        td.textContent = value ?? "—";
      }
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(head, body);
  wrap.className = "trace-wrap";
  wrap.replaceChildren(table);
}

