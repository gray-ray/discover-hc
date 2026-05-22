(function () {
  const DEFAULT_MODE = "discover";
  const MENU_MODES = ["discover", "crawl"];
  const PAGE_MODE = document.body.dataset.mode || DEFAULT_MODE;
  const MODE_LABELS = {
    discover: "名单整理",
    crawl: "招聘扫描",
    resume: "继续扫描",
    "refresh-failed": "失败补扫",
  };
  const KIND_LABELS = {
    discover: "名单整理结果",
    crawl: "招聘扫描结果",
  };
  const STATUS_LABELS = {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
  };
  const SUMMARY_LABELS = {
    industry: "行业",
    company_count: "公司数",
    completed_count: "完成数",
    hit_count: "命中数",
  };

  const state = {
    currentMode: DEFAULT_MODE,
    jobs: [],
    selectedJobId: "",
    selectedArtifactPath: "",
    selectedArtifactPayload: null,
    artifactRequestToken: 0,
    isStartingJob: false,
    selectedArtifactPaths: [],
    artifacts: { discover: [], crawl: [] },
    industries: [],
    eventSource: null,
    deleteArtifactsModal: null,
    clearJobLogsModal: null,
    usageModal: null,
    jobLogsModal: null,
  };

  function el(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function showNotice(message, type = "danger") {
    const notice = el("pageNotice");
    if (!notice) return;
    notice.textContent = message;
    notice.className = `alert alert-${type} mb-4`;
    notice.classList.remove("d-none");
  }

  function clearNotice() {
    const notice = el("pageNotice");
    if (!notice) return;
    notice.textContent = "";
    notice.className = "alert d-none mb-4";
  }

  function getActiveExecutionJobs() {
    return state.jobs.filter((job) => ["queued", "running"].includes(job.status));
  }

  function updateExecutionControls() {
    const startButton = el("startJob");
    const executionState = el("executionState");
    const formFields = document.querySelectorAll("[data-field]");
    const activeJobs = getActiveExecutionJobs();
    const isBusy = state.isStartingJob;

    if (startButton) {
      startButton.disabled = isBusy;
      startButton.textContent = state.isStartingJob
        ? "启动中..."
        : "开始执行";
    }

    formFields.forEach((field) => {
      field.disabled = isBusy;
    });

    if (!executionState) {
      return;
    }

    if (state.isStartingJob) {
      executionState.textContent = "正在提交任务，请稍候...";
      executionState.classList.remove("d-none");
      return;
    }

    if (activeJobs.length) {
      const lines = activeJobs.slice(0, 3).map((job) => {
        const modeLabel = MODE_LABELS[job.config?.mode] || job.config?.mode || "执行中";
        const industryText = job.config?.industry ? `，行业：${job.config.industry}` : "";
        const statusText = STATUS_LABELS[job.status] || job.status;
        return `${modeLabel}${industryText}，当前状态：${statusText}`;
      });
      const suffix = activeJobs.length > 3 ? `；另有 ${activeJobs.length - 3} 条任务进行中` : "";
      executionState.textContent = `当前进行中的任务 ${activeJobs.length} 个：${lines.join("；")}${suffix}。可继续发起新的任务。`;
      executionState.classList.remove("d-none");
      return;
    }

    executionState.textContent = "";
    executionState.classList.add("d-none");
  }

  function visibleArtifacts() {
    if (state.currentMode === "discover") {
      const deduped = new Map();
      (state.artifacts.discover || []).forEach((item) => {
        const key = `${item?.industry?.code || ""}::${item?.industry?.name || item?.path || ""}`;
        if (!deduped.has(key)) {
          deduped.set(key, item);
        }
      });
      return [...deduped.values()];
    }
    return [...state.artifacts.crawl];
  }

  function visibleJobs() {
    if (state.currentMode === "discover") {
      return state.jobs.filter((job) => job.config && job.config.mode === "discover");
    }
    return state.jobs.filter((job) => job.config && ["crawl", "resume", "refresh-failed"].includes(job.config.mode));
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }
    return response.json();
  }

  function resetArtifactPreview(message = "请选择一个文件查看结果。") {
    state.selectedArtifactPayload = null;
    el("artifactSummary").textContent = message;
    el("resultsTable").innerHTML = `<tr><td colspan="4" class="text-secondary">暂无结果</td></tr>`;
    updateExportButton();
  }

  function renderSummaryPills(summary) {
    const entries = Object.entries(summary || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
    if (!entries.length) {
      return '<span class="text-secondary">暂无摘要</span>';
    }
    return `<div class="summary-pills">${entries.map(([key, value]) => `
      <span class="summary-pill">
        <span class="text-secondary">${escapeHtml(SUMMARY_LABELS[key] || key)}</span>
        <strong>${escapeHtml(value)}</strong>
      </span>
    `).join("")}</div>`;
  }

  function ensureVisibleArtifactSelection() {
    const artifacts = visibleArtifacts();
    if (!artifacts.length) {
      state.selectedArtifactPath = "";
      resetArtifactPreview("当前页面暂无结果。");
      return;
    }

    const currentVisible = artifacts.some((item) => item.path === state.selectedArtifactPath);
    if (currentVisible) {
      return;
    }

    const nextArtifact = artifacts[0];
    if (nextArtifact) {
      selectArtifact(nextArtifact.path).catch((error) => showNotice(error.message));
    }
  }

  function resetModeFilters(mode, crawlModeOverride = "") {
    const form = el(`config-${mode}`);
    if (form) {
      form.reset();
    }

    if (mode === "crawl") {
      const selector = el("crawl-run-mode");
      if (selector) {
        selector.value = crawlModeOverride || "crawl";
      }
    }
  }

  function setCurrentMode(mode, pushHash = true) {
    let crawlModeOverride = "";
    if (mode === "resume" || mode === "refresh-failed") {
      crawlModeOverride = mode;
      mode = "crawl";
    }
    if (!MENU_MODES.includes(mode)) {
      mode = DEFAULT_MODE;
    }
    state.currentMode = mode;

    document.querySelectorAll("[data-mode-nav]").forEach((node) => {
      const active = node.dataset.modeNav === mode;
      node.classList.toggle("active", active);
      node.classList.toggle("text-bg-primary", active);
      node.classList.toggle("text-secondary", !active);
    });

    document.querySelectorAll("[data-mode-panel]").forEach((node) => {
      node.classList.toggle("d-none", node.dataset.modePanel !== mode);
    });

    resetModeFilters(mode, crawlModeOverride);

    const guide = el("modeGuide");
    const title = el("modeTitle");
    const desc = el("modeDescription");

    const meta = {
      discover: {
        title: "名单整理",
        description: "先获取行业内公司名单和官网，适合作为第一步。",
        guide: "输入行业名称后开始执行。这个页面只整理公司名单，不抓取招聘信息。",
      },
      crawl: {
        title: "招聘扫描",
        description: "支持招聘扫描、继续扫描、失败补扫三种处理方式。",
        guide: "选择处理方式后开始执行。继续扫描和失败补扫会基于当前选中的结果继续处理。",
      },
    }[mode];

    title.textContent = meta.title;
    desc.textContent = meta.description;
    guide.textContent = meta.guide;

    const currentVisibleJobs = visibleJobs();
    if (!currentVisibleJobs.some((item) => item.job_id === state.selectedJobId)) {
      state.selectedJobId = currentVisibleJobs[0]?.job_id || "";
    }
    renderJobs();
    renderJobDetail(currentVisibleJobs.find((item) => item.job_id === state.selectedJobId));
    renderArtifacts();
    updateArtifactSelectionInfo();
    ensureVisibleArtifactSelection();

    if (pushHash) {
      history.replaceState(null, "", `#${mode}`);
    }
  }

  function getModePayload(mode) {
    const payload = { mode };
    const form = el(`config-${mode}`);
    if (!form) {
      return payload;
    }
    const fields = form.querySelectorAll("[data-field]");
    fields.forEach((field) => {
      const key = field.dataset.field;
      const value = field.type === "checkbox" ? field.checked : field.value.trim();
      payload[key] = value;
    });
    if (mode === "crawl") {
      payload.mode = payload.mode_override || "crawl";
      delete payload.mode_override;
      delete payload.source_path;
      delete payload.page_limit;
      delete payload.result_limit;
      delete payload.output_path;
      delete payload.show_ssl_warning;
    } else if (mode === "discover") {
      delete payload.output_path;
      delete payload.show_ssl_warning;
    }
    if (payload.company_limit === "") delete payload.company_limit;
    if (payload.company_limit != null) payload.company_limit = Number(payload.company_limit);
    if (payload.page_limit != null && payload.page_limit !== "") payload.page_limit = Number(payload.page_limit);
    if (payload.result_limit != null && payload.result_limit !== "") payload.result_limit = Number(payload.result_limit);
    if (payload.timeout != null && payload.timeout !== "") payload.timeout = Number(payload.timeout);
    return payload;
  }

  function fillIndustryOptions(selectId, values) {
    const select = el(selectId);
    if (!select) return;
    const previousValue = select.value;
    select.replaceChildren();

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择行业";
    select.appendChild(placeholder);

    values.forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });

    if (values.includes(previousValue)) {
      select.value = previousValue;
    }
  }

  function fillIndustries() {
    fillIndustryOptions("discover-industry", state.industries);

    const crawlIndustries = [...new Set(
      (state.artifacts.discover || [])
        .map((item) => item?.industry?.name || "")
        .filter(Boolean)
    )].sort((a, b) => a.localeCompare(b, "zh-CN"));

    fillIndustryOptions("crawl-industry", crawlIndustries);
    el("industryCount").textContent = state.industries.length || "-";
  }

  function statusBadge(status) {
    const map = {
      running: ["soft-warning", "运行中"],
      completed: ["soft-success", "已完成"],
      failed: ["soft-danger", "失败"],
      queued: ["text-bg-primary", "排队中"],
    };
    const [klass, label] = map[status] || ["text-bg-secondary", status];
    const badge = document.createElement("span");
    badge.className = `badge rounded-pill ${klass}`;
    badge.textContent = label;
    return badge;
  }

  function updateArtifactSelectionInfo() {
    const count = state.selectedArtifactPaths.length;
    el("artifactSelectionInfo").textContent = `已选 ${count} 个文件`;
    el("deleteSelectedArtifacts").disabled = count === 0;
    updateArtifactSelectionToggle();
  }

  function updateArtifactSelectionToggle() {
    const toggle = el("toggleAllArtifacts");
    if (!toggle) return;
    const items = visibleArtifacts();
    const total = items.length;
    const selectedCount = items.filter((item) => state.selectedArtifactPaths.includes(item.path)).length;

    toggle.checked = total > 0 && selectedCount === total;
    toggle.indeterminate = selectedCount > 0 && selectedCount < total;
    toggle.disabled = total === 0;
  }

  function setSelectedArtifacts(paths) {
    state.selectedArtifactPaths = [...new Set(paths)];
    updateArtifactSelectionInfo();
  }

  function toggleArtifactSelection(path) {
    const selected = new Set(state.selectedArtifactPaths);
    if (selected.has(path)) {
      selected.delete(path);
    } else {
      selected.add(path);
    }
    setSelectedArtifacts([...selected]);
    renderArtifacts();
  }

  function renderJobs() {
    const container = el("jobList");
    if (!container) {
      return;
    }
    container.replaceChildren();
    const jobs = visibleJobs();
    if (!jobs.length) {
      const empty = document.createElement("div");
      empty.className = "job-empty text-secondary";
      empty.textContent = "当前页面暂无任务";
      container.appendChild(empty);
      return;
    }

    jobs.forEach((job) => {
      const wrapper = document.createElement("div");
      wrapper.className = `job-item p-3 ${job.job_id === state.selectedJobId ? "active" : ""}`;
      wrapper.dataset.jobId = job.job_id;

      const head = document.createElement("div");
      head.className = "d-flex align-items-start justify-content-between gap-2 mb-2";

      const left = document.createElement("div");
      const title = document.createElement("div");
      title.className = "fw-semibold";
      title.textContent = MODE_LABELS[job.config.mode] || job.config.mode;
      const sub = document.createElement("div");
      sub.className = "small text-secondary";
      sub.textContent = job.config.industry || "-";
      left.append(title, sub);
      head.append(left, statusBadge(job.status));

      const meta = document.createElement("div");
      meta.className = "small text-secondary";
      meta.innerHTML = `<div>创建时间: ${job.created_at}</div>`;

      wrapper.append(head, meta);
      wrapper.addEventListener("click", () => selectJob(job.job_id));
      container.appendChild(wrapper);
    });
  }

  function renderArtifacts() {
    const items = visibleArtifacts();
    el("discoverCount").textContent = [...new Set(
      (state.artifacts.discover || []).map((item) => `${item?.industry?.code || ""}::${item?.industry?.name || item?.path || ""}`)
    )].length || "0";
    el("crawlCount").textContent = state.artifacts.crawl.length || "0";

    const container = el("artifactList");
    container.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "artifact-empty text-secondary";
      empty.textContent = "暂无结果";
      container.appendChild(empty);
      return;
    }

    items.forEach((item) => {
      const wrapper = document.createElement("div");
      wrapper.className = `artifact-item p-3 ${item.path === state.selectedArtifactPath ? "active" : ""} ${state.selectedArtifactPaths.includes(item.path) ? "selected" : ""}`;

      const row = document.createElement("div");
      row.className = "d-flex align-items-start gap-2";

      const checkboxBox = document.createElement("div");
      checkboxBox.className = "pt-1";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "form-check-input";
      checkbox.checked = state.selectedArtifactPaths.includes(item.path);
      checkbox.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleArtifactSelection(item.path);
      });
      checkboxBox.appendChild(checkbox);

      const content = document.createElement("div");
      content.className = "flex-grow-1";
      content.addEventListener("click", () => selectArtifact(item.path));

      const head = document.createElement("div");
      head.className = "d-flex align-items-start justify-content-between gap-2 mb-2";
      const industryName = (item.industry && item.industry.name) || "未命名行业";
      const kindName = KIND_LABELS[item.kind] || item.kind;
      head.innerHTML = `
        <div>
          <div class="fw-semibold">${escapeHtml(industryName)}</div>
          <div class="small text-secondary">${escapeHtml(kindName)}</div>
        </div>
      `;
      const count = document.createElement("span");
      count.className = "badge rounded-pill text-bg-light border";
      count.textContent = item.company_count ?? "-";
      head.appendChild(count);

      const meta = document.createElement("div");
      meta.className = "small text-secondary";
      meta.innerHTML = `<div>${item.created_at || "-"}</div>`;
      content.append(head, meta);

      row.append(checkboxBox, content);
      wrapper.appendChild(row);
      container.appendChild(wrapper);
    });
  }

  function renderJobDetail(job) {
    if (!job) {
      el("jobSummary").textContent = "当前页面没有可展示的任务详情。";
      el("jobLogs").textContent = "暂无日志";
      return;
    }
    const summary = job.summary || {};
    el("jobSummary").innerHTML = `
      <div class="job-summary-card rounded-4 p-3">
        <div class="summary-grid">
          <div class="summary-item">
            <span class="summary-label">任务编号</span>
            <span class="summary-value">${escapeHtml(job.job_id)}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">处理方式</span>
            <span class="summary-value">${escapeHtml(MODE_LABELS[job.config.mode] || job.config.mode)}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">任务状态</span>
            <span class="summary-value">${escapeHtml(STATUS_LABELS[job.status] || job.status)}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">行业名称</span>
            <span class="summary-value">${escapeHtml(job.config.industry || "-")}</span>
          </div>
        </div>
        <div class="mt-3">
          <div class="summary-label">摘要</div>
          ${renderSummaryPills(summary)}
        </div>
      </div>
    `;
    el("jobLogs").textContent = (job.logs || []).join("\n") || "暂无日志";
  }

  function renderArtifactPreview(payload, path) {
    state.selectedArtifactPath = path || state.selectedArtifactPath;
    state.selectedArtifactPayload = payload || null;
    const summary = [];
    if (payload.mode) summary.push(`处理方式: ${MODE_LABELS[payload.mode] || payload.mode}`);
    if (payload.industry && payload.industry.name) summary.push(`行业: ${payload.industry.name}`);
    if (payload.company_count != null) summary.push(`公司数: ${payload.company_count}`);
    el("artifactSummary").textContent = summary.join(" | ") || "已加载结果";
    updateExportButton();

    const tbody = el("resultsTable");
    tbody.replaceChildren();

    if (payload.kind === "discover") {
      const companies = payload.companies || [];
      if (!companies.length) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td colspan="4" class="text-secondary">暂无公司</td>`;
        tbody.appendChild(tr);
        return;
      }
      companies.forEach((company) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><div class="fw-semibold">${escapeHtml(company.company_name || company.stock_name)}</div><div class="small text-secondary">${escapeHtml(company.stock_code)} ${escapeHtml(company.stock_name)}</div></td>
          <td class="small">${company.website ? `<a href="${escapeHtml(company.website)}" target="_blank" rel="noreferrer">${escapeHtml(company.website)}</a>` : "-"}</td>
          <td><span class="badge rounded-pill text-bg-light border">待抓取</span></td>
          <td class="result-snippet">可切换到招聘扫描页面继续处理</td>
        `;
        tbody.appendChild(tr);
      });
      return;
    }

    const results = payload.results || [];
    if (!results.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="4" class="text-secondary">暂无结果</td>`;
      tbody.appendChild(tr);
      return;
    }

    results.forEach((item) => {
      const company = item.company || {};
      const recruitment = item.recruitment_info || [];
      const preview = recruitment.slice(0, 2).map((info) => {
        const pos = info.position || info.source_title || "未命名";
        return escapeHtml(`${pos}${info.work_location ? ` / ${info.work_location}` : ""}`);
      }).join("<br />");

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><div class="fw-semibold">${escapeHtml(company.company_name || company.stock_name)}</div><div class="small text-secondary">${escapeHtml(company.stock_code)} ${escapeHtml(company.stock_name)}</div></td>
        <td class="small">${company.website ? `<a href="${escapeHtml(company.website)}" target="_blank" rel="noreferrer">${escapeHtml(company.website)}</a>` : "-"}</td>
        <td>${item.error ? `<span class="badge rounded-pill soft-danger">${escapeHtml(item.error)}</span>` : `<span class="badge rounded-pill soft-success">命中 ${escapeHtml(recruitment.length)} 条</span>`}</td>
        <td class="small result-snippet">${preview || "-"}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function updateExportButton() {
    const button = el("exportArtifactExcel");
    if (!button) return;
    button.disabled = !state.selectedArtifactPath || !state.selectedArtifactPayload;
  }

  async function loadIndustries() {
    const data = await api("/api/industries");
    state.industries = data.industries || [];
    fillIndustries();
  }

  function applyDashboardState(payload) {
    state.jobs = payload.jobs || [];
    state.artifacts = payload.artifacts || { discover: [], crawl: [] };
    fillIndustries();
    const visiblePaths = new Set(visibleArtifacts().map((item) => item.path));
    state.selectedArtifactPaths = state.selectedArtifactPaths.filter((path) => visiblePaths.has(path));
    if (state.selectedArtifactPath && !visiblePaths.has(state.selectedArtifactPath)) {
      state.selectedArtifactPath = "";
      resetArtifactPreview();
    }
    renderJobs();
    const jobs = visibleJobs();
    if (!jobs.some((item) => item.job_id === state.selectedJobId)) {
      state.selectedJobId = jobs[0]?.job_id || "";
    }
    if (state.selectedJobId) {
      const selected = jobs.find((item) => item.job_id === state.selectedJobId);
      if (selected) renderJobDetail(selected);
    } else {
      renderJobDetail(null);
    }
    renderArtifacts();
    updateArtifactSelectionInfo();
    updateExportButton();
    ensureVisibleArtifactSelection();
    updateExecutionControls();
  }

  function selectJob(jobId) {
    state.selectedJobId = jobId;
    renderJobs();
    const job = visibleJobs().find((item) => item.job_id === jobId);
    renderJobDetail(job);
  }

  async function selectArtifact(path) {
    const requestToken = ++state.artifactRequestToken;
    state.selectedArtifactPath = path;
    state.selectedArtifactPayload = null;
    updateExportButton();
    renderArtifacts();
    const payload = await api(`/api/artifact?path=${encodeURIComponent(path)}`);
    if (requestToken !== state.artifactRequestToken || state.selectedArtifactPath !== path) {
      return;
    }
    renderArtifactPreview(payload, path);
    const job = state.jobs.find((item) => item.artifact_path === path);
    if (job) selectJob(job.job_id);
  }

  async function startJob() {
    clearNotice();
    const payload = getModePayload(state.currentMode);
    if (state.currentMode === "crawl" && ["resume", "refresh-failed"].includes(payload.mode)) {
      if (!state.selectedArtifactPath) {
        throw new Error("请先在结果列表中选中一条招聘结果。");
      }
      payload.source_path = state.selectedArtifactPath;
    }
    state.isStartingJob = true;
    updateExecutionControls();
    showNotice("任务提交中，请稍候...", "info");

    try {
      const job = await api("/api/jobs/start", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.selectedJobId = job.job_id;
      state.jobs = [job, ...state.jobs.filter((item) => item.job_id !== job.job_id)];
      updateExecutionControls();
      showNotice("任务已加入执行队列，执行状态会自动刷新。", "success");
    } finally {
      state.isStartingJob = false;
      updateExecutionControls();
    }
  }

  async function refreshSnapshot() {
    const [jobsPayload, discoverPayload, crawlPayload] = await Promise.all([
      api("/api/jobs"),
      api("/api/artifacts?kind=discover"),
      api("/api/artifacts?kind=crawl"),
    ]);
    applyDashboardState({
      jobs: jobsPayload.jobs || [],
      artifacts: {
        discover: discoverPayload.artifacts || [],
        crawl: crawlPayload.artifacts || [],
      },
    });
  }

  async function refreshEverything() {
    clearNotice();
    await loadIndustries();
    await refreshSnapshot();
  }

  function connectEventStream() {
    if (state.eventSource) state.eventSource.close();
    const source = new EventSource("/api/events");
    state.eventSource = source;
    source.addEventListener("snapshot", (event) => {
      applyDashboardState(JSON.parse(event.data));
      clearNotice();
    });
    source.onerror = () => {
      source.close();
      state.eventSource = null;
      setTimeout(connectEventStream, 1500);
    };
  }

  function openDeleteArtifactsModal() {
    if (!state.selectedArtifactPaths.length) return;
    el("deleteArtifactsSummary").textContent = `已选 ${state.selectedArtifactPaths.length} 项结果，确认后会批量删除。`;
    state.deleteArtifactsModal.show();
  }

  async function deleteSelectedArtifacts() {
    const paths = [...state.selectedArtifactPaths];
    if (!paths.length) return;
    await api("/api/artifacts/delete", {
      method: "POST",
      body: JSON.stringify({ paths }),
    });
    state.deleteArtifactsModal.hide();
    setSelectedArtifacts([]);
    await refreshSnapshot();
  }

  async function clearJobLogs() {
    const result = await api("/api/jobs/clear", {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.clearJobLogsModal.hide();
    await refreshSnapshot();

    const clearedCount = (result.cleared_job_ids || []).length;
    const keptRunningCount = (result.kept_running_job_ids || []).length;
    if (keptRunningCount) {
      showNotice(`已清空 ${clearedCount} 条执行记录，保留 ${keptRunningCount} 条运行中任务。`, "success");
    } else {
      showNotice(`已清空 ${clearedCount} 条执行记录。`, "success");
    }
  }

  function exportArtifactExcel() {
    if (!state.selectedArtifactPath) return;
    const link = document.createElement("a");
    link.href = `/api/artifact/export.xlsx?path=${encodeURIComponent(state.selectedArtifactPath)}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function bindMenuEvents() {
    document.querySelectorAll("[data-mode-nav]").forEach((node) => {
      node.addEventListener("click", (event) => {
        event.preventDefault();
        setCurrentMode(node.dataset.modeNav);
      });
    });
    window.addEventListener("hashchange", () => {
      setCurrentMode(location.hash.replace("#", "") || DEFAULT_MODE, false);
    });
  }

  function bindEvents() {
    el("startJob").addEventListener("click", () => startJob().catch((error) => showNotice(error.message)));
    el("refreshEverything").addEventListener("click", () => refreshEverything().catch((error) => showNotice(error.message)));
    el("reloadArtifacts").addEventListener("click", () => refreshSnapshot().catch((error) => showNotice(error.message)));
    el("openJobLogsModal").addEventListener("click", () => state.jobLogsModal.show());
    el("toggleAllArtifacts").addEventListener("change", (event) => {
      if (event.target.checked) {
        setSelectedArtifacts(visibleArtifacts().map((item) => item.path));
      } else {
        setSelectedArtifacts([]);
      }
      renderArtifacts();
    });
    el("deleteSelectedArtifacts").addEventListener("click", openDeleteArtifactsModal);
    el("confirmDeleteArtifacts").addEventListener("click", () => deleteSelectedArtifacts().catch((error) => showNotice(error.message)));
    el("openClearJobLogsModal").addEventListener("click", () => state.clearJobLogsModal.show());
    el("confirmClearJobLogs").addEventListener("click", () => clearJobLogs().catch((error) => showNotice(error.message)));
    el("exportArtifactExcel").addEventListener("click", exportArtifactExcel);
    el("openUsageModal").addEventListener("click", () => state.usageModal.show());
    bindMenuEvents();
  }

  async function boot() {
    clearNotice();
    await loadIndustries();
    await refreshSnapshot();
    connectEventStream();
  }

  function init() {
    state.deleteArtifactsModal = new bootstrap.Modal(el("deleteArtifactsModal"));
    state.clearJobLogsModal = new bootstrap.Modal(el("clearJobLogsModal"));
    state.usageModal = new bootstrap.Modal(el("usageModal"));
    state.jobLogsModal = new bootstrap.Modal(el("jobLogsModal"));
    setCurrentMode(location.hash.replace("#", "") || PAGE_MODE, false);
    updateArtifactSelectionInfo();
    updateExecutionControls();
    bindEvents();
    boot().catch((error) => {
      console.error(error);
      showNotice(error.message);
    });
  }

  init();
})();
