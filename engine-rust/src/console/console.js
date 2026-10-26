// Console AI Station — tanpa framework, tanpa build step.
// Yang penting di sini: console tidak menyimpan apa pun sendiri. Semua keadaan
// berasal dari /api/wakers dan /api/engines supaya dua jendela tidak saling berbohong.

const boot = window.__BOOT__ || null;

const VIEWS = [
  "dashboard",
  "projects",
  "chat",
  "automations",
  "auto-pilot",
  "workflows",
  "runs",
  "traces",
  "approvals",
  "decide",
  "wakers",
  "groups",
  "channels",
  "engines",
  "models",
  "capabilities",
  "knowledge",
  "memory",
  "settings",
];
const requestedView = new URLSearchParams(location.search).get("view");
const initialView = VIEWS.includes(requestedView) ? requestedView : "dashboard";

const state = {
  view: initialView,
  wakers: boot?.wakers || [],
  engines: boot?.engines || [],
  engineGroups: [],
  quota: [],
  qoder: null,
  qoderLogin: null,
  antigravity: null,
  antigravityLogin: null,
  skipped: boot?.skipped || [],
  enginesError: boot?.enginesError || null,
  bootRuns: boot?.runs || [],
  runs: boot?.runs || [],
  backend: boot?.backend || null,
  limits: boot?.limits || null,
  stats: null,
  resources: null,
  query: "",
  selectedId: null,
  railCollapsed: false,
  editingId: null,
  chat: {},
  chatLoaded: {},
  runBusy: {},
  /// Run yang sedang berjalan per worker; dipakai tombol Stop.
  activeRuns: {},
  cancelling: {},
  channels: [],
  channelDetail: {},
  expandedChannel: null,
  readiness: null,
  nvidia: null,
  groups: [],
  groupDetail: {},
  expandedGroup: null,
  knowledge: [],
  knowledgeResults: null,
  knowledgeQuery: "",
  knowledgeMode: null,
  traces: [],
  traceSummary: null,
  traceDetail: null,
  selectedTrace: null,
  traceFilter: { status: "", waker: "" },
  agyModels: null,
  ollamaModels: null,
  permissions: {},
  memoryScopes: [],
  memoryFiles: [],
  memoryScope: null,
  memoryFile: null,
  automations: [],
  autoPilotMode: null,
  autoPilotLoading: false,
  autoPilotError: null,
  wakey: {
    decisions: [],
    runs: [],
    automations: 0,
    autoPilotOn: false,
    loading: false,
  },
  wakeyChat: {
    workerId: null,
    busy: false,
  },
  chatLiveSeen: {},
  // Posisi baca saat render ulang + permintaan turun ke bawah setelah kirim.
  restoreChat: null,
  forceChatBottom: null,
  wakeyForceBottom: false,
  contextSnapshot: {},
  stream: null,
  streamFetching: {},
  lastStreamAt: 0,
  workflows: [],
  workflowRuns: {},
  workflowVersions: {},
  versionsWorkflow: null,
  projects: [],
  projectsLoaded: false,
  projectsLoading: false,
  projectsError: null,
  projectsGeneratedAt: null,
  expandedWorkflow: null,
  editingWorkflow: null,
  approvals: [],
  autopilot: null,
  autopilotLoading: false,
  autopilotError: null,
  decisionMode: null,
  decisionModeLoading: false,
  decisionModeError: null,
  decideChoice: {},
  decideText: {},
  capabilities: null,
  mcp: {
    loaded: false,
    loading: false,
    servers: [],
    tools: [],
    errors: [],
    result: null,
  },
  catalog: { models: [], fetchedAt: 0, error: null, loading: false },
  // Ingatan terakhir per model, meniru `runtime-defaults` milik QoderWake:
  // saat model diganti lagi, effort/context terakhirnya ikut dipulihkan.
  modelDefaults: (() => {
    try {
      return JSON.parse(localStorage.getItem("station.modelDefaults") || "{}");
    } catch {
      return {};
    }
  })(),
  loading: !(boot && (boot.wakers || boot.engines)),
  error: null,
};

// Deep link `?waker=...` harus sudah terpilih pada paint pertama; kalau hanya
// diisi setelah loadAll, halaman sempat menampilkan daftar worker lebih dulu.
const bootWaker = new URLSearchParams(location.search).get("waker");
if (bootWaker) {
  const match = (boot?.wakers || []).find(
    (waker) =>
      waker.slug === bootWaker ||
      waker.id === bootWaker ||
      String(waker.name || "").toLowerCase() === bootWaker.toLowerCase()
  );
  if (match) state.selectedId = match.id;
}

const TITLES = {
  dashboard: ["Dashboard", "Ringkasan agen dan pekerjaan IRSofka Data Teknologi."],
  projects: ["Project", "Registry project, codename, dan jejak post/media di situs lokal."],
  wakers: ["Workers", "Pekerja digital yang menjalankan pekerjaanmu."],
  chat: ["@Worker", "Percakapan langsung dengan worker."],
  automations: ["Automations", "Tugas terjadwal dan alur kerja otonom."],
  "auto-pilot": ["Auto Pilot Task", "Loop dari Planning.md sampai ditandai Done."],
  workflows: ["Workflows", "Rangkaian langkah dengan gerbang persetujuan."],
  runs: ["Runs", "Riwayat eksekusi dan jejaknya."],
  traces: ["Traces & Board", "Semua eksekusi lintas worker, durasi, dan jejak langkah."],
  approvals: ["Approvals", "Permintaan izin yang menunggu manusia."],
  decide: ["Human Decide", "Pertanyaan yang menunggu keputusan manusia."],
  groups: ["Groups", "Beberapa worker menjawab dalam satu percakapan dengan SOP."],
  channels: ["Channels", "Jembatan ke chat tim."],
  engines: ["Engines", "Registry mesin dari config/engines.json."],
  capabilities: ["Capabilities & Resources", "Skills, MCP runtime, dan registry mesin."],
  models: ["Models", "Model per penyedia, plus endpoint worker Nvidia."],
  usage: ["Usage Model", "Sisa kuota setiap model per jendela waktu."],
  knowledge: ["Knowledge", "Sumber pengetahuan untuk worker."],
  memory: ["Memory", "Memori station: scope, berkas, dan isinya (read-only)."],
  settings: ["Settings", "Status daemon, organisasi, dan penyimpanan."],
};

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// Kartu adalah div dengan role=button: <button> tidak boleh berisi elemen blok.
function activateCard(event, run) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    run();
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { error: text };
  }
  if (!res.ok) {
    throw new Error((body && body.error) || `${res.status} ${res.statusText}`);
  }
  return body;
}

// ------------------------------------------------------- katalog QoderWake

const MODEL_TTL = 5 * 60 * 1000;

// Katalog model dimuat async; kalau chat sedang terbuka, tampilan ikut disegarkan.
function refreshChatIfOpen() {
  if (state.view !== "chat" || !state.selectedId) return;
  if (chatInputFocused()) return;
  render();
}

async function ensureAgyModels() {
  if (state.agyModels) return;
  try {
    const data = await api("/api/models/agy");
    state.agyModels = data.models || [];
  } catch {
    state.agyModels = [];
  }
  refreshChatIfOpen();
  if (state.view === "models") render();
}

async function ensureOllamaModels() {
  if (state.ollamaModels) return;
  try {
    const data = await api("/api/models/ollama");
    state.ollamaModels = data.models || [];
  } catch {
    state.ollamaModels = [];
  }
  refreshChatIfOpen();
  if (state.view === "models") render();
}

async function reloadEngines() {
  const data = await api("/api/engines");
  state.engines = data.engines || [];
  state.skipped = data.skipped || [];
  state.enginesError = data.error || null;
}

