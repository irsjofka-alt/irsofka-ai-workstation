//! Penyedia model di luar registry engine: kredensial Nvidia untuk worker endpoint.
//!
//! Kunci tidak pernah dikembalikan ke UI — yang tampil hanya sidik (4 karakter akhir)
//! dan status "tersimpan". Penyimpanan di `config/nvidia.json` (gitignored, mode 0600).
//! Verifikasi memanggil `<base_url>/models` lewat `curl --config -` supaya kunci tidak
//! pernah muncul di argv proses (ps memperlihatkan argv ke semua pengguna mesin).

use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::process::Stdio;
use std::time::Duration;
use tokio::io::AsyncWriteExt;

use crate::paths::station_dir;

const DEFAULT_BASE: &str = "https://integrate.api.nvidia.com/v1";

#[derive(Serialize, Deserialize, Default)]
struct NvidiaStore {
    #[serde(default)]
    base_url: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    api_key: String,
    /// Lane kedua memakai kunci dan (opsional) base URL sendiri. Kedua lane
    /// berbagi batas 40 RPM per kunci, sehingga total menjadi 80 RPM.
    #[serde(default)]
    base_url_2: String,
    #[serde(default)]
    api_key_2: String,
    #[serde(default)]
    models: Vec<String>,
    #[serde(default)]
    models_2: Vec<String>,
}

/// Satu jalur kredensial yang bisa dipakai runner. `key` tidak pernah
/// diserialisasi ke response atau dicetak ke log.
#[derive(Clone)]
pub(crate) struct Lane {
    pub(crate) base: String,
    pub(crate) key: String,
    pub(crate) label: String,
}

fn path() -> std::path::PathBuf {
    station_dir().join("config/nvidia.json")
}

fn base_or_default(base: &str) -> String {
    if base.trim().is_empty() {
        DEFAULT_BASE.to_string()
    } else {
        base.trim().to_string()
    }
}

/// Model worker yang benar-benar dipakai engine `nvidia`, dibaca dari registry.
/// Dipakai untuk probe chat saat menyimpan kunci, supaya kunci divalidasi pada
/// model yang sama dengan run nyata.
fn engine_model() -> Option<String> {
    let path = station_dir().join("config/engines.json");
    let text = std::fs::read_to_string(path).ok()?;
    let value: Value = serde_json::from_str(&text).ok()?;
    value
        .pointer("/engines/nvidia/model")
        .and_then(Value::as_str)
        .map(str::to_string)
}

/// Effective rate per key from the engine registry. The runner reads the same
/// field; keeping the status in sync avoids claiming 80 RPM when the operator
/// has configured a lower safe rate.
fn effective_rpm_per_key() -> u64 {
    let path = station_dir().join("config/engines.json");
    let value: Value = std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or(Value::Null);
    value
        .pointer("/engines/nvidia/rpm_per_key")
        .and_then(Value::as_u64)
        .unwrap_or(35)
}

/// Bangun lane dari store. Lane yang tidak diisi dilewati; lane yang diisi
/// tetapi tidak sah menolak seluruh konfigurasi supaya tidak ada fallback
/// diam-diam ke lane lain.
fn lanes_from(store: &NvidiaStore) -> Result<Vec<Lane>, String> {
    let mut lanes = Vec::new();
    if !store.api_key.trim().is_empty() {
        let base = base_or_default(&store.base_url);
        if !valid_url(&base) || !valid_key(&store.api_key) {
            return Err("lane 1 Nvidia tidak sah; periksa base URL dan API key".to_string());
        }
        lanes.push(Lane {
            base,
            key: store.api_key.clone(),
            label: "lane-1".to_string(),
        });
    }
    if !store.api_key_2.trim().is_empty() {
        let base = base_or_default(&store.base_url_2);
        if !valid_url(&base) || !valid_key(&store.api_key_2) {
            return Err("lane 2 Nvidia tidak sah; periksa base URL dan API key".to_string());
        }
        lanes.push(Lane {
            base,
            key: store.api_key_2.clone(),
            label: "lane-2".to_string(),
        });
    }
    if lanes.is_empty() {
        return Err("API key Nvidia belum diisi (Capabilities → Models → Nvidia)".to_string());
    }
    Ok(lanes)
}

