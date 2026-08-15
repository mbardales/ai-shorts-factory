/* AI Shorts Factory — lógica del frontend (sin dependencias externas).
 * Cliente mínimo sobre la API HTTP de src/api. No expone secretos.
 */

"use strict";

// API base del backend. Ajusta si el servidor corre en otro origen.
const API_BASE = "http://127.0.0.1:8000";
// Servidor estático para el video (manual test: `python -m http.server 8001`
// desde la raíz de runs, p. ej. output/runs). El frontend deriva la URL del
// video desde `video_path` recortando la parte previa a la marca "/runs/".
const STATIC_BASE = "http://127.0.0.1:8001";

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

const STATUS_RUNNING = "RUNNING";
const STATUS_SUCCESS = "SUCCESS";
const STATUS_FAILED = "FAILED";
const STATUS_QUALITY_FAILED = "QUALITY_FAILED";
const STATUS_UNKNOWN = "UNKNOWN";

const TERMINAL_STATUSES = new Set([
  STATUS_SUCCESS,
  STATUS_FAILED,
  STATUS_QUALITY_FAILED,
]);

const els = {
  form: document.getElementById("create-form"),
  topic: document.getElementById("topic"),
  offline: document.getElementById("offline"),
  generate: document.getElementById("generate"),
  generateLabel: document.getElementById("generate-label"),
  generateSpinner: document.getElementById("generate-spinner"),
  reset: document.getElementById("reset"),
  statusCard: document.getElementById("status-card"),
  status: document.getElementById("status"),
  stageList: document.getElementById("stage-list"),
  progress: document.getElementById("progress"),
  runLabel: document.getElementById("run-label"),
  elapsedLabel: document.getElementById("elapsed-label"),
  resultCard: document.getElementById("result-card"),
  qualityWarning: document.getElementById("quality-warning"),
  video: document.getElementById("video"),
  download: document.getElementById("download"),
  errorCard: document.getElementById("error-card"),
  errorMessage: document.getElementById("error-message"),
  historyList: document.getElementById("history-list"),
  historyEmpty: document.getElementById("history-empty"),
  historyFilter: document.getElementById("history-filter"),
  refreshHistory: document.getElementById("refresh-history"),
  refreshLabel: document.getElementById("refresh-label"),
  refreshSpinner: document.getElementById("refresh-spinner"),
  projectSelect: document.getElementById("project-select"),
  projectActive: document.getElementById("project-active"),
  newProjectName: document.getElementById("new-project-name"),
  createProject: document.getElementById("create-project"),
  projectFeedback: document.getElementById("project-feedback"),
};

// Etapas del pipeline en orden de ejecución.
const STAGE_ORDER = ["content", "image", "audio", "manifest", "render", "quality"];

// Texto de estado legible por etapa.
const STAGE_LABELS = {
  content: "Generando contenido",
  image: "Generando imágenes",
  audio: "Generando audio",
  manifest: "Preparando video",
  render: "Renderizando",
  quality: "Verificando calidad",
};

// Etiquetas y clase visual de cada estado para el historial.
const STATUS_LABELS = {
  [STATUS_RUNNING]: "Generando",
  [STATUS_SUCCESS]: "Completado",
  [STATUS_FAILED]: "Error",
  [STATUS_QUALITY_FAILED]: "Calidad insuficiente",
  [STATUS_UNKNOWN]: "Desconocido",
};

const STATUS_CLASS = {
  [STATUS_RUNNING]: "info",
  [STATUS_SUCCESS]: "ok",
  [STATUS_FAILED]: "err",
  [STATUS_QUALITY_FAILED]: "warn",
  [STATUS_UNKNOWN]: "info",
};

let runId = null;
let pollTimer = null;
let pollStartedAt = 0;
let elapsedTimer = null;
let projects = [];
let activeProjectId = null;

// --- Progreso por etapas ----------------------------------------------------

function resetStages() {
  for (const li of els.stageList.children) {
    li.className = "stage-item";
  }
}

function setStageStates(states) {
  // states: {content: "done"|"active"|"error", image: ...}
  for (const li of els.stageList.children) {
    const name = li.dataset.stage;
    li.className = "stage-item";
    if (states && states[name]) {
      li.classList.add(`stage-${states[name]}`);
    }
  }
}