async function changeEngineModel(engine, model) {
  if (model === undefined) return;
  try {
    await api(`/api/engines/${encodeURIComponent(engine.key)}/model`, {
      method: "PUT",
      body: JSON.stringify({ model }),
    });
    toast("Model default engine disimpan");
    await reloadEngines();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function engineModelControl(engine) {
  const provider = engine.provider;
  let options = null;
  if (provider === "qoder") {
    options = (state.catalog.models || []).map((model) => model.name);
  } else if (provider === "agy") {
    options = (state.agyModels || []).map((model) => model.id);
  } else if (provider === "ollama") {
    options = (state.ollamaModels || []).map((model) => model.id);
  }

  if (!options) {
    return h(
      "div",
      { class: "row-actions" },
      h("span", { text: engine.model || "—" }),
      h("button", {
        class: "link-btn",
        onclick: () => {
          const value = prompt(`Model untuk ${engine.key}`, engine.model || "");
          if (value !== null) changeEngineModel(engine, value.trim());
        },
        text: "Ubah",
      })
    );
  }

  const select = h("select", {});
  const unique = [...new Set(options.filter(Boolean))];
  if (engine.model && !unique.includes(engine.model)) {
    unique.unshift(engine.model);
  }
  unique.forEach((id) => select.append(h("option", { value: id, text: id })));
  select.value = engine.model || unique[0] || "";
  select.addEventListener("change", () => changeEngineModel(engine, select.value));
  return select;
}

async function ensureCatalog(force = false) {
  const fresh =
    state.catalog.fetchedAt && Date.now() - state.catalog.fetchedAt < MODEL_TTL;
  if (state.catalog.loading) return;
  if (!force && fresh) return;

  state.catalog.loading = true;
  try {
    const data = await api(`/api/models/catalog${force ? "?refresh=1" : ""}`);
    state.catalog.models = (data.models || []).filter((m) => m.is_enabled !== false);
    state.catalog.error = data.error || null;
    state.catalog.fetchedAt = Date.now();
  } catch (error) {
    state.catalog.error = error.message;
  } finally {
    state.catalog.loading = false;
  }

  if (!$("#modal").classList.contains("hidden") && $("#f-engine").value === "qoder") {
    syncModalModels(true);
  }
  if (!$("#drawer").classList.contains("hidden")) {
    const waker = state.wakers.find((w) => w.id === state.selectedId);
    if (waker) renderDrawerBody(waker);
  }
  refreshChatIfOpen();
  if (state.view === "models") render();
}

function catalogModel(name) {
  return state.catalog.models.find(
    (model) => model.name === name || model.display_name === name
  );
}

function engineForWorker(waker) {
  const direct = state.engines.find((engine) => engine.key === waker.engine_key);
  if (direct) return direct;
  const group = (state.engineGroups || []).find((item) => item.name === waker.engine_key);
  if (group && group.models.length) {
    return state.engines.find((engine) => engine.key === group.models[0]) || null;
  }
  return null;
}

/// Logo mengikuti model yang dipilih: model Gemini/Claude/GPT berjalan lewat
/// Antigravity, model Qwen/Qoder lewat Qoder. Bila model kosong, engine yang dipakai.
function providerForWorker(waker) {
  const model = String(waker.model || "").toLowerCase();
  if (/gemini|claude|gpt/.test(model)) return "antigravity";
  if (/qwen|qoder/.test(model)) return "qoder";
  if (/nvidia|nemotron/.test(model)) return "nvidia";
  if (/ollama/.test(String(waker.engine_key || "").toLowerCase())) return "ollama";
  const engine = engineForWorker(waker);
  if (!engine) return null;
  if (engine.provider === "qoder") return "qoder";
  if (engine.provider === "agy") return "antigravity";
  if (engine.provider === "nvidia" || engine.key === "nvidia") return "nvidia";
  if (engine.kind === "ollama" || engine.provider === "ollama") return "ollama";
  if (/local/.test(String(engine.key || ""))) return "ollama";
  return null;
}

const PROVIDER_LABEL = {
  qoder: "Qoder",
  antigravity: "Antigravity",
  nvidia: "NVIDIA",
  ollama: "Ollama",
};

function workerAvatar(waker) {
  const provider = providerForWorker(waker);
  if (provider) {
    return h(
      "div",
      { class: "avatar avatar-logo", title: PROVIDER_LABEL[provider] || provider },
      h("img", { src: `/console/${provider}.svg`, alt: PROVIDER_LABEL[provider] || provider })
    );
  }
  return h("div", { class: "avatar", style: `background:${hue(waker.name)}`, text: initials(waker.name) });
}

const viewerState = { open: false, kind: null, path: "", title: "", content: "", url: "" };

function applyChrome() {
  const app = document.querySelector(".app");
  if (!app) return;
  app.classList.toggle("viewer-open", viewerState.open);
  app.classList.toggle("rail-collapsed", !!state.railCollapsed);
}

function toggleRail() {
  state.railCollapsed = !state.railCollapsed;
  try {
    localStorage.setItem("station.railCollapsed", state.railCollapsed ? "1" : "0");
  } catch {
    // localStorage bisa diblokir; kolom tetap berfungsi untuk sesi ini
  }
  applyChrome();
}

function renderViewerPanel() {
  applyChrome();
  const name = document.getElementById("viewer-name");
  const path = document.getElementById("viewer-path");
  const body = document.getElementById("viewer-body");
  if (!name || !path || !body) return;
  name.textContent = viewerState.title || "Viewer";
  path.textContent = viewerState.path || "";
  clear(body);
  if (!viewerState.open) return;
  if (viewerState.kind === "image") {
    body.append(h("img", { src: viewerState.url, alt: viewerState.title || "gambar" }));
  } else if (viewerState.kind === "text") {
    body.append(h("pre", { text: viewerState.content }));
  } else if (viewerState.kind === "loading") {
    body.append(h("p", { class: "card-desc", text: "Memuat…" }));
  } else {
    body.append(
      h("p", {
        class: "card-desc",
        text: "Klik berkas di Skills/MCP/Rules, atau tautan berkas pada laporan worker.",
      })
    );
  }
}

async function openViewerPath(rawPath) {
  const path = String(rawPath || "").trim();
  if (!path) return;
  viewerState.open = true;
  viewerState.kind = "loading";
  viewerState.path = path;
  viewerState.title = path.split("/").pop();
  viewerState.content = "";
  viewerState.url = "";
  renderViewerPanel();
  try {
    const data = await api(`/api/files?path=${encodeURIComponent(path)}`);
    viewerState.kind = data.kind;
    viewerState.path = data.path || path;
    viewerState.title = String(viewerState.path).split("/").pop();
    viewerState.content = data.content || "";
    viewerState.url = data.url || "";
  } catch (error) {
    viewerState.kind = "text";
    viewerState.title = "Tidak bisa dibuka";
    viewerState.content = `${path}\n\n${error.message}`;
  }
  renderViewerPanel();
}

async function openViewerCapability(scope, relPath) {
  viewerState.open = true;
  viewerState.kind = "loading";
  viewerState.path = `${scope}/${relPath}`;
  viewerState.title = String(relPath).split("/").pop();
  viewerState.content = "";
  viewerState.url = "";
  renderViewerPanel();
  try {
    const data = await api(
      `/api/capabilities/file?scope=${encodeURIComponent(scope)}&path=${encodeURIComponent(relPath)}`
    );
    viewerState.kind = "text";
    viewerState.path = `${scope}/${data.path || relPath}`;
    viewerState.content = data.content || "";
  } catch (error) {
    viewerState.kind = "text";
    viewerState.title = "Tidak bisa dibuka";
    viewerState.content = `${scope}/${relPath}\n\n${error.message}`;
  }
  renderViewerPanel();
}

function minimizeViewer() {
  viewerState.open = false;
  renderViewerPanel();
}

function closeViewer() {
  viewerState.open = false;
  viewerState.kind = null;
  viewerState.path = "";
  viewerState.title = "";
  viewerState.content = "";
  viewerState.url = "";
  renderViewerPanel();
}

function toggleViewer() {
  if (viewerState.open) {
    minimizeViewer();
    return;
  }
  viewerState.open = true;
  if (!viewerState.kind) viewerState.kind = "empty";
  renderViewerPanel();
}

const PATH_RE = /((?:~\/|\/|home\/)[^\s"'`<>()\[\]]+?\.(?:md|markdown|txt|log|json|jsonl|ya?ml|toml|csv|tsv|py|js|ts|tsx|jsx|rs|sh|html|css|sql|png|jpe?g|gif|webp|svg|bmp|avif|pdf))/gi;

/// Path absolut di laporan worker dibuat bisa diklik; klik membuka kolom viewer.
function appendRichText(container, text) {
  const value = String(text || "");
  let last = 0;
  for (const match of value.matchAll(PATH_RE)) {
    if (match.index > last) {
      container.append(document.createTextNode(value.slice(last, match.index)));
    }
    const raw = match[0];
    const path = raw.startsWith("home/") ? `/${raw}` : raw;
    container.append(
      h(
        "button",
        {
          class: "path-link",
          type: "button",
          title: `Buka ${path}`,
          onclick: () => openViewerPath(path),
        },
        raw
      )
    );
    last = match.index + raw.length;
  }
  if (last < value.length) {
    container.append(document.createTextNode(value.slice(last)));
  }
}

/// Pemilih model di kepala percakapan: mengganti model worker tanpa membuka
/// form edit. Nilainya disimpan pada worker (PATCH), jadi run berikutnya memakai
/// model itu. Worker yang memakai grup dibiarkan tanpa pemilih karena model
/// ditentukan urutan grup.
/// Pemakaian context dari run terakhir worker: token nyata bila engine melaporkan
/// `usage`, selain itu perkiraan dari panjang prompt (ditandai ~). Persentase hanya
/// ditampilkan bila context window benar-benar diketahui.
function contextWindowFor(waker) {
  const entry = catalogEntry(waker.model);
  const agy = agyEntry(waker.model);
  const fallback =
    (entry && (entry.contexts || []).length
      ? (entry.contexts.find((ctx) => ctx.default) || entry.contexts[0]).tokens
      : 0) || (agy && agy.context_window) || 0;
  return waker.context_window || fallback || 0;
}

const CONTEXT_SNAPSHOT_VERSION = 2;

function loadContextSnapshot(waker) {
  if (state.contextSnapshot[waker.id]) return state.contextSnapshot[waker.id];
  try {
    // v2: snapshot lama (yang bisa berisi 56k dari bug contextRatioOf pertama)
    // sengaja tidak dibaca. Angka baru akan diisi dari event context terakhir.
    const raw = localStorage.getItem(`station.context.v${CONTEXT_SNAPSHOT_VERSION}.${waker.id}`);
    if (!raw) return null;
    const snapshot = JSON.parse(raw);
    if (!snapshot || snapshot.version !== CONTEXT_SNAPSHOT_VERSION) return null;
    if (snapshot.model !== (waker.model || null) || snapshot.engine !== (waker.engine_key || null)) {
      return null;
    }
    state.contextSnapshot[waker.id] = snapshot;
    return snapshot;
  } catch {
    return null;
  }
}

function saveContextSnapshot(waker, values) {
  if (!waker) return null;
  const window = contextWindowFor(waker) || values.window || 0;
  const ratio =
    typeof values.ratio === "number"
      ? values.ratio
      : values.tokens != null && window
        ? values.tokens / window
        : null;
  const tokens =
    values.tokens != null
      ? values.tokens
      : ratio != null && window
        ? Math.round(ratio * window)
        : null;
  if (ratio == null && tokens == null) return null;
  const snapshot = {
    version: CONTEXT_SNAPSHOT_VERSION,
    ratio,
    tokens,
    window,
    source: values.source || "engine",
    at: values.at || new Date().toISOString(),
    model: waker.model || null,
    engine: waker.engine_key || null,
  };
  state.contextSnapshot[waker.id] = snapshot;
  try {
    localStorage.setItem(
      `station.context.v${CONTEXT_SNAPSHOT_VERSION}.${waker.id}`,
      JSON.stringify(snapshot)
    );
  } catch {
    // localStorage bisa diblokir; state sesi tetap menyimpan angka
  }
  return snapshot;
}

function contextSnapshotText(snapshot, waiting, provider) {
  const fmt = (value) =>
    value >= 1_000_000
      ? `${(value / 1_000_000).toFixed(1)}M`
      : value >= 1_000
        ? `${(value / 1_000).toFixed(1)}k`
        : `${value}`;
  const suffix = waiting ? " · standby" : "";
  const window = snapshot.window || 0;
  const ratio =
    snapshot.ratio != null
      ? snapshot.ratio
      : snapshot.tokens != null && window > 0
        ? snapshot.tokens / window
        : null;
  const tokens =
    snapshot.tokens != null
      ? snapshot.tokens
      : ratio != null && window > 0
        ? Math.round(ratio * window)
        : null;
  const manual =
    provider === "antigravity" && snapshot.ratio == null && snapshot.source !== "engine"
      ? " · manual"
      : "";
  if (ratio != null && window > 0) {
    return `Context ${fmt(tokens)} / ${fmt(window)} · ${(ratio * 100).toFixed(1)}%${manual}${suffix}`;
  }
  if (tokens != null && window > 0) {
    return `Context ${fmt(tokens)} / ${fmt(window)}${manual}${suffix}`;
  }
  if (ratio != null) {
    return `Context ${(ratio * 100).toFixed(1)}%${manual}${suffix}`;
  }
  return "Context —";
}

function contextBadgeState(waker) {
  const provider = providerForWorker(waker);
  const stamp = (item) => Date.parse(item.started_at || item.finished_at || "") || 0;
  const runs = (state.runs || [])
    .filter((item) => item.waker_id === waker.id)
    .sort((a, b) => stamp(b) - stamp(a));
  const activeRun = runs.find(
    (item) => item.status === "running" || item.status === "queued"
  );
  const reportedRun = runs.find(
    (item) =>
      typeof item.context_ratio === "number" ||
      (item.input_tokens != null && item.input_tokens > 0)
  );
  const run = activeRun || reportedRun || runs[0] || null;
  const snapshot = loadContextSnapshot(waker);
  const window = contextWindowFor(waker) || (snapshot && snapshot.window) || 0;
  const fmt = (value) =>
    value >= 1_000_000
      ? `${(value / 1_000_000).toFixed(1)}M`
      : value >= 1_000
        ? `${(value / 1_000).toFixed(1)}k`
        : `${value}`;

  if (!run && !snapshot) {
    return {
      className: "badge draft",
      title:
        provider === "antigravity"
          ? "Menunggu laporan context Antigravity"
          : "Belum ada context yang dilaporkan engine",
      text: window ? `Context — / ${fmt(window)}` : "Context —",
    };
  }

  const currentRatio =
    run && typeof run.context_ratio === "number" ? run.context_ratio : null;
  const currentTokens =
    run && run.input_tokens != null && run.input_tokens > 0
      ? run.input_tokens + (run.cache_read_tokens || 0)
      : null;

  if (currentRatio != null || currentTokens != null) {
    const previous = state.contextSnapshot[waker.id];
    if (
      !previous ||
      previous.ratio !== currentRatio ||
      previous.tokens !== currentTokens ||
      previous.window !== window
    ) {
      saveContextSnapshot(waker, {
        ratio: currentRatio,
        tokens: currentTokens,
        source: currentRatio != null ? "engine" : "usage",
        at: run.started_at,
      });
    }
    const ratio =
      currentRatio != null
        ? currentRatio
        : currentTokens != null && window
          ? currentTokens / window
          : null;
    const tokens =
      currentRatio != null && window
        ? Math.round(currentRatio * window)
        : currentTokens;
    const manual =
      provider === "antigravity" && currentRatio == null && run?.source !== "engine"
        ? " · manual"
        : "";
    const pct = ratio != null && window ? Math.min(100, ratio * 100) : null;
    const badge = pct != null && pct > 80 ? "bad" : pct != null && pct > 60 ? "paused" : "ok";
    const title =
      provider === "qoder"
        ? "Rasio context dari Qoder stream-json (otomatis)"
        : provider === "antigravity"
          ? (currentRatio != null
              ? "Rasio context dari Antigravity stream-json (otomatis)"
              : "Token dari usage Antigravity")
          : provider === "nvidia"
            ? "Token dari usage NVIDIA"
            : "Context dari engine";
    if (pct != null) {
      return {
        className: `badge ${badge}`,
        title,
        text: `Context ${fmt(tokens)} / ${fmt(window)} · ${pct.toFixed(1)}%${manual}`,
      };
    }
    if (ratio != null) {
      return {
        className: "badge ok",
        title,
        text: `Context ${(ratio * 100).toFixed(1)}%${manual}`,
      };
    }
    return {
      className: "badge draft",
      title,
      text: `Context ${fmt(tokens)}${window ? ` / ${fmt(window)}` : ""}${manual}`,
    };
  }

  // Angka terakhir yang pernah dilaporkan bertahan (standby) sampai engine
  // melaporkan angka baru; jangan kembali ke "—" atau estimasi prompt.
  if (snapshot) {
    const waiting = !!activeRun;
    const pct =
      snapshot.ratio != null && window
        ? Math.min(100, snapshot.ratio * 100)
        : snapshot.tokens != null && window
          ? Math.min(100, (snapshot.tokens / window) * 100)
          : null;
    const badge = pct != null && pct > 80 ? "bad" : pct != null && pct > 60 ? "paused" : "ok";
    return {
      className: `badge ${badge}`,
      title: waiting
        ? "Angka context terakhir yang dilaporkan; menunggu laporan berikutnya"
        : "Angka context terakhir yang dilaporkan engine",
      text: contextSnapshotText(snapshot, waiting, provider),
    };
  }

  if (activeRun || provider === "qoder" || provider === "antigravity") {
    return {
      className: "badge draft",
      title: "Menunggu laporan context dari engine",
      text: window ? `Context — / ${fmt(window)} · menunggu` : "Context — · menunggu",
    };
  }
  if (provider === "ollama") {
    return {
      className: "badge draft",
      title: "Ollama tidak melaporkan context/usage",
      text: window ? `Context — / ${fmt(window)}` : "Context —",
    };
  }
  if (activeRun || provider === "qoder") {
    return {
      className: "badge draft",
      title: "Menunggu laporan context dari engine",
      text: window ? `Context — / ${fmt(window)} · menunggu` : "Context — · menunggu",
    };
  }
  return {
    className: "badge draft",
    title: "Engine tidak melaporkan context/usage",
    text: window ? `Context — / ${fmt(window)}` : "Context —",
  };
}

function contextUsageBadge(waker) {
  const info = contextBadgeState(waker);
  return h("span", {
    class: info.className,
    title: info.title,
    text: info.text,
    "data-waker-context": waker.id,
  });
}

function refreshContextBadges() {
  document.querySelectorAll("[data-waker-context]").forEach((element) => {
    const waker = state.wakers.find((item) => item.id === element.getAttribute("data-waker-context"));
    if (!waker) return;
    const info = contextBadgeState(waker);
    element.className = info.className;
    element.textContent = info.text;
    element.title = info.title;
  });
}

function agyEntry(model) {
  return (state.agyModels || []).find((item) => item.id === model);
}

function contextRatioOf(run) {
  // Context naik selama run. `.find()` mengambil laporan PERTAMA (mis. 5.6%)
  // sehingga badge jatuh ke 56k saat run selesai, padahal laporan TERAKHIR
  // run itu 12.9% (129k). Ambil event context terakhir, bukan yang pertama.
  const events = (run && run.events) || [];
  const event = [...events].reverse().find((item) => item.kind === "context");
  const match = /(\d+(?:\.\d+)?)%/.exec((event && event.text) || "");
  if (!match) return null;
  return Number.parseFloat(match[1]) / 100;
}

function upsertRunSummary(run) {
  if (!run || !run.id) return;
  const previous = (state.runs || []).find((item) => item.id === run.id);
  const summary = {
    ...run,
    events: [],
    context_ratio:
      contextRatioOf(run) ?? run.context_ratio ?? (previous && previous.context_ratio),
  };
  if (typeof summary.context_ratio === "number") {
    const waker = state.wakers.find((item) => item.id === summary.waker_id);
    if (waker) {
      saveContextSnapshot(waker, {
        ratio: summary.context_ratio,
        source: "engine",
        at: summary.started_at,
      });
    }
  }
  const index = (state.runs || []).findIndex((item) => item.id === run.id);
  if (index >= 0) state.runs[index] = summary;
  else state.runs = [summary, ...(state.runs || [])];
}

function catalogEntry(model) {
  return (state.catalog.models || []).find(
    (item) => item.name === model || item.id === model
  );
}

/// Pemilih model di kepala percakapan. Untuk model Qoder, thinking level dan
/// context window ikut ditampilkan dan hanya berisi nilai yang benar-benar
/// dilaporkan katalog untuk model itu; model tanpa thinking hanya menampilkan
/// "none" dan pilihannya dinonaktifkan.
function chatModelControl(waker, engine) {
  const group = (state.engineGroups || []).find((item) => item.name === waker.engine_key);
  if (group) {
    return h("span", { class: "card-slug", style: "margin-left:8px", text: `model dari grup ${group.name}` });
  }

  const provider = engine ? engine.provider : null;
  if (provider !== "qoder") {
    let options = [];
    if (provider === "agy") options = (state.agyModels || []).map((model) => model.id);
    else if (provider === "ollama") options = (state.ollamaModels || []).map((model) => model.id);
    else if (provider === "nvidia") options = (state.nvidia?.models || []).slice();
    if (engine && engine.model) options.unshift(engine.model);
    if (waker.model) options.unshift(waker.model);
    options = [...new Set(options.filter(Boolean))];
    if (!options.length) {
      return h("button", {
        class: "link-btn",
        style: "margin-left:8px",
        text: waker.model || "Atur model",
        onclick: async () => {
          const value = prompt(`Model untuk ${waker.name}`, waker.model || "");
          if (value && value.trim()) await patchWakerModel(waker, { model: value.trim() });
        },
      });
    }
    const select = h("select", { style: "margin-left:8px", title: "Model untuk worker ini" },
      ...options.map((id) => h("option", { value: id, text: id })));
    select.value = waker.model || (engine && engine.model) || options[0];
    select.addEventListener("change", () => patchWakerModel(waker, { model: select.value }));
    return select;
  }

  const catalog = state.catalog.models || [];
  const names = catalog.map((model) => model.name);
  if (waker.model && !names.includes(waker.model)) names.unshift(waker.model);
  if (engine && engine.model && !names.includes(engine.model)) names.unshift(engine.model);
  const uniqueNames = [...new Set(names.filter(Boolean))];

  const modelSelect = h("select", { title: "Model (katalog Qoder)" },
    ...uniqueNames.map((name) => h("option", { value: name, text: name })));
  const effortSelect = h("select", { title: "Thinking level" });
  const contextSelect = h("select", { title: "Context window" });

  const currentModel = () =>
    catalogEntry(modelSelect.value) || catalog.find((item) => item.name === engine?.model) || null;

  const syncOptions = () => {
    const model = currentModel();
    const efforts = model && model.thinking && (model.efforts || []).length ? model.efforts : [];
    clear(effortSelect);
    if (!efforts.length) {
      effortSelect.append(h("option", { value: "", text: "none" }));
      effortSelect.disabled = "disabled";
      effortSelect.value = "";
    } else {
      effortSelect.disabled = null;
      efforts.forEach((level) => effortSelect.append(h("option", { value: level, text: level })));
      const wanted = waker.reasoning_effort || model.default_effort || efforts[0];
      effortSelect.value = efforts.includes(wanted) ? wanted : efforts[0];
    }

    const contexts = (model && model.contexts) || [];
    clear(contextSelect);
    if (!contexts.length) {
      contextSelect.append(h("option", { value: "", text: "default" }));
      contextSelect.disabled = "disabled";
      contextSelect.value = "";
    } else {
      contextSelect.disabled = null;
      contexts.forEach((ctx) =>
        contextSelect.append(h("option", { value: String(ctx.tokens), text: ctx.key }))
      );
      const fallback = (contexts.find((ctx) => ctx.default) || contexts[0]).tokens;
      const wanted = waker.context_window || fallback;
      contextSelect.value = contexts.some((ctx) => ctx.tokens === wanted) ? String(wanted) : String(fallback);
    }
  };

  modelSelect.value = waker.model || (engine && engine.model) || uniqueNames[0] || "";
  syncOptions();

  const commit = () => {
    const effort = effortSelect.disabled ? "" : effortSelect.value;
    const context = contextSelect.disabled ? 0 : Number(contextSelect.value || 0);
    patchWakerModel(waker, {
      model: modelSelect.value,
      reasoning_effort: effort,
      context_window: context,
    });
  };
  modelSelect.addEventListener("change", () => {
    syncOptions();
    commit();
  });
  effortSelect.addEventListener("change", commit);
  contextSelect.addEventListener("change", commit);

  return h(
    "div",
    { class: "row-actions", style: "margin-left:8px;gap:6px" },
    modelSelect,
    effortSelect,
    contextSelect
  );
}

async function patchWakerModel(waker, payload) {
  try {
    const updated = await api(`/api/wakers/${waker.id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    const index = state.wakers.findIndex((item) => item.id === waker.id);
    if (index >= 0) state.wakers[index] = { ...state.wakers[index], ...updated };
    toast(`Pengaturan ${waker.name} disimpan`);
    render();
  } catch (error) {
    toast(error.message, true);
    render();
  }
}

function wakerSubtitle(waker, engine) {
  const parts = [waker.engine_key];
  const model = waker.model || (engine && engine.model);
  if (model) parts.push(model);
  if (waker.reasoning_effort) parts.push(`thinking ${waker.reasoning_effort}`);
  if (waker.context_window) parts.push(`${Math.round(waker.context_window / 1000)}K`);
  return parts.join(" · ");
}

function modelDefaultsFor(name) {
  return state.modelDefaults[name] || {};
}

function rememberModel(name, effort, context) {
  if (!name) return;
  state.modelDefaults[name] = { effort: effort || "", context: context || 0 };
  try {
    localStorage.setItem("station.modelDefaults", JSON.stringify(state.modelDefaults));
  } catch {
    // localStorage bisa ditolak; ingatan per model hanya pelengkap.
  }
}

function modelDefaultEffort(model) {
  if (!model || !model.efforts || !model.efforts.length) return "";
  return model.default_effort || model.efforts[model.efforts.length - 1];
}

function modelDefaultContext(model) {
  if (!model || !model.contexts || !model.contexts.length) return 0;
  const fallback = model.contexts[model.contexts.length - 1];
  return (model.contexts.find((c) => c.default) || fallback).tokens;
}

function fillEffortSelect(select, model, preferred) {
  clear(select);
  if (!model || !model.thinking || !(model.efforts || []).length) {
    select.append(h("option", { value: "", text: "tidak didukung model ini" }));
    select.disabled = true;
    return;
  }
  model.efforts.forEach((level) => select.append(h("option", { value: level, text: level })));
  select.value =
    preferred && model.efforts.includes(preferred) ? preferred : modelDefaultEffort(model);
  select.disabled = false;
}

function fillContextSelect(select, model, preferred) {
  clear(select);
  const contexts = (model && model.contexts) || [];
  contexts.forEach((ctx) =>
    select.append(
      h("option", {
        value: String(ctx.tokens),
        text: `${ctx.key}${ctx.default ? " · bawaan" : ""}`,
      })
    )
  );
  const chosen =
    preferred && contexts.some((ctx) => ctx.tokens === preferred)
      ? preferred
      : modelDefaultContext(model);
  if (chosen) select.value = String(chosen);
  select.disabled = contexts.length === 0;
}

function hue(name) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return `linear-gradient(145deg, hsl(${hash} 62% 54%), hsl(${(hash + 38) % 360} 58% 42%))`;
}

function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
}

function timeAgo(iso) {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, Date.now() - then);
  const min = Math.floor(diff / 60000);
  if (min < 1) return "baru saja";
  if (min < 60) return `${min} mnt lalu`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours} jam lalu`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} hari lalu`;
  return new Date(then).toLocaleDateString("id-ID");
}

function statusBadge(status) {
  const map = {
    active: ["ok", "aktif"],
    paused: ["paused", "dijeda"],
    draft: ["draft", "draf"],
    archived: ["draft", "arsip"],
  };
  const [cls, label] = map[status] || ["draft", status];
  return h("span", { class: `badge ${cls}`, text: label });
}

function setStatus(ok, text) {
  const dot = $("#status-dot");
  dot.classList.toggle("ok", ok);
  dot.classList.toggle("bad", !ok);
  $("#status-text").textContent = text;
}

let toastTimer = null;
function toast(message, bad = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("bad", bad);
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 3200);
}

// ---------------------------------------------------------------- data

async function loadAll() {
  // Bila halaman sudah dihidrasi server, jangan turunkan lagi ke kerangka "memuat".
  const hydrated = state.wakers.length > 0 || state.engines.length > 0;
  state.loading = !hydrated;
  state.error = null;
  if (!hydrated) render();
  try {
    const [
      engines,
      wakers,
      stats,
      runs,
      resources,
      quota,
      qoderAccount,
      qoderLogin,
      antigravityAccount,
      agyCatalog,
    ] = await Promise.all([
      api("/api/engines"),
      api("/api/wakers"),
      api("/api/stats").catch(() => null),
      api("/api/runs").catch(() => null),
      api("/api/system/resources").catch(() => null),
      api("/api/engines/quota").catch(() => null),
      api("/api/qoder/account").catch(() => null),
      api("/api/qoder/login").catch(() => null),
      api("/api/antigravity/account").catch(() => null),
      api("/api/models/agy").catch(() => null),
    ]);
    try {
      state.nvidia = await api("/api/providers/nvidia").catch(() => null);
    } catch {
      state.nvidia = null;
    }
    state.qoder = qoderAccount?.qoder || null;
    state.qoderLogin = qoderLogin || null;
    state.antigravity = antigravityAccount?.antigravity || null;
    state.agyModels = agyCatalog?.models || state.agyModels;
    state.quota = quota?.engines || [];
    state.engines = engines.engines || [];
    state.engineGroups = engines.groups || [];
    state.skipped = engines.skipped || [];
    state.enginesError = engines.error || null;
    state.wakers = wakers.wakers || [];
    const requestedWaker = new URLSearchParams(location.search).get("waker");
    if (requestedWaker && !state.selectedId) {
      const match = state.wakers.find(
        (waker) =>
          waker.slug === requestedWaker ||
          waker.id === requestedWaker ||
          waker.name.toLowerCase() === requestedWaker.toLowerCase()
      );
      if (match) state.selectedId = match.id;
    }
    state.backend = wakers.backend || state.backend;
    state.stats = stats;
    state.resources = resources;
    if (runs) state.runs = runs.runs || [];
    setStatus(true, stats ? `daemon aktif · ${stats.db_engine || "db"}` : "daemon aktif");
  } catch (error) {
    state.error = error.message;
    setStatus(false, "daemon tidak merespons");
  } finally {
    state.loading = false;
    render();
  }
}

// ---------------------------------------------------------------- views

function render() {
  const title = TITLES[state.view] || TITLES.wakers;
  $("#page-title").textContent = title[0];
  $("#page-sub").textContent = title[1];

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.view === state.view);
  });

  $("#search").parentElement.style.display = state.view === "wakers" ? "" : "none";
  $("#new-waker").style.display = state.view === "wakers" ? "" : "none";

  const view = $("#view");
  // Render ulang di tengah membaca: posisi chat dicatat dulu, lalu dipulihkan
  // renderChat. Hanya berlaku saat tetap di tampilan chat; membuka chat lagi
  // dari menu tetap mendarat di ujung bawah.
  const previousLog = state.view === "chat" ? document.querySelector(".chat-log") : null;
  const previousWaker = previousLog && previousLog.dataset.waker ? previousLog.dataset.waker : "";
  // Mengirim pesan adalah tindakan yang disengaja: setelah itu view selalu
  // turun ke bawah, meski sebelumnya sedang membaca ke atas.
  const forceBottom = !!previousWaker && state.forceChatBottom === previousWaker;
  state.forceChatBottom = null;
  state.restoreChat = previousWaker
    ? {
        wakerId: previousWaker,
        follow: forceBottom || nearBottom(previousLog),
        scrollTop: previousLog.scrollTop,
      }
    : null;
  clear(view);
  view.classList.remove("chat-view");
  window.scrollTo(0, 0);
  document.body.classList.toggle("chat-active", state.view === "chat");
  const app = document.querySelector(".app");
  if (app) {
    app.classList.toggle("chat-mode", state.view === "chat" && !!state.selectedId);
  }

  if (state.loading) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "◌" }),
        h("strong", { text: "Memuat konsol…" }),
        h("p", { text: "Membaca worker dan registry mesin dari daemon." })
      )
    );
    return;
  }

  if (state.error) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "!" }),
        h("strong", { text: "Tidak bisa menghubungi daemon" }),
        h("p", { text: state.error }),
        h("button", { class: "btn", onclick: loadAll }, "Coba lagi")
      )
    );
    return;
  }

  switch (state.view) {
    case "dashboard":
      renderDashboard(view);
      break;
    case "projects":
      renderProjects(view);
      break;
    case "wakers":
      renderWakers(view);
      break;
    case "chat":
      renderChat(view);
      break;
    case "runs":
      renderRuns(view);
      break;
    case "traces":
      renderTraces(view);
      break;
    case "automations":
      renderAutomations(view);
      break;
    case "auto-pilot":
      renderAutoPilot(view);
      break;
    case "workflows":
      renderWorkflows(view);
      break;
    case "approvals":
      renderApprovals(view);
      break;
    case "decide":
      renderDecide(view);
      break;
    case "engines":
      renderEngines(view);
      break;
    case "capabilities":
      renderCapabilities(view);
      break;
    case "knowledge":
      renderKnowledge(view);
      break;
    case "memory":
      renderMemory(view);
      break;
    case "models":
      renderModels(view);
      break;
    case "usage":
      renderUsage(view);
      break;
    case "settings":
      renderSettings(view);
      break;
    case "groups":
      renderGroups(view);
      break;
    case "channels":
      renderChannels(view);
      break;
    default:
      renderPlaceholder(view, state.view);
  }
}

async function loadDashboardData() {
  await Promise.all([loadAutomations(), loadApprovals()]);
  try {
    const data = await api("/api/runs");
    state.runs = data.runs || [];
  } catch {
    // ringkasan memakai data terakhir yang ada
  }
}

function renderDashboard(view) {
  const activeWorkers = state.wakers.filter((w) => w.status === "active").length;
  const running = state.runs.filter((r) => r.status === "running").length;
  const queued = state.runs.filter((r) => r.status === "queued").length;
  const enabled = state.automations.filter((a) => a.enabled).length;
  const pending = state.approvals.filter((a) => a.status === "pending").length;

  const stats = [
    ["Worker aktif", `${activeWorkers}`, `${state.wakers.length} terdaftar`, "wakers"],
    ["Agen berjalan", `${running}`, `${queued} menunggu slot`, "runs"],
    ["Automation aktif", `${enabled}`, `${state.automations.length} total`, "automations"],
    ["Menunggu izin", `${pending}`, "inbox persetujuan", "approvals"],
    ["Engine", `${state.engines.length}`, "registry mesin", "engines"],
    ["Run dimuat", `${state.runs.length}`, "riwayat di memori", "runs"],
  ];

  const grid = h("div", { class: "grid" });
  stats.forEach(([label, value, sub, target]) => {
    const go = () => {
      state.view = target;
      history.replaceState(null, "", `?view=${target}`);
      render();
    };
    grid.append(
      h(
        "div",
        {
          class: "card",
          role: "button",
          tabindex: "0",
          onclick: go,
          onkeydown: (event) => activateCard(event, go),
        },
        h("div", { class: "stat-value", text: value }),
        h("div", { class: "stat-label", text: label }),
        h("div", { class: "stat-sub", text: sub })
      )
    );
  });
  view.append(h("div", { class: "section-head" }, h("h2", {}, "Ringkasan")), grid);

  view.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:24px" },
      h("h2", {}, "Run terbaru")
    )
  );

  if (!state.runs.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("strong", { text: "Belum ada run" }),
        h("p", { text: "Mulai percakapan dari @Worker atau jalankan automation." })
      )
    );
  } else {
    const body = h("tbody");
    state.runs.slice(0, 6).forEach((run) => {
      const worker = state.wakers.find((w) => w.id === run.waker_id);
      body.append(
        h(
          "tr",
          {},
          h("td", { text: timeAgo(run.started_at) }),
          h("td", { text: worker ? worker.name : run.waker_id }),
          h("td", { text: run.engine_key }),
          h("td", {}, runStatusBadge(run.status)),
          h("td", {
            class: "path",
            text: (run.output || "").replace(/\s+/g, " ").slice(0, 70) || "—",
          })
        )
      );
    });
    view.append(
      h(
        "div",
        { class: "panel" },
        h(
          "table",
          { class: "table" },
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              ...["Waktu", "Worker", "Engine", "Status", "Keluaran"].map((label) =>
                h("th", {}, label)
              )
            )
          ),
          body
        )
      )
    );
  }

  view.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:24px" },
      h("h2", {}, "Aksi cepat")
    ),
    h(
      "div",
      { class: "drawer-actions" },
      h("button", { class: "btn primary", onclick: () => openModal(null) }, "Worker baru"),
      h(
        "button",
        {
          class: "btn",
          onclick: () => {
            state.view = "chat";
            render();
          },
        },
        "Buka @Worker"
      ),
      h("button", { class: "btn", onclick: () => openAutomationModal() }, "Automation baru")
    )
  );
}

async function loadCapabilities() {
  try {
    state.capabilities = await api("/api/capabilities");
  } catch (error) {
    toast(error.message, true);
  }
}

function chipList(items, empty) {
  if (!items || !items.length) {
    return h("p", { class: "card-desc", text: empty });
  }
  const wrap = h("div", { class: "card-meta" });
  items.forEach((item) => wrap.append(h("span", { class: "badge", text: item })));
  return wrap;
}

let capabilityOpen = null;
let capabilityTree = [];
let capabilityFile = null;

async function toggleCapabilityFolder(scope, name) {
  const key = `${scope}:${name}`;
  if (capabilityOpen === key) {
    capabilityOpen = null;
    capabilityTree = [];
    capabilityFile = null;
    render();
    return;
  }
  capabilityOpen = key;
  capabilityTree = [];
  capabilityFile = null;
  render();
  try {
    const data = await api(
      `/api/capabilities/tree?scope=${encodeURIComponent(scope)}&path=${encodeURIComponent(name)}`
    );
    capabilityTree = (data.files || []).filter((item) => item.kind === "file");
  } catch {
    capabilityTree = [];
  }
  render();
}

async function openCapabilityFile(scope, path) {
  try {
    const data = await api(
      `/api/capabilities/file?scope=${encodeURIComponent(scope)}&path=${encodeURIComponent(path)}`
    );
    capabilityFile = { scope, path, content: data.content || "" };
  } catch (error) {
    capabilityFile = { scope, path, content: `Gagal membaca: ${error.message}` };
  }
  render();
}

function capabilityFolders(items, scope, emptyText) {
  if (!items || !items.length) {
    return h("p", { class: "card-desc", text: emptyText });
  }
  const list = h("div", { class: "cap-list" });
  items.forEach((name) => {
    const key = `${scope}:${name}`;
    const active = capabilityOpen === key;
    list.append(
      h(
        "button",
        {
          class: "cap-row",
          type: "button",
          onclick: () => toggleCapabilityFolder(scope, name),
        },
        h("span", { class: "cap-caret", text: active ? "▾" : "▸" }),
        h("span", { text: name })
      )
    );
    if (!active) return;
    if (!capabilityTree.length) {
      list.append(
        h("p", { class: "card-desc", style: "padding-left:28px", text: "Tidak ada berkas teks." })
      );
      return;
    }
    capabilityTree.forEach((file) => {
      list.append(
        h("button", {
          class: "cap-file",
          type: "button",
          text: file.path,
          onclick: () => openViewerCapability(scope, file.path),
        })
      );
    });
  });
  return list;
}

function capabilityFiles(items, scope, emptyText) {
  if (!items || !items.length) {
    return h("p", { class: "card-desc", text: emptyText });
  }
  const list = h("div", { class: "cap-list" });
  items.forEach((name) => {
    list.append(
      h("button", {
        class: "cap-file",
        style: "padding-left:8px",
        type: "button",
        text: name,
        onclick: () => openViewerCapability(scope, name),
      })
    );
  });
  return list;
}

function renderModels(view) {
  ensureCatalog();
  ensureAgyModels();
  ensureOllamaModels();
  const catalog = state.catalog.models || [];

  view.append(
    h("div", { class: "section-head" }, h("h2", {}, "Qoder", h("span", { class: "count", text: `· ${catalog.length} model` })))
  );
  const qoderTable = h("table", { class: "table" },
    h("thead", {}, h("tr", {},
      h("th", {}, "Model"), h("th", {}, "Thinking"), h("th", {}, "Context"),
      h("th", {}, "Harga"), h("th", {}, "Status")))
  );
  const qoderBody = h("tbody");
  catalog.forEach((model) => {
    qoderBody.append(h("tr", {},
      h("td", {}, h("strong", { text: model.display_name || model.name })),
      h("td", { text: (model.efforts || []).join(", ") || "none" }),
      h("td", { text: (model.contexts || []).map((c) => c.key).join(", ") || "—" }),
      h("td", { text: model.is_free ? "gratis" : `×${model.price_factor ?? "—"}` }),
      h("td", {}, h("span", { class: `badge ${model.is_enabled === false ? "draft" : "ok"}` }, model.is_enabled === false ? "nonaktif" : "aktif"))
    ));
  });
  qoderTable.append(qoderBody);
  view.append(h("div", { class: "panel" }, qoderTable));

  const gemini = state.agyModels || [];
  view.append(h("div", { class: "section-head", style: "margin-top:20px" }, h("h2", {}, "Gemini (Antigravity)", h("span", { class: "count", text: `· ${gemini.length} model` }))));
  view.append(h("div", { class: "panel" },
    h("div", { class: "chip-list" }, ...gemini.map((model) => h("span", { class: "badge cli", text: model.id })))
  ));

  const local = state.ollamaModels || [];
  view.append(h("div", { class: "section-head", style: "margin-top:20px" }, h("h2", {}, "Lokal (Ollama)", h("span", { class: "count", text: `· ${local.length} model` }))));
  view.append(h("div", { class: "panel" },
    h("div", { class: "chip-list" }, ...local.map((model) => h("span", { class: "badge ollama", text: model.id })))
  ));

  const nvidia = state.nvidia || {};
  const primary = (nvidia.lanes || []).find((lane) => lane.label === "lane-1") || {};
  const secondary = (nvidia.lanes || []).find((lane) => lane.label === "lane-2") || {};
  const baseInput = h("input", { value: nvidia.base_url || "https://integrate.api.nvidia.com/v1", placeholder: "https://integrate.api.nvidia.com/v1", autocomplete: "off" });
  const modelInput = h("input", { value: nvidia.model || "", placeholder: "model opsional, mis. meta/llama-3.1-70b-instruct", autocomplete: "off" });
  const keyInput = h("input", { type: "password", placeholder: primary.configured ? `tersimpan (${primary.key_hint || nvidia.key_hint}) — kosongkan untuk mempertahankan` : "API key lane 1 (nvapi-...)", autocomplete: "off" });
  const base2Input = h("input", { value: secondary.base_url || "https://integrate.api.nvidia.com/v1", placeholder: "https://integrate.api.nvidia.com/v1", autocomplete: "off" });
  const key2Input = h("input", { type: "password", placeholder: secondary.configured ? `tersimpan (${secondary.key_hint}) — kosongkan untuk mempertahankan` : "API key lane 2 (nvapi-...)", autocomplete: "off" });
  const laneCount = nvidia.lane_count || 0;
  const laneSummary = h("p", {
    class: "card-desc",
    text: laneCount
      ? `${laneCount} lane aktif · ${nvidia.rpm_total || 35 * laneCount} RPM total (${nvidia.rpm_per_key || 35} RPM/key, headroom di bawah 40). Lane 2 dipakai otomatis saat lane 1 mencapai batas efektifnya.`
      : "Belum ada lane. Isi lane 1; lane 2 opsional dan membuat total menjadi 70 RPM (35 RPM/key).",
  });
  const result = h("p", { class: "card-desc", text: laneCount ? `Tersimpan: ${laneCount} lane aktif.` : "Belum ada kunci tersimpan." });
  const save = h("button", {
    class: "btn primary",
    text: "Verifikasi & simpan",
    onclick: async () => {
      save.disabled = "disabled";
      result.textContent = "Memverifikasi lane…";
      try {
        const response = await api("/api/providers/nvidia", {
          method: "PUT",
          body: JSON.stringify({
            api_key: keyInput.value.trim() || null,
            base_url: baseInput.value.trim(),
            model: modelInput.value.trim(),
            api_key_2: key2Input.value.trim() || null,
            base_url_2: base2Input.value.trim(),
          }),
        });
        state.nvidia = response.nvidia || null;
        keyInput.value = "";
        key2Input.value = "";
        const lanes = state.nvidia?.lane_count || 1;
        const rpm = state.nvidia?.rpm_total || 40;
        const warnings = response.warnings || [];
        result.textContent = `Tersimpan: ${lanes} lane · ${rpm} RPM total; ${(state.nvidia?.models || []).length} model terbaca.${warnings.length ? ` Catatan: ${warnings.join("; ")}` : ""}`;
        render();
      } catch (error) {
        result.textContent = `Gagal: ${error.message}`;
        save.disabled = null;
      }
    },
  });
  const clear2 = secondary.configured
    ? h("button", {
        class: "btn",
        type: "button",
        text: "Hapus lane 2",
        onclick: async () => {
          clear2.disabled = "disabled";
          result.textContent = "Menghapus lane 2…";
          try {
            const response = await api("/api/providers/nvidia", {
              method: "PUT",
              body: JSON.stringify({
                clear_secondary: true,
                base_url: baseInput.value.trim(),
                model: modelInput.value.trim(),
              }),
            });
            state.nvidia = response.nvidia || null;
            const warnings = response.warnings || [];
            result.textContent = `Lane 2 dihapus; kembali ke 40 RPM.${warnings.length ? ` Catatan: ${warnings.join("; ")}` : ""}`;
            render();
          } catch (error) {
            result.textContent = `Gagal: ${error.message}`;
            clear2.disabled = null;
          }
        },
      })
    : null;
  view.append(
    h("div", { class: "section-head", style: "margin-top:20px" }, h("h2", {}, "Nvidia Worker Endpoint")),
    h("div", { class: "panel" },
      h("div", { class: "grid-2" },
        h("div", { class: "field" }, h("label", {}, "Base URL lane 1"), baseInput),
        h("div", { class: "field" }, h("label", {}, "Model default"), modelInput)),
      h("div", { class: "field" }, h("label", {}, "API key lane 1"), keyInput),
      h("div", { class: "grid-2", style: "margin-top:10px" },
        h("div", { class: "field" }, h("label", {}, "Base URL lane 2"), base2Input),
        h("div", { class: "field" }, h("label", {}, "API key lane 2 (opsional)"), key2Input)),
      laneSummary,
      h("div", { class: "row-actions", style: "margin-top:12px" }, save, clear2),
      result,
      (nvidia.models || []).length
        ? h("div", { class: "chip-list", style: "margin-top:10px" }, ...nvidia.models.map((id) => h("span", { class: "badge cli", text: id })))
        : null
    )
  );
}

function renderUsage(view) {
  const quota = state.quota || [];
  const gated = quota.filter((item) => (item.gate_windows || []).length);
  const count = (status) => gated.filter((item) => item.status === status).length;
  view.append(
    h("div", { class: "grid" },
      ...[
        ["Siap dipakai", String(count("ok")), "status ok"],
        ["Di bawah ambang", String(count("low")), "status low"],
        ["Belum terbaca", String(count("unsupported")), "meter belum lengkap"],
      ].map(([label, value, sub]) =>
        h("div", { class: "card", style: "cursor:default" },
          h("div", { class: "stat-value", text: value }),
          h("div", { class: "stat-label", text: label }),
          h("div", { class: "stat-sub", text: sub })))));

  const table = h("table", { class: "table" },
    h("thead", {}, h("tr", {},
      h("th", {}, "Model / engine"), h("th", {}, "Peran"), h("th", {}, "Jendela & sisa"),
      h("th", {}, "Reset"), h("th", {}, "Status"), h("th", {}, "Catatan"))));
  const body = h("tbody");
  gated.forEach((item) => {
    const windows = (item.windows || []).map((window) => {
      const pct = window.remaining_fraction == null ? "—" : `${(window.remaining_fraction * 100).toFixed(1)}%`;
      return `${window.window}: ${pct}`;
    }).join(" · ") || "—";
    const reset = (item.windows || [])
      .map((window) => window.reset_at)
      .filter(Boolean)
      .map((value) => new Date(value).toLocaleString("id-ID"))
      .join(" · ") || "—";
    const badge = item.status === "ok" ? "ok" : item.status === "low" ? "paused" : "draft";
    body.append(h("tr", {},
      h("td", {}, h("strong", { text: item.engine_key }), h("div", { class: "card-slug", text: item.model || "" })),
      h("td", { text: item.role || "—" }),
      h("td", { text: windows }),
      h("td", { text: reset }),
      h("td", {}, h("span", { class: `badge ${badge}` }, item.status)),
      h("td", { class: "card-desc", text: item.reason || "" })));
  });
  table.append(body);
  view.append(h("div", { class: "panel", style: "margin-top:16px" },
    h("div", { class: "section-head" }, h("h2", {}, "Sisa kuota per model", h("span", { class: "count", text: `· ${gated.length} model` }))),
    table,
    h("p", { class: "card-desc", text: "Angka dibaca dari tabel engine_quota. Model tanpa quota_gate atau tanpa meter tidak dipakai untuk routing otomatis." })));
}

function renderCapabilities(view) {
  const caps = state.capabilities || {};
  ensureMcp();
  view.append(
    h(
      "div",
      { class: "grid" },
      h(
        "div",
        { class: "panel" },
        h(
          "div",
          { class: "section-head" },
          h("h2", {}, "Skills"),
          h("span", { class: "count", text: `· ${(caps.skills || []).length}` })
        ),
        capabilityFolders(caps.skills, "skills", "Belum ada skill terpasang.")
      ),
      h(
        "div",
        { class: "panel" },
        h(
          "div",
          { class: "section-head" },
          h("h2", {}, "MCP"),
          h("span", { class: "count", text: `· ${(caps.mcp || []).length}` })
        ),
        capabilityFolders(caps.mcp, "mcp", "Belum ada server MCP.")
      ),
      h(
        "div",
        { class: "panel" },
        h(
          "div",
          { class: "section-head" },
          h("h2", {}, "Rules"),
          h("span", { class: "count", text: `· ${(caps.rules || []).length}` })
        ),
        capabilityFiles(caps.rules, "rules", "Belum ada aturan.")
      )
    )
  );

  view.append(
    h(
      "div",
      { class: "panel", style: "margin-top:16px" },
      h(
        "div",
        { class: "section-head" },
        h("h2", {}, "MCP runtime", h("span", { class: "count", text: `· ${state.mcp.tools.length} tool` })),
        h("button", {
          class: "link-btn",
          onclick: () => ensureMcp(true),
          text: "Segarkan katalog",
        })
      ),
      state.mcp.loading
        ? h("p", { class: "card-desc", text: "Memuat katalog MCP…" })
        : null,
      state.mcp.errors.length
        ? h("p", {
            class: "form-note",
            text: `Sebagian server tidak terbaca: ${state.mcp.errors.join(" · ")}`,
          })
        : null,
      h(
        "div",
        { class: "chip-list", style: "margin-bottom:12px" },
        ...state.mcp.servers.map((server) =>
          h("span", {
            class: `badge ${server.enabled ? "ok" : "draft"}`,
            text: `${server.key} · ${server.tool_count} tool · ${server.transport}`,
          })
        ),
        state.mcp.servers.length ? null : h("span", { class: "count", text: "Belum ada server MCP." })
      ),
      state.mcp.tools.length
        ? h(
            "table",
            { class: "table" },
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", {}, "Tool"),
                h("th", {}, "Deskripsi"),
                h("th", {}, "Argumen"),
                h("th", {}, "")
              )
            ),
            h(
              "tbody",
              {},
              ...state.mcp.tools.map((tool) =>
                h(
                  "tr",
                  {},
                  h("td", {}, h("strong", { text: `${tool.server}.${tool.name}` })),
                  h("td", { text: tool.description || "—" }),
                  h("td", { text: mcpArgs(tool) }),
                  h(
                    "td",
                    {},
                    h("button", {
                      class: "link-btn",
                      onclick: () => callMcpTool(tool),
                      text: "Panggil",
                    })
                  )
                )
              )
            )
          )
        : null,
      state.mcp.result
        ? h("pre", { class: "prompt-box", style: "margin-top:12px", text: state.mcp.result })
        : null
    )
  );

  view.append(
    h("div", { class: "section-head", style: "margin-top:24px" },
      h("h2", {}, "Engines & Registry"),
      h("button", {
        class: "link-btn",
        text: "Buka Usage Model",
        onclick: () => { state.view = "usage"; render(); },
      }))
  );
  renderEngines(view);
}

function mcpArgs(tool) {
  const properties = tool.input_schema && tool.input_schema.properties;
  if (!properties || typeof properties !== "object") return "—";
  const keys = Object.keys(properties);
  return keys.length ? keys.join(", ") : "—";
}

async function ensureMcp(force = false) {
  if (state.mcp.loading) return;
  if (!force && state.mcp.loaded) return;
  state.mcp.loading = true;
  try {
    const [servers, tools] = await Promise.all([
      api("/api/mcp/servers"),
      api(`/api/mcp/tools${force ? "?refresh=1" : ""}`),
    ]);
    state.mcp.servers = servers.servers || [];
    state.mcp.errors = [...(servers.errors || []), ...(tools.errors || [])];
    state.mcp.tools = tools.tools || [];
    state.mcp.loaded = true;
  } catch (error) {
    state.mcp.errors = [error.message];
    // Jangan mencoba ulang di setiap render; operator bisa menekan Segarkan.
    state.mcp.loaded = true;
  } finally {
    state.mcp.loading = false;
  }
  if (state.view === "capabilities") render();
  if (!$("#drawer").classList.contains("hidden") && state.selectedId) {
    const waker = state.wakers.find((item) => item.id === state.selectedId);
    if (waker) renderDrawerBody(waker);
  }
}

async function callMcpTool(tool) {
  const raw = prompt(`Argumen JSON untuk ${tool.server}.${tool.name}`, "{}");
  if (raw === null) return;
  let args;
  try {
    args = JSON.parse(raw);
  } catch {
    toast("Argumen bukan JSON yang sah", true);
    return;
  }
  try {
    const result = await api("/api/mcp/call", {
      method: "POST",
      body: JSON.stringify({
        server: tool.server,
        tool: tool.name,
        arguments: args,
      }),
    });
    state.mcp.result = `${tool.server}.${tool.name}\n\n${result.text}`;
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadMemoryScopes() {
  try {
    const data = await api("/api/memory/scopes");
    state.memoryScopes = data.scopes || [];
  } catch (error) {
    toast(error.message, true);
  }
}

async function openMemoryScope(scope) {
  try {
    const data = await api(`/api/memory/files?scope=${encodeURIComponent(scope)}`);
    state.memoryFiles = data.files || [];
    state.memoryScope = scope;
    state.memoryFile = null;
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function openMemoryFile(path) {
  try {
    state.memoryFile = await api(`/api/memory/file?path=${encodeURIComponent(path)}`);
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderMemory(view) {
  view.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Scope memori", h("span", { class: "count", text: `· ${state.memoryScopes.length}` })),
      h(
        "button",
        { class: "btn", onclick: () => loadMemoryScopes().then(render) },
        "Muat ulang"
      )
    ),
    h("p", {
      class: "card-note",
      style: "color:var(--faint);font-size:12px;margin:0 0 12px",
      text: "Read-only. MEMORY.md dan lifecycle tetap dijalankan tool station; console tidak menulis memori.",
    })
  );

  const grid = h("div", { class: "grid" });
  state.memoryScopes.forEach((scope) => {
    grid.append(
      h(
        "div",
        {
          class: "card",
          role: "button",
          tabindex: "0",
          onclick: () => openMemoryScope(scope.scope),
          onkeydown: (event) => activateCard(event, () => openMemoryScope(scope.scope)),
        },
        h("div", { class: "stat-value", text: String(scope.files) }),
        h("div", { class: "stat-label", text: scope.scope }),
        h("div", {
          class: "stat-sub",
          text: `${Math.round((scope.bytes || 0) / 1024)} KB · ${timeAgo(scope.updated)}`,
        })
      )
    );
  });
  view.append(grid);

  if (!state.memoryScope) return;

  view.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:20px" },
      h("h2", {}, `Berkas · ${state.memoryScope}`),
      h(
        "button",
        {
          class: "link-btn",
          onclick: () => {
            state.memoryScope = null;
            state.memoryFiles = [];
            state.memoryFile = null;
            render();
          },
        },
        "Tutup"
      )
    )
  );

  if (!state.memoryFiles.length) {
    view.append(
      h("div", { class: "empty" }, h("strong", { text: "Tidak ada berkas di scope ini" }))
    );
  } else {
    const body = h("tbody");
    state.memoryFiles.forEach((file) => {
      body.append(
        h(
          "tr",
          { onclick: () => openMemoryFile(file.path) },
          h("td", { text: file.name }),
          h("td", { text: `${Math.round((file.bytes || 0) / 1024)} KB` }),
          h("td", { text: timeAgo(file.modified) })
        )
      );
    });
    view.append(
      h(
        "div",
        { class: "panel" },
        h(
          "table",
          { class: "table" },
          h(
            "thead",
            {},
            h("tr", {}, ...["Berkas", "Ukuran", "Diubah"].map((label) => h("th", {}, label)))
          ),
          body
        )
      )
    );
  }

  if (state.memoryFile) {
    view.append(
      h(
        "div",
        { class: "panel", style: "margin-top:16px" },
        h(
          "div",
          { class: "section-head" },
          h("h2", {}, state.memoryFile.path),
          h(
            "button",
            {
              class: "link-btn",
              onclick: () => {
                state.memoryFile = null;
                render();
              },
            },
            "Tutup"
          )
        ),
        h("p", {
          class: "card-desc",
          text: `${Math.round((state.memoryFile.bytes || 0) / 1024)} KB · ${formatWhen(
            state.memoryFile.modified
          )}${state.memoryFile.truncated ? " · dipotong 200k karakter" : ""}`,
        }),
        h("pre", {
          class: "prompt-box",
          style: "max-height:520px",
          text: state.memoryFile.content,
        })
      )
    );
  }
}

async function loadKnowledge() {
  try {
    const data = await api("/api/knowledge");
    state.knowledge = data.knowledge || [];
  } catch (error) {
    toast(error.message, true);
  }
}

async function addKnowledge() {
  const title = $("#k-title") ? $("#k-title").value.trim() : "";
  const content = $("#k-content") ? $("#k-content").value.trim() : "";
  const tags = $("#k-tags") ? $("#k-tags").value.trim() : "";
  const scope = $("#k-scope") ? $("#k-scope").value : "";
  if (!title || !content) {
    toast("Judul dan isi wajib diisi", true);
    return;
  }
  try {
    await api("/api/knowledge", {
      method: "POST",
      body: JSON.stringify({
        title,
        content,
        tags,
        waker_id: scope || null,
      }),
    });
    toast("Pengetahuan disimpan");
    $("#k-title").value = "";
    $("#k-content").value = "";
    $("#k-tags").value = "";
    await loadKnowledge();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteKnowledge(id) {
  if (!confirm("Hapus entri pengetahuan ini?")) return;
  try {
    await api(`/api/knowledge/${id}`, { method: "DELETE" });
    toast("Entri dihapus");
    await loadKnowledge();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function reindexKnowledge() {
  try {
    const result = await api("/api/knowledge/reindex", { method: "POST" });
    toast(`Embedding dibuat: ${result.embedded}/${result.requested}`);
    await loadKnowledge();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function runKnowledgeSearch() {
  const query = $("#k-query") ? $("#k-query").value.trim() : "";
  if (!query) return;
  try {
    const data = await api("/api/knowledge/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    state.knowledgeQuery = query;
    state.knowledgeResults = data.results || [];
    state.knowledgeMode = data.mode || "keyword";
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderKnowledge(view) {
  view.append(
    h(
      "div",
      { class: "panel" },
      h("div", { class: "section-head" }, h("h2", {}, "Tambah pengetahuan")),
      h(
        "div",
        { class: "field" },
        h("label", {}, "Judul"),
        h("input", { id: "k-title", placeholder: "mis. Kebijakan cuti" })
      ),
      h(
        "div",
        { class: "field", style: "margin-top:8px" },
        h("label", {}, "Isi"),
        h("textarea", {
          id: "k-content",
          rows: "4",
          placeholder: "Teks yang akan dicari dan disisipkan ke prompt worker.",
        })
      ),
      h(
        "div",
        { class: "grid-2", style: "margin-top:8px" },
        h(
          "div",
          { class: "field" },
          h("label", {}, "Tag"),
          h("input", { id: "k-tags", placeholder: "hr, cuti" })
        ),
        h(
          "div",
          { class: "field" },
          h("label", {}, "Berlaku untuk"),
          h(
            "select",
            { id: "k-scope" },
            h("option", { value: "" }, "Semua worker"),
            ...state.wakers.map((waker) =>
              h("option", { value: waker.id }, waker.name)
            )
          )
        )
      ),
      h(
        "div",
        { class: "form-actions" },
        h("button", { class: "btn primary", onclick: addKnowledge }, "Simpan")
      ),
      h("p", {
        class: "form-note",
        text: "Pencarian berbasis kata kunci (bukan embedding). Tiga hasil teratas otomatis disisipkan ke prompt worker.",
      })
    )
  );

  view.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:20px" },
      h("h2", {}, "Pencarian"),
      h(
        "div",
        { class: "row-actions" },
        h("span", { class: "count", text: `· ${state.knowledge.length} entri` }),
        h("button", { class: "btn", onclick: reindexKnowledge }, "Reindex")
      )
    ),
    h(
      "div",
      { class: "row-actions" },
      h("input", {
        id: "k-query",
        placeholder: "Cari kata kunci…",
        value: state.knowledgeQuery,
        style: "flex:1",
      }),
      h("button", { class: "btn", onclick: runKnowledgeSearch }, "Cari")
    )
  );

  if (state.knowledgeResults) {
    const panel = h("div", { class: "panel", style: "margin-top:12px" });
    panel.append(
      h("p", {
        class: "card-desc",
        text: `Mode: ${state.knowledgeMode === "vector" ? "vektor (semantik)" : "kata kunci"}`,
      })
    );
    if (!state.knowledgeResults.length) {
      panel.append(h("p", { class: "card-desc", text: "Tidak ada yang cocok." }));
    } else {
      state.knowledgeResults.forEach((result) => {
        panel.append(
          h("div", { style: "margin-bottom:10px" },
            h("strong", { text: result.title }),
            h("pre", { class: "prompt-box process-box", text: result.snippet })
          )
        );
      });
    }
    view.append(panel);
  }

  view.append(
    h("div", { class: "section-head", style: "margin-top:20px" }, h("h2", {}, "Entri"))
  );
  if (!state.knowledge.length) {
    view.append(
      h("div", { class: "empty" }, h("strong", { text: "Belum ada pengetahuan" }))
    );
    return;
  }

  const body = h("tbody");
  state.knowledge.forEach((entry) => {
    body.append(
      h(
        "tr",
        {},
        h(
          "td",
          {},
          h("strong", { text: entry.title }),
          h("div", { class: "card-slug", text: entry.tags || "—" }),
          entry.has_embedding
            ? h("span", { class: "badge ok", text: "vektor" })
            : h("span", { class: "badge draft", text: "kata kunci" })
        ),
        h("td", { text: entry.waker_name || "semua worker" }),
        h("td", { text: `${Math.round((entry.size || 0) / 1024)} KB` }),
        h("td", { text: formatWhen(entry.created_at) }),
        h(
          "td",
          {},
          h("button", {
            class: "link-btn",
            onclick: () => deleteKnowledge(entry.id),
            text: "Hapus",
          })
        )
      )
    );
  });
  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h("tr", {}, ...["Judul", "Lingkup", "Ukuran", "Dibuat", "Aksi"].map((label) => h("th", {}, label)))
        ),
        body
      )
    )
  );
}

function renderWakers(view) {
  const query = state.query.trim().toLowerCase();
  const items = state.wakers.filter((w) => {
    if (!query) return true;
    return [w.name, w.slug, w.engine_key, w.model || ""]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  view.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Semua worker", h("span", { class: "count", text: `· ${items.length}` }))
    )
  );

  if (state.wakers.length === 0) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "＋" }),
        h("strong", { text: "Belum ada worker" }),
        h("p", {
          text: "Worker adalah pegawai digital: satu nama, satu engine, satu workspace. Mulai dari satu pekerjaan yang paling sering kamu ulang.",
        }),
        h("button", { class: "btn primary", onclick: () => openModal(null) }, "Buat worker pertama")
      )
    );
    return;
  }

  if (items.length === 0) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("strong", { text: "Tidak ada hasil" }),
        h("p", { text: `Tidak ada worker yang cocok dengan “${state.query}”.` })
      )
    );
    return;
  }

  const grid = h("div", { class: "grid" });
  items.forEach((w) => grid.append(wakerCard(w)));
  view.append(grid);
}

function wakerCard(w) {
  const engine = state.engines.find((e) => e.key === w.engine_key);
  const kind = engine ? engine.kind : "unknown";
  return h(
    "div",
    {
      class: "card",
      role: "button",
      tabindex: "0",
      onclick: () => openDrawer(w.id),
      onkeydown: (event) => activateCard(event, () => openDrawer(w.id)),
    },
    h(
      "div",
      { class: "card-top" },
      workerAvatar(w),
      h(
        "div",
        { class: "card-id" },
        h("h3", { class: "card-name", text: w.name }),
        h("span", { class: "card-slug", text: `@${w.slug}` })
      )
    ),
    h("p", { class: "card-desc", text: w.description || "Belum ada deskripsi." }),
    h(
      "div",
      { class: "card-meta" },
      h("span", { class: `badge ${kind}` }, h("span", { class: "swatch" }), w.engine_key),
      w.model ? h("span", { class: "badge", text: w.model }) : null,
      w.reasoning_effort ? h("span", { class: "badge", text: w.reasoning_effort }) : null,
      w.context_window
        ? h("span", { class: "badge", text: `${Math.round(w.context_window / 1000)}K` })
        : null,
      w.approval_mode === "require"
        ? h("span", { class: "badge paused", text: "butuh izin" })
        : null,
      statusBadge(w.status)
    ),
    h(
      "div",
      { class: "card-foot" },
      h("span", { class: "path", text: w.workspace, title: w.workspace }),
      h("span", { text: timeAgo(w.updated_at) })
    )
  );
}

function insertChatCommand(text) {
  const input = document.querySelector(".chat-input");
  if (!input) return;
  if (input.disabled) {
    toast("Worker sedang bekerja; tunggu sampai selesai", true);
    return;
  }
  input.value = text;
  input.focus();
  input.dispatchEvent(new Event("input", { bubbles: true }));
  try {
    input.setSelectionRange(text.length, text.length);
  } catch {
    // textarea lama mungkin tidak mendukung setSelectionRange
  }
}

// Baris command di atas composer. Tombol hanya MENULIS ke input; operator
// tetap menekan Enter. Itu disengaja: worker load/recovery memuat konteks besar
// dan tidak boleh berangkat karena satu klik tak sengaja.
function chatCommandBar() {
  return h(
    "div",
    { class: "chat-commands" },
    h("span", { class: "chat-commands__label", text: "Command :" }),
    h("button", {
      class: "chat-command",
      type: "button",
      title: "Muat seluruh konteks AI-Station, registry model, MCP, dan status sekarang",
      onclick: () => insertChatCommand("worker load"),
      text: "Worker Load",
    }),
    h("button", {
      class: "chat-command",
      type: "button",
      title: "Recovery 40: baca ledger/SQL/handoff terakhir lalu susun resume",
      onclick: () => insertChatCommand("worker recovery"),
      text: "Worker Recovery",
    }),
    h("span", {
      class: "chat-commands__hint",
      text: "klik untuk masukkan ke kolom chat, lalu Enter",
    })
  );
}

function renderChat(view) {
  const restore = state.restoreChat;
  state.restoreChat = null;
  const waker = state.wakers.find((w) => w.id === state.selectedId);
  if (!waker) {
    if (state.wakers.length === 0) {
      view.append(
        h(
          "div",
          { class: "empty" },
          h("div", { class: "empty-icon", text: "◌" }),
          h("strong", { text: "Belum ada worker untuk diajak bicara" }),
          h("p", { text: "Buat worker dulu, lalu percakapan bisa dimulai dari sini." })
        )
      );
      return;
    }
    const available = state.wakers.filter((w) => w.chat_available !== false);
    const unavailable = state.wakers.filter((w) => w.chat_available === false);

    view.append(h("div", { class: "section-head" }, h("h2", {}, "Pilih worker")));
    const grid = h("div", { class: "grid" });
    available.forEach((w) =>
      grid.append(
        h(
          "div",
          {
            class: "card",
            role: "button",
            tabindex: "0",
            onclick: () => {
              state.selectedId = w.id;
              render();
            },
            onkeydown: (event) =>
              activateCard(event, () => {
                state.selectedId = w.id;
                render();
              }),
          },
          h(
            "div",
            { class: "card-top" },
            workerAvatar(w),
            h("div", { class: "card-id" }, h("h3", { class: "card-name", text: w.name }), h("span", { class: "card-slug", text: `@${w.slug}` }))
          )
        )
      )
    );
    if (!available.length) {
      grid.append(
        h("p", { class: "card-desc", text: "Tidak ada worker yang dibuka untuk sesi percakapan." })
      );
    }
    view.append(grid);

    if (unavailable.length) {
      view.append(
        h(
          "div",
          { class: "section-head", style: "margin-top:24px" },
          h("h2", {}, "Unavailable for working session"),
          h("span", { class: "count", text: `· ${unavailable.length}` })
        ),
        h("p", {
          class: "card-desc",
          text: "Worker ini dipanggil lewat automation, grup, atau @mention — bukan sesi percakapan langsung.",
        })
      );
      const unavailableGrid = h("div", { class: "grid" });
      unavailable.forEach((w) =>
        unavailableGrid.append(
          h(
            "div",
            { class: "card", style: "opacity:.6;cursor:default" },
            h(
              "div",
              { class: "card-top" },
              workerAvatar(w),
              h(
                "div",
                { class: "card-id" },
                h("h3", { class: "card-name", text: w.name }),
                h("span", { class: "card-slug", text: `@${w.slug} · tidak untuk sesi` })
              )
            )
          )
        )
      );
      view.append(unavailableGrid);
    }
    return;
  }

  const engine = state.engines.find((e) => e.key === waker.engine_key);
  const messages = state.chat[waker.id] || (state.chat[waker.id] = []);
  const busy = !!state.runBusy[waker.id] || messages.some((message) => message.pending);
  ensureChatHistory(waker);
  ensureCatalog();
  ensureAgyModels();
  ensureOllamaModels();

  view.classList.add("chat-view");

  const log = h("div", { class: "chat-body chat-log", "data-waker": waker.id });
  const thread = h("div", { class: "chat-thread" });
  if (messages.length === 0) {
    thread.append(
      h(
        "div",
        { class: "chat-note" },
        h("strong", { text: "Mulai percakapan" }),
        `Pesanmu dijalankan headless oleh ${waker.engine_key}. Keluaran muncul saat proses menulis ke stdout.`
      )
    );
  }
  // Pesan tanpa isi (sisa run yang tidak menghasilkan teks) hanya menyisakan
  // ruang kosong di transkrip; yang kosong sepenuhnya tidak ditampilkan.
  messages
    .filter(
      (message) =>
        (message.text && message.text.trim()) ||
        message.error ||
        (message.events && message.events.length) ||
        message.pending
    )
    .forEach((message, index) => thread.append(messageBubble(message, index)));
  log.append(thread);

  const activeRunId = activeRunFor(waker.id);
  const stopping = !!state.cancelling[waker.id];
  const input = h("textarea", {
    class: "chat-input",
    rows: "1",
    placeholder: busy
      ? "Worker sedang bekerja…"
      : `Tulis pesan untuk ${waker.name}… (Enter kirim, Shift+Enter baris baru)`,
    disabled: busy ? "disabled" : null,
  });
  // Selama worker bekerja, tombol Kirim yang sama menjadi Stop. Ini jalur
  // satu-satunya untuk menghentikan run yang sudah berjalan.
  const send = h(
    "button",
    {
      class: busy ? "btn danger" : "btn primary",
      type: busy ? "button" : "submit",
      disabled: busy && stopping ? "disabled" : null,
      title: busy ? "Hentikan worker yang sedang bekerja" : "Kirim pesan",
      onclick: busy
        ? (event) => {
            event.preventDefault();
            // Id bisa belum ada saat tombol digambar (POST masih berjalan);
            // baca ulang dari state pada saat klik.
            stopRun(waker, activeRunId || activeRunFor(waker.id));
          }
        : null,
    },
    stopping ? "Menghentikan…" : busy ? "Stop" : "Kirim"
  );
  const form = h(
    "form",
    {
      class: "chat-composer",
      onsubmit: (event) => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text || state.runBusy[waker.id]) return;
        input.value = "";
        sendMessage(waker, text);
      },
    },
    input,
    send
  );

  const autoGrow = () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  };
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  view.append(
    h(
      "div",
      { class: "chat-shell" },
      h(
        "div",
        { class: "chat-head" },
        // Jalan keluar dari percakapan: tanpa ini, setelah memilih worker tidak
        // ada cara kembali ke daftar atau pindah ke worker lain.
        h(
          "button",
          {
            class: "btn ghost",
            type: "button",
            title: "Kembali ke daftar worker",
            onclick: () => {
              state.selectedId = null;
              render();
            },
          },
          "←"
        ),
        workerAvatar(waker),
        h(
          "div",
          {},
          h("h2", { text: waker.name }),
          h("p", { text: wakerSubtitle(waker, engine) })
        ),
        chatModelControl(waker, engine),
        contextUsageBadge(waker),
        h("span", { style: "margin-left:auto" }, statusBadge(waker.status))
      ),
      log,
      h("div", { class: "chat-footer" }, chatCommandBar(), form)
    )
  );

  ensureChatLivePolling();
  requestAnimationFrame(() => {
    // Render ulang karena run selesai tidak boleh menarik pandangan yang
    // sedang membaca ke atas; posisinya dipulihkan apa adanya.
    if (restore && restore.wakerId === waker.id && !restore.follow) {
      log.scrollTop = restore.scrollTop;
    } else {
      log.scrollTop = log.scrollHeight;
    }
    // Fokus tidak boleh menggulir halaman: header dan sidebar harus tetap di tempat.
    try {
      input.focus({ preventScroll: true });
    } catch {
      input.focus();
    }
    window.scrollTo(0, 0);
  });
}

// Riwayat diambil dari daftar run, sehingga percakapan tidak hilang saat halaman
// dimuat ulang. Hanya diambil sekali per waker per sesi browser.
function runPromptLabel(run) {
  const content = (run && (run.content || run.prompt)) || "";
  const match = /Auto Pilot Task:\s*([^\n]+)/.exec(content);
  if (content.includes("[[auto-pilot-plan:") && match) {
    return `Auto Pilot Task · ${match[1].trim()}`;
  }
  return content;
}

function fillHistory(waker, runs) {
  if (!runs || !runs.length) return 0;
  const messages = state.chat[waker.id] || (state.chat[waker.id] = []);
  if (messages.length) return messages.length;
  let filled = 0;
  [...runs]
    .filter((run) => run.waker_id === waker.id)
    .reverse()
    .forEach((run) => {
      messages.push({ role: "user", text: runPromptLabel(run) });
      messages.push({
        role: "waker",
        text: run.output || "",
        error: run.status === "failed" ? run.error : null,
        events: run.events || [],
        runId: run.id,
        pending: run.status === "running" || run.status === "queued",
      });
      filled += 2;
    });
  return filled;
}

// Daftar run sengaja tidak membawa event (payload bisa besar). Untuk riwayat
// chat, ambil event penuh hanya dari beberapa run terakhir supaya Deep Thinking
// dan View Steps tetap ada setelah halaman dimuat ulang.
async function hydrateChatEvents(waker) {
  const messages = state.chat[waker.id] || [];
  const targets = messages
    .filter(
      (message) =>
        message.role === "waker" &&
        message.runId &&
        (!message.events || message.events.length === 0)
    )
    .slice(-12);
  await Promise.all(
    targets.map(async (message) => {
      try {
        const run = await api(`/api/runs/${message.runId}`);
        message.events = run.events || [];
        const ratio = contextRatioOf(run);
        if (ratio != null) {
          const summary = (state.runs || []).find((item) => item.id === message.runId);
          if (summary) summary.context_ratio = ratio;
          saveContextSnapshot(waker, { ratio, source: "engine", at: run.started_at });
        }
      } catch {
        // event bukan syarat untuk membaca jawaban; biarkan kosong
      }
    })
  );
}

let chatLiveTimer = null;
let chatLiveBusy = false;

function chatInputFocused() {
  const active = document.activeElement;
  return !!(active && active.classList && active.classList.contains("chat-input"));
}

// Ujung bawah hanya diikuti selama pandangan memang ada di sana. Begitu
// operator menggulir ke atas, stream yang terus datang tidak boleh menariknya
// kembali ke dasar.
function nearBottom(el, slack = 48) {
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight <= slack;
}

function applyLiveRun(waker, run) {
  const messages = state.chat[waker.id] || (state.chat[waker.id] = []);
  const pending = run.status === "running" || run.status === "queued";
  let index = messages.findIndex(
    (message) => message.role === "waker" && message.runId === run.id
  );
  if (index < 0) {
    // Gelembung optimistis dari composer belum punya runId bila event SSE
    // mendahului respons POST; adopsi gelembung itu, jangan bikin pesan kedua.
    const pendingIndex = messages.findIndex(
      (message) => message.role === "waker" && message.pending && !message.runId
    );
    if (pendingIndex >= 0) {
      messages[pendingIndex].runId = run.id;
      index = pendingIndex;
    }
  }
  if (pending) state.activeRuns[waker.id] = run.id;
  if (index < 0) {
    // Riwayat belum mengenal run ini (mis. dimulai oleh Auto Pilot di tab lain).
    messages.push({ role: "user", text: runPromptLabel(run) });
    messages.push({
      role: "waker",
      text: run.output || "",
      error: run.status === "failed" ? run.error : null,
      events: run.events || [],
      runId: run.id,
      pending,
    });
    index = messages.length - 1;
    if (state.view === "chat" && state.selectedId === waker.id) {
      if (chatInputFocused()) {
        // Jangan hapus draft yang sedang diketik; render setelah fokus lepas.
        setTimeout(() => {
          if (!chatInputFocused() && state.view === "chat" && state.selectedId === waker.id) render();
        }, 1600);
      } else {
        render();
      }
    }
  } else {
    const message = messages[index];
    message.text = run.output || message.text || "";
    message.events = run.events || message.events || [];
    message.error = run.status === "failed" ? run.error || "run gagal" : null;
    message.pending = pending;
    updateBubble(index, message.text, pending, message.events);
  }
  upsertRunSummary(run);
  refreshContextBadges();
  if (wakeyPanelOpenFor(waker.id)) renderWakey();
  if (!pending) {
    state.runBusy[waker.id] = false;
    if (state.activeRuns[waker.id] === run.id) delete state.activeRuns[waker.id];
    if (!chatInputFocused() && state.view === "chat" && state.selectedId === waker.id) {
      render();
    }
  }
}

// Chat juga harus hidup untuk run yang TIDAK dikirim dari composer ini:
// Auto Pilot, automation, atau tab lain. Tanpa poll ini layar hanya berubah
// setelah "Muat ulang" ditekan.
function ensureChatLivePolling() {
  if (chatLiveTimer) return;
  chatLiveTimer = setInterval(async () => {
    if (chatLiveBusy || state.view !== "chat" || !state.selectedId) return;
    // SSE adalah jalur utama; poll ini hanya jaring pemulihan.
    if (Date.now() - (state.lastStreamAt || 0) < 5000) return;
    const waker = state.wakers.find((item) => item.id === state.selectedId);
    // Jangan lewati poll hanya karena run ini milik kita: bila SSE tersendat,
    // runBusy membuat chat diam sementara Waker Console (poll sendiri) tetap
    // bergerak. applyLiveRun mencocokkan runId, jadi tidak ada duplikasi.
    if (!waker) return;
    chatLiveBusy = true;
    try {
      const data = await api(`/api/runs?waker=${encodeURIComponent(waker.id)}`);
      const runs = data.runs || [];
      const active = runs.find(
        (run) => run.status === "running" || run.status === "queued"
      );
      const latest = active || runs[0];
      if (!latest) return;
      const seen = state.chatLiveSeen[waker.id];
      if (!active && seen === latest.id) return;
      const run = await api(`/api/runs/${latest.id}`);
      state.chatLiveSeen[waker.id] = run.id;
      applyLiveRun(waker, run);
    } catch {
      // percobaan berikutnya menyusul; jangan mengosongkan layar
    } finally {
      chatLiveBusy = false;
    }
  }, 5000);
}

async function ensureChatHistory(waker) {
  if (state.chatLoaded[waker.id]) return;
  state.chatLoaded[waker.id] = true;
  let filled = fillHistory(waker, state.bootRuns);
  if (!filled) {
    try {
      const data = await api(`/api/runs?waker=${encodeURIComponent(waker.id)}`);
      filled = fillHistory(waker, data.runs || []);
    } catch {
      // Riwayat bersifat pelengkap; kegagalan memuatnya tidak boleh menghalangi chat.
    }
  }
  if (filled) await hydrateChatEvents(waker);
  refreshContextBadges();
  if (state.view === "chat" && state.selectedId === waker.id) render();
}

function formatProcess(events) {
  if (!events || !events.length) return "";
  return events
    .slice(-80)
    .map((event) => `${String(event.kind).padEnd(12)} ${event.text}`)
    .join("\n");
}

function processElapsed(events, pending) {
  if (!events || !events.length) return 0;
  const first = Date.parse(events[0].at || "");
  if (!Number.isFinite(first)) return 0;
  const last = pending ? Date.now() : Date.parse(events[events.length - 1].at || "") || Date.now();
  return Math.max(0, Math.floor((last - first) / 1000));
}

function stepIcon(event) {
  if (event.kind === "tool_finished") return /^completed/i.test(event.text || "") ? "✓" : "✕";
  if (event.kind === "tool_started") return "▷";
  if (event.kind === "result") return "✓";
  if (event.kind === "context") return "◔";
  if (event.kind === "stderr") return "!";
  if (event.kind === "status") return /^error/i.test(event.text || "") ? "✕" : "•";
  return "•";
}

function eventClock(event) {
  const at = Date.parse((event && event.at) || "");
  if (!Number.isFinite(at)) return "";
  return new Date(at).toLocaleTimeString("id-ID", { hour12: false });
}

// Jejak proses yang bisa dibaca manusia: thinking, langkah tool, status, dan
// hasil. `delta`/`output` tetap menjadi isi jawaban dan tidak diulang di sini.
function processPanel(events, pending, wasThinkingOpen, wasStepsOpen) {
  const all = events || [];
  const thinking = all
    .filter((event) => event.kind === "thinking" && event.text)
    .map((event) => event.text);
  const steps = all.filter((event) =>
    ["tool_started", "tool_finished", "status", "result", "context", "stderr"].includes(event.kind)
  );
  const startAt = all.length ? Date.parse(all[0].at || "") : 0;
  const liveStart = pending && Number.isFinite(startAt) ? String(startAt) : null;
  const wrap = h("div", { class: "msg-process-wrap" });
  if (pending) {
    const lastAt = Date.parse((all[all.length - 1] && all[all.length - 1].at) || "") || Date.now();
    wrap.dataset.last = String(lastAt);
  }
  if (!thinking.length && !steps.length) {
    if (pending) {
      wrap.append(
        h(
          "div",
          { class: "msg-live-step running is-live", "data-start": liveStart },
          h("span", { class: "step-spinner" }),
          h("span", { class: "step-text", text: "Bekerja…" }),
          h("span", { class: "step-elapsed", text: `${processElapsed(all, true)}s` })
        )
      );
    }
    return wrap;
  }

  if (thinking.length) {
    const box = h("details", { class: "msg-thinking" });
    if (wasThinkingOpen) box.open = true;
    // Fragmen `reasoning_content` adalah potongan kata yang menyambung, bukan
    // paragraf terpisah; memberi pemisah di antaranya memecah kalimat.
    box.append(
      h("summary", {}, "Deep Thinking"),
      h("pre", { class: "thinking-box", text: thinking.join("") })
    );
    wrap.append(box);
  }

  if (steps.length) {
    const list = h("ol", { class: "step-list" });
    steps.slice(-120).forEach((event) => {
      list.append(
        h(
          "li",
          {
            class: `step-row ${event.kind}`,
            "data-bad":
              event.kind === "status" && /^error/i.test(event.text || "") ? "1" : null,
          },
          h("span", { class: "step-icon", text: stepIcon(event) }),
          h("span", { class: "step-text", text: event.text || "" }),
          h("span", { class: "step-time", text: eventClock(event) })
        )
      );
    });
    const box = h("details", { class: "msg-steps", "data-start": liveStart });
    if (wasStepsOpen) box.open = true;
    box.append(
      h(
        "summary",
        {},
        `View ${steps.length} Steps · `,
        h("span", { class: "steps-elapsed", text: `${processElapsed(all, pending)}s` })
      ),
      list
    );
    wrap.append(box);
  }

  const latest = steps[steps.length - 1];
  if (latest) {
    wrap.append(
      h(
        "div",
        {
          class: `msg-live-step ${latest.kind}${pending ? " is-live" : ""}`,
          "data-start": liveStart,
        },
        h("span", { class: "step-icon", text: stepIcon(latest) }),
        h("span", { class: "step-text", text: latest.text || "" }),
        pending ? h("span", { class: "step-spinner" }) : null,
        pending
          ? h("span", { class: "step-elapsed", text: `${processElapsed(all, true)}s` })
          : null,
        h("span", { class: "step-time", text: eventClock(latest) })
      )
    );
  } else if (pending) {
    wrap.append(
      h(
        "div",
        { class: "msg-live-step running is-live", "data-start": liveStart },
        h("span", { class: "step-spinner" }),
        h("span", { class: "step-text", text: "Bekerja…" }),
        h("span", { class: "step-elapsed", text: `${processElapsed(all, true)}s` })
      )
    );
  }
  if (pending) {
    const last = all[all.length - 1];
    const lastAt = Date.parse((last && last.at) || "");
    if (Number.isFinite(lastAt)) {
      const idle = Math.max(0, Math.round((Date.now() - lastAt) / 1000));
      if (idle >= 45) {
        wrap.append(
          h("div", {
            class: "msg-idle-warning",
            text: `Belum ada event baru selama ${idle}s — worker masih berjalan, sedang berpikir, atau macet.`,
          })
        );
      }
    }
  }
  return wrap;
}

function messageBubble(message, index) {
  const bubble = h(
    "div",
    { class: `msg ${message.role === "user" ? "user" : "waker"}`, "data-msg": String(index) },
    h("div", { class: "msg-role", text: message.role === "user" ? "Kamu" : "Worker" }),
    (() => {
      const node = h("div", { class: "msg-text" });
      appendRichText(node, message.text || (message.pending ? "…" : ""));
      return node;
    })()
  );
  if (message.error) bubble.append(h("div", { class: "msg-error", text: message.error }));

  // Jejak proses: Deep Thinking, View Steps, dan langkah terakhir yang terlihat
  // tanpa harus membuka fold. Ini bukan karangan; isinya event run sebenarnya.
  if (message.events && message.events.length) {
    bubble.append(processPanel(message.events, message.pending, false, false));
  } else if (message.pending) {
    bubble.append(processPanel([], true, false, false));
  }
  return bubble;
}

// ------------------------------------------------------- konteks @Project
// `@MeshCleanRoom` maupun `@Project MeshCleanRoom` menyertakan dokumen project
// ke pesan worker. Isinya berasal dari site/project-context/<slug>.json yang
// generator-nya sama dengan MCP project_context (redacted, tanpa path absolut).

const PROJECT_CODE = /(^|[\s(])@([A-Za-z][A-Za-z0-9_-]{1,40})/g;

function projectMentions(text) {
  const names = [];
  const push = (raw) => {
    const name = String(raw || "").replace(/^@/, "").trim();
    if (!name || /^project$/i.test(name)) return;
    if (!names.some((item) => item.toLowerCase() === name.toLowerCase())) names.push(name);
  };
  const source = String(text || "");
  const phrase = /(^|[\s(])@project\s+([A-Za-z][A-Za-z0-9_-]{1,40})/gi;
  let match;
  while ((match = phrase.exec(source))) push(match[2]);
  PROJECT_CODE.lastIndex = 0;
  while ((match = PROJECT_CODE.exec(source))) push(match[2]);
  return names.slice(0, 4);
}

// Batas aman satu argumen CLI (Linux MAX_ARG_STRLEN = 131.072 byte). Konteks
// dipotong pada batas dokumen, bukan di tengah kalimat; sisanya jadi cuplikan.
const PROJECT_CONTEXT_BYTES = 100_000;
const PROJECT_EXCERPT_CHARS = 320;

function projectContextText(pack) {
  const project = pack.project || {};
  const budget = pack.budget || {};
  const files = pack.files || [];
  const encoder = new TextEncoder();
  const head = [
    `## Konteks @Project ${project.codename || ""} — ${project.name || ""}`,
    `${project.workspaces ?? "?"} workspace · ${files.length} dokumen · ` +
      `${budget.returned_chars ?? "?"} dari ${budget.total_chars ?? "?"} karakter. ` +
      "Dokumen ini dibaca dari registry AI Station; jangan mengarang isi atau path lain.",
  ].join("\n");
  const parts = [];
  const duplicates = new Map();
  const trimmed = [];
  let used = encoder.encode(head).length;

  const push = (section) => {
    const cost = encoder.encode(section).length + 2;
    if (used + cost > PROJECT_CONTEXT_BYTES) return false;
    parts.push(section);
    used += cost;
    return true;
  };

  for (const file of files) {
    const label = file.label;
    const content = file.mode === "full" ? file.content || "" : "";
    if (content) {
      if (duplicates.has(content)) {
        push(`### ${label} (isi identik dengan ${duplicates.get(content)}; tidak diulang)`);
        continue;
      }
      if (push(`### ${label} (${file.bytes} byte)\n${content}`)) {
        duplicates.set(content, label);
      } else {
        trimmed.push({ label, excerpt: content.slice(0, PROJECT_EXCERPT_CHARS) });
      }
      continue;
    }
    if (file.mode === "outline" && file.outline) {
      if (!push(`### ${label} (outline; ${file.bytes} byte)\n${file.outline}`)) {
        trimmed.push({ label, excerpt: file.outline.slice(0, PROJECT_EXCERPT_CHARS) });
      }
      continue;
    }
    push(`### ${label} (tidak tersedia)`);
  }

  if (trimmed.length) {
    parts.push(
      "### Dokumen di luar anggaran inline\n" +
        trimmed
          .map((row) => `- ${row.label} (cuplikan): ${row.excerpt.replace(/\s+/g, " ")}…`)
          .join("\n") +
        `\nKonteks lengkap: ~/.ai-station/site/project-context/${project.slug}.json`
    );
  }
  return `${head}\n\n${parts.join("\n\n")}`;
}

async function projectContextPacks(names) {
  const registry = await api("/site/projects.json", { cache: "no-store" });
  const rows = (registry && registry.projects) || [];
  const sections = [];
  for (const name of names) {
    const wanted = name.toLowerCase();
    const row = rows.find(
      (project) =>
        String(project.slug || "").toLowerCase() === wanted ||
        String(project.codename || "").replace(/^@/, "").toLowerCase() === wanted
    );
    if (!row) {
      const available = rows.map((project) => project.codename).filter(Boolean).join(", ");
      throw new Error(`@Project "${name}" tidak terdaftar. Tersedia: ${available}`);
    }
    const pack = await api(`/site/project-context/${encodeURIComponent(row.slug)}.json`, {
      cache: "no-store",
    });
    sections.push(projectContextText(pack));
  }
  return sections;
}

/// Run aktif milik worker ini. Peta `activeRuns` diisi saat POST diterima;
/// daftar run menjadi cadangan untuk run yang dimulai dari tab lain.
function activeRunFor(wakerId) {
  if (state.activeRuns[wakerId]) return state.activeRuns[wakerId];
  const run = (state.runs || []).find(
    (item) =>
      item.waker_id === wakerId &&
      (item.status === "running" || item.status === "queued")
  );
  return run ? run.id : null;
}

async function stopRun(waker, runId) {
  if (state.cancelling[waker.id]) return;
  if (!runId) {
    toast("Run belum tercatat; coba lagi sebentar", true);
    return;
  }
  state.cancelling[waker.id] = true;
  render();
  try {
    await api(`/api/runs/${runId}/cancel`, { method: "POST" });
    toast("Perintah stop dikirim");
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.cancelling[waker.id] = false;
    if (state.view === "chat" && state.selectedId === waker.id) render();
  }
}

async function sendMessage(waker, text, surface = "chat", options = {}) {
  const list = state.chat[waker.id] || (state.chat[waker.id] = []);
  list.push({ role: "user", text });
  const assistant = { role: "waker", text: "", pending: true };
  list.push(assistant);
  state.runBusy[waker.id] = true;
  const paint = () => {
    if (surface === "wakey") state.wakeyForceBottom = true;
    else state.forceChatBottom = waker.id;
    if (surface === "wakey") renderWakey();
    else render();
  };
  paint();

  let content = options.contextPrefix ? `${options.contextPrefix}${text}` : text;
  const command = /^worker\s+(load|recovery)$/i.exec(text.trim());
  if (command) {
    assistant.text = "Memuat konteks AI Station…";
    paint();
    try {
      const pack = await api("/api/worker/context", {
        method: "POST",
        body: JSON.stringify({ mode: command[1].toLowerCase(), limit: 40 }),
      });
      if (!pack || !pack.context) throw new Error("context pack kosong");
      content =
        `${pack.context}\n\n---\n` +
        `Operator command: ${text}\n` +
        "Gunakan konteks di atas sebagai keadaan nyata. Jangan mengaku model terhubung " +
        "tanpa bukti READY. Jika ada yang DEGRADED/BLOCKED, sebutkan alasannya dan lanjutkan " +
        "dengan yang READY. Untuk pekerjaan besar, baca Planning.md dan kerjakan per fase.";
      assistant.text = "";
      paint();
    } catch (error) {
      assistant.pending = false;
      assistant.error = `Gagal memuat konteks: ${error.message}`;
      state.runBusy[waker.id] = false;
      paint();
      return;
    }
  }

  const mentions = projectMentions(text);
  if (mentions.length) {
    assistant.text = `Memuat konteks @Project: ${mentions.join(", ")}…`;
    paint();
    let packs;
    try {
      packs = await projectContextPacks(mentions);
    } catch (error) {
      assistant.pending = false;
      assistant.error = `Gagal memuat konteks project: ${error.message}`;
      state.runBusy[waker.id] = false;
      paint();
      return;
    }
    content =
      `${packs.join("\n\n---\n\n")}\n\n---\n` +
      "Pesan operator menyebut project di atas. Pakai dokumen yang dimuat sebagai " +
      `sumber kebenaran; jangan mengarang path atau isi yang tidak ada.\n\n${content}`;
    assistant.text = "";
    paint();
  }

  try {
    const started = await api(`/api/wakers/${waker.id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    if (started.status === "pending_approval") {
      assistant.pending = false;
      assistant.text =
        "Menunggu persetujuan manusia. Buka halaman Approvals untuk menyetujui atau menolak.";
      state.runBusy[waker.id] = false;
      loadApprovals();
      paint();
      return;
    }
    // runId ditempel SEBELUM event SSE pertama tiba. Tanpa ini SSE tidak
    // mengenali gelembung optimistis dan menambahkan pasangan pesan kedua.
    assistant.runId = started.run_id;
    state.activeRuns[waker.id] = started.run_id;
    pollRun(waker, started.run_id, assistant, surface);
  } catch (error) {
    assistant.pending = false;
    assistant.error = error.message;
    state.runBusy[waker.id] = false;
    delete state.activeRuns[waker.id];
    paint();
  }
}

async function pollRun(waker, runId, assistant, surface = "chat") {
  const index = (state.chat[waker.id] || []).indexOf(assistant);
  for (let tick = 0; tick < 1200; tick += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    let run;
    try {
      run = await api(`/api/runs/${runId}`);
    } catch (error) {
      assistant.error = error.message;
      break;
    }
    assistant.text = run.output || "";
    assistant.events = run.events || [];
    upsertRunSummary(run);
    refreshContextBadges();
    if (surface === "wakey") renderWakey();
    else updateBubble(index, assistant.text, true, assistant.events);
    if (run.status !== "running") {
      assistant.pending = false;
      if (run.status === "failed") assistant.error = run.error || "run gagal";
      break;
    }
  }
  state.runBusy[waker.id] = false;
  if (state.activeRuns[waker.id] === runId) delete state.activeRuns[waker.id];
  try {
    const data = await api("/api/runs");
    state.runs = data.runs || [];
    refreshContextBadges();
  } catch {
    // daftar run akan disegarkan lagi saat halaman Runs dibuka
  }
  if (surface === "wakey") renderWakey();
  else render();
}

function updateBubble(index, text, pending, events) {
  if (index < 0) return;
  const bubble = document.querySelector(`[data-msg="${index}"]`);
  const node = bubble && bubble.querySelector(".msg-text");
  if (!node) return;
  const log = node.closest(".chat-log");
  // Dibaca sebelum DOM berubah: setelah panel diganti, tinggi isinya sudah
  // lain dan posisi lama tidak bisa dipakai untuk menilai lagi.
  const follow = nearBottom(log);
  node.textContent = text || (pending ? "…" : "");

  if (bubble) {
    const oldThinking = bubble.querySelector(".msg-thinking");
    const oldSteps = bubble.querySelector(".msg-steps");
    const wasThinkingOpen = !!(oldThinking && oldThinking.open);
    const wasStepsOpen = !!(oldSteps && oldSteps.open);
    const oldPanel = bubble.querySelector(".msg-process-wrap");
    const panel = processPanel(events, pending, wasThinkingOpen, wasStepsOpen);
    if (oldPanel) oldPanel.replaceWith(panel);
    else bubble.append(panel);
  }

  if (log && follow) log.scrollTop = log.scrollHeight;
}

async function loadApprovals() {
  try {
    const data = await api("/api/approvals");
    state.approvals = data.approvals || [];
  } catch (error) {
    toast(error.message, true);
  }
}

async function decideApproval(id, decision) {
  try {
    const result = await api(`/api/approvals/${id}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
    toast(
      decision === "approved"
        ? `Disetujui${result.run_id ? " · " + result.run_id : ""}`
        : "Permintaan ditolak"
    );
    if (decision === "approved") {
      try {
        const data = await api("/api/runs");
        state.runs = data.runs || [];
      } catch {
        // riwayat run akan dimuat saat halaman Runs dibuka
      }
    }
    await loadApprovals();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function decideInputFocused() {
  return !!document.querySelector("#view textarea:focus, #view input:focus");
}

function updateModeSwitch() {
  const select = document.getElementById("mode-select");
  if (!select) return;
  const mode = state.decisionMode?.mode || "";
  select.disabled = !mode;
  if (mode) select.value = mode;
  select.title =
    mode === "AUTO"
      ? "AUTO: @Decider memutuskan; keputusan irreversible tetap ke manusia."
      : mode === "SEMI"
        ? "SEMI: @Decider merekomendasikan; Anda mengonfirmasi."
        : mode === "OFF"
          ? "OFF: Anda menjawab semua pertanyaan; tidak ada keputusan otomatis."
          : "Mode keputusan belum terbaca.";
}

async function ensureDecisionMode(force = false) {
  if (state.decisionModeLoading) return;
  if (!force && state.decisionMode) return;
  state.decisionModeLoading = true;
  try {
    state.decisionMode = await api("/api/decision-mode");
    state.decisionModeError = null;
  } catch (error) {
    state.decisionModeError = error.message;
  } finally {
    state.decisionModeLoading = false;
    updateModeSwitch();
  }
}

async function setDecisionMode(mode) {
  const select = document.getElementById("mode-select");
  if (!select || select.disabled) return;
  if (
    mode === "AUTO" &&
    !window.confirm(
      "AUTO: @Decider akan menjawab pertanyaan otomatis. Keputusan irreversible tetap dikembalikan ke manusia. Aktifkan AUTO?"
    )
  ) {
    updateModeSwitch();
    return;
  }
  select.disabled = true;
  try {
    await api("/api/decision-mode", {
      method: "POST",
      body: JSON.stringify({ mode, by: "operator-console" }),
    });
    toast(`Mode ${mode} aktif`);
    await ensureDecisionMode(true);
  } catch (error) {
    toast(`Gagal mengganti mode: ${error.message}`, true);
  } finally {
    select.disabled = false;
    updateModeSwitch();
  }
}

async function ensureAutopilot(force = false) {
  if (state.autopilotLoading) return;
  if (!force && state.autopilot) return;
  state.autopilotLoading = true;
  try {
    const data = await api("/api/autopilot");
    state.autopilot = data;
    state.autopilotError = null;
  } catch (error) {
    state.autopilotError = error.message;
  } finally {
    state.autopilotLoading = false;
    if (state.view === "decide" && !decideInputFocused()) render();
  }
}

function decideCard(decision) {
  const id = decision.id;
  let options = [];
  if (Array.isArray(decision.options)) {
    options = decision.options;
  } else {
    try {
      options = JSON.parse(decision.options || "[]");
    } catch {
      options = [];
    }
  }
  const choice = state.decideChoice[id] || "";
  const draft = state.decideText[id] || "";
  const optionRows = options.map((raw, index) => {
    const option = raw && typeof raw === "object" ? raw : { label: String(raw || `Opsi ${index + 1}`) };
    const label = option.label || `Opsi ${index + 1}`;
    const selected = choice === label;
    const button = h(
      "button",
      {
        type: "button",
        class: `decision-option${selected ? " selected" : ""}`,
        "aria-pressed": selected ? "true" : "false",
        onclick: () => {
          state.decideChoice[id] = label;
          state.decideText[id] = label;
          const area = document.getElementById(`decide-text-${id}`);
          if (area) area.value = label;
          const group = button.closest(".decision-options");
          if (group) {
            group.querySelectorAll(".decision-option").forEach((node) => {
              const on = node === button;
              node.classList.toggle("selected", on);
              node.setAttribute("aria-pressed", on ? "true" : "false");
            });
          }
        },
      },
      h("span", { class: "decision-option-label", text: label }),
      option.evidence ? h("span", { class: "card-desc", text: ` — ${option.evidence}` }) : null,
      option.cost ? h("span", { class: "card-desc", text: ` · biaya: ${option.cost}` }) : null,
      option.recommended ? h("span", { class: "badge ok", text: "Recommended" }) : null,
      option.recommended && option.recommended_reason
        ? h("span", { class: "card-desc", text: ` (${option.recommended_reason})` })
        : null
    );
    return button;
  });
  const textarea = h(
    "textarea",
    {
      id: `decide-text-${id}`,
      placeholder: "Tulis jawabanmu…",
      oninput: (event) => {
        state.decideText[id] = event.target.value;
      },
    },
    draft
  );
  const reply = h("p", { class: "card-desc" });
  const save = h("button", {
    class: "btn primary",
    type: "button",
    text: "Simpan jawaban",
    onclick: async () => {
      const answer = (state.decideText[id] || "").trim() || state.decideChoice[id] || "";
      if (!answer) {
        reply.textContent = "Pilih opsi atau tulis jawaban.";
        return;
      }
      save.disabled = "disabled";
      reply.textContent = "Menyimpan…";
      try {
        await api("/api/decide", {
          method: "POST",
          body: JSON.stringify({ id, answer, by: "operator-console" }),
        });
        delete state.decideChoice[id];
        delete state.decideText[id];
        toast("Jawaban tersimpan");
        await ensureAutopilot(true);
      } catch (error) {
        reply.textContent = `Gagal: ${error.message}`;
        save.disabled = null;
      }
    },
  });
  return h(
    "div",
    { class: "panel", style: "margin-bottom:12px" },
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, `#${id}${decision.item_id ? ` · item #${decision.item_id}` : ""}`),
      h("span", { class: "badge", text: decision.weight || "heavy" })
    ),
    h("p", { class: "card-desc", text: decision.question || "" }),
    decision.note ? h("p", { class: "card-desc", text: `catatan: ${decision.note}` }) : null,
    options.length
      ? h("div", { class: "field" }, h("label", {}, "Pilihan"), h("div", { class: "decision-options" }, ...optionRows))
      : h("p", { class: "card-desc", text: "Tanpa pilihan — tulis jawaban di bawah." }),
    h("div", { class: "field" }, h("label", {}, "Jawaban"), textarea),
    h("div", { class: "row-actions", style: "margin-top:8px" }, save),
    reply
  );
}

function renderDecide(view) {
  const ap = state.autopilot;
  view.append(
    h(
      "div",
      { class: "section-head" },
      h(
        "h2",
        {},
        "Human Decide",
        h("span", {
          class: "count",
          text: ap ? `· ${(ap.decisions_open || []).length} menunggu` : "",
        })
      ),
      h(
        "button",
        { class: "btn", type: "button", onclick: () => ensureAutopilot(true) },
        "Muat ulang"
      )
    )
  );
  if (!ap) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "◌" }),
        h("strong", {}, state.autopilotError ? "Tidak bisa membaca antrean" : "Memuat antrean…"),
        state.autopilotError ? h("p", { text: state.autopilotError }) : null
      )
    );
    if (!state.autopilotError) ensureAutopilot();
    return;
  }
  const open = ap.decisions_open || [];
  if (!open.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "✓" }),
        h("strong", {}, "Tidak ada yang menunggu"),
        h("p", {
          text: "Antrean kosong adalah keadaan normal. Pertanyaan dari worker atau autopilot akan muncul di sini.",
        })
      )
    );
  } else {
    open.forEach((decision) => view.append(decideCard(decision)));
  }
  const done = ap.decisions_answered || [];
  if (done.length) {
    view.append(
      h(
        "details",
        { style: "margin-top:16px" },
        h("summary", { class: "card-desc", text: `Sudah dijawab (${done.length})` }),
        ...done.map((decision) =>
          h(
            "div",
            { class: "panel", style: "margin-top:8px" },
            h("strong", { text: `#${decision.id} ${decision.question || ""}` }),
            h("p", { class: "card-desc", text: decision.answer || "(tanpa jawaban)" }),
            h("p", {
              class: "card-desc",
              text: `oleh ${decision.answered_by || decision.model_id || "—"} · ${
                decision.state || ""
              }`,
            })
          )
        )
      )
    );
  }
}