/// Lane untuk provider yang dipilih engine `openai`. Provider tanpa
/// penyimpanan kredensial ditolak dengan pesan eksplisit, supaya endpoint
/// baru tidak diam-diam memakai kunci Nvidia.
pub(crate) fn lanes_for(provider: &str) -> Result<Vec<Lane>, String> {
    match provider {
        "nvidia" => lanes_from(&load()),
        other => Err(format!(
            "provider \"{other}\" belum punya penyimpanan kredensial; tambahkan di api/providers.rs"
        )),
    }
}

fn load() -> NvidiaStore {
    std::fs::read_to_string(path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn hint(key: &str) -> String {
    let chars: Vec<char> = key.chars().collect();
    if chars.len() <= 4 {
        return "••••".to_string();
    }
    let tail: String = chars[chars.len() - 4..].iter().collect();
    format!("••••{tail}")
}

fn save(store: &NvidiaStore) -> Result<(), String> {
    let text = serde_json::to_string_pretty(store).map_err(|error| error.to_string())?;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .mode(0o600)
        .open(path())
        .map_err(|error| format!("tidak bisa menulis {}: {error}", path().display()))?;
    file.write_all(text.as_bytes())
        .map_err(|error| format!("tidak bisa menulis kunci: {error}"))?;
    // `mode(0o600)` hanya berlaku saat berkas dibuat; berkas lama harus dipaksa.
    let _ = std::fs::set_permissions(path(), std::fs::Permissions::from_mode(0o600));
    Ok(())
}

fn valid_url(url: &str) -> bool {
    (url.starts_with("https://") || url.starts_with("http://"))
        && !url.chars().any(|ch| ch.is_whitespace() || ch.is_control())
}

fn valid_key(key: &str) -> bool {
    !key.trim().is_empty() && !key.chars().any(|ch| ch.is_whitespace() || ch.is_control())
}

/// Daftar model dari endpoint OpenAI-compatible. Kunci dikirim lewat stdin curl config.
async fn probe_models(base_url: &str, api_key: &str) -> Result<Vec<String>, String> {
    if !valid_url(base_url) {
        return Err("base URL harus http(s) dan tanpa spasi/baris baru".to_string());
    }
    if !valid_key(api_key) {
        return Err("API key kosong atau memuat spasi/baris baru".to_string());
    }
    let url = format!("{}/models", base_url.trim_end_matches('/'));
    let key = api_key.replace('\\', "\\\\").replace('"', "\\\"");
    let config = format!("url = \"{url}\"\nheader = \"Authorization: Bearer {key}\"\nsilent\n");
    let mut child = tokio::process::Command::new("curl")
        .args(["--max-time", "25", "--config", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| format!("curl tidak bisa dijalankan: {error}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(config.as_bytes())
            .await
            .map_err(|error| format!("stdin curl: {error}"))?;
        drop(stdin);
    }
    let out = tokio::time::timeout(Duration::from_secs(30), child.wait_with_output())
        .await
        .map_err(|_| "endpoint melewati batas waktu 30 detik".to_string())?
        .map_err(|error| format!("curl: {error}"))?;
    if !out.status.success() {
        return Err(format!(
            "endpoint menolak: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    let payload: Value = serde_json::from_slice(&out.stdout)
        .map_err(|error| format!("jawaban bukan JSON: {error}"))?;
    let mut models: Vec<String> = payload
        .get("data")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("id").and_then(Value::as_str))
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    models.sort();
    if models.is_empty() {
        return Err("endpoint menjawab tanpa daftar model".to_string());
    }
    Ok(models)
}

/// Probe chat minimal: `/models` Nvidia bisa diakses tanpa kunci yang sah,
/// jadi daftar model saja tidak membuktikan kunci bisa dipakai. Satu completion
/// pendek dengan model worker memastikan kunci dan akses model benar-benar hidup.
fn transient_chat_error(message: &str) -> bool {
    let lower = message.to_lowercase();
    [
        "overload",
        "temporar",
        "try again",
        "rate limit",
        "too many requests",
        "429",
        "503",
    ]
    .iter()
    .any(|needle| lower.contains(needle))
}

async fn probe_chat(
    base_url: &str,
    api_key: &str,
    model: &str,
) -> Result<Option<String>, String> {
    if !valid_url(base_url) {
        return Err("base URL harus http(s) dan tanpa spasi/baris baru".to_string());
    }
    if !valid_key(api_key) {
        return Err("API key kosong atau memuat spasi/baris baru".to_string());
    }
    if model.trim().is_empty() {
        return Err("model untuk verifikasi chat kosong".to_string());
    }
    let url = format!("{}/chat/completions", base_url.trim_end_matches('/'));
    let key = api_key.replace('\\', "\\\\").replace('"', "\\\"");
    let body = json!({
        "model": model,
        "messages": [{ "role": "user", "content": "ping" }],
        "max_tokens": 8,
        "temperature": 0,
        "stream": false
    })
    .to_string();
    let config = format!(
        "url = \"{url}\"\nheader = \"Authorization: Bearer {key}\"\nheader = \"Content-Type: application/json\"\nsilent\nshow-error\n"
    );
    let mut child = tokio::process::Command::new("curl")
        .args(["--max-time", "30", "--config", "-", "--data-binary", body.as_str()])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| format!("curl tidak bisa dijalankan: {error}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(config.as_bytes())
            .await
            .map_err(|error| format!("stdin curl: {error}"))?;
        drop(stdin);
    }
    let out = tokio::time::timeout(Duration::from_secs(35), child.wait_with_output())
        .await
        .map_err(|_| "chat verifikasi melewati batas waktu 35 detik".to_string())?
        .map_err(|error| format!("curl: {error}"))?;
    let redact = |text: &str| text.replace(api_key, "[redacted]");
    if !out.status.success() {
        return Err(format!(
            "chat verifikasi ditolak: {}",
            redact(String::from_utf8_lossy(&out.stderr).trim())
        ));
    }
    let payload: Value = serde_json::from_slice(&out.stdout)
        .map_err(|error| format!("jawaban chat bukan JSON: {error}"))?;
    if let Some(error) = payload.get("error") {
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("endpoint mengembalikan error");
        // Overload/rate-limit berarti autentikasi sudah lolos; jangan gagalkan
        // penyimpanan kunci hanya karena model sedang sibuk.
        if transient_chat_error(message) {
            return Ok(Some(format!("chat probe tertunda: {}", redact(message))));
        }
        return Err(format!("chat verifikasi ditolak: {}", redact(message)));
    }
    let has_choices = payload
        .get("choices")
        .and_then(Value::as_array)
        .map(|items| !items.is_empty())
        .unwrap_or(false);
    if has_choices {
        Ok(None)
    } else {
        let raw = String::from_utf8_lossy(&out.stdout);
        if transient_chat_error(&raw) {
            return Ok(Some(
                "chat probe tertunda: endpoint sibuk".to_string(),
            ));
        }
        Err("chat verifikasi tidak mengembalikan choices".to_string())
    }
}

fn status(store: &NvidiaStore, extra: Value) -> Value {
    let lane_count = [!store.api_key.trim().is_empty(), !store.api_key_2.trim().is_empty()]
        .into_iter()
        .filter(|filled| *filled)
        .count();
    let mut body = json!({
        "configured": !store.api_key.trim().is_empty(),
        "base_url": base_or_default(&store.base_url),
        "model": store.model,
        "key_hint": if store.api_key.is_empty() { Value::Null } else { json!(hint(&store.api_key)) },
        "models": store.models,
        "lanes": [
            {
                "label": "lane-1",
                "configured": !store.api_key.trim().is_empty(),
                "base_url": base_or_default(&store.base_url),
                "key_hint": if store.api_key.is_empty() { Value::Null } else { json!(hint(&store.api_key)) },
                "models": store.models,
            },
            {
                "label": "lane-2",
                "configured": !store.api_key_2.trim().is_empty(),
                "base_url": base_or_default(&store.base_url_2),
                "key_hint": if store.api_key_2.is_empty() { Value::Null } else { json!(hint(&store.api_key_2)) },
                "models": store.models_2,
            }
        ],
        "lane_count": lane_count,
        "rpm_per_key": effective_rpm_per_key(),
        "rpm_total": effective_rpm_per_key() * lane_count as u64,
    });
    if let (Some(object), Some(extra)) = (body.as_object_mut(), extra.as_object()) {
        for (key, value) in extra {
            object.insert(key.clone(), value.clone());
        }
    }
    body
}

pub(crate) async fn get_nvidia() -> Json<Value> {
    Json(status(&load(), json!({})))
}

#[derive(Deserialize)]
pub(crate) struct NvidiaInput {
    #[serde(default)]
    pub(crate) api_key: Option<String>,
    #[serde(default)]
    pub(crate) base_url: Option<String>,
    #[serde(default)]
    pub(crate) model: Option<String>,
    #[serde(default)]
    pub(crate) api_key_2: Option<String>,
    #[serde(default)]
    pub(crate) base_url_2: Option<String>,
    #[serde(default)]
    pub(crate) clear_secondary: bool,
}

/// Simpan kunci/endpoint lalu verifikasi dengan membaca daftar model. Kunci yang
/// sudah ada dipertahankan bila field terkait dikirim kosong. Dua kunci menjadi
/// dua lane; masing-masing tetap dibatasi 40 RPM, jadi total 80 RPM.
pub(crate) async fn put_nvidia(Json(body): Json<NvidiaInput>) -> Response {
    let mut store = load();
    if let Some(url) = body.base_url {
        store.base_url = url.trim().to_string();
    }
    if store.base_url.trim().is_empty() {
        store.base_url = DEFAULT_BASE.to_string();
    }
    if let Some(url) = body.base_url_2 {
        store.base_url_2 = url.trim().to_string();
    }
    if let Some(model) = body.model {
        store.model = model.trim().to_string();
    }
    if let Some(key) = body.api_key {
        if !key.trim().is_empty() {
            store.api_key = key.trim().to_string();
        }
    }
    if let Some(key) = body.api_key_2 {
        if !key.trim().is_empty() {
            store.api_key_2 = key.trim().to_string();
        }
    }
    if body.clear_secondary {
        store.api_key_2.clear();
        store.base_url_2.clear();
        store.models_2.clear();
    }

    let primary = !store.api_key.trim().is_empty();
    let secondary = !store.api_key_2.trim().is_empty();
    if !primary && !secondary {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "ok": false, "error": "API key belum diisi" })),
        )
            .into_response();
    }
    if primary && (!valid_url(&base_or_default(&store.base_url)) || !valid_key(&store.api_key)) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "ok": false, "error": "lane 1: base URL atau API key memuat karakter yang tidak sah" })),
        )
            .into_response();
    }
    if secondary && (!valid_url(&base_or_default(&store.base_url_2)) || !valid_key(&store.api_key_2)) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "ok": false, "error": "lane 2: base URL atau API key memuat karakter yang tidak sah" })),
        )
            .into_response();
    }
    if primary && secondary && store.api_key == store.api_key_2 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "ok": false,
                "error": "lane 2 memakai API key yang sama dengan lane 1; gunakan key kedua yang berbeda agar total RPM benar-benar bertambah"
            })),
        )
            .into_response();
    }

    let preferred_model = if !store.model.trim().is_empty() {
        Some(store.model.clone())
    } else {
        engine_model()
    };
    let mut warnings: Vec<String> = Vec::new();
    let mut primary_models = Vec::new();
    if primary {
        match probe_models(&base_or_default(&store.base_url), &store.api_key).await {
            Ok(models) => {
                primary_models = models;
                let model = preferred_model
                    .clone()
                    .or_else(|| primary_models.first().cloned())
                    .unwrap_or_default();
                match probe_chat(&base_or_default(&store.base_url), &store.api_key, &model).await {
                    Ok(Some(warning)) => warnings.push(format!("lane-1: {warning}")),
                    Ok(None) => {}
                    Err(error) => {
                        return (
                            StatusCode::BAD_GATEWAY,
                            Json(json!({
                                "ok": false,
                                "lane": "lane-1",
                                "error": error,
                                "nvidia": status(&store, json!({})),
                            })),
                        )
                            .into_response();
                    }
                }
            }
            Err(error) => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({
                        "ok": false,
                        "lane": "lane-1",
                        "error": error,
                        "nvidia": status(&store, json!({})),
                    })),
                )
                    .into_response();
            }
        }
    }
    let mut secondary_models = Vec::new();
    if secondary {
        match probe_models(&base_or_default(&store.base_url_2), &store.api_key_2).await {
            Ok(models) => {
                secondary_models = models;
                let model = preferred_model
                    .clone()
                    .or_else(|| secondary_models.first().cloned())
                    .unwrap_or_default();
                match probe_chat(&base_or_default(&store.base_url_2), &store.api_key_2, &model).await {
                    Ok(Some(warning)) => warnings.push(format!("lane-2: {warning}")),
                    Ok(None) => {}
                    Err(error) => {
                        return (
                            StatusCode::BAD_GATEWAY,
                            Json(json!({
                                "ok": false,
                                "lane": "lane-2",
                                "error": error,
                                "nvidia": status(&store, json!({})),
                            })),
                        )
                            .into_response();
                    }
                }
            }
            Err(error) => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({
                        "ok": false,
                        "lane": "lane-2",
                        "error": error,
                        "nvidia": status(&store, json!({})),
                    })),
                )
                    .into_response();
            }
        }
    }

    let mut all = primary_models.clone();
    all.extend(secondary_models.iter().cloned());
    all.sort();
    all.dedup();
    store.models = all;
    store.models_2 = secondary_models;

    if let Err(error) = save(&store) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "ok": false, "error": error }))).into_response();
    }
    Json(json!({
        "ok": true,
        "qoder": Value::Null,
        "nvidia": status(&store, json!({})),
        "warnings": warnings,
    }))
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(primary: &str, secondary: &str) -> NvidiaStore {
        NvidiaStore {
            base_url: "https://primary.example/v1".to_string(),
            api_key: primary.to_string(),
            base_url_2: "https://secondary.example/v1".to_string(),
            api_key_2: secondary.to_string(),
            ..Default::default()
        }
    }

    #[test]
    fn dua_kunci_menjadi_dua_lane() {
        let lanes = lanes_from(&store("kunci-1", "kunci-2")).unwrap();
        assert_eq!(lanes.len(), 2);
        assert_eq!(lanes[0].label, "lane-1");
        assert_eq!(lanes[0].key, "kunci-1");
        assert_eq!(lanes[1].label, "lane-2");
        assert_eq!(lanes[1].base, "https://secondary.example/v1");
    }

    #[test]
    fn satu_kunci_tetap_satu_lane() {
        let lanes = lanes_from(&store("kunci-1", "")).unwrap();
        assert_eq!(lanes.len(), 1);
    }

    #[test]
    fn overload_bukan_kegagalan_autentikasi() {
        assert!(transient_chat_error("Service temporarily overloaded"));
        assert!(transient_chat_error("429 too many requests"));
        assert!(!transient_chat_error("Invalid API key"));
    }

    #[test]
    fn status_dua_lane_mengikuti_rpm_config() {
        let body = status(&store("kunci-1", "kunci-2"), json!({}));
        assert_eq!(body["lane_count"], 2);
        let rpm = body["rpm_per_key"].as_u64().unwrap();
        assert!(rpm > 0);
        assert_eq!(body["rpm_total"], rpm * 2);
        assert_eq!(body["lanes"][1]["configured"], true);
    }
}
