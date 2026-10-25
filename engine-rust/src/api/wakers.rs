//! Waker — identitas kerja, bukan tab terminal.
//!
//! Sebelum ini, "agent" di workstation adalah entri di `cli_profiles.json` yang terikat
//! satu pane. Di sini waker punya nama, engine, model, workspace, izin, dan riwayat sendiri.
//! Penyimpanan tahap ini berupa JSON di `~/.ai-station/data/wakers.json`; bentuk field-nya
//! sengaja sama dengan tabel `waker` di `migrations/002_waker_console.sql`, sehingga
//! pemindahan ke PostgreSQL nanti tidak mengubah kontrak HTTP.
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::adapters::EngineRegistry;
use crate::api::AppState;
use crate::paths::{engines_registry_path, home_dir, wakers_path};

const VALID_STATUS: [&str; 4] = ["draft", "active", "paused", "archived"];
/// Tingkat penalaran yang dikenal QoderWake. Divalidasi di sini supaya nilai salah
/// ketik tidak baru ketahuan saat engine dijalankan.
const VALID_EFFORT: [&str; 6] = ["auto", "low", "medium", "high", "xhigh", "max"];
const VALID_APPROVAL: [&str; 2] = ["auto", "require"];
const VALID_STYLE: [&str; 3] = ["concise", "normal", "detailed"];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct Waker {
    pub(crate) id: String,
    pub(crate) slug: String,
    pub(crate) name: String,
    #[serde(default)]
    pub(crate) description: String,
    #[serde(default)]
    pub(crate) system_prompt: String,
    pub(crate) engine_key: String,
    #[serde(default)]
    pub(crate) model: Option<String>,
    /// Thinking level pilihan pengguna, mis. `high`. `None` = ikut bawaan model.
    #[serde(default)]
    pub(crate) reasoning_effort: Option<String>,
    /// Context window dalam token, mis. 200000. `None` = ikut bawaan model.
    #[serde(default)]
    pub(crate) context_window: Option<u64>,
    pub(crate) workspace: String,
    pub(crate) status: String,
    /// `auto` atau `require`. Waker yang `require` tidak pernah berjalan tanpa
    /// persetujuan manusia, termasuk saat dijalankan automation.
    #[serde(default = "default_approval_mode")]
    pub(crate) approval_mode: String,
    /// `concise` | `normal` | `detailed` — memengaruhi instruksi di system prompt.
    #[serde(default = "default_answer_style")]
    pub(crate) answer_style: String,
    /// Batas keluaran (token) bila engine mendukung; saat ini diteruskan sebagai
    /// instruksi prompt.
    #[serde(default)]
    pub(crate) max_output_tokens: Option<i64>,
    /// `false` = sengaja tidak ditawarkan di @Worker (dipanggil lewat automation/grup).
    #[serde(default = "default_chat_available")]
    pub(crate) chat_available: bool,
    pub(crate) created_at: String,
    pub(crate) updated_at: String,
}

fn default_chat_available() -> bool {
    true
}

fn default_approval_mode() -> String {
    "auto".to_string()
}

fn default_answer_style() -> String {
    "normal".to_string()
}

pub(crate) type WakerStore = Arc<RwLock<Vec<Waker>>>;

/// Dibaca sekali saat daemon mulai. PostgreSQL diutamakan; berkas JSON menjadi
/// cadangan sekaligus bahan impor pertama kali basis data masih kosong.
/// Berkas yang rusak tidak menjatuhkan daemon: daftar kosong lebih jujur daripada
/// menolak melayani console.
pub(crate) async fn load_store(store: &crate::store::Store) -> WakerStore {
    let json_items: Vec<Waker> = fs::read_to_string(wakers_path())
        .ok()
        .and_then(|text| serde_json::from_str::<Vec<Waker>>(&text).ok())
        .unwrap_or_default();

    if let Some(mut items) = store.load_wakers().await {
        if items.is_empty() && !json_items.is_empty() {
            for waker in &json_items {
                let _ = store.upsert_waker(waker).await;
            }
            items = json_items;
        }
        return Arc::new(RwLock::new(items));
    }

    Arc::new(RwLock::new(json_items))
}