function renderApprovals(view) {
  const pending = state.approvals.filter((item) => item.status === "pending");
  const history = state.approvals.filter((item) => item.status !== "pending");

  view.append(
    h(
      "div",
      { class: "section-head" },
      h(
        "h2",
        {},
        "Menunggu keputusan",
        h("span", { class: "count", text: `· ${pending.length}` })
      )
    )
  );

  if (!pending.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "✓" }),
        h("strong", { text: "Tidak ada yang menunggu" }),
        h("p", {
          text: "Worker dengan mode “butuh izin” akan menaruh permintaannya di sini sebelum berjalan.",
        })
      )
    );
  } else {
    pending.forEach((approval) => {
      const payload = approval.payload || {};
      const waker = state.wakers.find((item) => item.id === payload.waker_id);
      const origin =
        approval.origin === "automation"
          ? "Dari automation"
          : approval.origin === "manual"
            ? "Run manual"
            : "Dari chat";
      view.append(
        h(
          "div",
          { class: "panel", style: "margin-bottom:12px" },
          h(
            "div",
            { class: "section-head" },
            h("h2", {}, origin),
            h("span", { class: "badge paused", text: formatWhen(approval.requested_at) })
          ),
          h("p", {
            class: "card-desc",
            text: `${waker ? waker.name : payload.waker_id} · ${payload.engine_key || "?"}${
              payload.model ? " · " + payload.model : ""
            }`,
          }),
          h("pre", {
            class: "prompt-box",
            text: (payload.prompt || "").slice(0, 600) || "(tanpa prompt)",
          }),
          h(
            "div",
            { class: "drawer-actions" },
            h(
              "button",
              {
                class: "btn primary",
                onclick: () => decideApproval(approval.id, "approved"),
              },
              "Setujui"
            ),
            h(
              "button",
              {
                class: "btn danger",
                onclick: () => decideApproval(approval.id, "denied"),
              },
              "Tolak"
            )
          )
        )
      );
    });
  }

  view.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:24px" },
      h("h2", {}, "Riwayat", h("span", { class: "count", text: `· ${history.length}` }))
    )
  );

  if (!history.length) {
    view.append(h("div", { class: "empty" }, h("strong", { text: "Belum ada riwayat" })));
    return;
  }

  const body = h("tbody");
  history.slice(0, 20).forEach((approval) => {
    const payload = approval.payload || {};
    const waker = state.wakers.find((item) => item.id === payload.waker_id);
    const [cls, label] =
      approval.status === "approved"
        ? ["ok", "disetujui"]
        : approval.status === "denied"
          ? ["bad", "ditolak"]
          : ["draft", approval.status];
    body.append(
      h(
        "tr",
        {},
        h("td", { text: formatWhen(approval.requested_at) }),
        h("td", { text: waker ? waker.name : payload.waker_id || "—" }),
        h("td", { text: approval.origin || "—" }),
        h("td", {}, h("span", { class: `badge ${cls}`, text: label })),
        h("td", { text: approval.run_id || "—" })
      )
    );
  });

  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            ...["Waktu", "Worker", "Asal", "Status", "Run"].map((label) =>
              h("th", {}, label)
            )
          )
        ),
        body
      )
    )
  );
}