function stageStatesForRun(activeStage, terminalStatus, errorText) {
  const states = {};
  let failedIndex = -1;
  if (terminalStatus === STATUS_SUCCESS) {
    for (const name of STAGE_ORDER) {
      states[name] = "done";
    }
    return states;
  }
  if (terminalStatus === STATUS_QUALITY_FAILED) {
    for (const name of STAGE_ORDER) {
      states[name] = "done";
    }
    states.quality = "error";
    return states;
  }
  if (terminalStatus === STATUS_FAILED) {
    const match = /La etapa '(\w+)' terminó/.exec(errorText || "");
    if (match) {
      failedIndex = STAGE_ORDER.indexOf(match[1]);
    } else if (errorText && errorText.indexOf("Quality Gate") !== -1) {
      failedIndex = STAGE_ORDER.indexOf("quality");
    }
    if (failedIndex !== -1) {
      for (let i = 0; i < failedIndex; i += 1) {
        states[STAGE_ORDER[i]] = "done";
      }
      states[STAGE_ORDER[failedIndex]] = "error";
    }
    return states;
  }
  // En ejecución: etapas previas hechas, la actual activa, el resto pendiente.
  const active = STAGE_ORDER.indexOf(activeStage || "");
  for (let i = 0; i < STAGE_ORDER.length; i += 1) {
    if (i < active) {
      states[STAGE_ORDER[i]] = "done";
    } else if (i === active) {
      states[STAGE_ORDER[i]] = "active";
    }
  }
  return states;
}

function startElapsed() {
  stopElapsed();
  updateElapsed();
  elapsedTimer = window.setInterval(updateElapsed, 1000);
}