fn persist(items: &[Waker]) -> Result<(), String> {
    let path = wakers_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("tidak bisa membuat {}: {e}", parent.display()))?;
    }
    let body = serde_json::to_string_pretty(items).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, body).map_err(|e| format!("tidak bisa menulis {}: {e}", tmp.display()))?;
    fs::rename(&tmp, &path).map_err(|e| format!("tidak bisa memindahkan {}: {e}", path.display()))
}

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339()
}

fn new_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("wk_{nanos:x}")
}

fn slugify(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    let mut dash = false;
    for ch in name.chars().flat_map(char::to_lowercase) {
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
            dash = false;
        } else if !dash && !out.is_empty() {
            out.push('-');
            dash = true;
        }
    }
    let trimmed = out.trim_matches('-').to_string();
    if trimmed.len() > 40 {
        trimmed[..40].trim_end_matches('-').to_string()
    } else if trimmed.is_empty() {
        "waker".to_string()
    } else {
        trimmed
    }
}

fn unique_slug(base: &str, items: &[Waker]) -> String {
    if !items.iter().any(|w| w.slug == base) {
        return base.to_string();
    }
    for n in 2..1000 {
        let candidate = format!("{base}-{n}");
        if !items.iter().any(|w| w.slug == candidate) {
            return candidate;
        }
    }
    format!("{base}-{}", new_id())
}

fn bad_request(message: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": message.into() }))).into_response()
}

fn not_found(message: impl Into<String>) -> Response {
    (StatusCode::NOT_FOUND, Json(json!({ "error": message.into() }))).into_response()
}

fn registry() -> Result<EngineRegistry, String> {
    EngineRegistry::load(engines_registry_path()).map_err(|e| e.to_string())
}

fn ensure_effort(value: &str) -> Result<(), String> {
    if VALID_EFFORT.contains(&value) {
        Ok(())
    } else {
        Err(format!(
            "thinking level \"{value}\" tidak dikenal (pilih {})",
            VALID_EFFORT.join(", ")
        ))
    }
}

fn ensure_context(value: u64) -> Result<(), String> {
    if value >= 1_000 {
        Ok(())
    } else {
        Err("context window minimal 1000 token".to_string())
    }
}

fn ensure_engine(key: &str) -> Result<(), String> {
    // `auto` bukan engine: ia diselesaikan saat run dimulai lewat
    // /api/routing/suggest, memakai sisa kuota yang dilaporkan meter.
    if key == "auto" {
        return Ok(());
    }
    let reg = registry()?;
    if reg.get(key).is_none() && reg.group(key).is_none() {
        return Err(format!(
            "engine atau grup \"{key}\" tidak terdaftar di config/engines.json"
        ));
    }
    Ok(())
}

#[derive(Deserialize)]
pub(crate) struct CreateWaker {
    pub(crate) name: String,
    #[serde(default)]
    pub(crate) chat_available: Option<bool>,
    #[serde(default)]
    pub(crate) description: Option<String>,
    #[serde(default)]
    pub(crate) system_prompt: Option<String>,
    pub(crate) engine_key: String,
    #[serde(default)]
    pub(crate) model: Option<String>,
    #[serde(default)]
    pub(crate) reasoning_effort: Option<String>,
    #[serde(default)]
    pub(crate) context_window: Option<u64>,
    #[serde(default)]
    pub(crate) workspace: Option<String>,
    #[serde(default)]
    pub(crate) approval_mode: Option<String>,
    #[serde(default)]
    pub(crate) answer_style: Option<String>,
    #[serde(default)]
    pub(crate) max_output_tokens: Option<i64>,
}

#[derive(Deserialize)]
pub(crate) struct PatchWaker {
    #[serde(default)]
    pub(crate) name: Option<String>,
    #[serde(default)]
    pub(crate) chat_available: Option<bool>,
    #[serde(default)]
    pub(crate) description: Option<String>,
    #[serde(default)]
    pub(crate) system_prompt: Option<String>,
    #[serde(default)]
    pub(crate) engine_key: Option<String>,
    #[serde(default)]
    pub(crate) model: Option<String>,
    #[serde(default)]
    pub(crate) reasoning_effort: Option<String>,
    #[serde(default)]
    pub(crate) context_window: Option<u64>,
    #[serde(default)]
    pub(crate) workspace: Option<String>,
    #[serde(default)]
    pub(crate) status: Option<String>,
    #[serde(default)]
    pub(crate) approval_mode: Option<String>,
    #[serde(default)]
    pub(crate) answer_style: Option<String>,
    #[serde(default)]
    pub(crate) max_output_tokens: Option<i64>,
}