async function loadWorkflows() {
  try {
    const data = await api("/api/workflows");
    state.workflows = data.workflows || [];
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadWorkflowRuns(workflowId) {
  try {
    const data = await api(`/api/workflows/${workflowId}/runs`);
    state.workflowRuns[workflowId] = data.runs || [];
  } catch (error) {
    toast(error.message, true);
  }
}

function workflowStatusBadge(status) {
  const map = {
    succeeded: ["ok", "berhasil"],
    failed: ["bad", "gagal"],
    running: ["pane", "berjalan"],
    queued: ["draft", "menunggu"],
    waiting_approval: ["paused", "menunggu izin"],
    cancelled: ["draft", "dihentikan"],
  };
  const [cls, label] = map[status] || ["draft", status];
  return h("span", { class: `badge ${cls}`, text: label });
}

function formatWorkflowLog(log) {
  if (!Array.isArray(log) || !log.length) return "(belum ada langkah selesai)";
  return log
    .map(
      (entry) =>
        `#${(entry.step ?? 0) + 1} ${entry.name || ""} · ${entry.status || ""}\n${
          entry.text || ""
        }`
    )
    .join("\n\n");
}

function renderWorkflows(view) {
  view.append(
    h(
      "div",
      { class: "section-head" },
      h(
        "h2",
        {},
        "Workflow",
        h("span", { class: "count", text: `· ${state.workflows.length}` })
      ),
      h("button", { class: "btn primary", onclick: openWorkflowModal }, "＋ Workflow baru")
    )
  );

  if (!state.workflows.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "⇢" }),
        h("strong", { text: "Belum ada workflow" }),
        h("p", {
          text: "Workflow menjalankan beberapa langkah berurutan. Setiap baris adalah satu langkah; awali dengan approval: untuk gerbang persetujuan.",
        }),
        h(
          "button",
          { class: "btn primary", onclick: openWorkflowModal },
          "Buat workflow"
        )
      )
    );
    return;
  }

  const body = h("tbody");
  state.workflows.forEach((workflow) => {
    const worker = state.wakers.find((w) => w.id === workflow.waker_id);
    body.append(
      h(
        "tr",
        {},
        h(
          "td",
          {},
          h("strong", { text: workflow.name }),
          h("div", { class: "card-slug", text: `${workflow.steps.length} langkah · v${workflow.version}` })
        ),
        h("td", { text: worker ? worker.name : workflow.waker_id }),
        h(
          "td",
          {},
          h(
            "div",
            { class: "row-actions" },
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => runWorkflow(workflow),
                text: "Jalankan",
              }
            ),
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => showWorkflowRuns(workflow.id),
                text: "Riwayat",
              }
            ),
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => openWorkflowModal(workflow),
                text: "Edit",
              }
            ),
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => showWorkflowVersions(workflow.id),
                text: "Versi",
              }
            ),
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => deleteWorkflow(workflow),
                text: "Hapus",
              }
            )
          )
        )
      )
    );
  });

  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            ...["Workflow", "Worker", "Aksi"].map((label) => h("th", {}, label))
          )
        ),
        body
      )
    )
  );

  if (state.expandedWorkflow) {
    const runs = state.workflowRuns[state.expandedWorkflow] || [];
    const section = h("div", { class: "panel", style: "margin-top:16px" });
    section.append(
      h(
        "div",
        { class: "section-head" },
        h("h2", {}, "Riwayat workflow"),
        h(
          "button",
          {
            class: "link-btn",
            onclick: () => {
              state.expandedWorkflow = null;
              render();
            },
          },
          "Tutup"
        )
      )
    );

    if (!runs.length) {
      section.append(h("p", { class: "card-desc", text: "Belum ada eksekusi." }));
    } else {
      runs.forEach((run) => {
        section.append(
          h(
            "div",
            { class: "panel", style: "margin-bottom:10px;background:var(--bg-soft)" },
            h(
              "div",
              { class: "section-head" },
              h("h2", {}, run.id),
              workflowStatusBadge(run.status)
            ),
            h("p", {
              class: "card-desc",
              text: `langkah ${run.current_step + 1} · ${formatWhen(run.started_at)}`,
            }),
            h(
              "details",
              { class: "msg-process" },
              h("summary", {}, "Log langkah"),
              h("pre", { class: "prompt-box process-box", text: formatWorkflowLog(run.log) })
            ),
            ["running", "queued", "waiting_approval"].includes(run.status)
              ? h(
                  "button",
                  {
                    class: "btn danger",
                    style: "margin-top:8px",
                    onclick: () => terminateWorkflowRun(run.id, run.workflow_id),
                  },
                  "Hentikan"
                )
              : h(
                  "button",
                  {
                    class: "btn",
                    style: "margin-top:8px",
                    onclick: () => retryWorkflowRun(run.id, run.workflow_id),
                  },
                  "Ulangi"
                )
          )
        );
      });
    }
    view.append(section);
  }

  if (state.versionsWorkflow) {
    const workflow = state.workflows.find((w) => w.id === state.versionsWorkflow);
    const versions = state.workflowVersions[state.versionsWorkflow] || [];
    const section = h("div", { class: "panel", style: "margin-top:16px" });
    section.append(
      h(
        "div",
        { class: "section-head" },
        h("h2", {}, `Versi · ${workflow ? workflow.name : state.versionsWorkflow}`),
        h(
          "button",
          {
            class: "link-btn",
            onclick: () => {
              state.versionsWorkflow = null;
              render();
            },
          },
          "Tutup"
        )
      )
    );
    if (!versions.length) {
      section.append(h("p", { class: "card-desc", text: "Belum ada versi tersimpan." }));
    } else {
      versions.forEach((version) => {
        section.append(
          h(
            "div",
            { class: "row-actions", style: "margin-bottom:8px" },
            h("span", { class: "badge", text: `v${version.version}` }),
            h("span", { class: "card-slug", text: formatWhen(version.created_at) }),
            workflow && version.version === workflow.version
              ? h("span", { class: "badge ok", text: "aktif" })
              : h(
                  "button",
                  {
                    class: "link-btn",
                    onclick: () => rollbackWorkflow(state.versionsWorkflow, version.version),
                    text: "Rollback",
                  }
                )
          )
        );
      });
    }
    view.append(section);
  }
}

function parseWorkflowSteps(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((raw, index) => {
      let line = raw;
      let retry = 0;
      const retryMatch = line.match(/\|\s*retry=(\d+)\s*$/i);
      if (retryMatch) {
        retry = Math.min(5, Number(retryMatch[1]));
        line = line.slice(0, retryMatch.index).trim();
      }
      const lower = line.toLowerCase();
      if (lower.startsWith("approval:")) {
        return {
          name: `Persetujuan ${index + 1}`,
          kind: "approval",
          prompt: line.slice("approval:".length).trim(),
          max_retries: retry,
        };
      }
      if (lower.startsWith("tool:")) {
        return {
          name: `Tool ${index + 1}`,
          kind: "tool",
          command: line.slice("tool:".length).trim(),
          max_retries: retry,
        };
      }
      if (lower.startsWith("branch:")) {
        const rest = line.slice("branch:".length).trim();
        const contains = rest.match(/contains="([^"]*)"/i);
        const goto = rest.match(/goto=(\d+)/i);
        return {
          name: `Cabang ${index + 1}`,
          kind: "branch",
          branch_contains: contains ? contains[1] : "",
          branch_goto: goto ? Number(goto[1]) : null,
          max_retries: 0,
        };
      }
      const prompt = lower.startsWith("prompt:")
        ? line.slice("prompt:".length).trim()
        : line;
      return {
        name: prompt.slice(0, 40),
        kind: "prompt",
        prompt,
        max_retries: retry,
      };
    });
}

function stepsToText(steps) {
  return steps
    .map((step) => {
      if (step.kind === "approval") return `approval: ${step.prompt || ""}`;
      if (step.kind === "tool") {
        return `tool: ${step.command || ""}${step.max_retries ? ` | retry=${step.max_retries}` : ""}`;
      }
      if (step.kind === "branch") {
        return `branch: contains="${step.branch_contains || ""}" goto=${step.branch_goto || 1}`;
      }
      return `${step.prompt || step.name}${
        step.max_retries ? ` | retry=${step.max_retries}` : ""
      }`;
    })
    .join("\n");
}

function openWorkflowModal(workflow = null) {
  const active = state.wakers.filter((waker) => waker.status === "active");
  if (!active.length && !workflow) {
    toast("Buat dan aktifkan worker dulu", true);
    return;
  }
  state.editingWorkflow = workflow ? workflow.id : null;
  $("#workflow-title").textContent = workflow ? "Edit workflow" : "Workflow baru";
  const select = $("#w-waker");
  clear(select);
  active.forEach((waker) =>
    select.append(h("option", { value: waker.id, text: waker.name }))
  );
  if (workflow) {
    select.append(
      h("option", { value: workflow.waker_id, text: "Worker saat ini" })
    );
    select.value = workflow.waker_id;
  }
  $("#w-name").value = workflow ? workflow.name : "";
  $("#w-steps").value = workflow ? stepsToText(workflow.steps) : "";
  $("#workflow-error").classList.add("hidden");
  $("#workflow-modal").classList.remove("hidden");
  setTimeout(() => $("#w-name").focus(), 30);
}

function closeWorkflowModal() {
  $("#workflow-modal").classList.add("hidden");
  state.editingWorkflow = null;
}