function stopElapsed() {
  if (elapsedTimer !== null) {
    window.clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
}

function updateElapsed() {
  const seconds = Math.max(0, Math.floor((Date.now() - pollStartedAt) / 1000));
  els.elapsedLabel.textContent = `Tiempo: ${formatDuration(seconds)}`;
  els.elapsedLabel.hidden = false;
}

function showRunMeta(meta) {
  if (meta) {
    els.runLabel.textContent = `Run: ${meta}`;
    els.runLabel.hidden = false;
  } else {
    els.runLabel.hidden = true;
  }
}

function setStatus(text, kind) {
  els.status.textContent = text;
  els.status.dataset.kind = kind || "info";
}

function showSection(section) {
  section.hidden = false;
}

function hideSection(section) {
  section.hidden = true;
}

function setBusy(busy) {
  els.generate.disabled = busy;
  els.generateSpinner.hidden = !busy;
  els.generateLabel.textContent = busy ? "Creando…" : "Crear Short";
  els.topic.disabled = busy;
  els.offline.disabled = busy;
  els.progress.hidden = !busy;
  els.reset.hidden = !busy;
}

function startPolling(newRunId) {
  stopPolling();
  runId = newRunId;
  pollStartedAt = Date.now();
  pollTimer = window.setInterval(() => pollRun(), POLL_INTERVAL_MS);
  pollRun();
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollRun() {
  if (runId === null) {
    return;
  }
  const elapsed = Date.now() - pollStartedAt;
  if (elapsed > POLL_TIMEOUT_MS) {
    stopPolling();
    stopElapsed();
    setStatus("Tiempo de espera agotado. Revisa el estado del servidor.", "err");
    setBusy(false);
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/v1/runs/${encodeURIComponent(runId)}`);
    if (!res.ok) {
      setStatus(`El servidor respondió ${res.status} al consultar el run.`, "err");
      return;
    }
    const data = await res.json();
    renderStatus(data);
  } catch (err) {
    setStatus("No se pudo consultar el estado del run.", "err");
  }
}

function renderStatus(data) {
  const status = data.status;
  if (status === STATUS_SUCCESS) {
    stopPolling();
    stopElapsed();
    setStatus("Completado", "ok");
    setStageStates(stageStatesForRun(null, STATUS_SUCCESS, ""));
    setBusy(false);
    showResult(data);
loadProjects();
loadHistory();
    return;
  }
  if (status === STATUS_QUALITY_FAILED) {
    stopPolling();
    stopElapsed();
    setStatus("Calidad insuficiente", "warn");
    setStageStates(stageStatesForRun(null, STATUS_QUALITY_FAILED, ""));
    setBusy(false);
    if (data.video_path) {
      showResult(data, {
        warn: "El video se generó pero no superó la verificación de calidad.",
      });
    } else {
      showError(
        data.error ||
          "El run no superó la verificación de calidad y no dejó video."
      );
    }
    return;
  }
  if (status === STATUS_FAILED) {
    stopPolling();
    stopElapsed();
    setStatus("Error", "err");
    setStageStates(stageStatesForRun(null, STATUS_FAILED, data.error));
    setBusy(false);
    showError(data.error || "El run terminó con errores.");
    return;
  }
  if (status === STATUS_UNKNOWN) {
    setStatus("Estado desconocido del run.", "err");
    return;
  }
  // En ejecución: estado legible por etapa + checklist + tiempo transcurrido.
  const active = data.stage || null;
  setStatus(active ? STAGE_LABELS[active] : "Preparando", "info");
  setStageStates(stageStatesForRun(active, null, ""));
  updateElapsed();
}

function showResult(data, options) {
  const opts = options || {};
  hideSection(els.errorCard);
  els.qualityWarning.textContent = opts.warn || "";
  els.qualityWarning.hidden = !opts.warn;
  showRunMeta(data.run_id);
  const url = buildVideoUrl(data.video_path);
  els.video.src = url;
  els.video.hidden = false;
  els.download.href = url;
  els.download.download = basename(data.video_path) || "short.mp4";
  els.reset.hidden = false;
  showSection(els.resultCard);
}

function showError(message) {
  hideSection(els.resultCard);
  els.errorMessage.textContent =
    message || "El run terminó con errores. Revisa que FFmpeg esté disponible y vuelve a generar.";
  els.reset.hidden = false;
  showSection(els.errorCard);
}

function buildVideoUrl(videoPath) {
  if (!videoPath) {
    return "";
  }
  if (/^https?:\/\//i.test(videoPath)) {
    return videoPath;
  }
  const normalized = String(videoPath).replace(/\\/g, "/");
  const marker = "/runs/";
  const idx = normalized.lastIndexOf(marker);
  if (idx !== -1) {
    return `${STATIC_BASE}/${normalized.slice(idx + marker.length)}`;
  }
  return `${STATIC_BASE}/${basename(normalized)}`;
}

function basename(value) {
  if (!value) {
    return "";
  }
  const parts = String(value).split(/[\\/]/);
  return parts[parts.length - 1];
}

// --- Historial de Shorts ---------------------------------------------------

function statusLabel(status) {
  return STATUS_LABELS[status] || "Desconocido";
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "—";
  }
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

function videoSrc(videoUrl) {
  if (!videoUrl) {
    return "";
  }
  if (/^https?:\/\//i.test(videoUrl)) {
    return videoUrl;
  }
  return `${API_BASE}${videoUrl}`;
}

function createHistoryCard(run) {
  const item = document.createElement("article");
  item.className = "history-item";

  const head = document.createElement("div");
  head.className = "history-item-head";
  const runLabel = document.createElement("span");
  runLabel.className = "history-run";
  runLabel.textContent = run.run_id || "—";
  const badge = document.createElement("span");
  badge.className = `badge badge-${STATUS_CLASS[run.status] || "info"}`;
  badge.textContent = statusLabel(run.status);
  head.append(runLabel, badge);

  const meta = document.createElement("p");
  meta.className = "history-meta";
  const project = projectLabel(run.project_id);
  meta.textContent = `${formatDate(run.created_at)} · ${formatDuration(run.duration_seconds)}`;
  if (project) {
    meta.textContent += ` · ${project}`;
  }

  item.append(head, meta);

  if (run.video_url) {
    const src = videoSrc(run.video_url);
    const video = document.createElement("video");
    video.className = "history-video";
    video.controls = true;
    video.preload = "metadata";
    video.playsInline = true;
    video.src = src;

    const download = document.createElement("a");
    download.className = "btn btn-primary btn-sm";
    download.href = src;
    download.download = `${run.run_id}.mp4`;
    download.textContent = "Descargar MP4";

    const actions = document.createElement("div");
    actions.className = "actions";
    actions.append(download);
    item.append(video, actions);
  }

  if (run.error) {
    const error = document.createElement("p");
    error.className = "history-error";
    error.textContent = run.error;
    item.append(error);
  }

  return item;
}

function renderHistory(runs) {
  els.historyList.replaceChildren();
  if (!runs || runs.length === 0) {
    els.historyEmpty.hidden = false;
    return;
  }
  els.historyEmpty.hidden = true;
  const sorted = runs
    .slice()
    .sort(
      (a, b) =>
        new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()
    );
  for (const run of sorted) {
    els.historyList.append(createHistoryCard(run));
  }
}

async function loadHistory() {
  els.refreshSpinner.hidden = false;
  els.refreshLabel.textContent = "…";
  try {
    const filter = els.historyFilter.value;
    const query = filter ? `?project_id=${encodeURIComponent(filter)}` : "";
    const res = await fetch(`${API_BASE}/api/v1/runs${query}`);
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    renderHistory(data);
  } catch (err) {
    // El generador sigue funcionando aunque el historial no esté disponible.
  } finally {
    els.refreshSpinner.hidden = true;
    els.refreshLabel.textContent = "Actualizar";
  }
}

// --- Proyectos --------------------------------------------------------------

function projectLabel(projectId) {
  if (!projectId) {
    return null;
  }
  const project = projects.find((item) => item.project_id === projectId);
  return project ? project.name : projectId;
}

function renderProjectSelect() {
  els.projectSelect.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "Sin proyecto";
  els.projectSelect.append(none);
  for (const project of projects) {
    const option = document.createElement("option");
    option.value = project.project_id;
    option.textContent = project.name;
    els.projectSelect.append(option);
  }
  els.projectSelect.value = activeProjectId || "";
  if (activeProjectId) {
    const label = projectLabel(activeProjectId);
    els.projectActive.textContent = `Proyecto activo: ${label}`;
    els.projectActive.hidden = false;
  } else {
    els.projectActive.hidden = true;
  }
}

function renderProjectFilter() {
  const keep = els.historyFilter.value;
  els.historyFilter.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Todos los proyectos";
  els.historyFilter.append(all);
  const none = document.createElement("option");
  none.value = "none";
  none.textContent = "Sin proyecto";
  els.historyFilter.append(none);
  for (const project of projects) {
    const option = document.createElement("option");
    option.value = project.project_id;
    option.textContent = project.name;
    els.historyFilter.append(option);
  }
  els.historyFilter.value = keep;
}

async function loadProjects() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/projects`);
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    projects = data.projects || [];
    activeProjectId = data.active_project_id || null;
    renderProjectSelect();
    renderProjectFilter();
  } catch (err) {
    // El generador sigue funcionando aunque los proyectos no estén disponibles.
  }
}

async function setActiveProject(projectId) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/projects/active`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    });
    if (!res.ok) {
      return;
    }
    activeProjectId = projectId || null;
    renderProjectSelect();
  } catch (err) {
    // Selección local degradada; el servidor decide en la creación del run.
  }
}

async function createProject() {
  const name = els.newProjectName.value.trim();
  if (!name) {
    els.projectFeedback.textContent = "Escribe un nombre para el proyecto.";
    els.projectFeedback.hidden = false;
    els.newProjectName.focus();
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/v1/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      els.projectFeedback.textContent =
        data.detail || "No se pudo crear el proyecto.";
      els.projectFeedback.hidden = false;
      return;
    }
    els.newProjectName.value = "";
    els.projectFeedback.hidden = true;
    await loadProjects();
  } catch (err) {
    els.projectFeedback.textContent = "No se pudo conectar con el servidor.";
    els.projectFeedback.hidden = false;
  }
}

els.projectSelect.addEventListener("change", () => {
  setActiveProject(els.projectSelect.value || null);
});
els.createProject.addEventListener("click", createProject);
els.historyFilter.addEventListener("change", loadHistory);
els.refreshHistory.addEventListener("click", loadHistory);

async function generateShort(event) {
  event.preventDefault();
  const topic = els.topic.value.trim();
  if (!topic) {
    setStatus("Escribe un tema antes de generar.", "err");
    els.topic.focus();
    return;
  }
  hideSection(els.resultCard);
  hideSection(els.errorCard);
  els.qualityWarning.hidden = true;
  els.qualityWarning.textContent = "";
  resetStages();
  setBusy(true);
  setStatus("Preparando", "info");

  const body = {
    topic: topic,
    run_id: null,
    project_id: activeProjectId || null,
    offline: els.offline.checked,
  };

  try {
    const res = await fetch(`${API_BASE}/api/v1/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      stopPolling();
      setBusy(false);
      setStatus("No se pudo iniciar la generación.", "err");
      showError(data.detail || `El servidor respondió ${res.status}.`);
      return;
    }
    setStatus("Preparando", "info");
    showRunMeta(data.run_id);
    startPolling(data.run_id);
    startElapsed();
  } catch (err) {
    stopPolling();
    setBusy(false);
    setStatus("No se pudo conectar con el servidor.", "err");
  }
}

function resetForm() {
  stopPolling();
  stopElapsed();
  runId = null;
  els.topic.value = "";
  hideSection(els.statusCard);
  hideSection(els.resultCard);
  hideSection(els.errorCard);
  els.video.removeAttribute("src");
  els.video.hidden = true;
  els.download.removeAttribute("href");
  els.qualityWarning.hidden = true;
  els.qualityWarning.textContent = "";
  showRunMeta(null);
  els.elapsedLabel.hidden = true;
  resetStages();
  setBusy(false);
  els.generate.disabled = false;
  els.topic.focus();
}

els.form.addEventListener("submit", generateShort);
els.reset.addEventListener("click", resetForm);

loadHistory();