pub(crate) async fn list(State(state): State<AppState>) -> Json<Value> {
    let items = state.wakers.read().await;
    Json(json!({
        "wakers": *items,
        "count": items.len(),
        "backend": state.store.backend,
    }))
}

pub(crate) async fn get(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    let items = state.wakers.read().await;
    match items.iter().find(|w| w.id == id || w.slug == id) {
        Some(w) => Json(json!(w)).into_response(),
        None => not_found(format!("waker \"{id}\" tidak ada")),
    }
}

pub(crate) async fn create(
    State(state): State<AppState>,
    Json(payload): Json<CreateWaker>,
) -> Response {
    let name = payload.name.trim().to_string();
    if name.is_empty() {
        return bad_request("nama waker tidak boleh kosong");
    }
    if let Err(message) = ensure_engine(&payload.engine_key) {
        return bad_request(message);
    }
    let effort = payload
        .reasoning_effort
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty());
    if let Some(value) = effort.as_deref() {
        if let Err(message) = ensure_effort(value) {
            return bad_request(message);
        }
    }
    if let Some(value) = payload.context_window {
        if let Err(message) = ensure_context(value) {
            return bad_request(message);
        }
    }
    let approval_mode = payload
        .approval_mode
        .unwrap_or_else(|| "auto".to_string());
    if !VALID_APPROVAL.contains(&approval_mode.as_str()) {
        return bad_request(format!(
            "approval_mode \"{approval_mode}\" tidak dikenal (pilih auto atau require)"
        ));
    }
    let answer_style = payload
        .answer_style
        .unwrap_or_else(|| "normal".to_string());
    if !VALID_STYLE.contains(&answer_style.as_str()) {
        return bad_request(format!(
            "answer_style \"{answer_style}\" tidak dikenal (pilih concise, normal, detailed)"
        ));
    }
    if let Some(limit) = payload.max_output_tokens {
        if limit <= 0 || limit > 100_000 {
            return bad_request("max_output_tokens harus 1..100000");
        }
    }

    let mut items = state.wakers.write().await;
    let slug = unique_slug(&slugify(&name), &items);
    let stamp = now_iso();
    let waker = Waker {
        id: new_id(),
        slug,
        name,
        description: payload.description.unwrap_or_default(),
        system_prompt: payload.system_prompt.unwrap_or_default(),
        engine_key: payload.engine_key,
        model: payload.model.filter(|m| !m.trim().is_empty()),
        reasoning_effort: effort,
        context_window: payload.context_window.filter(|c| *c > 0),
        workspace: payload
            .workspace
            .filter(|w| !w.trim().is_empty())
            .unwrap_or_else(|| home_dir().to_string_lossy().to_string()),
        status: "active".to_string(),
        approval_mode,
        answer_style,
        max_output_tokens: payload.max_output_tokens,
        chat_available: payload.chat_available.unwrap_or(true),
        created_at: stamp.clone(),
        updated_at: stamp,
    };
    items.push(waker.clone());
    if let Err(message) = state.store.upsert_waker(&waker).await {
        items.retain(|w| w.id != waker.id);
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": message }))).into_response();
    }
    if state.store.backend == "JSON" {
        if let Err(message) = persist(&items) {
            items.retain(|w| w.id != waker.id);
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": message }))).into_response();
        }
    } else {
        // Cermin JSON dijaga best-effort supaya tetap ada salinan saat database mati.
        let _ = persist(&items);
    }
    (StatusCode::CREATED, Json(json!(waker))).into_response()
}