async function submitWorkflow(event) {
  event.preventDefault();
  const errorBox = $("#workflow-error");
  errorBox.classList.add("hidden");

  const payload = {
    name: $("#w-name").value.trim(),
    waker_id: $("#w-waker").value,
    steps: parseWorkflowSteps($("#w-steps").value),
  };

  try {
    if (state.editingWorkflow) {
      await api(`/api/workflows/${state.editingWorkflow}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      toast("Workflow diperbarui (versi baru)");
    } else {
      await api("/api/workflows", { method: "POST", body: JSON.stringify(payload) });
      toast("Workflow dibuat");
    }
    closeWorkflowModal();
    await loadWorkflows();
    render();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

async function showWorkflowVersions(workflowId) {
  try {
    const data = await api(`/api/workflows/${workflowId}/versions`);
    state.workflowVersions[workflowId] = data.versions || [];
    state.versionsWorkflow = workflowId;
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function rollbackWorkflow(workflowId, version) {
  try {
    await api(`/api/workflows/${workflowId}/rollback`, {
      method: "POST",
      body: JSON.stringify({ version }),
    });
    toast(`Rollback ke versi ${version}`);
    await loadWorkflows();
    await showWorkflowVersions(workflowId);
  } catch (error) {
    toast(error.message, true);
  }
}

async function retryWorkflowRun(runId, workflowId) {
  try {
    const result = await api(`/api/workflow-runs/${runId}/retry`, { method: "POST" });
    toast(`Workflow diulang · ${result.workflow_run_id}`);
    state.expandedWorkflow = workflowId;
    await loadWorkflowRuns(workflowId);
    render();
    pollWorkflowRun(workflowId, result.workflow_run_id);
  } catch (error) {
    toast(error.message, true);
  }
}

async function runWorkflow(workflow) {
  try {
    const result = await api(`/api/workflows/${workflow.id}/run`, { method: "POST" });
    toast(`Workflow berjalan · ${result.workflow_run_id}`);
    state.expandedWorkflow = workflow.id;
    await loadWorkflowRuns(workflow.id);
    render();
    pollWorkflowRun(workflow.id, result.workflow_run_id);
  } catch (error) {
    toast(error.message, true);
  }
}

async function pollWorkflowRun(workflowId, runId) {
  for (let tick = 0; tick < 600; tick += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    let run;
    try {
      run = await api(`/api/workflow-runs/${runId}`);
    } catch {
      break;
    }
    await loadWorkflowRuns(workflowId);
    if (state.view === "workflows") render();
    if (!["queued", "running"].includes(run.status)) break;
  }
}

async function showWorkflowRuns(workflowId) {
  state.expandedWorkflow = workflowId;
  await loadWorkflowRuns(workflowId);
  render();
}

async function terminateWorkflowRun(runId, workflowId) {
  try {
    await api(`/api/workflow-runs/${runId}/terminate`, { method: "POST" });
    toast("Workflow dihentikan");
    await loadWorkflowRuns(workflowId);
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteWorkflow(workflow) {
  if (!confirm(`Hapus workflow “${workflow.name}”?`)) return;
  try {
    await api(`/api/workflows/${workflow.id}`, { method: "DELETE" });
    toast("Workflow dihapus");
    if (state.expandedWorkflow === workflow.id) state.expandedWorkflow = null;
    await loadWorkflows();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadAutomations() {
  try {
    const data = await api("/api/automations");
    state.automations = data.automations || [];
  } catch (error) {
    toast(error.message, true);
  }
}

function formatWhen(iso) {
  if (!iso) return "—";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "—";
  return new Date(at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" });
}

function automationStatus(automation) {
  if (automation.running) return ["ok", "berjalan"];
  if (!automation.enabled) return ["draft", "dijeda"];
  if (!automation.last_status) return ["draft", "belum jalan"];
  const map = {
    succeeded: ["ok", "berhasil"],
    failed: ["bad", "gagal"],
    running: ["pane", "berjalan"],
    skipped: ["paused", "dilewati · worker sibuk"],
  };
  return map[automation.last_status] || ["draft", automation.last_status];
}

let automationPollTimer = null;

// Status "berjalan" hanya benar bila dibaca ulang; halaman ini menyegarkan diri
// selama operator melihatnya, tanpa mengganggu halaman lain.
function ensureAutomationPolling() {
  if (automationPollTimer) return;
  automationPollTimer = setInterval(async () => {
    if (state.view !== "automations") return;
    try {
      await loadAutomations();
      render();
    } catch {
      // biarkan status terakhir tampil; percobaan berikutnya menyusul
    }
  }, 5000);
}

function renderAutomations(view) {
  ensureAutomationPolling();
  // Auto Pilot Task punya menunya sendiri; jangan tampil dua kali di daftar
  // automation biasa (dan jangan sampai modal automation mengubah kind-nya).
  const automations = state.automations.filter(
    (automation) => !String(automation.kind || "").startsWith("autopilot")
  );
  view.append(
    h(
      "div",
      { class: "section-head" },
      h(
        "h2",
        {},
        "Automation",
        h("span", { class: "count", text: `· ${automations.length}` })
      ),
      h(
        "button",
        { class: "btn primary", onclick: () => openAutomationModal() },
        "＋ Automation baru"
      )
    )
  );

  if (!automations.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "⏱" }),
        h("strong", { text: "Belum ada automation" }),
        h("p", {
          text: "Automation menjalankan worker pada jadwal cron — tanpa kamu membuka console.",
        }),
        h(
          "button",
          { class: "btn primary", onclick: () => openAutomationModal() },
          "Buat automation"
        )
      )
    );
    return;
  }

  const body = h("tbody");
  automations.forEach((automation) => {
    const [cls, label] = automationStatus(automation);
    body.append(
      h(
        "tr",
        {},
        h(
          "td",
          {},
          h("strong", { text: automation.name }),
          h("div", { class: "card-slug", text: `${cronSummary(automation.cron)}${automation.kind === "command" ? " · perintah" : ""}` })
        ),
        h("td", { text: automation.waker_name || automation.waker_id }),
        h("td", { text: automation.timezone === "UTC" ? "UTC" : "Lokal" }),
        h("td", { text: formatWhen(automation.next_run_at) }),
        h("td", {}, h("span", { class: `badge ${cls}`, text: label })),
        h(
          "td",
          {},
          h("span", {
            class: `badge ${automation.enabled ? "ok" : "draft"}`,
            text: automation.enabled ? "Aktif" : "Nonaktif",
            title: automation.enabled
              ? "Automation ikut jadwal dan boleh berjalan"
              : "Automation disimpan tetapi tidak dijalankan",
          })
        ),
        h(
          "td",
          {},
          h(
            "div",
            { class: "row-actions" },
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => openAutomationModal(automation),
                text: "Edit",
              }
            ),
            h(
              "button",
              {
                class: `automation-power ${automation.enabled ? "is-on" : "is-off"}`,
                type: "button",
                title: automation.enabled ? "Jeda automation" : "Mulai automation",
                "aria-label": automation.enabled ? "Jeda automation" : "Mulai automation",
                onclick: () => toggleAutomation(automation),
              },
              h("span", { "aria-hidden": "true", text: automation.enabled ? "■" : "▶" })
            ),
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => runAutomation(automation),
                text: "Jalankan",
              }
            ),
            h(
              "button",
              {
                class: "link-btn",
                onclick: () => deleteAutomation(automation),
                text: "Hapus",
              }
            )
          )
        )
      )
    );
  });

  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            ...["Nama", "Worker", "Zona", "Berikutnya", "Status", "Aktif", "Aksi"].map((label) =>
              h("th", {}, label)
            )
          )
        ),
        body
      )
    )
  );
}

const WEEKDAYS = ["Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"];

function fillSelect(select, items, selected) {
  clear(select);
  items.forEach(([value, label]) => {
    const option = h("option", { value, text: label });
    if (String(value) === String(selected)) option.selected = true;
    select.append(option);
  });
}

// Pilihan jam/menit/hari diisi sekali; operator tidak perlu mengetik cron.
function ensureAutomationScheduleOptions() {
  const hour = $("#a-hour");
  const minute = $("#a-minute");
  const weekday = $("#a-weekday");
  const dom = $("#a-dom");
  if (!hour || hour.options.length) return;
  const two = (n) => String(n).padStart(2, "0");
  fillSelect(hour, Array.from({ length: 24 }, (_, i) => [two(i), two(i)]), "08");
  fillSelect(minute, Array.from({ length: 60 }, (_, i) => [two(i), two(i)]), "00");
  fillSelect(weekday, WEEKDAYS.map((name, i) => [String(i), name]), "1");
  fillSelect(dom, Array.from({ length: 28 }, (_, i) => [String(i + 1), String(i + 1)]), "1");
}

function buildAutomationCron() {
  const mode = $("#a-sched").value;
  if (mode === "custom") return $("#a-cron").value.trim();
  const minute = $("#a-minute").value;
  const hour = $("#a-hour").value;
  if (mode === "hourly") return `${minute} * * * *`;
  if (mode === "weekly") return `${minute} ${hour} * * ${$("#a-weekday").value}`;
  if (mode === "monthly") return `${minute} ${hour} ${$("#a-dom").value} * *`;
  return `${minute} ${hour} * * *`;
}

function automationScheduleSummary() {
  const mode = $("#a-sched").value;
  const hour = $("#a-hour").value;
  const minute = $("#a-minute").value;
  const tz = $("#a-tz").value === "UTC" ? "UTC" : "waktu lokal";
  if (mode === "hourly") return `Berjalan setiap jam pada menit :${minute} (${tz}).`;
  if (mode === "weekly") {
    return `Berjalan setiap ${WEEKDAYS[Number($("#a-weekday").value)]} pukul ${hour}:${minute} (${tz}).`;
  }
  if (mode === "monthly") {
    return `Berjalan setiap tanggal ${$("#a-dom").value} pukul ${hour}:${minute} (${tz}).`;
  }
  if (mode === "custom") {
    const cron = $("#a-cron").value.trim();
    return cron ? `Cron khusus: ${cron}` : "Isi cron khusus, atau pilih jadwal di atas.";
  }
  return `Berjalan setiap hari pukul ${hour}:${minute} (${tz}).`;
}

const KIND_HELP = {
  prompt:
    "Worker memakai model untuk menjawab atau mengerjakan tugas. Cocok untuk laporan, ringkasan, analisis. Memakai kuota engine.",
  command:
    "Komputer menjalankan skrip langsung, tanpa model. Cocok untuk backup, bersih-bersih file, cek disk. Hasilnya sama setiap kali dan tidak memakai kuota.",
};

function updateAutomationForm() {
  const mode = $("#a-sched").value;
  const kind = $("#a-kind").value;
  const custom = mode === "custom";
  $("#a-hour-field").classList.toggle("hidden", mode === "hourly" || custom);
  $("#a-minute-field").classList.toggle("hidden", custom);
  $("#a-weekday-field").classList.toggle("hidden", mode !== "weekly");
  $("#a-dom-field").classList.toggle("hidden", mode !== "monthly");
  $("#a-cron-field").classList.toggle("hidden", !custom);
  $("#a-command-field").classList.toggle("hidden", kind !== "command");
  $("#a-prompt-field").classList.toggle("hidden", kind === "command");
  $("#a-kind-help").textContent = KIND_HELP[kind] || "";
  const summary = $("#a-sched-summary");
  summary.textContent = automationScheduleSummary();
  summary.classList.toggle("hidden", custom && !$("#a-cron").value.trim());
}

function cronSummary(cron) {
  const parts = String(cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return cron || "—";
  const [minute, hour, dom, , weekday] = parts;
  if (hour === "*" && dom === "*" && weekday === "*") return `Tiap jam :${minute.padStart(2, "0")}`;
  if (/^\d+$/.test(hour) && dom === "*" && weekday === "*") return `Harian ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  if (/^\d+$/.test(hour) && dom === "*" && /^\d$/.test(weekday)) {
    return `${WEEKDAYS[Number(weekday)]} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  }
  if (/^\d+$/.test(hour) && /^\d+$/.test(dom) && weekday === "*") {
    return `Tgl ${dom} ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  }
  return cron;
}

function parseCron(cron) {
  const parts = String(cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return { mode: "custom" };
  const [minute, hour, dom, , weekday] = parts;
  if (/^\d+$/.test(minute) && hour === "*" && dom === "*" && weekday === "*") return { mode: "hourly", minute };
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && dom === "*" && weekday === "*") return { mode: "daily", minute, hour };
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && dom === "*" && /^\d$/.test(weekday)) return { mode: "weekly", minute, hour, weekday };
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && /^\d+$/.test(dom) && weekday === "*") return { mode: "monthly", minute, hour, dom };
  return { mode: "custom" };
}

function openAutomationModal(automation = null) {
  // Event handler lama memanggil fungsi ini tanpa pembungkus, jadi argumennya
  // MouseEvent — bukan automation. Tanpa normalisasi ini modal membuka nilai
  // acak/undefined dan "Simpan" bisa menulis ke baris yang salah.
  if (!automation || typeof automation !== "object" || typeof automation.id !== "string") {
    automation = null;
  }
  state.editingAutomation = automation ? automation.id : null;
  const form = $("#automation-form");
  if (form && typeof form.reset === "function") form.reset();
  const active = state.wakers.filter(
    (waker) => waker.status === "active" || (automation && waker.id === automation.waker_id)
  );
  if (!active.length) {
    toast("Buat dan aktifkan worker dulu", true);
    return;
  }
  const select = $("#a-waker");
  clear(select);
  active.forEach((waker) =>
    select.append(h("option", { value: waker.id, text: waker.name }))
  );
  ensureAutomationScheduleOptions();
  const parsed = automation ? parseCron(automation.cron) : { mode: "daily" };
  const two = (value) => String(value).padStart(2, "0");
  $("#a-name").value = automation ? automation.name : "";
  $("#a-sched").value = parsed.mode;
  // Opsi jam/menit bernilai "00".."23"/"00".."59". Cron menyimpan "2"/"0",
  // dan `select.value = "2"` tidak cocok dengan opsi "02" — select lalu jatuh
  // ke opsi pertama. Itulah sebabnya Edit dulu menampilkan jam yang salah.
  $("#a-hour").value = two(parsed.hour || "08");
  $("#a-minute").value = two(parsed.minute || "00");
  $("#a-weekday").value = parsed.weekday || "1";
  $("#a-dom").value = parsed.dom || "1";
  $("#a-cron").value = automation && automation.cron && parsed.mode === "custom" ? automation.cron : "";
  const kind = automation && automation.kind === "command" ? "command" : "prompt";
  $("#a-kind").value = kind;
  $("#a-command").value = automation && kind === "command" && automation.command ? automation.command : "";
  $("#a-tz").value = automation && automation.timezone === "UTC" ? "UTC" : "local";
  $("#a-overlap").value = automation ? automation.overlap_policy : "skip";
  $("#a-prompt").value = automation && kind === "prompt" && automation.prompt ? automation.prompt : "";
  if (automation) {
    $("#a-waker").value = automation.waker_id;
  }
  $("#automation-title").textContent = automation ? "Edit automation" : "Automation baru";
  $("#automation-submit").textContent = automation ? "Simpan perubahan" : "Simpan";
  updateAutomationForm();
  $("#automation-error").classList.add("hidden");
  $("#automation-modal").classList.remove("hidden");
  setTimeout(() => $("#a-name").focus(), 30);
}

function closeAutomationModal() {
  $("#automation-modal").classList.add("hidden");
}

async function submitAutomation(event) {
  event.preventDefault();
  const errorBox = $("#automation-error");
  errorBox.classList.add("hidden");

  const kind = $("#a-kind").value;
  const cron = buildAutomationCron();
  if (!cron) {
    errorBox.textContent = "Isi cron lanjutan atau pilih jadwal yang tersedia";
    errorBox.classList.remove("hidden");
    return;
  }
  const payload = {
    waker_id: $("#a-waker").value,
    name: $("#a-name").value.trim(),
    cron,
    timezone: $("#a-tz").value,
    overlap_policy: $("#a-overlap").value,
    kind,
    command: kind === "command" ? $("#a-command").value.trim() : null,
    prompt: kind === "prompt" ? $("#a-prompt").value : null,
  };

  try {
    if (state.editingAutomation) {
      await api(`/api/automations/${state.editingAutomation}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } else {
      await api("/api/automations", { method: "POST", body: JSON.stringify(payload) });
    }
    closeAutomationModal();
    toast(state.editingAutomation ? "Automation diperbarui" : "Automation dibuat");
    await loadAutomations();
    render();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

async function toggleAutomation(automation) {
  try {
    await api(`/api/automations/${automation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !automation.enabled }),
    });
    await loadAutomations();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function runAutomation(automation) {
  try {
    const result = await api(`/api/automations/${automation.id}/run`, {
      method: "POST",
    });
    toast(`Dijalankan · ${result.run_id}`);
    await loadAutomations();
    try {
      const data = await api("/api/runs");
      state.runs = data.runs || [];
    } catch {
      // daftar run akan dimuat lagi saat halaman Runs dibuka
    }
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteAutomation(automation) {
  if (!confirm(`Hapus automation “${automation.name}”?`)) return;
  try {
    await api(`/api/automations/${automation.id}`, { method: "DELETE" });
    toast("Automation dihapus");
    await loadAutomations();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

// ---------------------------------------------------------- Auto Pilot Task
// Auto Pilot Task bukan penjadwal kedua: ia automation `kind=autopilot` dengan
// irama 1 menit. Yang ditambahkan di sini hanya sakelar global dan muka yang
// membaca Planning.md sebagai sumber tugas.

const AUTO_PILOT_CRON = "*/1 * * * *";
const AUTO_PILOT_PLAN_MARK = /\[\[auto-pilot-plan:([^\]]+)\]\]/;

function autoPilotPlanPath(automation) {
  const match = AUTO_PILOT_PLAN_MARK.exec((automation && automation.prompt) || "");
  return match ? match[1] : "";
}

function autoPilotPrompt(name, path) {
  return [
    `[[auto-pilot-plan:${path}]]`,
    `Auto Pilot Task: ${name}`,
    "",
    `Baca berkas Planning.md di ${path}.`,
    "Kerjakan SATU langkah berikutnya yang belum selesai — yang belum bertanda ✅ — dengan bukti nyata (berkas, keluaran perintah, atau uji).",
    "Perbarui Planning.md: centang langkah yang sudah selesai dan tulis bukti singkat. Jangan mengubah tujuan atau menambah lingkup.",
    // Publikasi disetujui agen: verifier untuk sha, decider untuk wording.
    "Publikasi disetujui agen, bukan manusia: commit dan push hanya setelah verifier SETUJU untuk sha itu dan decider menyetujui wording.",
    "Prosedur: cross_verify → push-verified → push-gate → git push hanya bila gate ok. Jangan pernah menaruh kredensial di repo.",
    "Jangan menunggu perintah tambahan; ini putaran otomatis berikutnya.",
    'Jika SELURUH langkah sudah bertanda ✅, balas tepat satu baris "AUTO_PILOT_DONE: <ringkasan>" dan jangan mengerjakan hal lain.',
  ].join("\n");
}

function autoPilotCadence(cron) {
  const parts = String(cron || "").trim().split(/\s+/);
  if (
    parts.length === 5 &&
    /^\*\/\d+$/.test(parts[0]) &&
    parts.slice(1).every((part) => part === "*")
  ) {
    return `setiap ${parts[0].slice(2)} menit`;
  }
  return cronSummary(cron);
}

function autoPilotTaskStatus(automation) {
  if (automation.kind === "autopilot-done") return ["ok", "Done"];
  if (!automation.enabled) return ["draft", "dijeda"];
  if (automation.running) return ["pane", "berjalan"];
  if (!automation.last_status) return ["draft", "menunggu putaran"];
  const map = {
    succeeded: ["ok", "berhasil"],
    failed: ["bad", "gagal"],
    running: ["pane", "berjalan"],
    skipped: ["paused", "dilewati · worker sibuk"],
  };
  return map[automation.last_status] || ["draft", automation.last_status];
}

function overlapLabel(policy) {
  if (policy === "queue") return "Antrekan bila worker sibuk";
  if (policy === "replace") return "Ganti run yang sedang berjalan";
  return "Lewati putaran bila worker sibuk";
}

async function loadAutoPilot() {
  if (state.autoPilotLoading) return;
  state.autoPilotLoading = true;
  try {
    const [automations, mode, ledger] = await Promise.all([
      api("/api/automations"),
      api("/api/auto-pilot-mode"),
      api("/api/autopilot").catch(() => null),
    ]);
    state.automations = automations.automations || [];
    state.autoPilotMode = mode;
    // Ledger adalah sakelar yang dibaca `claim`; ia bisa berbeda dari mode scheduler.
    state.autoPilotLedger = (ledger && ledger.autopilot) || null;
    state.autoPilotError = null;
  } catch (error) {
    state.autoPilotError = error.message;
  } finally {
    state.autoPilotLoading = false;
  }
}

async function setAutoPilotMode(on) {
  try {
    const mode = await api("/api/auto-pilot-mode", {
      method: "POST",
      body: JSON.stringify({ mode: on ? "AUTO" : "OFF", by: "operator-console" }),
    });
    state.autoPilotMode = mode;
    // Satu tombol harus menggerakkan KEDUA sakelar. Mode scheduler yang ON tanpa
    // ledger ON adalah keadaan yang membuat worker terus menjawab "OFF".
    const ledger = await api("/api/autopilot", {
      method: "POST",
      body: JSON.stringify({
        mode: on ? "on" : "off",
        minutes: 60,
        budget: 12,
        by: "operator-console",
      }),
    });
    state.autoPilotLedger = ledger;
    toast(
      on
        ? "Auto Pilot ON · ledger dibuka 60 menit"
        : "Auto Pilot OFF · ledger ditutup"
    );
    await loadAutoPilot();
    render();
  } catch (error) {
    toast(error.message, true);
    await loadAutoPilot();
    render();
  }
}

async function createAutoPilotTask() {
  const wakerId = $("#ap-waker").value;
  const path = ($("#ap-path").value || "").trim();
  const name = ($("#ap-name").value || "").trim() || path.split("/").filter(Boolean).pop() || "Auto Pilot Task";
  const overlap = $("#ap-overlap").value;
  if (!path) {
    toast("Path Planning.md wajib diisi", true);
    return;
  }
  if (!path.startsWith("/")) {
    toast("Pakai path absolut, misalnya /home/irsofka/proyek/Planning.md", true);
    return;
  }
  try {
    await api("/api/automations", {
      method: "POST",
      body: JSON.stringify({
        waker_id: wakerId,
        name,
        cron: AUTO_PILOT_CRON,
        timezone: "local",
        overlap_policy: overlap,
        kind: "autopilot",
        prompt: autoPilotPrompt(name, path),
      }),
    });
    toast("Auto Pilot Task ditambahkan");
    await loadAutoPilot();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function changeAutoPilotWorker(automation, wakerId) {
  if (!wakerId || wakerId === automation.waker_id) return;
  try {
    await api(`/api/automations/${automation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ waker_id: wakerId }),
    });
    toast("Worker Auto Pilot Task diganti");
    await loadAutoPilot();
    render();
  } catch (error) {
    // Kembalikan dropdown ke worker yang benar-benar tersimpan.
    toast(error.message, true);
    await loadAutoPilot();
    render();
  }
}

async function markAutoPilotDone(automation, done) {
  try {
    await api(`/api/automations/${automation.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        kind: done ? "autopilot-done" : "autopilot",
        enabled: !done,
      }),
    });
    toast(done ? "Auto Pilot Task ditandai Done" : "Auto Pilot Task aktif lagi");
    await loadAutoPilot();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function playAutoPilotTask(automation) {
  if (automation.running) {
    toast("Task sedang berjalan; gunakan Pause atau Stop", true);
    return;
  }
  try {
    // Play = aktifkan task + kirim satu putaran sekarang. Mode Auto Pilot tetap
    // menjadi sakelar global untuk putaran berikutnya; Play tidak menyalakannya.
    await api(`/api/automations/${automation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ kind: "autopilot", enabled: true }),
    });
    const result = await api(`/api/automations/${automation.id}/run`, {
      method: "POST",
    });
    toast(`Play: putaran dikirim${result.run_id ? ` · ${result.run_id}` : ""}`);
    await loadAutoPilot();
    render();
  } catch (error) {
    toast(`Play gagal: ${error.message}`, true);
    await loadAutoPilot();
    render();
  }
}

async function pauseAutoPilotTask(automation) {
  try {
    await api(`/api/automations/${automation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: false }),
    });
    toast(
      automation.running
        ? "Task dijeda; run aktif tetap berjalan. Gunakan Stop untuk membatalkan."
        : "Task dijeda"
    );
    await loadAutoPilot();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function stopAutoPilotTask(automation) {
  if (!confirm(`Stop Auto Pilot Task “${automation.name}”? Run aktif akan dibatalkan.`)) return;
  try {
    let cancelled = null;
    try {
      const data = await api(`/api/automations/${automation.id}/runs`);
      const active = (data.runs || []).find(
        (item) =>
          item.run_id &&
          ["running", "queued"].includes(item.run_status || item.status)
      );
      if (active && active.run_id) {
        await api(`/api/runs/${active.run_id}/cancel`, { method: "POST" });
        cancelled = active.run_id;
      }
    } catch {
      // Status run tidak terbaca bukan alasan untuk membiarkan task tetap aktif.
    }
    await api(`/api/automations/${automation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: false }),
    });
    toast(
      cancelled
        ? `Stop: run ${cancelled} dibatalkan; task dinonaktifkan`
        : "Task dihentikan; tidak ada run aktif"
    );
    await loadAutoPilot();
    render();
  } catch (error) {
    toast(`Stop gagal: ${error.message}`, true);
    await loadAutoPilot();
    render();
  }
}

async function deleteAutoPilotTask(automation) {
  if (!confirm(`Hapus Auto Pilot Task “${automation.name}”?`)) return;
  try {
    await api(`/api/automations/${automation.id}`, { method: "DELETE" });
    toast("Auto Pilot Task dihapus");
    await loadAutoPilot();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

let autoPilotPollTimer = null;

function ensureAutoPilotPolling() {
  if (autoPilotPollTimer) return;
  autoPilotPollTimer = setInterval(async () => {
    if (state.view !== "auto-pilot") return;
    if ($("#view input:focus, #view textarea:focus, #view select:focus")) return;
    await loadAutoPilot();
    render();
  }, 10000);
}

function renderAutoPilot(view) {
  ensureAutoPilotPolling();
  const tasks = state.automations.filter(
    (automation) => automation.kind === "autopilot" || automation.kind === "autopilot-done"
  );
  const done = tasks.filter((automation) => automation.kind === "autopilot-done").length;
  const active = tasks.filter(
    (automation) => automation.kind === "autopilot" && automation.enabled
  ).length;
  const paused = tasks.filter(
    (automation) => automation.kind === "autopilot" && !automation.enabled
  ).length;
  const mode = state.autoPilotMode;
  const loaded = !!mode;
  const on = !!(mode && mode.on);
  const ledger = state.autoPilotLedger;
  const ledgerOn = !!(ledger && String(ledger.mode || "").toUpperCase() === "ON");
  const ledgerUntil =
    ledgerOn && ledger.until_epoch
      ? new Date(ledger.until_epoch * 1000).toLocaleTimeString("id-ID", {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })
      : "";

  view.append(
    h(
      "div",
      { class: "section-head" },
      h(
        "h2",
        {},
        "Auto Pilot Task",
        h("span", {
          class: "count",
          text: loaded
            ? `· ${active} aktif · ${done} Done${paused ? ` · ${paused} dijeda` : ""}`
            : "· memuat…",
        })
      ),
      h(
        "button",
        { class: "btn", type: "button", onclick: async () => { await loadAutoPilot(); render(); } },
        "Muat ulang"
      )
    )
  );

  view.append(
    h(
      "div",
      { class: "panel", style: "margin-bottom:12px" },
      h(
        "div",
        { class: "section-head" },
        h("h3", {}, "Mode Auto Pilot"),
        h("span", {
          class: `badge ${loaded ? (on ? "ok" : "draft") : "draft"}`,
          text: loaded ? (on ? "ON" : "OFF") : "memuat…",
        })
      ),
      h("p", {
        class: "card-desc",
        text: !loaded
          ? "Membaca mode Auto Pilot dan daftar task…"
          : on
            ? "ON — setiap task aktif mengirim satu putaran ke worker-nya sesuai jadwal task (default 1 menit) sampai ditandai Done."
            : "OFF — task tetap tersimpan, tetapi tidak ada putaran baru yang dikirim.",
      }),
      // Dua sakelar yang berbeda: scheduler (di atas) dan ledger (gerbang claim).
      // Menampilkan ledger di sini mencegah "ON di layar, ditolak di claim".
      h("p", {
        class: "card-desc",
        style: on && !ledgerOn ? "color:var(--red)" : null,
        text: !ledger
          ? "Ledger: tidak terbaca."
          : ledgerOn
            ? `Ledger: ON sampai ${ledgerUntil} · claim diizinkan (level ${ledger.level || "observe"}).`
            : on
              ? "Ledger: OFF — worker akan menolak claim. Tekan OFF lalu ON lagi untuk membuka ledger 60 menit."
              : "Ledger: OFF · claim ditolak selama Auto Pilot mati.",
      }),
      h(
        "div",
        { class: "row-actions", style: "margin-top:8px" },
        h("button", {
          class: on ? "btn" : "btn primary",
          type: "button",
          disabled: loaded ? null : "disabled",
          onclick: () => setAutoPilotMode(!on),
          text: loaded ? (on ? "Matikan Auto Pilot" : "Nyalakan Auto Pilot") : "Memuat…",
        })
      ),
      h("p", {
        class: "card-desc",
        text: "Tidak ada Timer Aktif atau Total Item: putaran berhenti hanya ketika task ditandai Done atau mode dimatikan.",
      })
    )
  );

  view.append(
    h(
      "div",
      { class: "panel", style: "margin-bottom:12px" },
      h("div", { class: "section-head" }, h("h3", {}, "Tambah dari Planning.md")),
      h(
        "div",
        { class: "grid-2" },
        h(
          "div",
          { class: "field" },
          h("label", { for: "ap-waker" }, "Worker"),
          (() => {
            const select = h("select", { id: "ap-waker" });
            state.wakers
              .filter((waker) => waker.status === "active")
              .forEach((waker) => select.append(h("option", { value: waker.id, text: waker.name })));
            return select;
          })()
        ),
        h(
          "div",
          { class: "field" },
          h("label", { for: "ap-overlap" }, "Bila worker masih berjalan"),
          (() => {
            const select = h("select", { id: "ap-overlap" });
            [
              ["skip", "Lewati putaran ini"],
              ["queue", "Antrekan putaran ini"],
              ["replace", "Ganti run yang sedang berjalan"],
            ].forEach(([value, label]) => select.append(h("option", { value, text: label })));
            return select;
          })()
        )
      ),
      h(
        "div",
        { class: "field", style: "margin-top:10px" },
        h("label", { for: "ap-path" }, "Path Planning.md"),
        h("input", { id: "ap-path", type: "text", placeholder: "/home/irsofka/proyek/Planning.md" })
      ),
      h(
        "div",
        { class: "field", style: "margin-top:10px" },
        h("label", { for: "ap-name" }, "Nama task (opsional)"),
        h("input", { id: "ap-name", type: "text", placeholder: "Nama proyek / Planning.md" })
      ),
      h(
        "div",
        { class: "row-actions", style: "margin-top:10px" },
        h("button", { class: "btn primary", type: "button", onclick: createAutoPilotTask }, "Tambah task"),
        h("span", {
          class: "card-desc",
          text: "Atau minta @Worker: “tambahkan /path/Planning.md ke auto pilot task”.",
        })
      )
    )
  );

  if (state.autoPilotError) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "!" }),
        h("strong", {}, "Tidak bisa membaca Auto Pilot Task"),
        h("p", { text: state.autoPilotError })
      )
    );
    return;
  }

  if (!tasks.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: loaded ? "◎" : "◌" }),
        h("strong", {}, loaded ? "Belum ada Auto Pilot Task" : "Memuat Auto Pilot Task…"),
        h("p", {
          text: loaded
            ? "Buat Planning.md, lalu tambahkan path-nya di atas atau lewat @Worker. Setelah mode AUTO, worker akan looping setiap 1 menit sampai task ditandai Done."
            : "Membaca antrean task dan mode dari daemon.",
        })
      )
    );
    return;
  }

  tasks.forEach((automation) => {
    const [cls, label] = autoPilotTaskStatus(automation);
    const path = autoPilotPlanPath(automation);
    const isDone = automation.kind === "autopilot-done";
    view.append(
      h(
        "div",
        { class: "panel", style: "margin-bottom:12px" },
        h(
          "div",
          { class: "section-head" },
          h("h3", {}, automation.name),
          h("span", { class: `badge ${cls}`, text: label })
        ),
        path ? h("p", { class: "card-desc", text: `Planning: ${path}` }) : null,
        h("p", {
          class: "card-desc",
          text: `${autoPilotCadence(automation.cron)} · ${overlapLabel(automation.overlap_policy)}`,
        }),
        // Operator boleh memindahkan task ke worker lain tanpa menghapus task.
        // Pilihan disimpan lewat PATCH waker_id; putaran berikutnya memakai worker itu.
        h(
          "div",
          { class: "field", style: "margin-top:8px;max-width:380px" },
          h("label", {}, "Worker untuk task ini"),
          (() => {
            const select = h("select", { title: "Ganti worker Auto Pilot Task" });
            state.wakers
              .filter((waker) => waker.status === "active")
              .forEach((waker) =>
                select.append(
                  h("option", {
                    value: waker.id,
                    text: `${waker.name} · ${waker.engine_key}`,
                  })
                )
              );
            // Worker saat ini bisa saja sudah nonaktif; tetap tampilkan agar
            // pilihan tidak diam-diam berpindah ke worker lain.
            if (!state.wakers.some((waker) => waker.id === automation.waker_id)) {
              select.append(
                h("option", {
                  value: automation.waker_id,
                  text: automation.waker_name || automation.waker_id,
                })
              );
            }
            select.value = automation.waker_id;
            select.addEventListener("change", () =>
              changeAutoPilotWorker(automation, select.value)
            );
            return select;
          })()
        ),
        h("p", {
          class: "card-desc",
          text: `Berikutnya: ${formatWhen(automation.next_run_at)} · terakhir: ${formatWhen(automation.last_run_at)}`,
        }),
        h(
          "div",
          { class: "row-actions", style: "margin-top:8px" },
          h("button", {
            class: "btn",
            type: "button",
            onclick: () => {
              state.selectedId = automation.waker_id;
              const item = document.querySelector('.nav-item[data-view="chat"]');
              if (item) item.click();
            },
            text: "Lihat proses",
          }),
          h("button", {
            class: "btn primary",
            type: "button",
            disabled: automation.running ? "disabled" : null,
            title: "Aktifkan task dan kirim satu putaran sekarang",
            onclick: () => playAutoPilotTask(automation),
            text: "▶ Play",
          }),
          h("button", {
            class: "btn",
            type: "button",
            disabled: !automation.enabled && !automation.running ? "disabled" : null,
            title: "Hentikan jadwal; run yang sedang berjalan tidak dibatalkan",
            onclick: () => pauseAutoPilotTask(automation),
            text: "⏸ Pause",
          }),
          h("button", {
            class: "btn danger",
            type: "button",
            disabled: !automation.enabled && !automation.running ? "disabled" : null,
            title: "Batalkan run aktif dan nonaktifkan task",
            onclick: () => stopAutoPilotTask(automation),
            text: "■ Stop",
          }),
          h("button", {
            class: isDone ? "btn" : "btn primary",
            type: "button",
            onclick: () => markAutoPilotDone(automation, !isDone),
            text: isDone ? "Aktifkan lagi" : "Tandai Done",
          }),
          h("button", {
            class: "link-btn",
            type: "button",
            onclick: () => deleteAutoPilotTask(automation),
            text: "Hapus",
          })
        ),
        h("p", {
          class: "card-desc",
          text: "Play = aktifkan + kirim satu putaran sekarang. Pause = hentikan jadwal. Stop = batalkan run aktif + nonaktifkan task.",
        })
      )
    );
  });
}

async function loadTraces() {
  try {
    const params = new URLSearchParams();
    if (state.traceFilter.status) params.set("status", state.traceFilter.status);
    if (state.traceFilter.waker) params.set("waker", state.traceFilter.waker);
    params.set("limit", "200");
    const [traces, summary] = await Promise.all([
      api(`/api/traces?${params.toString()}`),
      api("/api/traces/summary").catch(() => null),
    ]);
    state.traces = traces.traces || [];
    state.traceSummary = summary;
  } catch (error) {
    toast(error.message, true);
  }
}

async function openTrace(id) {
  try {
    state.selectedTrace = id;
    state.traceDetail = await api(`/api/traces/${id}`);
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function durationLabel(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} dtk`;
  return `${Math.floor(seconds / 60)} mnt ${Math.round(seconds % 60)} dtk`;
}

function renderTraces(view) {
  const summary = state.traceSummary || {};
  const byStatus = Object.fromEntries(
    (summary.by_status || []).map((item) => [item.status, item.count])
  );
  const total = Object.values(byStatus).reduce((sum, value) => sum + value, 0);

  const cards = [
    ["Total run", `${total}`, "seluruh riwayat"],
    ["Berhasil", `${byStatus.succeeded || 0}`, "status succeeded"],
    ["Gagal", `${byStatus.failed || 0}`, "status failed"],
    ["Berjalan", `${(byStatus.running || 0) + (byStatus.queued || 0)}`, "running + queued"],
    ["24 jam", `${summary.last_24h ?? 0}`, "run terakhir"],
    [
      "Rata-rata",
      summary.avg_seconds !== null && summary.avg_seconds !== undefined
        ? `${Number(summary.avg_seconds).toFixed(1)} dtk`
        : "—",
      "durasi run",
    ],
  ];

  const grid = h("div", { class: "grid" });
  cards.forEach(([label, value, sub]) => {
    grid.append(
      h(
        "div",
        { class: "card", style: "cursor:default" },
        h("div", { class: "stat-value", text: value }),
        h("div", { class: "stat-label", text: label }),
        h("div", { class: "stat-sub", text: sub })
      )
    );
  });
  view.append(h("div", { class: "section-head" }, h("h2", {}, "Papan kerja")), grid);

  // Token per worker: hanya run yang benar-benar melaporkan usage yang dijumlahkan.
  const byWaker = (summary.by_waker || []).filter((row) => row.token_reported_runs > 0);
  if (byWaker.length) {
    const nameOf = (id) => (state.wakers.find((waker) => waker.id === id) || {}).name || id;
    const tokenTable = h(
      "table",
      { class: "table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Worker"),
          h("th", {}, "Run"),
          h("th", {}, "Token masuk"),
          h("th", {}, "Token keluar"),
          h("th", {}, "Melaporkan")
        )
      )
    );
    const tokenBody = h("tbody");
    byWaker.forEach((row) => {
      const num = (value) => (value == null ? "—" : Number(value).toLocaleString("id-ID"));
      tokenBody.append(
        h(
          "tr",
          {},
          h("td", {}, h("strong", { text: nameOf(row.waker_id) })),
          h("td", { text: `${row.count}` }),
          h("td", { text: num(row.input_tokens) }),
          h("td", { text: num(row.output_tokens) }),
          h("td", { text: `${row.token_reported_runs}/${row.count}` })
        )
      );
    });
    tokenTable.append(tokenBody);
    view.append(
      h(
        "div",
        { class: "panel", style: "margin-top:16px" },
        h("div", { class: "section-head" }, h("h2", {}, "Token per worker")),
        tokenTable,
        h("p", {
          class: "card-desc",
          text:
            "Hanya run yang melaporkan usage yang dijumlahkan. Kolom terakhir menunjukkan berapa run yang angkanya nyata, bukan NULL.",
        })
      )
    );
  }

  const statusSelect = h("select", {
    onchange: (event) => {
      state.traceFilter.status = event.target.value;
      loadTraces().then(render);
    },
  });
  ["", "succeeded", "failed", "running", "queued", "cancelled"].forEach((value) => {
    const option = h("option", {
      value,
      text: value === "" ? "Semua status" : value,
    });
    if (state.traceFilter.status === value) option.selected = true;
    statusSelect.append(option);
  });

  const wakerSelect = h("select", {
    onchange: (event) => {
      state.traceFilter.waker = event.target.value;
      loadTraces().then(render);
    },
  });
  const allWorkers = h("option", { value: "", text: "Semua worker" });
  wakerSelect.append(allWorkers);
  state.wakers.forEach((waker) => {
    const option = h("option", { value: waker.id, text: waker.name });
    if (state.traceFilter.waker === waker.id) option.selected = true;
    wakerSelect.append(option);
  });

  view.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:20px" },
      h("h2", {}, "Trace", h("span", { class: "count", text: `· ${state.traces.length}` })),
      h(
        "div",
        { class: "row-actions" },
        statusSelect,
        wakerSelect,
        h("button", { class: "btn", onclick: () => loadTraces().then(render) }, "Muat ulang")
      )
    )
  );

  if (!state.traces.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("strong", { text: "Belum ada trace" }),
        h("p", { text: "Setiap run worker akan muncul di sini beserta langkahnya." })
      )
    );
    return;
  }

  const body = h("tbody");
  state.traces.forEach((trace) => {
    body.append(
      h(
        "tr",
        { onclick: () => openTrace(trace.id) },
        h("td", { text: timeAgo(trace.started_at) }),
        h("td", { text: trace.waker_name }),
        h("td", { text: trace.engine_key }),
        h("td", {}, runStatusBadge(trace.status)),
        h("td", { text: durationLabel(trace.duration_ms) }),
        h("td", { text: String(trace.events) }),
        h("td", {
          text:
            trace.input_tokens !== null && trace.input_tokens !== undefined
              ? `${trace.input_tokens}/${trace.output_tokens ?? 0}`
              : "—",
        }),
        h("td", { class: "path", text: (trace.content || "").slice(0, 60) || "—" })
      )
    );
  });
  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            ...["Waktu", "Worker", "Engine", "Status", "Durasi", "Event", "Token (in/out)", "Pesan"].map(
              (label) => h("th", {}, label)
            )
          )
        ),
        body
      )
    )
  );

  if (state.traceDetail) {
    const detail = state.traceDetail;
    const panel = h("div", { class: "panel", style: "margin-top:16px" });
    panel.append(
      h(
        "div",
        { class: "section-head" },
        h("h2", {}, `Trace · ${detail.id}`),
        h(
          "button",
          {
            class: "link-btn",
            onclick: () => {
              state.traceDetail = null;
              state.selectedTrace = null;
              render();
            },
          },
          "Tutup"
        )
      ),
      h("p", {
        class: "card-desc",
        text: `${detail.waker_name} · ${detail.engine_key}${
          detail.model ? " · " + detail.model : ""
        } · ${detail.status}${
          detail.input_tokens !== null && detail.input_tokens !== undefined
            ? ` · token ${detail.input_tokens}/${detail.output_tokens ?? 0}`
            : ""
        }`,
      }),
      h("pre", { class: "prompt-box", text: detail.prompt || "" }),
      h("div", { class: "section-head", style: "margin-top:12px" }, h("h2", {}, "Langkah"))
    );
    (detail.events || []).forEach((event) => {
      panel.append(
        h(
          "div",
          { class: "row-actions", style: "margin-bottom:4px" },
          h("span", { class: "badge", text: event.kind }),
          h("span", { class: "card-slug", text: `${event.seq} · ${formatWhen(event.at)}` }),
          h("span", { text: (event.text || "").slice(0, 120) })
        )
      );
    });
    view.append(panel);
  }
}

function runStatusBadge(status) {
  const map = {
    succeeded: ["ok", "berhasil"],
    failed: ["bad", "gagal"],
    running: ["pane", "berjalan"],
    queued: ["draft", "menunggu slot"],
  };
  const [cls, label] = map[status] || ["draft", status];
  return h("span", { class: `badge ${cls}`, text: label });
}

async function cancelRun(runId) {
  try {
    await api(`/api/runs/${runId}/cancel`, { method: "POST" });
    toast("Run dibatalkan");
    const data = await api("/api/runs");
    state.runs = data.runs || [];
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderRuns(view) {
  if (!state.runs.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "▶" }),
        h("strong", { text: "Belum ada run" }),
        h("p", { text: "Kirim pesan dari halaman Chat; setiap eksekusi tercatat di sini." })
      )
    );
    return;
  }

  view.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Riwayat eksekusi", h("span", { class: "count", text: `· ${state.runs.length}` }))
    )
  );

  const body = h("tbody");
  state.runs.forEach((run) => {
    const waker = state.wakers.find((w) => w.id === run.waker_id);
    const preview = (run.output || "").replace(/\s+/g, " ").slice(0, 90);
    body.append(
      h(
        "tr",
        {},
        h("td", { text: timeAgo(run.started_at) }),
        h("td", {}, h("strong", { text: waker ? waker.name : run.waker_id })),
        h("td", { text: run.engine_key }),
        h("td", {}, runStatusBadge(run.status)),
        h("td", { class: "path", text: preview || "—", title: run.output || "" }),
        h(
          "td",
          {},
          ["running", "queued"].includes(run.status)
            ? h("button", {
                class: "link-btn",
                onclick: () => cancelRun(run.id),
                text: "Hentikan",
              })
            : null
        )
      )
    );
  });

  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h("tr", {}, ...["Waktu", "Worker", "Engine", "Status", "Keluaran", "Aksi"].map((label) => h("th", {}, label)))
        ),
        body
      )
    )
  );
}

function renderEngines(view) {
  if (state.enginesError) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "!" }),
        h("strong", { text: "Registry tidak terbaca" }),
        h("p", { text: state.enginesError })
      )
    );
    return;
  }

  const groups = state.engineGroups || [];
  if (groups.length) {
    const groupTable = h(
      "table",
      { class: "table" },
      h(
        "thead",
        {},
        h("tr", {}, h("th", {}, "Grup"), h("th", {}, "Urutan model (failover)"))
      )
    );
    const groupBody = h("tbody");
    groups.forEach((group) => {
      groupBody.append(
        h(
          "tr",
          {},
          h("td", {}, h("strong", { text: group.name })),
          h("td", { text: group.models.join(" → ") })
        )
      );
    });
    groupTable.append(groupBody);
    view.append(
      h(
        "div",
        { class: "panel" },
        h(
          "div",
          { class: "section-head" },
          h("h2", {}, "Grup model", h("span", { class: "count", text: `· ${groups.length} grup` }))
        ),
        groupTable,
        h("p", {
          class: "card-desc",
          text: "Worker yang memilih nama grup mencoba model pertama; bila gagal, run berikutnya memakai model di kanannya.",
        })
      )
    );
  }

  view.append(
    h(
      "div",
      { class: "section-head", style: groups.length ? "margin-top:20px" : null },
      h("h2", {}, "Registry", h("span", { class: "count", text: `· ${state.engines.length} mesin` }))
    )
  );

  const table = h(
    "table",
    { class: "table" },
    h(
      "thead",
      {},
      h("tr", {}, h("th", {}, "Kunci"), h("th", {}, "Jenis"), h("th", {}, "Jalur run"), h("th", {}, "Peran"), h("th", {}, "Model"), h("th", {}, "Kuota"))
    )
  );
  const body = h("tbody");
  state.engines.forEach((e) => {
    body.append(
      h(
        "tr",
        {},
        h("td", {}, h("strong", { text: e.key })),
        h(
          "td",
          {},
          h("span", { class: `badge ${e.kind}` }, h("span", { class: "swatch" }), e.kind),
          h("span", { class: "card-slug", style: "margin-left:6px", text: e.provider || "other" })
        ),
        h(
          "td",
          {},
          h("span", {
            class: `badge ${e.run_path === "unsupported" ? "bad" : "ok"}`,
            text: e.run_path || "—",
          })
        ),
        h("td", { text: e.role || "—" }),
        h("td", {}, engineModelControl(e)),
        h("td", { text: e.quota_gated ? (e.windows || []).join(" · ") || "terpantau" : "—" })
      )
    );
  });
  table.append(body);
  view.append(h("div", { class: "panel" }, table));

  // Potret kuota: angka dari tabel engine_quota, bukan taksiran console.
  const gated = (state.quota || []).filter((q) => (q.gate_windows || []).length > 0);
  if (gated.length) {
    const quotaTable = h(
      "table",
      { class: "table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Mesin"),
          h("th", {}, "Status"),
          h("th", {}, "Sisa terkecil"),
          h("th", {}, "Ambang"),
          h("th", {}, "Jendela"),
          h("th", {}, "Alasan")
        )
      )
    );
    const quotaBody = h("tbody");
    gated.forEach((q) => {
      const badge = q.status === "ok" ? "ok" : q.status === "low" ? "paused" : "draft";
      const worst =
        q.worst_remaining == null ? "—" : `${(q.worst_remaining * 100).toFixed(1)}%`;
      const windows =
        (q.windows || [])
          .map((w) => `${w.window} ${(w.remaining_fraction * 100).toFixed(1)}%`)
          .join(" · ") || "—";
      quotaBody.append(
        h(
          "tr",
          {},
          h("td", {}, h("strong", { text: q.engine_key })),
          h("td", {}, h("span", { class: `badge ${badge}` }, q.status)),
          h("td", { text: worst }),
          h("td", { text: `${Math.round((q.min_fraction || 0) * 100)}%` }),
          h("td", { text: windows }),
          h("td", { class: "card-desc", text: q.reason || "" })
        )
      );
    });
    quotaTable.append(quotaBody);
    view.append(
      h(
        "div",
        { class: "panel", style: "margin-top:16px" },
        h(
          "div",
          { class: "section-head" },
          h("h2", {}, "Sisa kuota", h("span", { class: "count", text: `· ${gated.length} mesin bergerbang` }))
        ),
        quotaTable,
        h("p", {
          class: "card-desc",
          text:
            "Angka dibaca dari tabel engine_quota. Mesin tanpa meter atau tanpa quota_gate tidak pernah dipilih oleh engine_key=auto.",
        })
      )
    );
  }

  if (state.skipped.length) {
    view.append(
      h(
        "div",
        { class: "panel", style: "margin-top:16px" },
        h("div", { class: "section-head" }, h("h2", {}, "Entri yang ditolak")),
        ...state.skipped.map((s) => h("p", { class: "card-desc", text: `${s.key}: ${s.reason}` }))
      )
    );
  }
}

let qoderPollTimer = null;

function pollQoderLogin() {
  if (qoderPollTimer) clearInterval(qoderPollTimer);
  qoderPollTimer = setInterval(async () => {
    let status = null;
    try {
      status = await api("/api/qoder/login");
    } catch {
      clearInterval(qoderPollTimer);
      qoderPollTimer = null;
      return;
    }
    state.qoderLogin = status;
    try {
      const account = await api("/api/qoder/account");
      state.qoder = account.qoder || state.qoder;
    } catch {
      // status akun dibiarkan apa adanya; panel tetap menampilkan alur login
    }
    if (!status.running) {
      clearInterval(qoderPollTimer);
      qoderPollTimer = null;
      if (state.view === "settings") render();
      // Login mengubah katalog model yang dilaporkan CLI; segarkan tanpa reload halaman.
      if (status.status === "succeeded") loadAll();
      return;
    }
    if (state.view === "settings") render();
  }, 1500);
}

function qoderLoginPanel() {
  const qoder = state.qoder || {};
  const login = state.qoderLogin || {};
  const loggedIn = !!qoder.logged_in;
  const running = !!login.running;
  const lines = (login.lines || []).slice(-10);

  const loginBtn = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      disabled: loggedIn || running ? "disabled" : null,
      onclick: async () => {
        try {
          const result = await api("/api/qoder/login", { method: "POST", body: "{}" });
          state.qoderLogin = {
            running: true,
            status: "running",
            lines: [result?.note || "login dimulai"],
          };
          render();
          pollQoderLogin();
        } catch (error) {
          state.qoderLogin = {
            running: false,
            status: "failed",
            lines: [String(error.message || error)],
          };
          render();
        }
      },
    },
    running ? "Menunggu login…" : "Login Qoder"
  );

  const logoutBtn = h(
    "button",
    {
      class: "btn danger",
      type: "button",
      disabled: !loggedIn || running ? "disabled" : null,
      onclick: async () => {
        state.qoderLogin = { running: false, status: "logging_out", lines: [] };
        render();
        try {
          const result = await api("/api/qoder/logout", { method: "POST", body: "{}" });
          state.qoder = result.qoder || state.qoder;
          state.qoderLogin = {
            running: false,
            status: result.ok ? "logged_out" : "failed",
            lines: result.output ? [result.output] : [],
          };
        } catch (error) {
          state.qoderLogin = {
            running: false,
            status: "failed",
            lines: [String(error.message || error)],
          };
        }
        render();
        loadAll();
      },
    },
    "Logout"
  );

  const body = h(
    "div",
    { class: "panel", style: "margin-top:16px" },
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Login Qoder"),
      h("span", { class: `badge ${loggedIn ? "ok" : "bad"}` }, loggedIn ? "terhubung" : "belum login")
    ),
    h(
      "dl",
      { class: "kv" },
      h("dt", {}, "Akun"),
      h("dd", {
        text: loggedIn
          ? `terhubung · sidik akun ${qoder.fingerprint || "—"}`
          : "belum ada akun aktif",
      }),
      h("dt", {}, "Versi CLI"),
      h("dd", { text: String(qoder.version || "—") }),
      h("dt", {}, "Terakhir diperiksa"),
      h("dd", {
        text: qoder.checked_at
          ? new Date(qoder.checked_at).toLocaleString("id-ID")
          : "—",
      }),
      h("dt", {}, "Status login"),
      h("dd", { text: login.status || "idle" })
    ),
    h("p", {
      class: "card-desc",
      text:
        "Identitas akun disamarkan di API station; sidik dipakai untuk membedakan akun. Percakapan, run, dan worker tersimpan per worker di station — berganti akun Qoder tidak menghapus sesi.",
    }),
    h("div", { class: "row-actions", style: "margin-top:12px" }, loginBtn, logoutBtn)
  );

  if (lines.length) {
    body.append(
      h("pre", {
        style:
          "margin-top:12px;padding:10px;border-radius:8px;background:rgba(255,255,255,0.03);font-size:11px;line-height:1.5;white-space:pre-wrap;max-height:180px;overflow:auto",
        text: lines.join("\n"),
      })
    );
  }
  return body;
}

let antigravityPoll = null;

function pollAntigravityLogin() {
  if (antigravityPoll) return;
  let ticks = 0;
  antigravityPoll = setInterval(async () => {
    ticks += 1;
    try {
      const data = await api("/api/antigravity/account");
      state.antigravity = data.antigravity || state.antigravity;
      if (state.antigravity && state.antigravity.logged_in) {
        clearInterval(antigravityPoll);
        antigravityPoll = null;
        state.antigravityLogin = {
          running: false,
          status: "succeeded",
          lines: ["Login Antigravity terdeteksi."],
        };
        render();
      } else if (ticks >= 150) {
        clearInterval(antigravityPoll);
        antigravityPoll = null;
        state.antigravityLogin = {
          running: false,
          status: "timeout",
          lines: ["Belum ada login terdeteksi setelah 5 menit."],
        };
        render();
      }
    } catch (error) {
      clearInterval(antigravityPoll);
      antigravityPoll = null;
      state.antigravityLogin = {
        running: false,
        status: "failed",
        lines: [String(error.message || error)],
      };
      render();
    }
  }, 2000);
}

function antigravityLoginPanel() {
  const ag = state.antigravity || {};
  const login = state.antigravityLogin || {};
  const loggedIn = !!ag.logged_in;
  const running = !!login.running;
  const lines = (login.lines || []).slice(-10);
  const loginBtn = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      disabled: running ? "disabled" : null,
      onclick: async () => {
        state.antigravityLogin = {
          running: true,
          status: "running",
          lines: ["Membuka aplikasi Antigravity…"],
        };
        render();
        try {
          const result = await api("/api/antigravity/login", { method: "POST", body: "{}" });
          state.antigravityLogin = {
            running: true,
            status: "waiting",
            lines: [result?.note || "Selesaikan OAuth di aplikasi Antigravity"],
          };
          render();
          pollAntigravityLogin();
        } catch (error) {
          state.antigravityLogin = {
            running: false,
            status: "failed",
            lines: [String(error.message || error)],
          };
          render();
        }
      },
    },
    running ? "Menunggu login…" : loggedIn ? "Buka aplikasi Antigravity" : "Login Antigravity"
  );
  const body = h(
    "div",
    { class: "panel", style: "margin-top:16px" },
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Login Antigravity"),
      h(
        "span",
        { class: `badge ${loggedIn ? "ok" : "bad"}` },
        loggedIn ? "terhubung" : "belum login"
      )
    ),
    h(
      "dl",
      { class: "kv" },
      h("dt", {}, "Akun"),
      h("dd", {
        text: loggedIn
          ? `terhubung · ${ag.account_hint || "akun aktif"}`
          : "belum ada akun aktif",
      }),
      h("dt", {}, "Sumber status"),
      h("dd", { text: ag.source ? `log ${ag.source}` : "—" }),
      h("dt", {}, "Terakhir diperiksa"),
      h("dd", {
        text: ag.checked_at ? new Date(ag.checked_at).toLocaleString("id-ID") : "—",
      }),
      h("dt", {}, "Status login"),
      h("dd", { text: login.status || ag.status || "idle" })
    ),
    h("p", {
      class: "card-desc",
      text:
        "Antigravity tidak punya perintah login di CLI; tombol ini membuka aplikasi Antigravity untuk OAuth Google. Jika sudah login, gunakan menu akun di aplikasi untuk berganti akun. Status dibaca dari log CLI terbaru, bukan ditebak.",
    }),
    h("div", { class: "row-actions", style: "margin-top:12px" }, loginBtn)
  );
  if (lines.length) {
    body.append(
      h("pre", {
        style:
          "margin-top:12px;padding:10px;border-radius:8px;background:rgba(255,255,255,0.03);font-size:11px;line-height:1.5;white-space:pre-wrap;max-height:180px;overflow:auto",
        text: lines.join("\n"),
      })
    );
  }
  return body;
}

function renderSettings(view) {
  const stats = state.stats || {};
  const tabs = stats.tabs && typeof stats.tabs === "object" ? Object.entries(stats.tabs) : [];
  view.append(
    h(
      "div",
      { class: "panel" },
      h("div", { class: "section-head" }, h("h2", {}, "Daemon")),
      h(
        "dl",
        { class: "kv" },
        h("dt", {}, "Organisasi"),
        h("dd", { text: "IRSofka Data Teknologi" }),
        h("dt", {}, "Mesin basis data"),
        h("dd", { text: stats.db_engine || "—" }),
        h("dt", {}, "Pengguna"),
        h("dd", { text: stats.user_name || "—" }),
        h("dt", {}, "Worker tersimpan"),
        h("dd", { text: String(state.wakers.length) }),
        h("dt", {}, "Engine terdaftar"),
        h("dd", { text: String(state.engines.length) }),
        h("dt", {}, "Console"),
        h("dd", { text: location.origin + "/console" }),
        h("dt", {}, "Agen paralel"),
        h("dd", {
          text: state.limits ? `${state.limits.parallel} slot` : "—",
        }),
        h("dt", {}, "Riwayat di memori"),
        h("dd", {
          text: state.limits ? `${state.limits.keep} run` : "—",
        }),
        h("dt", {}, "Batas menganggur run"),
        h("dd", {
          text: state.limits
            ? `${Math.round(state.limits.timeout_s / 60)} menit tanpa aktivitas`
            : "—",
        }),
        h("dt", {}, "Penyimpanan waker"),
        h("dd", {
          text:
            state.backend === "POSTGRESQL"
              ? "PostgreSQL (waker, run, event, pesan)"
              : "JSON · ~/.ai-station/data/wakers.json",
        })
      )
    )
  );

  // Registry bukan tempat kerja sehari-hari: peran seperti decider/reviewer kini
  // berupa worker yang bisa di-@. Halaman mesin tetap ada untuk pemeriksaan kuota.
  view.append(
    h(
      "div",
      { class: "panel", style: "margin-top:16px" },
      h("div", { class: "section-head" }, h("h2", {}, "Model & kuota")),
      h("p", {
        class: "card-desc",
        text:
          "Peran Decider dan Verifier sekarang berupa worker yang bisa di-@ di grup. Registry mesin dibuka hanya untuk memeriksa urutan model dan sisa kuota.",
      }),
      h("button", {
        class: "link-btn",
        text: "Buka registry mesin & kuota",
        onclick: () => {
          state.view = "engines";
          render();
        },
      })
    )
  );

  view.append(qoderLoginPanel());
  view.append(antigravityLoginPanel());

  const res = state.resources;
  const gpu = res?.gpu || null;
  const decision = res?.decision || null;
  const gb = (mb) => ((mb || 0) / 1024).toFixed(1);
  const gate = (value) =>
    h("span", {
      class: `badge ${value ? "ok" : "draft"}`,
      text: value ? "diizinkan" : "fallback",
    });
  view.append(
    h(
      "div",
      { class: "panel", style: "margin-top:16px" },
      h(
        "div",
        { class: "section-head" },
        h("h2", {}, "Sumber daya & model lokal"),
        h("button", {
          class: "link-btn",
          onclick: () => loadAll(),
          text: "Muat ulang status",
        })
      ),
      h(
        "dl",
        { class: "kv" },
        h("dt", {}, "GPU"),
        h("dd", {
          text:
            gpu && gpu.available
              ? `${gpu.name} · ${gb(gpu.used_mb)} / ${gb(gpu.total_mb)} GB (bebas ${gb(gpu.free_mb)} GB)`
              : "nvidia-smi tidak tersedia",
        }),
        h("dt", {}, "Model termuat"),
        h("dd", {
          text:
            res && res.loaded_models && res.loaded_models.length
              ? res.loaded_models
                  .map((model) => `${model.name} (${model.size}, ${model.processor})`)
                  .join(" · ")
              : "tidak ada",
        }),
        h("dt", {}, "Proses render"),
        h("dd", {
          text:
            res && res.busy_processes && res.busy_processes.length
              ? res.busy_processes.join(", ")
              : "tidak ada",
        }),
        h("dt", {}, "Ringkasan lokal"),
        h(
          "dd",
          {},
          gate(decision && decision.summary_allowed),
          " ",
          h("span", {
            text: decision
              ? `${decision.summary_model} — ${decision.summary_reason}`
              : "—",
          })
        ),
        h("dt", {}, "Embedding"),
        h(
          "dd",
          {},
          gate(decision && decision.embedding_allowed),
          " ",
          h("span", { text: decision ? decision.embedding_reason : "—" })
        ),
        h("dt", {}, "Kebijakan"),
        h("dd", { text: res ? res.policy_source : "—" })
      )
    )
  );

  if (tabs.length) {
    const body = h("tbody");
    tabs.forEach(([name, info]) => {
      body.append(
        h(
          "tr",
          {},
          h("td", {}, h("strong", { text: name })),
          h("td", {}, h("span", { class: `badge ${info.alive ? "ok" : "draft"}`, text: info.alive ? "hidup" : "mati" })),
          h("td", { text: info.pid ? `pid ${info.pid}` : "—" }),
          h("td", { text: info.process || "—" })
        )
      );
    });
    view.append(
      h(
        "div",
        { class: "panel", style: "margin-top:16px" },
        h("div", { class: "section-head" }, h("h2", {}, "Pane terminal")),
        h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "Tab"), h("th", {}, "Status"), h("th", {}, "PID"), h("th", {}, "Proses"))), body)
      )
    );
  }
}

async function loadGroups() {
  try {
    const data = await api("/api/groups");
    state.groups = data.groups || [];
  } catch (error) {
    toast(error.message, true);
  }
}

async function fetchGroup(groupId) {
  const data = await api(`/api/groups/${groupId}`);
  state.groupDetail[groupId] = data;
  return data;
}

async function loadGroupDetail(groupId) {
  try {
    await fetchGroup(groupId);
    state.expandedGroup = groupId;
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function openGroupModal() {
  const active = state.wakers.filter((waker) => waker.status === "active");
  if (!active.length) {
    toast("Buat dan aktifkan worker dulu", true);
    return;
  }
  const box = $("#g-members");
  clear(box);
  active.forEach((waker) => {
    box.append(
      h(
        "label",
        { class: "member-row" },
        h("input", { type: "checkbox", value: waker.id }),
        h("span", { text: waker.name })
      )
    );
  });
  $("#g-name").value = "";
  $("#g-sop").value = "";
  $("#group-error").classList.add("hidden");
  $("#group-modal").classList.remove("hidden");
  setTimeout(() => $("#g-name").focus(), 30);
}

function closeGroupModal() {
  $("#group-modal").classList.add("hidden");
}

async function submitGroup(event) {
  event.preventDefault();
  const errorBox = $("#group-error");
  errorBox.classList.add("hidden");
  const members = [...document.querySelectorAll("#g-members input:checked")].map(
    (input) => input.value
  );
  try {
    await api("/api/groups", {
      method: "POST",
      body: JSON.stringify({
        name: $("#g-name").value.trim(),
        sop: $("#g-sop").value,
        members,
      }),
    });
    closeGroupModal();
    toast("Grup dibuat");
    await loadGroups();
    render();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

async function deleteGroup(group) {
  if (!confirm(`Hapus grup “${group.name}”?`)) return;
  try {
    await api(`/api/groups/${group.id}`, { method: "DELETE" });
    toast("Grup dihapus");
    if (state.expandedGroup === group.id) state.expandedGroup = null;
    await loadGroups();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function sendGroupMessage(groupId) {
  const input = $("#group-input");
  const text = input ? input.value.trim() : "";
  if (!text) return;
  try {
    await api(`/api/groups/${groupId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: text }),
    });
    if (input) input.value = "";
    toast("Pesan dikirim; anggota menjawab berurutan");
    let lastCount = (state.groupDetail[groupId]?.messages || []).length;
    let stable = 0;
    for (let tick = 0; tick < 80; tick += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const data = await fetchGroup(groupId);
      if (state.view === "groups") render();
      if (data.messages.length === lastCount) {
        stable += 1;
        if (stable >= 3) break;
      } else {
        stable = 0;
        lastCount = data.messages.length;
      }
    }
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveGoal(groupId, objective, status) {
  try {
    await api(`/api/groups/${groupId}/goal`, {
      method: "PUT",
      body: JSON.stringify({ objective, status }),
    });
    await fetchGroup(groupId);
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function addTodo(groupId) {
  const input = $("#todo-input");
  const title = input ? input.value.trim() : "";
  if (!title) return;
  try {
    await api(`/api/groups/${groupId}/todos`, {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    if (input) input.value = "";
    await fetchGroup(groupId);
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function toggleTodo(groupId, todo) {
  try {
    await api(`/api/todos/${todo.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: todo.status === "done" ? "open" : "done" }),
    });
    await fetchGroup(groupId);
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function removeTodo(groupId, todoId) {
  try {
    await api(`/api/todos/${todoId}`, { method: "DELETE" });
    await fetchGroup(groupId);
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderGroups(view) {
  view.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Groups", h("span", { class: "count", text: `· ${state.groups.length}` })),
      h("button", { class: "btn primary", onclick: openGroupModal }, "＋ Grup baru")
    )
  );

  if (!state.groups.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "◍" }),
        h("strong", { text: "Belum ada grup" }),
        h("p", {
          text: "Grup menjalankan beberapa worker berurutan dalam satu percakapan, dengan SOP yang sama.",
        }),
        h("button", { class: "btn primary", onclick: openGroupModal }, "Buat grup")
      )
    );
    return;
  }

  const body = h("tbody");
  state.groups.forEach((group) => {
    body.append(
      h(
        "tr",
        {},
        h("td", {}, h("strong", { text: group.name }), h("div", { class: "card-slug", text: `${group.members} anggota` })),
        h("td", { class: "path", text: (group.sop || "").slice(0, 80) || "—" }),
        h(
          "td",
          {},
          h(
            "div",
            { class: "row-actions" },
            h("button", { class: "link-btn", onclick: () => loadGroupDetail(group.id), text: "Buka" }),
            h("button", { class: "link-btn", onclick: () => deleteGroup(group), text: "Hapus" })
          )
        )
      )
    );
  });

  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h("tr", {}, ...["Nama", "SOP", "Aksi"].map((label) => h("th", {}, label)))
        ),
        body
      )
    )
  );

  if (!state.expandedGroup) return;
  const detail = state.groupDetail[state.expandedGroup] || { members: [], messages: [] };
  const section = h("div", { class: "panel", style: "margin-top:16px" });
  section.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, `Percakapan · ${detail.name || state.expandedGroup}`),
      h(
        "button",
        {
          class: "link-btn",
          onclick: () => {
            state.expandedGroup = null;
            render();
          },
        },
        "Tutup"
      )
    )
  );

  const chips = h("div", { class: "card-meta", style: "margin-bottom:10px" });
  (detail.members || []).forEach((member) =>
    chips.append(h("span", { class: "badge", text: member.waker_name }))
  );
  section.append(chips);

  if (detail.sop) {
    section.append(h("pre", { class: "prompt-box", text: detail.sop }));
  }

  section.append(
    h("div", { class: "section-head", style: "margin-top:14px" }, h("h2", {}, "Target"))
  );
  if (detail.goal) {
    const goalStatus =
      detail.goal.status === "achieved"
        ? ["ok", "tercapai"]
        : detail.goal.status === "abandoned"
          ? ["draft", "dibatalkan"]
          : ["pane", "aktif"];
    section.append(
      h(
        "div",
        { class: "row-actions" },
        h("span", { class: `badge ${goalStatus[0]}`, text: goalStatus[1] }),
        h("span", { text: detail.goal.objective }),
        detail.goal.status === "active"
          ? h(
              "button",
              {
                class: "link-btn",
                onclick: () =>
                  saveGoal(state.expandedGroup, detail.goal.objective, "achieved"),
              },
              "Tandai tercapai"
            )
          : null
      )
    );
  } else {
    section.append(
      h(
        "div",
        { class: "row-actions" },
        h("input", { id: "goal-input", placeholder: "Target grup…", style: "flex:1" }),
        h(
          "button",
          {
            class: "btn",
            onclick: () => {
              const value = $("#goal-input") ? $("#goal-input").value.trim() : "";
              if (value) saveGoal(state.expandedGroup, value, "active");
            },
          },
          "Tetapkan target"
        )
      )
    );
  }

  section.append(
    h("div", { class: "section-head", style: "margin-top:14px" }, h("h2", {}, "Todo")),
    h(
      "div",
      { class: "row-actions", style: "margin-bottom:8px" },
      h("input", { id: "todo-input", placeholder: "Tambah todo…", style: "flex:1" }),
      h(
        "button",
        {
          class: "btn",
          onclick: () => addTodo(state.expandedGroup),
        },
        "Tambah"
      )
    )
  );
  (detail.todos || []).forEach((todo) => {
    section.append(
      h(
        "div",
        { class: "row-actions", style: "margin-bottom:6px" },
        h("span", {
          class: `badge ${todo.status === "done" ? "ok" : "draft"}`,
          text: todo.status,
        }),
        h("span", {
          text: todo.title,
          style: todo.status === "done" ? "text-decoration:line-through" : "",
        }),
        h(
          "button",
          {
            class: "link-btn",
            onclick: () => toggleTodo(state.expandedGroup, todo),
            text: todo.status === "done" ? "Buka" : "Selesai",
          }
        ),
        h(
          "button",
          {
            class: "link-btn",
            onclick: () => removeTodo(state.expandedGroup, todo.id),
            text: "Hapus",
          }
        )
      )
    );
  });

  (detail.messages || []).forEach((message) => {
    if (message.role === "system") {
      section.append(
        h("p", { class: "card-desc", style: "margin:6px 0", text: message.content })
      );
      return;
    }
    section.append(
      h(
        "div",
        {
          class: "msg " + (message.role === "user" ? "user" : "waker"),
          style: "max-width:100%;margin-bottom:8px",
        },
        h("div", {
          class: "msg-role",
          text: message.role === "user" ? "Operator" : message.who,
        }),
        h("div", { class: "msg-text", text: message.content })
      )
    );
  });

  section.append(
    h(
      "div",
      { class: "chat-composer", style: "margin-top:12px;padding:0" },
      h("input", { id: "group-input", placeholder: "Tulis pesan untuk grup…" }),
      h(
        "button",
        {
          class: "btn primary",
          onclick: () => sendGroupMessage(state.expandedGroup),
        },
        "Kirim"
      )
    )
  );

  view.append(section);
}