pub(crate) async fn patch(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(payload): Json<PatchWaker>,
) -> Response {
    if let Some(status) = payload.status.as_deref() {
        if !VALID_STATUS.contains(&status) {
            return bad_request(format!(
                "status \"{status}\" tidak dikenal (pilih draft, active, paused, archived)"
            ));
        }
    }
    if let Some(engine) = payload.engine_key.as_deref() {
        if let Err(message) = ensure_engine(engine) {
            return bad_request(message);
        }
    }

    let mut items = state.wakers.write().await;
    let Some(pos) = items.iter().position(|w| w.id == id || w.slug == id) else {
        return not_found(format!("waker \"{id}\" tidak ada"));
    };
    let snapshot = items[pos].clone();

    if let Some(name) = payload.name {
        let trimmed = name.trim().to_string();
        if trimmed.is_empty() {
            return bad_request("nama waker tidak boleh kosong");
        }
        items[pos].name = trimmed;
    }
    if let Some(v) = payload.description {
        items[pos].description = v;
    }
    if let Some(v) = payload.system_prompt {
        items[pos].system_prompt = v;
    }
    if let Some(v) = payload.chat_available {
        items[pos].chat_available = v;
    }
    if let Some(v) = payload.engine_key {
        items[pos].engine_key = v;
    }
    if let Some(v) = payload.model {
        items[pos].model = if v.trim().is_empty() { None } else { Some(v) };
    }
    if let Some(v) = payload.reasoning_effort {
        let trimmed = v.trim().to_string();
        if trimmed.is_empty() {
            items[pos].reasoning_effort = None;
        } else {
            if let Err(message) = ensure_effort(&trimmed) {
                return bad_request(message);
            }
            items[pos].reasoning_effort = Some(trimmed);
        }
    }
    if let Some(v) = payload.context_window {
        if v == 0 {
            items[pos].context_window = None;
        } else {
            if let Err(message) = ensure_context(v) {
                return bad_request(message);
            }
            items[pos].context_window = Some(v);
        }
    }
    if let Some(v) = payload.workspace {
        if !v.trim().is_empty() {
            items[pos].workspace = v;
        }
    }
    if let Some(v) = payload.status {
        items[pos].status = v;
    }
    if let Some(v) = payload.approval_mode {
        if !VALID_APPROVAL.contains(&v.as_str()) {
            return bad_request(format!(
                "approval_mode \"{v}\" tidak dikenal (pilih auto atau require)"
            ));
        }
        items[pos].approval_mode = v;
    }
    if let Some(v) = payload.answer_style {
        if !VALID_STYLE.contains(&v.as_str()) {
            return bad_request(format!(
                "answer_style \"{v}\" tidak dikenal (pilih concise, normal, detailed)"
            ));
        }
        items[pos].answer_style = v;
    }
    if let Some(v) = payload.max_output_tokens {
        if v == 0 {
            items[pos].max_output_tokens = None;
        } else if v > 100_000 {
            return bad_request("max_output_tokens harus 1..100000");
        } else {
            items[pos].max_output_tokens = Some(v);
        }
    }
    items[pos].updated_at = now_iso();
    let updated = items[pos].clone();

    if let Err(message) = state.store.upsert_waker(&updated).await {
        items[pos] = snapshot;
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": message }))).into_response();
    }
    if state.store.backend == "JSON" {
        if let Err(message) = persist(&items) {
            items[pos] = snapshot;
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": message }))).into_response();
        }
    } else {
        let _ = persist(&items);
    }
    Json(json!(updated)).into_response()
}

pub(crate) async fn remove(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    let mut items = state.wakers.write().await;
    let Some(pos) = items.iter().position(|w| w.id == id || w.slug == id) else {
        return not_found(format!("waker \"{id}\" tidak ada"));
    };
    let removed = items[pos].clone();
    if let Err(message) = state.store.delete_waker(&removed.id).await {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": message }))).into_response();
    }
    items.remove(pos);
    if state.store.backend == "JSON" {
        if let Err(message) = persist(&items) {
            items.insert(pos, removed.clone());
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": message }))).into_response();
        }
    } else {
        let _ = persist(&items);
    }
    Json(json!({ "deleted": removed.id })).into_response()
}

#[derive(Deserialize)]
pub(crate) struct ModelOverride {
    pub(crate) model: String,
}