async function loadChannels() {
  try {
    const data = await api("/api/channels");
    state.channels = data.channels || [];
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadChannelDetail(channelId) {
  try {
    const [bindings, messages] = await Promise.all([
      api(`/api/channels/${channelId}/bindings`),
      api(`/api/channels/${channelId}/messages`),
    ]);
    state.channelDetail[channelId] = {
      bindings: bindings.bindings || [],
      messages: messages.messages || [],
    };
    state.expandedChannel = channelId;
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function openChannelModal() {
  $("#c-provider").value = "local";
  $("#c-name").value = "";
  $("#c-cred").value = "";
  $("#channel-error").classList.add("hidden");
  $("#channel-modal").classList.remove("hidden");
  setTimeout(() => $("#c-name").focus(), 30);
}

function closeChannelModal() {
  $("#channel-modal").classList.add("hidden");
}

async function submitChannel(event) {
  event.preventDefault();
  const errorBox = $("#channel-error");
  errorBox.classList.add("hidden");
  try {
    await api("/api/channels", {
      method: "POST",
      body: JSON.stringify({
        provider: $("#c-provider").value,
        name: $("#c-name").value.trim(),
        credentials_ref: $("#c-cred").value.trim(),
      }),
    });
    closeChannelModal();
    toast("Channel dibuat");
    await loadChannels();
    render();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

async function testChannelWebhook(channel) {
  const text = prompt(
    "Pesan uji ke channel:",
    "AI Station terhubung. Pesan uji dari IRSofka Data Teknologi."
  );
  if (!text) return;
  try {
    await api(`/api/channels/${channel.id}/test`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    toast("Pesan uji terkirim");
    await loadChannelDetail(channel.id);
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteChannel(channel) {
  if (!confirm(`Hapus channel “${channel.name}”? Binding dan riwayat pesannya ikut terhapus.`)) return;
  try {
    await api(`/api/channels/${channel.id}`, { method: "DELETE" });
    toast("Channel dihapus");
    if (state.expandedChannel === channel.id) state.expandedChannel = null;
    await loadChannels();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

async function addChannelBinding(channelId) {
  const workerId = $("#bind-worker") ? $("#bind-worker").value : "";
  const chatId = $("#bind-chat") ? $("#bind-chat").value.trim() : "";
  if (!chatId) {
    toast("Isi ID chat/grup tujuan", true);
    return;
  }
  try {
    await api(`/api/channels/${channelId}/bindings`, {
      method: "POST",
      body: JSON.stringify({ waker_id: workerId, external_chat_id: chatId }),
    });
    toast("Binding ditambahkan");
    await loadChannelDetail(channelId);
  } catch (error) {
    toast(error.message, true);
  }
}

async function removeChannelBinding(channelId, bindingId) {
  try {
    await api(`/api/channels/${channelId}/bindings/${bindingId}`, { method: "DELETE" });
    toast("Binding dihapus");
    await loadChannelDetail(channelId);
  } catch (error) {
    toast(error.message, true);
  }
}

async function sendTestMessage(channelId) {
  const chatId = $("#test-chat") ? $("#test-chat").value.trim() : "";
  const text = $("#test-text") ? $("#test-text").value.trim() : "";
  if (!chatId || !text) {
    toast("Isi ID chat dan pesan", true);
    return;
  }
  try {
    await api(`/api/channels/${channelId}/inbound`, {
      method: "POST",
      body: JSON.stringify({ external_chat_id: chatId, text }),
    });
    toast("Pesan masuk dikirim ke worker");
    if ($("#test-text")) $("#test-text").value = "";
    for (let tick = 0; tick < 30; tick += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await loadChannelDetail(channelId);
      const detail = state.channelDetail[channelId] || { messages: [] };
      if (detail.messages.some((message) => message.direction === "out")) break;
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function renderChannels(view) {
  view.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Channel", h("span", { class: "count", text: `· ${state.channels.length}` })),
      h(
        "div",
        { class: "row-actions" },
        h("button", { class: "btn", onclick: checkCredentials }, "Periksa kredensial"),
        h("button", { class: "btn primary", onclick: openChannelModal }, "＋ Channel baru")
      )
    )
  );

  if (!state.channels.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-icon", text: "⇄" }),
        h("strong", { text: "Belum ada channel" }),
        h("p", {
          text: "Channel menghubungkan chat tim ke worker. Mulai dengan provider Lokal untuk menguji alur tanpa kredensial.",
        }),
        h("button", { class: "btn primary", onclick: openChannelModal }, "Buat channel")
      )
    );
    renderReadiness(view);
    return;
  }

  const body = h("tbody");
  state.channels.forEach((channel) => {
    body.append(
      h(
        "tr",
        {},
        h("td", {}, h("strong", { text: channel.name }), h("div", { class: "card-slug", text: channel.id })),
        h("td", {}, h("span", { class: "badge", text: channel.provider })),
        h("td", {}, h("span", { class: `badge ${channel.status === "connected" ? "ok" : "draft"}`, text: channel.status })),
        h("td", { text: String(channel.bindings) }),
        h(
          "td",
          {},
          h(
            "div",
            { class: "row-actions" },
            h("button", { class: "link-btn", onclick: () => loadChannelDetail(channel.id), text: "Detail" }),
            h("button", { class: "link-btn", onclick: () => testChannelWebhook(channel), text: "Tes webhook" }),
            h("button", { class: "link-btn", onclick: () => deleteChannel(channel), text: "Hapus" })
          )
        )
      )
    );
  });

  view.append(
    h(
      "div",
      { class: "panel" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h("tr", {}, ...["Nama", "Provider", "Status", "Binding", "Aksi"].map((label) => h("th", {}, label)))
        ),
        body
      )
    )
  );

  if (!state.expandedChannel) {
    renderReadiness(view);
    return;
  }
  const channel = state.channels.find((item) => item.id === state.expandedChannel);
  const detail = state.channelDetail[state.expandedChannel] || { bindings: [], messages: [] };
  const section = h("div", { class: "panel", style: "margin-top:16px" });
  section.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, `Binding · ${channel ? channel.name : state.expandedChannel}`),
      h(
        "button",
        {
          class: "link-btn",
          onclick: () => {
            state.expandedChannel = null;
            render();
          },
        },
        "Tutup"
      )
    )
  );

  if (!detail.bindings.length) {
    section.append(h("p", { class: "card-desc", text: "Belum ada chat yang dipasangkan." }));
  } else {
    detail.bindings.forEach((binding) => {
      section.append(
        h(
          "div",
          { class: "row-actions", style: "margin-bottom:8px" },
          h("span", { class: "badge", text: binding.external_chat_id }),
          h("span", { text: binding.waker_name || binding.waker_id }),
          h(
            "button",
            {
              class: "link-btn",
              onclick: () => removeChannelBinding(state.expandedChannel, binding.id),
              text: "Hapus",
            }
          )
        )
      );
    });
  }

  const workers = state.wakers.filter((waker) => waker.status === "active");
  const workerSelect = h("select", { id: "bind-worker" });
  workers.forEach((waker) =>
    workerSelect.append(h("option", { value: waker.id, text: waker.name }))
  );
  section.append(
    h(
      "div",
      { class: "row-actions", style: "margin-top:12px" },
      workerSelect,
      h("input", { id: "bind-chat", placeholder: "ID chat/grup", style: "max-width:220px" }),
      h("button", { class: "btn", onclick: () => addChannelBinding(state.expandedChannel) }, "Tambah binding")
    )
  );

  const firstChat = detail.bindings[0] ? detail.bindings[0].external_chat_id : "";
  section.append(
    h(
      "div",
      { class: "row-actions", style: "margin-top:16px" },
      h("input", { id: "test-chat", placeholder: "ID chat", value: firstChat, style: "max-width:200px" }),
      h("input", { id: "test-text", placeholder: "Pesan uji untuk worker", style: "flex:1" }),
      h("button", { class: "btn primary", onclick: () => sendTestMessage(state.expandedChannel) }, "Kirim uji")
    )
  );

  section.append(h("div", { class: "section-head", style: "margin-top:16px" }, h("h2", {}, "Pesan terakhir")));
  if (!detail.messages.length) {
    section.append(h("p", { class: "card-desc", text: "Belum ada pesan." }));
  } else {
    detail.messages.slice(0, 10).forEach((message) => {
      section.append(
        h(
          "div",
          { class: "msg " + (message.direction === "in" ? "user" : "waker"), style: "max-width:100%;margin-bottom:8px" },
          h("div", { class: "msg-role", text: `${message.direction === "in" ? "Masuk" : "Keluar"} · ${formatWhen(message.created_at)} · ${message.status}` }),
          h("div", { class: "msg-text", text: message.content })
        )
      );
    });
  }

  view.append(section);
  renderReadiness(view);
}

async function checkCredentials() {
  try {
    state.readiness = await api("/api/channels/readiness");
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderReadiness(view) {
  if (!state.readiness) return;
  const data = state.readiness;
  const section = h("div", { class: "panel", style: "margin-top:16px" });
  section.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Kesiapan kredensial"),
      h(
        "button",
        {
          class: "link-btn",
          onclick: () => {
            state.readiness = null;
            render();
          },
        },
        "Tutup"
      )
    )
  );

  if (!data.channels.length) {
    section.append(h("p", { class: "card-desc", text: "Belum ada channel yang dibuat." }));
  } else {
    data.channels.forEach((channel) => {
      section.append(
        h(
          "div",
          { class: "row-actions", style: "margin-bottom:8px" },
          h("span", { class: "badge", text: channel.provider }),
          h("strong", { text: channel.name || channel.provider }),
          h("span", { class: "card-slug", text: channel.credentials_ref || "(tanpa env)" }),
          h("span", {
            class: `badge ${channel.ready ? "ok" : "bad"}`,
            text: channel.ready ? "siap" : "belum diset",
          })
        )
      );
    });
  }

  section.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:12px" },
      h("h2", {}, "Yang dibutuhkan (keluar)")
    ),
    h("p", {
      class: "card-desc",
      text: "Isi variabel lingkungan di service, lalu tulis NAMA variabelnya pada channel.",
    })
  );
  Object.entries(data.outbound || {}).forEach(([provider, names]) => {
    section.append(
      h(
        "div",
        { class: "row-actions", style: "margin-bottom:6px" },
        h("span", { class: "badge", text: provider }),
        h("span", {
          class: "card-slug",
          text: names && names.length ? names.join(", ") : "tanpa kredensial",
        })
      )
    );
  });

  section.append(
    h(
      "div",
      { class: "section-head", style: "margin-top:12px" },
      h("h2", {}, "Kredensial masuk (belum aktif)")
    ),
    h("p", {
      class: "card-desc",
      text: "Ingress resmi platform belum diimplementasikan; daftar ini hanya checklist bila nanti diaktifkan.",
    })
  );
  Object.entries(data.inbound_app || {}).forEach(([provider, names]) => {
    section.append(
      h(
        "div",
        { class: "row-actions", style: "margin-bottom:6px" },
        h("span", { class: "badge", text: provider }),
        h("span", { class: "card-slug", text: (names || []).join(", ") })
      )
    );
  });

  view.append(section);
}

function renderPlaceholder(view, key) {
  const copy = {
    automations: ["Automation lahir setelah run engine", "Jadwal cron dan workflow membutuhkan eksekusi headless yang stabil lebih dulu. UI-nya sudah punya tempat."],
    runs: ["Belum ada run", "Setiap eksekusi akan muncul di sini lengkap dengan event, durasi, dan artefaknya."],
    approvals: ["Tidak ada yang menunggu", "Saat waker meminta izin untuk tool berisiko, permintaannya masuk ke sini."],
  }[key] || ["Belum ada isi", ""];
  view.append(
    h(
      "div",
      { class: "empty" },
      h("div", { class: "empty-icon", text: "◌" }),
      h("strong", { text: copy[0] }),
      h("p", { text: copy[1] })
    )
  );
}

// ---------------------------------------------------------------- drawer

function openDrawer(id) {
  const waker = state.wakers.find((w) => w.id === id || w.slug === id);
  if (!waker) return;
  state.selectedId = waker.id;
  renderDrawerBody(waker);
  $("#drawer").classList.remove("hidden");
  $("#scrim").classList.remove("hidden");
  if (waker.engine_key === "qoder") ensureCatalog();
  ensureMcp();
  loadPermissions(waker.id);
}

function renderDrawerBody(waker) {
  const engine = state.engines.find((e) => e.key === waker.engine_key);

  const body = $("#drawer-body");
  clear(body);
  body.append(
    h(
      "div",
      { class: "drawer-identity" },
      workerAvatar(waker),
      h("div", {}, h("h2", { text: waker.name }), h("p", { text: `@${waker.slug}` }))
    ),
    h(
      "dl",
      { class: "kv" },
      h("dt", {}, "Status"),
      h("dd", {}, statusBadge(waker.status)),
      h("dt", {}, "Engine"),
      h("dd", { text: `${waker.engine_key}${engine ? ` · ${engine.kind}` : " · tidak terdaftar"}` }),
      h("dt", {}, "Jalur run"),
      h("dd", { text: engine?.run_path || "—" }),
      h("dt", {}, "Model"),
      h("dd", { text: waker.model || "bawaan katalog" }),
      h("dt", {}, "Thinking"),
      h("dd", { text: waker.reasoning_effort || "bawaan model" }),
      h("dt", {}, "Context"),
      h("dd", {
        text: waker.context_window
          ? `${Math.round(waker.context_window / 1000)}K token`
          : "bawaan model",
      }),
      h("dt", {}, "Workspace"),
      h("dd", { text: waker.workspace }),
      h("dt", {}, "Dibuat"),
      h("dd", { text: new Date(waker.created_at).toLocaleString("id-ID") }),
      h("dt", {}, "Diperbarui"),
      h("dd", { text: new Date(waker.updated_at).toLocaleString("id-ID") })
    ),
    waker.description ? h("p", { class: "card-desc", text: waker.description }) : null,
    drawerStyleSection(waker),
    drawerApprovalSection(waker),
    drawerPermissionSection(waker),
    drawerModelSection(waker),
    h("div", { class: "section-head" }, h("h2", {}, "System prompt")),
    h("pre", { class: "prompt-box", text: waker.system_prompt || "(kosong)" }),
    h(
      "div",
      { class: "drawer-actions" },
      h(
        "button",
        {
          class: "btn primary",
          onclick: () => {
            state.view = "chat";
            render();
            closeDrawer();
          },
        },
        "Buka chat"
      ),
      h("button", { class: "btn", onclick: () => openModal(waker) }, "Edit"),
      h(
        "button",
        {
          class: "btn",
          onclick: () => toggleStatus(waker),
        },
        waker.status === "active" ? "Jeda" : "Aktifkan"
      ),
      h("button", { class: "btn danger", onclick: () => deleteWaker(waker) }, "Hapus")
    )
  );
}

async function loadPermissions(wakerId) {
  try {
    const data = await api(`/api/wakers/${wakerId}/permissions`);
    state.permissions[wakerId] = data.permissions || [];
    if (
      state.selectedId === wakerId &&
      !$("#drawer").classList.contains("hidden")
    ) {
      const waker = state.wakers.find((item) => item.id === wakerId);
      if (waker) renderDrawerBody(waker);
    }
  } catch {
    // izin bersifat pelengkap; drawer tetap bisa dipakai tanpa daftar ini
  }
}

async function setToolPermission(wakerId, tool, decision) {
  try {
    await api(`/api/wakers/${wakerId}/permissions`, {
      method: "PUT",
      body: JSON.stringify({ tool, decision }),
    });
    toast("Izin tool disimpan");
    const data = await api(`/api/wakers/${wakerId}/permissions`);
    state.permissions[wakerId] = data.permissions || [];
  } catch (error) {
    toast(error.message, true);
  }
}

function drawerPermissionSection(waker) {
  const permissions = state.permissions[waker.id] || [];
  const current =
    (permissions.find((item) => item.tool === "shell") || {}).decision || "allow";
  const select = h("select", {});
  [
    ["allow", "Izinkan"],
    ["ask", "Tanya dulu"],
    ["deny", "Blokir"],
  ].forEach(([value, label]) =>
    select.append(h("option", { value, text: label }))
  );
  select.value = current;
  select.addEventListener("change", () =>
    setToolPermission(waker.id, "shell", select.value)
  );

  const decisionFor = (tool) => {
    for (const key of [
      `mcp:${tool.server}:${tool.name}`,
      `mcp:${tool.server}`,
      "mcp",
    ]) {
      const found = permissions.find((item) => item.tool === key);
      if (found) return found.decision;
    }
    return "ask";
  };
  const tools = state.mcp.tools || [];
  const mcpSelects = tools.map((tool) => {
    const select = h("select", {});
    [
      ["allow", "Izinkan"],
      ["ask", "Tanya dulu"],
      ["deny", "Blokir"],
    ].forEach(([value, label]) =>
      select.append(h("option", { value, text: label }))
    );
    select.value = decisionFor(tool);
    select.addEventListener("change", async () => {
      await setToolPermission(
        waker.id,
        `mcp:${tool.server}:${tool.name}`,
        select.value
      );
      renderDrawerBody(waker);
    });
    return h(
      "div",
      { class: "field" },
      h("label", {}, `${tool.server}.${tool.name}`),
      select
    );
  });

  return h(
    "div",
    { class: "drawer-model" },
    h("div", { class: "section-head" }, h("h2", {}, "Izin tool")),
    h(
      "div",
      { class: "field" },
      h("label", {}, "Langkah tool WakerFlow (shell)"),
      select
    ),
    h("p", {
      class: "form-note",
      text: "Blokir menghentikan langkah tool; Tanya menahan alur sampai kamu menyetujui perintahnya.",
    }),
    h(
      "details",
      { style: "margin-top:12px" },
      h("summary", {
        style: "cursor:pointer;font-weight:600",
        text: `Tool MCP (${tools.length})`,
      }),
      tools.length
        ? h(
            "div",
            {},
            h("p", {
              class: "form-note",
              text: "Izin per tool. Tanpa izin, worker hanya melihat tool dan panggilannya ditolak.",
            }),
            ...mcpSelects
          )
        : h("p", {
            class: "form-note",
            text: "Katalog MCP belum termuat atau belum ada server aktif.",
          })
    )
  );
}

function drawerStyleSection(waker) {
  const styleSelect = h("select", {});
  [
    ["concise", "Ringkas"],
    ["normal", "Normal"],
    ["detailed", "Detail"],
  ].forEach(([value, label]) =>
    styleSelect.append(h("option", { value, text: label }))
  );
  styleSelect.value = waker.answer_style || "normal";
  styleSelect.addEventListener("change", () =>
    autosaveModel(waker, { answer_style: styleSelect.value })
  );

  const tokens = h("input", {
    type: "number",
    min: "1",
    max: "100000",
    placeholder: "mengikuti engine",
    value: waker.max_output_tokens ? String(waker.max_output_tokens) : "",
  });
  tokens.addEventListener("change", () => {
    const value = Number(tokens.value) || 0;
    autosaveModel(waker, { max_output_tokens: value });
  });

  return h(
    "div",
    { class: "drawer-model" },
    h("div", { class: "section-head" }, h("h2", {}, "Gaya jawaban")),
    h(
      "div",
      { class: "grid-2" },
      h("div", { class: "field" }, h("label", {}, "Gaya"), styleSelect),
      h("div", { class: "field" }, h("label", {}, "Maks token keluaran"), tokens)
    ),
    h("p", {
      class: "form-note",
      text: "Dikirim sebagai instruksi di system prompt. Kosongkan token untuk mengikuti engine.",
    })
  );
}

function drawerApprovalSection(waker) {
  const select = h("select", {});
  select.append(h("option", { value: "auto", text: "Otomatis (langsung jalan)" }));
  select.append(h("option", { value: "require", text: "Butuh persetujuan manusia" }));
  select.value = waker.approval_mode === "require" ? "require" : "auto";
  select.addEventListener("change", () =>
    autosaveModel(waker, { approval_mode: select.value })
  );

  return h(
    "div",
    { class: "drawer-model" },
    h("div", { class: "section-head" }, h("h2", {}, "Persetujuan")),
    h(
      "div",
      { class: "field" },
      h("label", {}, "Sebelum berjalan"),
      select
    ),
    h("p", {
      class: "form-note",
      text: "Mode require: chat, run-now, dan automation menunggu keputusanmu di inbox.",
    })
  );
}

/// Model, thinking level, dan context — disimpan otomatis setiap kali diubah,
/// sama seperti panel model QoderWake.
function drawerModelSection(waker) {
  const wrap = h("div", { class: "drawer-model" });
  const status = h("span", { class: "save-state", id: "drawer-save" });
  wrap.append(h("div", { class: "section-head" }, h("h2", {}, "Model & penalaran"), status));

  const engine = state.engines.find((item) => item.key === waker.engine_key);
  if (engine && engine.provider === "agy") {
    if (!state.agyModels) {
      ensureAgyModels().then(() => {
        const current = state.wakers.find((item) => item.id === waker.id);
        if (current && state.selectedId === waker.id && !$("#drawer").classList.contains("hidden")) {
          renderDrawerBody(current);
        }
      });
      wrap.append(h("p", { class: "card-desc", text: "Memuat model Antigravity…" }));
      return wrap;
    }
    const select = h("select", {});
    state.agyModels.forEach((model) =>
      select.append(
        h("option", {
          value: model.id,
          text: `${model.label || model.id} · ${model.id}`,
        })
      )
    );
    select.value = waker.model || (engine.model || state.agyModels[0]?.id);
    select.addEventListener("change", () =>
      autosaveModel(waker, { model: select.value })
    );
    wrap.append(
      h("div", { class: "field" }, h("label", {}, "Model Antigravity (agy)"), select),
      h("p", {
        class: "form-note",
        text: "Daftar dibaca dari `agy models`. Kuota/pengaturan model dikelola Antigravity.",
      })
    );
    return wrap;
  }

  if (waker.engine_key !== "qoder") {
    wrap.append(
      h("p", {
        class: "card-desc",
        text: "Engine ini memakai model bebas/registry; pemilih model hanya tersedia untuk qoder dan Antigravity.",
      })
    );
    return wrap;
  }

  if (!state.catalog.models.length) {
    wrap.append(
      h("p", {
        class: "card-desc",
        text: state.catalog.loading
          ? "Memuat katalog QoderWake…"
          : `Katalog tidak tersedia: ${state.catalog.error || "kosong"}`,
      })
    );
    wrap.append(
      h("button", { class: "btn", onclick: () => ensureCatalog(true) }, "Muat ulang katalog")
    );
    return wrap;
  }

  const modelSelect = h("select", {});
  state.catalog.models.forEach((model) =>
    modelSelect.append(
      h("option", {
        value: model.name,
        text: `${model.display_name || model.name}${model.is_free ? " · gratis" : ""}`,
      })
    )
  );
  const current =
    state.catalog.models.find((m) => m.name === waker.model) ||
    state.catalog.models.find((m) => m.is_default) ||
    state.catalog.models[0];
  modelSelect.value = current.name;

  const effortSelect = h("select", {});
  const contextSelect = h("select", {});
  const remembered = modelDefaultsFor(current.name);
  fillEffortSelect(effortSelect, current, waker.reasoning_effort || remembered.effort);
  fillContextSelect(contextSelect, current, waker.context_window || remembered.context);

  modelSelect.addEventListener("change", async () => {
    const model = catalogModel(modelSelect.value);
    if (!model) return;
    const memory = modelDefaultsFor(model.name);
    const effort = memory.effort || modelDefaultEffort(model);
    const context = memory.context || modelDefaultContext(model);
    fillEffortSelect(effortSelect, model, effort);
    fillContextSelect(contextSelect, model, context);
    await autosaveModel(waker, {
      model: model.name,
      reasoning_effort: effort,
      context_window: context,
    });
  });

  effortSelect.addEventListener("change", () =>
    autosaveModel(waker, { reasoning_effort: effortSelect.value })
  );
  contextSelect.addEventListener("change", () =>
    autosaveModel(waker, { context_window: Number(contextSelect.value) || 0 })
  );

  wrap.append(
    h("div", { class: "field" }, h("label", {}, "Model"), modelSelect),
    h(
      "div",
      { class: "grid-2" },
      h("div", { class: "field" }, h("label", {}, "Thinking level"), effortSelect),
      h("div", { class: "field" }, h("label", {}, "Context window"), contextSelect)
    )
  );
  return wrap;
}

async function autosaveModel(waker, patch) {
  const status = $("#drawer-save");
  if (status) {
    status.textContent = "Menyimpan…";
    status.classList.remove("bad");
  }
  try {
    const updated = await api(`/api/wakers/${waker.id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    Object.assign(waker, updated);
    const index = state.wakers.findIndex((w) => w.id === waker.id);
    if (index >= 0) state.wakers[index] = updated;
    rememberModel(updated.model, updated.reasoning_effort, updated.context_window);
    if (status) status.textContent = "Tersimpan otomatis";
  } catch (error) {
    if (status) {
      status.textContent = "Gagal menyimpan";
      status.classList.add("bad");
    }
    toast(error.message, true);
  }
}

function closeDrawer() {
  $("#drawer").classList.add("hidden");
  $("#scrim").classList.add("hidden");
}

async function toggleStatus(waker) {
  const next = waker.status === "active" ? "paused" : "active";
  try {
    await api(`/api/wakers/${waker.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: next }),
    });
    toast(next === "active" ? "Worker diaktifkan" : "Worker dijeda");
    closeDrawer();
    await loadAll();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteWaker(waker) {
  if (!confirm(`Hapus worker “${waker.name}”? Tindakan ini tidak bisa dibatalkan.`)) return;
  try {
    await api(`/api/wakers/${waker.id}`, { method: "DELETE" });
    toast("Worker dihapus");
    closeDrawer();
    await loadAll();
  } catch (error) {
    toast(error.message, true);
  }
}

// ----------------------------------------------------------------- modal

async function openModal(waker = null) {
  state.editingId = waker ? waker.id : null;
  $("#modal-title").textContent = waker ? "Edit worker" : "Worker baru";
  $("#form-submit").textContent = waker ? "Simpan perubahan" : "Buat worker";
  $("#form-error").classList.add("hidden");
  $("#catalog-note").classList.add("hidden");

  const select = $("#f-engine");
  clear(select);
  if (!state.engines.length) {
    select.append(h("option", { value: "", text: "registry kosong" }));
  }
  (state.engineGroups || []).forEach((group) => {
    select.append(
      h("option", {
        value: group.name,
        text: `Grup ${group.name} · ${group.models.join(" → ")}`,
      })
    );
  });
  state.engines.forEach((engine) => {
    select.append(
      h("option", {
        value: engine.key,
        text: `${engine.key} · ${engine.kind}${
          engine.run_path === "unsupported" ? " · tidak tersedia" : ""
        }`,
      })
    );
  });

  $("#f-name").value = waker?.name || "";
  $("#f-desc").value = waker?.description || "";
  const preferred = state.engines.find((e) => e.key === "qoder") || state.engines[0];
  select.value = waker?.engine_key || preferred?.key || "";
  $("#f-model-free").value = waker?.model || "";
  $("#f-workspace").value = waker?.workspace || "";
  $("#f-prompt").value = waker?.system_prompt || "";

  $("#modal").classList.remove("hidden");
  await ensureCatalog();
  await syncModalModels(false, waker);
  setTimeout(() => $("#f-name").focus(), 30);
}

/// Menyesuaikan kontrol model dengan engine yang dipilih. Engine qoder memakai
/// katalog QoderWake; engine lain tetap memakai isian bebas (antigravity menyusul).
async function syncModalModels(keepCurrent, waker = null) {
  const engineKey = $("#f-engine").value;
  const group = (state.engineGroups || []).find((item) => item.name === engineKey);
  if (group) {
    $("#f-model").classList.add("hidden");
    $("#f-model-free").classList.add("hidden");
    $("#f-reasoning").classList.add("hidden");
    $("#f-context-free-wrap").classList.add("hidden");
    const note = $("#catalog-note");
    note.textContent = `Grup ${group.name}: ${group.models.join(" → ")}. Bila satu model gagal (kuota habis, login, rate limit), run berikutnya memakai model di bawahnya.`;
    note.classList.remove("hidden");
    return;
  }
  const engine = state.engines.find((item) => item.key === engineKey);
  const provider = engine ? engine.provider : "other";
  const selectable = provider === "qoder" || provider === "agy";

  $("#f-model").classList.toggle("hidden", !selectable);
  $("#f-model-free").classList.toggle("hidden", selectable);
  $("#f-reasoning").classList.toggle("hidden", provider !== "qoder");
  // Engine non-Qoder tidak punya daftar context dari CLI. Isian bebas ini hanya
  // dipakai untuk menghitung persentase di header chat, bukan untuk mengubah model.
  const freeContext = $("#f-context-free-wrap");
  freeContext.classList.toggle("hidden", provider === "qoder");
  $("#f-context-free").value = (waker && waker.context_window) || "";

  const note = $("#catalog-note");
  note.classList.add("hidden");

  if (provider === "agy") {
    await ensureAgyModels();
    const select = $("#f-model");
    const previous = keepCurrent ? select.value : (waker && waker.model) || (engine && engine.model);
    clear(select);
    (state.agyModels || []).forEach((model) =>
      select.append(
        h("option", {
          value: model.id,
          text: `${model.label || model.id} · ${model.id}`,
        })
      )
    );
    if (!(state.agyModels || []).length) {
      select.append(h("option", { value: "", text: "model Antigravity tidak terbaca" }));
      note.textContent = "Jalankan `agy models` untuk memastikan CLI Antigravity aktif.";
      note.classList.remove("hidden");
      return;
    }
    const target =
      (state.agyModels || []).find((m) => m.id === previous) ||
      (state.agyModels || []).find((m) => m.id === (engine && engine.model)) ||
      state.agyModels[0];
    select.value = target.id;
    return;
  }

  if (provider !== "qoder") return;

  const select = $("#f-model");
  const previous = keepCurrent ? select.value : (waker && waker.model) || select.value;
  clear(select);

  const models = state.catalog.models;
  if (!models.length) {
    select.append(
      h("option", {
        value: "",
        text: state.catalog.loading ? "memuat katalog…" : "katalog tidak tersedia",
      })
    );
    if (state.catalog.error) {
      note.textContent = `Katalog QoderWake: ${state.catalog.error}`;
      note.classList.remove("hidden");
    }
    return;
  }

  models.forEach((model) =>
    select.append(
      h("option", {
        value: model.name,
        text: `${model.display_name || model.name}${model.is_free ? " · gratis" : ""}${
          model.is_new ? " · baru" : ""
        }`,
      })
    )
  );
  const target =
    models.find((m) => m.name === previous) ||
    models.find((m) => m.is_default) ||
    models[0];
  select.value = target.name;
  syncModalReasoning(waker);
}

function syncModalReasoning(waker = null) {
  const model = catalogModel($("#f-model").value);
  const remembered = modelDefaultsFor(model && model.name);
  fillEffortSelect(
    $("#f-effort"),
    model,
    (waker && waker.reasoning_effort) || remembered.effort
  );
  fillContextSelect(
    $("#f-context"),
    model,
    (waker && waker.context_window) || remembered.context
  );
}

function closeModal() {
  $("#modal").classList.add("hidden");
  state.editingId = null;
}

async function submitWaker(event) {
  event.preventDefault();
  const errorBox = $("#form-error");
  errorBox.classList.add("hidden");

  const engine = state.engines.find((item) => item.key === $("#f-engine").value);
  const provider = engine ? engine.provider : "other";
  const isQoder = provider === "qoder";
  const selectable = isQoder || provider === "agy";
  const model = selectable ? $("#f-model").value : $("#f-model-free").value.trim();
  const effort = isQoder ? $("#f-effort").value : "";
  const context = isQoder
    ? Number($("#f-context").value) || 0
    : Number($("#f-context-free").value) || 0;

  const payload = {
    name: $("#f-name").value.trim(),
    description: $("#f-desc").value.trim(),
    engine_key: $("#f-engine").value,
    model,
    reasoning_effort: effort,
    context_window: context,
    workspace: $("#f-workspace").value.trim(),
    system_prompt: $("#f-prompt").value,
  };

  const button = $("#form-submit");
  button.disabled = true;
  try {
    if (state.editingId) {
      await api(`/api/wakers/${state.editingId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      toast("Perubahan tersimpan");
    } else {
      await api("/api/wakers", { method: "POST", body: JSON.stringify(payload) });
      toast("Worker dibuat");
    }
    if (isQoder && model) rememberModel(model, effort, context);
    closeModal();
    await loadAll();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  } finally {
    button.disabled = false;
  }
}

// ------------------------------------------------------- Waker Console launcher
// Padanan `qc-wakey-console-launcher` di QoderWake: satu tombol mengambang di
// semua halaman yang membuka chat assistant. Konteks halaman ikut dikirim ke
// worker supaya pertanyaan seperti "apa yang menunggu?" bisa dijawab tanpa
// berpindah halaman. Ini bukan panel dashboard; panel ini adalah percakapan.

const WAKEY_WORKER_KEY = "station.wakeyWorker";

function wakeyGo(view) {
  const item = document.querySelector(`.nav-item[data-view="${view}"]`);
  if (item) item.click();
  closeWakey();
}

function wakeyChatWorkers() {
  return state.wakers.filter(
    (waker) => waker.status === "active" && waker.chat_available !== false
  );
}

function wakeyCurrentWorker() {
  return state.wakers.find((waker) => waker.id === state.wakeyChat.workerId) || null;
}

function wakeyEnsureWorker() {
  const workers = wakeyChatWorkers();
  if (!workers.length) return null;
  const has = (id) => !!id && workers.some((waker) => waker.id === id);
  let id = state.wakeyChat.workerId;
  if (!has(id)) id = localStorage.getItem(WAKEY_WORKER_KEY);
  if (!has(id)) id = state.selectedId;
  // Gemini Flash High (engine gemini-verify) adalah worker chat yang paling
  // responsif di mesin ini; Nvidia dipakai sebagai cadangan berikutnya. Jangan
  // memilih worker pertama begitu saja — qoder bisa menolak karena belum login.
  if (!has(id)) id = (workers.find((waker) => waker.engine_key === "gemini-verify") || {}).id;
  if (!has(id)) id = (workers.find((waker) => /nvidia/i.test(waker.name || "")) || {}).id;
  if (!has(id)) id = workers[0].id;
  state.wakeyChat.workerId = id;
  return wakeyCurrentWorker();
}

function wakeyTruncate(text, limit) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
}

function wakeyContextString() {
  const data = state.wakey || {};
  const decisions = (data.decisions || []).filter(
    (d) => d.state === "OPEN" || d.state === "PENDING"
  );
  const runs = (data.runs || []).filter(
    (r) => r.status === "running" || r.status === "queued"
  );
  // Jangan kirim prefix jika tidak ada task atau keputusan yang aktif/mendesak
  if (!decisions.length && !runs.length) {
    return "";
  }
  const title = (TITLES[state.view] || ["Console"])[0];
  const decisionLines = decisions.slice(0, 3).map(
    (decision) => `#${decision.id} ${wakeyTruncate(decision.question, 90)}`
  );
  const runLines = runs.slice(0, 3).map(
    (run) => `${wakeyWorkerName(run)} [${run.status || "running"}]`
  );
  return [
    "[KONTEKS AI STATION]",
    `Halaman: ${title} (${state.view})`,
    decisionLines.length ? `Human Decide: ${decisionLines.join("; ")}` : "",
    runLines.length ? `Worker aktif: ${runLines.join("; ")}` : "",
    "[/KONTEKS]",
    "",
  ].filter(Boolean).join("\n");
}

function ensureWakeyLauncher() {
  if (document.getElementById("wakey-ball")) return;

  const badge = h("span", { class: "wakey-ball__badge hidden", id: "wakey-badge" });
  const ball = h(
    "button",
    {
      id: "wakey-ball",
      class: "wakey-ball",
      type: "button",
      title: "Waker Console",
      "aria-label": "Buka Waker Console",
      "aria-expanded": "false",
      onclick: () => toggleWakey(),
    },
    h("span", { class: "wakey-ball__avatar", text: "W" }),
    h("span", { class: "wakey-ball__dot", "aria-hidden": "true" }),
    badge
  );

  const panel = h(
    "aside",
    {
      id: "wakey-panel",
      class: "wakey-panel hidden",
      role: "dialog",
      "aria-label": "Waker Console",
    },
    h(
      "div",
      { class: "wakey-panel__head" },
      h(
        "div",
        { class: "wakey-panel__id" },
        h("span", { class: "wakey-panel__avatar", text: "W" }),
        h(
          "div",
          {},
          h("strong", { text: "Waker Console" }),
          h("span", { class: "wakey-panel__status", id: "wakey-status", text: "membaca…" })
        )
      ),
      h(
        "div",
        { class: "wakey-head-actions" },
        h("button", {
          class: "link-btn",
          type: "button",
          title: "Buka @Worker",
          "aria-label": "Buka @Worker",
          text: "⛶",
          onclick: () => wakeyFullscreen(),
        }),
        h("button", {
          class: "icon-btn",
          type: "button",
          "aria-label": "Tutup Waker Console",
          text: "×",
          onclick: () => closeWakey(),
        })
      )
    ),
    h(
      "div",
      { class: "wakey-worker-row" },
      h("label", { for: "wakey-worker-select", text: "Worker" }),
      (() => {
        const select = h("select", {
          id: "wakey-worker-select",
          onchange: (event) => wakeySwitchWorker(event.target.value),
        });
        return select;
      })()
    ),
    h(
      "div",
      { class: "wakey-context", id: "wakey-context" },
      h("div", { class: "wakey-chips", id: "wakey-chips" })
    ),
    h("div", { class: "wakey-chat", id: "wakey-chat" }),
    h(
      "form",
      { class: "wakey-composer", id: "wakey-form", onsubmit: (event) => sendWakeyMessage(event) },
      h("textarea", {
        id: "wakey-input",
        rows: "1",
        placeholder: "Tanya Waker Console…",
        onkeydown: (event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            const form = document.getElementById("wakey-form");
            if (form && typeof form.requestSubmit === "function") form.requestSubmit();
            else sendWakeyMessage(event);
          }
        },
      }),
      h("button", { class: "btn primary", type: "submit", id: "wakey-send", text: "Kirim" })
    ),
    h(
      "div",
      { class: "wakey-panel__foot" },
      h("span", { id: "wakey-updated", text: "belum dimuat" }),
      h("span", { class: "wakey-hint", text: "Konteks halaman ikut dikirim" })
    )
  );

  document.body.append(ball, panel);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.classList.contains("hidden")) closeWakey();
  });
  // Isi langsung dari state boot; refresh berikutnya menggantinya dengan data
  // terbaru. Tanpa ini panel terbuka kosong selama satu putaran fetch.
  wakeyRenderWorkerSelect();
  wakeyRenderChips();
  wakeyRenderChat();
}

function toggleWakey() {
  const panel = document.getElementById("wakey-panel");
  if (!panel) return;
  const open = panel.classList.toggle("hidden") === false;
  const ball = document.getElementById("wakey-ball");
  if (ball) ball.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    wakeyEnsureWorker();
    wakeyEnsureHistory();
    refreshWakey();
    setTimeout(() => {
      const input = document.getElementById("wakey-input");
      if (input) input.focus();
    }, 40);
  }
}

function closeWakey() {
  const panel = document.getElementById("wakey-panel");
  if (panel) panel.classList.add("hidden");
  const ball = document.getElementById("wakey-ball");
  if (ball) ball.setAttribute("aria-expanded", "false");
}

function wakeyWorkerName(run) {
  const waker = state.wakers.find((w) => w.id === run.waker_id);
  return (waker && waker.name) || run.waker_id || "worker";
}

function wakeyRenderWorkerSelect() {
  const select = document.getElementById("wakey-worker-select");
  if (!select) return;
  const workers = wakeyChatWorkers();
  wakeyEnsureWorker();
  const current = state.wakeyChat.workerId;
  clear(select);
  workers.forEach((waker) => {
    const option = h("option", { value: waker.id, text: waker.name });
    if (waker.id === current) option.selected = true;
    select.append(option);
  });
  if (!workers.length) {
    select.append(h("option", { value: "", text: "Tidak ada worker chat aktif" }));
    select.value = "";
  }
  select.disabled = state.wakeyChat.busy || workers.length === 0;
}

function wakeyRenderChips() {
  const chips = document.getElementById("wakey-chips");
  if (!chips) return;
  clear(chips);
  const data = state.wakey;
  const add = (label, view) => {
    chips.append(
      h("button", {
        class: "wakey-chip",
        type: "button",
        onclick: () => wakeyGo(view),
        text: label,
      })
    );
  };
  add(`Human Decide ${data.decisions.length}`, "decide");
  add(`Runs ${data.runs.length}`, "runs");
  add(`Auto Pilot ${data.autoPilotOn ? "ON" : "OFF"}`, "auto-pilot");
  add(`Automation ${data.automations}`, "automations");
}

function wakeyRenderChat() {
  const chat = document.getElementById("wakey-chat");
  if (!chat) return;
  // Panel dibangun ulang setiap event; posisi baca diambil dulu supaya
  // jawaban yang bertambah tidak menggeser pandangan yang sedang di atas.
  const follow = !!state.wakeyForceBottom || nearBottom(chat);
  state.wakeyForceBottom = false;
  const keepTop = chat.scrollTop;
  const worker = wakeyCurrentWorker();
  const messages = worker ? state.chat[worker.id] || [] : [];
  clear(chat);
  if (!messages.length) {
    chat.append(
      h(
        "div",
        { class: "wakey-welcome" },
        h("strong", { text: "Tanya Waker Console" }),
        h("p", {
          text: "Percakapan ini sama dengan @Worker untuk worker yang dipilih; riwayat dan langkahnya dibaca dari run yang sama.",
        }),
        h(
          "div",
          { class: "wakey-suggestions" },
          ...[
            "Apa yang menunggu di Human Decide?",
            "Worker mana yang sedang berjalan?",
            "Ringkas status automation dan Auto Pilot.",
          ].map((text) =>
            h("button", {
              class: "wakey-chip",
              type: "button",
              text,
              onclick: () => sendWakeyText(text),
            })
          )
        )
      )
    );
    return;
  }
  messages.forEach((message) => {
    const role = message.role === "user" ? "user" : "assistant";
    chat.append(
      h(
        "div",
        { class: `wakey-msg ${role}` },
        h("span", {
          class: "wakey-msg__role",
          text: role === "user" ? "Kamu" : (worker && worker.name) || "Waker",
        }),
        h(
          "div",
          { class: "wakey-msg__text" },
          message.pending && !message.text
            ? h(
                "span",
                { class: "wakey-typing", "aria-label": "menjawab" },
                h("i", {}),
                h("i", {}),
                h("i", {})
              )
            : message.text || (message.error ? "" : "…")
        ),
        message.error ? h("span", { class: "wakey-msg__error", text: message.error }) : null,
        message.events && message.events.length
          ? processPanel(message.events, message.pending, false, false)
          : message.pending
            ? processPanel([], true, false, false)
            : null
      )
    );
  });
  chat.scrollTop = follow ? chat.scrollHeight : keepTop;
}

function renderWakey() {
  const data = state.wakey;
  if (!data || !document.getElementById("wakey-panel")) return;
  const status = document.getElementById("wakey-status");
  const badge = document.getElementById("wakey-badge");
  const ball = document.getElementById("wakey-ball");
  const updated = document.getElementById("wakey-updated");
  const worker = wakeyEnsureWorker();
  const thread = worker ? state.chat[worker.id] || [] : [];
  state.wakeyChat.busy = !!(
    worker &&
    (state.runBusy[worker.id] || thread.some((message) => message.pending))
  );
  wakeyRenderWorkerSelect();
  wakeyRenderChips();
  wakeyRenderChat();

  if (status) {
    status.textContent = worker
      ? `${worker.name} · ${state.wakeyChat.busy ? "menjawab…" : "siap"}`
      : "tidak ada worker chat aktif";
  }
  if (ball) ball.dataset.status = data.runs.length ? "running" : "idle";
  if (badge) {
    badge.textContent = data.decisions.length > 99 ? "99+" : String(data.decisions.length);
    badge.classList.toggle("hidden", data.decisions.length === 0);
  }
  if (updated) updated.textContent = `diperbarui ${new Date().toLocaleTimeString("id-ID")}`;
}

async function refreshWakey() {
  if (state.wakey.loading) return;
  if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
  state.wakey.loading = true;
  try {
    const [ap, runs, automations, mode, wakers] = await Promise.all([
      api("/api/autopilot").catch(() => null),
      api("/api/runs").catch(() => null),
      api("/api/automations").catch(() => null),
      api("/api/auto-pilot-mode").catch(() => null),
      api("/api/wakers").catch(() => null),
    ]);
    if (wakers && Array.isArray(wakers.wakers)) state.wakers = wakers.wakers;
    state.wakey.decisions = (ap && ap.decisions_open) || [];
    state.wakey.runs = ((runs && runs.runs) || []).filter(
      (run) => run.status === "running" || run.status === "queued"
    );
    state.wakey.automations = ((automations && automations.automations) || []).filter(
      (automation) => automation.enabled
    ).length;
    state.wakey.autoPilotOn = !!(mode && mode.on);
    renderWakey();
  } finally {
    state.wakey.loading = false;
  }
}

function wakeySwitchWorker(id) {
  if (!id || id === state.wakeyChat.workerId) return;
  state.wakeyChat.workerId = id;
  localStorage.setItem(WAKEY_WORKER_KEY, id);
  wakeyEnsureHistory();
  renderWakey();
}

function wakeyPanelOpenFor(wakerId) {
  const panel = document.getElementById("wakey-panel");
  return !!(
    panel &&
    !panel.classList.contains("hidden") &&
    state.wakeyChat.workerId === wakerId
  );
}

async function wakeyEnsureHistory() {
  const worker = wakeyEnsureWorker();
  if (!worker) {
    wakeyRenderChat();
    return;
  }
  await ensureChatHistory(worker);
  if (wakeyPanelOpenFor(worker.id)) renderWakey();
}

function wakeyFullscreen() {
  const worker = wakeyCurrentWorker();
  if (worker) state.selectedId = worker.id;
  wakeyGo("chat");
}

function sendWakeyMessage(event) {
  event.preventDefault();
  const input = document.getElementById("wakey-input");
  const text = (input && input.value.trim()) || "";
  if (!text) return;
  if (input) input.value = "";
  sendWakeyText(text);
}

async function sendWakeyText(text) {
  const worker = wakeyEnsureWorker();
  if (!worker) {
    toast("Tidak ada worker chat aktif", true);
    return;
  }
  if (state.runBusy[worker.id]) return;
  const ctx = wakeyContextString();
  return sendMessage(worker, text, "wakey", {
    contextPrefix: ctx ? `${ctx}\nPertanyaan operator:\n` : "",
  });
}

function startWakeyLauncher() {
  ensureWakeyLauncher();
  wakeyEnsureWorker();
  refreshWakey();
  setInterval(() => {
    refreshWakey();
  }, 15000);
  // Deep link untuk membuka panel langsung (mis. dari notifikasi atau tes).
  // QoderWake memakai `wakeyOverlay=open`; di sini `wakey=open` dibersihkan
  // dari URL setelah dipakai supaya refresh berikutnya tidak membuka paksa.
  const params = new URLSearchParams(location.search);
  if (params.get("wakey") === "open") {
    params.delete("wakey");
    history.replaceState(null, "", `${location.pathname}${params.toString() ? `?${params}` : ""}${location.hash}`);
    toggleWakey();
  }
}

// ---------------------------------------------------------------- projects
// Menu Project membaca hasil generate situs (site/projects.json), bukan registry
// mentah: angka post/media di sini harus sama dengan yang dilihat di halaman.

async function loadProjects() {
  if (state.projectsLoading) return;
  state.projectsLoading = true;
  state.projectsError = null;
  try {
    const res = await fetch("/site/projects.json", { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`situs belum siap (${res.status}); jalankan python3 tools/site/build.py`);
    }
    const data = await res.json();
    state.projects = data.projects || [];
    state.projectsGeneratedAt = data.generated_at || null;
    state.projectsLoaded = true;
  } catch (error) {
    state.projectsError = error.message;
    state.projectsLoaded = true;
  } finally {
    state.projectsLoading = false;
  }
}

function openSiteUrl(url) {
  if (!url) return;
  // Native webview can block target="_blank"; fall back to same-window
  // navigation so the button is never a dead click.
  const opened = window.open(url, "_blank", "noopener");
  if (!opened) window.location.assign(url);
}

function renderProjects(view) {
  if (!state.projectsLoaded && !state.projectsLoading) {
    loadProjects().then(() => render());
  }

  view.append(
    h(
      "div",
      { class: "section-head" },
      h("h2", {}, "Registry project"),
      h(
        "div",
        { class: "row-actions" },
        h(
          "a",
          {
            class: "btn",
            href: "/site/",
            onclick: (event) => {
              event.preventDefault();
              openSiteUrl("/site/");
            },
          },
          "Buka situs"
        ),
        h(
          "button",
          {
            class: "btn ghost",
            type: "button",
            onclick: async () => {
              state.projectsLoaded = false;
              await loadProjects();
              render();
            },
          },
          "Muat ulang"
        )
      )
    )
  );

  if (state.projectsLoading && !state.projectsLoaded) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("strong", { text: "Membaca registry project…" }),
        h("p", { text: "site/projects.json dibangun oleh tools/site/build.py." })
      )
    );
    return;
  }

  if (state.projectsError) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("strong", { text: "Situs belum bisa dibaca" }),
        h("p", { text: state.projectsError })
      )
    );
    return;
  }

  if (!state.projects.length) {
    view.append(
      h(
        "div",
        { class: "empty" },
        h("strong", { text: "Belum ada project terdaftar" }),
        h("p", { text: "Isi config/site_projects.json lalu build ulang situs." })
      )
    );
    return;
  }

  const grid = h("div", { class: "grid" });
  state.projects.forEach((project) => {
    const initials = String(project.codename || "@")
      .replace(/^@/, "")
      .slice(0, 2)
      .toUpperCase();
    grid.append(
      h(
        "div",
        {
          class: "card",
          role: "button",
          tabindex: "0",
          title: `Buka ${project.name}`,
          onclick: (event) => {
            if (event.target.closest("a,button")) return;
            openSiteUrl(project.url);
          },
          onkeydown: (event) =>
            activateCard(event, () => openSiteUrl(project.url)),
        },
        h(
          "div",
          { class: "card-top" },
          h("div", { class: "avatar", style: "background:#7c5cff", text: initials }),
          h(
            "div",
            { class: "card-id" },
            h("h3", { class: "card-name", text: project.name }),
            h("span", { class: "card-slug", text: project.codename })
          )
        ),
        h("p", {
          class: "card-desc",
          text: `${project.mode} · ${project.posts} post · ${project.media} media · ${project.docs} dokumen`,
        }),
        h(
          "div",
          { class: "card-meta" },
          h("span", {
            class: `badge ${project.available ? "ok" : "bad"}`,
            text: project.status || (project.available ? "aktif" : "belum tersedia"),
          }),
          project.available ? null : h("span", { class: "badge bad", text: "path belum ada" })
        ),
        h(
          "div",
          { class: "row-actions" },
          h(
            "a",
            {
              class: "btn",
              href: project.url,
              onclick: (event) => {
                event.preventDefault();
                openSiteUrl(project.url);
              },
            },
            "Halaman project"
          ),
          h(
            "a",
            {
              class: "btn ghost",
              href: `/site/gallery.html?project=${encodeURIComponent(project.slug)}`,
              onclick: (event) => {
                event.preventDefault();
                openSiteUrl(`/site/gallery.html?project=${encodeURIComponent(project.slug)}`);
              },
            },
            "Gallery"
          )
        )
      )
    );
  });
  view.append(grid);
  view.append(
    h("p", {
      class: "form-note",
      text: `Situs lokal 127.0.0.1:8999/site/ · build ${
        state.projectsGeneratedAt || "belum tercatat"
      } · feed hanya memuat post public.`,
    })
  );
}

// --------------------------------------------------------- Station event stream
// Satu koneksi SSE dari daemon. Semua perubahan run (thinking, tool, context,
// selesai) datang dari sini; chat tidak lagi mengandalkan polling untuk live.

function applyContextEvent(wakerId, runId, event) {
  const match = /(\d+(?:\.\d+)?)%/.exec((event && event.text) || "");
  if (!match) return;
  const ratio = Number.parseFloat(match[1]) / 100;
  let run = (state.runs || []).find((item) => item.id === runId);
  if (!run) {
    run = {
      id: runId,
      waker_id: wakerId,
      started_at: (event && event.at) || new Date().toISOString(),
      events: [],
    };
    state.runs.unshift(run);
  }
  run.context_ratio = ratio;
  const waker = state.wakers.find((item) => item.id === wakerId);
  if (waker) saveContextSnapshot(waker, { ratio, source: "engine", at: event.at });
  refreshContextBadges();
}

async function ensureStreamRun(wakerId, runId) {
  if (state.streamFetching[runId]) return;
  state.streamFetching[runId] = true;
  try {
    const run = await api(`/api/runs/${runId}`);
    const waker = state.wakers.find((item) => item.id === wakerId);
    if (waker) applyLiveRun(waker, run);
  } catch {
    // event berikutnya akan mencoba lagi
  } finally {
    delete state.streamFetching[runId];
  }
}

function handleRunEvent(payload) {
  const wakerId = payload.waker_id;
  const runId = payload.run_id;
  const event = payload.event;
  if (!wakerId || !runId || !event) return;
  const messages = state.chat[wakerId] || (state.chat[wakerId] = []);
  const index = messages.findIndex(
    (message) => message.role === "waker" && message.runId === runId
  );
  if (index < 0) {
    ensureStreamRun(wakerId, runId);
    return;
  }
  const message = messages[index];
  message.events = message.events || [];
  if (event.seq != null && message.events.some((item) => item.seq === event.seq)) return;
  message.events.push(event);
  if (event.kind === "delta") message.text = (message.text || "") + (event.text || "");
  if (event.kind === "output") {
    // Engine CLI tanpa stream-json (mis. cline) menulis baris demi baris.
    // Baris itu bagian dari jawaban, jadi ikut tampil saat datang — sama
    // seperti delta milik qoder/openai/ollama, bukan menunggu run selesai.
    const line = event.text || "";
    message.text = message.text ? `${message.text}\n${line}` : line;
  }
  if (event.kind === "context") applyContextEvent(wakerId, runId, event);
  if (event.kind === "started") {
    message.pending = true;
    state.activeRuns[wakerId] = runId;
  }
  if (event.kind === "status" && /^error/i.test(event.text || "")) {
    message.error = (event.text || "").replace(/^error\s*·\s*/i, "");
  }
  if (state.view === "chat" && state.selectedId === wakerId) {
    updateBubble(index, message.text, message.pending, message.events);
  }
  if (wakeyPanelOpenFor(wakerId)) renderWakey();
}

function handleRunState(payload) {
  const wakerId = payload.waker_id;
  const runId = payload.run_id;
  if (!wakerId || !runId) return;
  const active = payload.status === "running" || payload.status === "queued";
  const messages = state.chat[wakerId] || (state.chat[wakerId] = []);
  const index = messages.findIndex(
    (message) => message.role === "waker" && message.runId === runId
  );
  upsertRunSummary({ ...payload, events: [] });
  refreshContextBadges();
  if (state.view === "auto-pilot") loadAutoPilot().then(render);
  if (index < 0) {
    ensureStreamRun(wakerId, runId);
    return;
  }
  const message = messages[index];
  message.pending = active;
  if (typeof payload.output === "string" && payload.output) message.text = payload.output;
  message.error = payload.status === "failed" ? payload.error || "run gagal" : null;
  state.runBusy[wakerId] = active;
  if (active) state.activeRuns[wakerId] = runId;
  else if (state.activeRuns[wakerId] === runId) delete state.activeRuns[wakerId];
  if (state.view === "chat" && state.selectedId === wakerId) {
    updateBubble(index, message.text, message.pending, message.events);
    if (!active && !chatInputFocused()) {
      ensureStreamRun(wakerId, runId);
      render();
    }
  }
  if (wakeyPanelOpenFor(wakerId)) renderWakey();
}

function handleStationEvent(payload) {
  if (!payload || typeof payload !== "object") return;
  switch (payload.type) {
    case "run_event":
      handleRunEvent(payload);
      break;
    case "run_state":
      handleRunState(payload);
      break;
    case "resync":
      state.lastStreamAt = 0;
      break;
    case "automation":
      if (state.view === "automations") loadAutomations().then(render);
      if (state.view === "auto-pilot") loadAutoPilot().then(render);
      refreshWakey();
      break;
    case "decision_answered":
    case "decision_mode":
      ensureDecisionMode();
      if (state.view === "decide" || state.view === "dashboard") ensureAutopilot(true);
      refreshWakey();
      break;
    case "auto_pilot_mode":
      if (payload.mode) state.autoPilotMode = { mode: payload.mode, on: !!payload.on };
      if (state.view === "auto-pilot") loadAutoPilot().then(render);
      refreshWakey();
      break;
    default:
      break;
  }
}

function startStationStream() {
  if (state.stream || typeof EventSource === "undefined") return;
  const source = new EventSource("/api/stream");
  state.stream = source;
  source.onmessage = (event) => {
    state.lastStreamAt = Date.now();
    try {
      handleStationEvent(JSON.parse(event.data));
    } catch {
      // event rusak tidak boleh mematikan stream
    }
  };
  source.onerror = () => {
    // EventSource melakukan reconnect sendiri; fallback poll mengambil alih
    // sementara dengan lastStreamAt yang sudah basi.
    state.lastStreamAt = 0;
  };
}

let processTicker = null;

function startProcessTicker() {
  if (processTicker) return;
  processTicker = setInterval(() => {
    const now = Date.now();
    document.querySelectorAll(".msg-steps[data-start]").forEach((box) => {
      const start = Number(box.dataset.start);
      const label = box.querySelector(".steps-elapsed");
      if (Number.isFinite(start) && label) {
        label.textContent = `${Math.max(0, Math.floor((now - start) / 1000))}s`;
      }
    });
    document.querySelectorAll(".msg-live-step[data-start]").forEach((row) => {
      const start = Number(row.dataset.start);
      const label = row.querySelector(".step-elapsed");
      if (Number.isFinite(start) && label) {
        label.textContent = `${Math.max(0, Math.floor((now - start) / 1000))}s`;
      }
    });
    document.querySelectorAll(".msg-process-wrap[data-last]").forEach((wrap) => {
      const last = Number(wrap.dataset.last);
      if (!Number.isFinite(last)) return;
      const idle = Math.max(0, Math.round((now - last) / 1000));
      let warn = wrap.querySelector(".msg-idle-warning");
      if (idle >= 45) {
        if (!warn) {
          warn = document.createElement("div");
          warn.className = "msg-idle-warning";
          wrap.append(warn);
        }
        warn.textContent = `Belum ada event baru selama ${idle}s — worker masih berjalan, sedang berpikir, atau macet.`;
      } else if (warn) {
        warn.remove();
      }
    });
  }, 250);
}

// ------------------------------------------------------------------ boot

function bind() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", async () => {
      state.view = item.dataset.view;
      history.replaceState(null, "", `?view=${state.view}`);
      if (state.view === "runs") {
        try {
          const data = await api("/api/runs");
          state.runs = data.runs || [];
        } catch {
          // biarkan daftar lama tampil; kegagalan jaringan sudah terlihat di status
        }
      }
      if (state.view === "automations") {
        await loadAutomations();
      }
      if (state.view === "auto-pilot") {
        await loadAutoPilot();
      }
      if (state.view === "approvals") {
        await loadApprovals();
      }
      if (state.view === "decide") {
        ensureAutopilot(true);
      }
      if (state.view === "workflows") {
        await loadWorkflows();
      }
      if (state.view === "channels") {
        await loadChannels();
      }
      if (state.view === "groups") {
        await loadGroups();
      }
      if (state.view === "knowledge") {
        await loadKnowledge();
      }
      if (state.view === "traces") {
        await loadTraces();
      }
      if (state.view === "memory") {
        await loadMemoryScopes();
      }
      if (state.view === "engines") {
        await Promise.all([ensureCatalog(), ensureAgyModels(), ensureOllamaModels()]);
      }
      if (state.view === "dashboard") {
        await loadDashboardData();
      }
      if (state.view === "projects") {
        await loadProjects();
      }
      if (state.view === "capabilities") {
        await loadCapabilities();
      }
      render();
    });
  });

  $("#search").addEventListener("input", (event) => {
    state.query = event.target.value;
    if (state.view === "wakers") render();
  });

  const modeSelect = document.getElementById("mode-select");
  if (modeSelect) {
    modeSelect.addEventListener("change", () => setDecisionMode(modeSelect.value));
  }

  $("#refresh").addEventListener("click", () => {
    // Muat ulang penuh: HTML, CSS, JS, dan data. Berguna di jendela native
    // yang tidak punya tombol reload browser.
    location.reload();
  });
  $("#new-waker").addEventListener("click", () => openModal(null));
  try {
    state.railCollapsed = localStorage.getItem("station.railCollapsed") === "1";
  } catch {
    state.railCollapsed = false;
  }
  applyChrome();
  window.scrollTo(0, 0);
  const railToggle = document.getElementById("rail-toggle");
  if (railToggle) railToggle.addEventListener("click", toggleRail);
  const viewerToggle = document.getElementById("viewer-toggle");
  if (viewerToggle) viewerToggle.addEventListener("click", toggleViewer);
  const viewerMin = document.getElementById("viewer-min");
  if (viewerMin) viewerMin.addEventListener("click", minimizeViewer);
  const viewerClose = document.getElementById("viewer-close");
  if (viewerClose) viewerClose.addEventListener("click", closeViewer);

  // Jendela native tidak punya tombol reload bawaan: denutkan tick dari daemon
  // supaya POST /api/ui/reload benar-benar memuat ulang halaman ini.
  let lastReloadTick = null;
  setInterval(async () => {
    try {
      const stats = await api("/api/stats");
      if (typeof stats.ui_reload_tick !== "number") return;
      if (lastReloadTick === null) {
        lastReloadTick = stats.ui_reload_tick;
      } else if (stats.ui_reload_tick !== lastReloadTick) {
        location.reload();
      }
    } catch {
      // daemon sibuk/terputus: percobaan berikutnya menyusul
    }
  }, 5000);

  $("#automation-close").addEventListener("click", closeAutomationModal);
  $("#automation-cancel").addEventListener("click", closeAutomationModal);
  $("#automation-form").addEventListener("submit", submitAutomation);
  ["a-sched", "a-hour", "a-minute", "a-weekday", "a-dom", "a-kind", "a-tz"].forEach((id) => {
    const element = document.getElementById(id);
    if (element) element.addEventListener("change", updateAutomationForm);
  });
  const cronInput = document.getElementById("a-cron");
  if (cronInput) cronInput.addEventListener("input", updateAutomationForm);
  $("#workflow-close").addEventListener("click", closeWorkflowModal);
  $("#workflow-cancel").addEventListener("click", closeWorkflowModal);
  $("#workflow-form").addEventListener("submit", submitWorkflow);
  $("#channel-close").addEventListener("click", closeChannelModal);
  $("#channel-cancel").addEventListener("click", closeChannelModal);
  $("#channel-form").addEventListener("submit", submitChannel);
  $("#group-close").addEventListener("click", closeGroupModal);
  $("#group-cancel").addEventListener("click", closeGroupModal);
  $("#group-form").addEventListener("submit", submitGroup);
  $("#f-engine").addEventListener("change", () => syncModalModels(false));
  $("#f-model").addEventListener("change", () => syncModalReasoning(null));
  $("#model-refresh").addEventListener("click", async (event) => {
    event.preventDefault();
    await ensureCatalog(true);
    toast("Katalog QoderWake diperbarui");
  });
  $("#modal-close").addEventListener("click", closeModal);
  $("#form-cancel").addEventListener("click", closeModal);
  $("#waker-form").addEventListener("submit", submitWaker);
  $("#scrim").addEventListener("click", closeDrawer);
  $("#drawer-close").addEventListener("click", closeDrawer);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("#group-modal").classList.contains("hidden")) closeGroupModal();
    else if (!$("#channel-modal").classList.contains("hidden")) closeChannelModal();
    else if (!$("#workflow-modal").classList.contains("hidden")) closeWorkflowModal();
    else if (!$("#automation-modal").classList.contains("hidden")) closeAutomationModal();
    else if (!$("#modal").classList.contains("hidden")) closeModal();
    else closeDrawer();
  });

  setInterval(async () => {
    try {
      const stats = await api("/api/stats");
      state.stats = stats;
      setStatus(true, `daemon aktif · ${stats.db_engine || "db"}`);
    } catch {
      setStatus(false, "daemon tidak merespons");
    }
  }, 15000);

  // Badge context harus berubah tanpa reload: run dari automation/IM dan
  // compact/refresh tidak selalu melewati pollRun milik chat yang sedang dibuka.
  let runsRefreshing = false;
  setInterval(async () => {
    if (runsRefreshing) return;
    runsRefreshing = true;
    try {
      const data = await api("/api/runs");
      state.runs = data.runs || [];
      refreshContextBadges();
    } catch {
      // daemon sibuk/terputus: percobaan berikutnya menyusul
    } finally {
      runsRefreshing = false;
    }
  }, 4000);

  // Human Decide: poll hanya saat view terbuka dan tidak ada input yang sedang
  // diketik, supaya refresh tidak menghapus jawaban yang belum disimpan.
  setInterval(() => {
    if (state.view === "decide" && !decideInputFocused()) {
      ensureAutopilot(true);
    }
  }, 10000);
}

if (boot) setStatus(true, "daemon aktif");

bind();
render();
loadAll();
ensureDecisionMode();
ensureAutopilot();
startWakeyLauncher();
startStationStream();
startProcessTicker();

if (state.view === "automations") {
  loadAutomations().then(render);
}
if (state.view === "auto-pilot") {
  loadAutoPilot().then(render);
}
if (state.view === "approvals") {
  loadApprovals().then(render);
}
if (state.view === "workflows") {
  loadWorkflows().then(render);
}
if (state.view === "channels") {
  loadChannels().then(render);
}
if (state.view === "groups") {
  loadGroups().then(render);
}
if (state.view === "knowledge") {
  loadKnowledge().then(render);
}
if (state.view === "traces") {
  loadTraces().then(render);
}
if (state.view === "memory") {
  loadMemoryScopes().then(render);
}
if (state.view === "engines") {
  Promise.all([ensureCatalog(), ensureAgyModels(), ensureOllamaModels()]).then(() => render());
}
if (state.view === "dashboard") {
  loadDashboardData().then(render);
}
if (state.view === "capabilities") {
  loadCapabilities().then(render);
}

// Tautan langsung: ?new=1 membuka formulir, ?waker=<id|slug> membuka detail.
const deepLink = new URLSearchParams(location.search);
const deepWaker = deepLink.get("waker");
if (deepLink.get("new") === "1") {
  openModal(null);
} else if (deepWaker) {
  const found = state.wakers.find((w) => w.id === deepWaker || w.slug === deepWaker);
  if (found) {
    state.selectedId = found.id;
    if (state.view === "chat") {
      // Tunggu riwayat sebelum paint pertama: tautan langsung ke percakapan tidak
      // boleh menampilkan ruang kosong lalu berubah.
      await ensureChatHistory(found);
      render();
    } else {
      // Katalog ditunggu supaya tautan langsung ke detail tidak menampilkan
      // pemilih model dalam keadaan kosong.
      await ensureCatalog();
      openDrawer(found.id);
    }
  }
}