/// Menyimpan model default sebuah engine ke `config/engine_overrides.json`.
/// Registry asli tidak ditulis ulang agar suntingan tangan tetap utuh.
pub(crate) async fn set_engine_model(
    axum::extract::Path(key): axum::extract::Path<String>,
    Json(body): Json<ModelOverride>,
) -> Response {
    let registry = match crate::adapters::EngineRegistry::load(engines_registry_path()) {
        Ok(registry) => registry,
        Err(error) => return bad_request(error.to_string()),
    };
    if registry.get(&key).is_none() {
        return not_found(format!("engine \"{key}\" tidak terdaftar"));
    }

    let path = crate::paths::engine_overrides_path();
    let mut overrides: std::collections::BTreeMap<String, String> = std::fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default();

    let model = body.model.trim().to_string();
    if model.is_empty() {
        overrides.remove(&key);
    } else {
        overrides.insert(key.clone(), model.clone());
    }

    let body = serde_json::to_string_pretty(&overrides).unwrap_or_else(|_| "{}".to_string());
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(error) = std::fs::write(&path, body) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("tidak bisa menulis override: {error}") })),
        )
            .into_response();
    }
    Json(json!({ "engine": key, "model": model })).into_response()
}

/// Katalog mesin untuk pemilih di console. Entri yang gagal dimuat ikut dilaporkan
/// (`skipped`) supaya registry yang bolong terlihat, bukan tersembunyi.
pub(crate) async fn engines() -> Json<Value> {
    Json(engines_payload())
}

/// Bentuk yang sama dipakai `/api/engines` dan bootstrap console, supaya halaman
/// dan API tidak pernah berbeda pendapat tentang isi registry.
pub(crate) fn engines_payload() -> Value {
    match registry() {
        Ok(reg) => {
            let items: Vec<Value> = reg
                .iter()
                .map(|spec| {
                    json!({
                        "key": spec.key,
                        "kind": spec.kind.as_str(),
                        "role": spec.role,
                        "model": spec.model,
                        "binary": spec.binary,
                        "tab": spec.tab,
                        "quota_gated": spec.quota_gate.is_some(),
                        "run_path": crate::adapters::run_path(spec).as_str(),
                        // Registry boleh menyatakan provider secara eksplisit; heuristik
                        // di bawah hanya cadangan untuk entri lama.
                        "provider": if let Some(explicit) =
                            spec.raw.get("provider").and_then(Value::as_str)
                        {
                            explicit
                        } else if spec.key == "qoder"
                            || spec.binary.as_deref() == Some("qoder")
                        {
                            "qoder"
                        } else if spec.binary.as_deref() == Some("agy")
                            || spec.tab.as_deref() == Some("antigravity")
                        {
                            "agy"
                        } else if spec.kind == crate::adapters::EngineKind::Ollama {
                            "ollama"
                        } else if spec
                            .binary
                            .as_deref()
                            .map(|binary| binary.starts_with("cline"))
                            .unwrap_or(false)
                        {
                            "cline"
                        } else {
                            "other"
                        },
                        "windows": spec.quota_gate.as_ref().map(|g| g.windows.clone()).unwrap_or_default(),
                    })
                })
                .collect();
            let skipped: Vec<Value> = reg
                .skipped()
                .iter()
                .map(|(key, reason)| json!({ "key": key, "reason": reason }))
                .collect();
            let groups: Vec<Value> = reg
                .groups()
                .iter()
                .map(|(name, models)| json!({ "name": name, "models": models }))
                .collect();
            json!({
                "engines": items,
                "count": items.len(),
                "groups": groups,
                "skipped": skipped,
                "source": reg.source().display().to_string(),
            })
        }
        Err(message) => json!({ "engines": [], "count": 0, "error": message }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slug_stabil_dan_unik() {
        assert_eq!(slugify("Daily  Report!!"), "daily-report");
        assert_eq!(slugify("  "), "waker");

        let items = vec![
            Waker {
                id: "1".into(),
                slug: "daily-report".into(),
                name: "A".into(),
                description: String::new(),
                system_prompt: String::new(),
                engine_key: "qoder".into(),
                model: None,
                reasoning_effort: None,
                context_window: None,
                workspace: "/tmp".into(),
                status: "active".into(),
                approval_mode: "auto".into(),
                answer_style: "normal".into(),
                max_output_tokens: None,
                chat_available: true,
                created_at: "t".into(),
                updated_at: "t".into(),
            },
        ];
        assert_eq!(unique_slug("daily-report", &items), "daily-report-2");
        assert_eq!(unique_slug("baru", &items), "baru");
    }
}
