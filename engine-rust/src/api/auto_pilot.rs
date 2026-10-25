//! Sakelar global Auto Pilot Task.
//!
//! Task-nya sendiri adalah automation biasa (`kind=autopilot`) supaya penjadwal,
//! riwayat, dan kebijakan overlap tidak punya dua implementasi. Berkas ini hanya
//! menyimpan satu keputusan: apakah putaran Auto Pilot boleh berjalan sekarang.
//! Disimpan di config/auto_pilot.json, bukan di tabel baru, karena bentuknya satu
//! nilai yang dibaca penjadwal dan ditulis tombol — dua tempat, satu kata.
use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::PathBuf;

use crate::paths::config_dir;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct AutoPilotMode {
    #[serde(default = "default_mode")]
    pub(crate) mode: String,
    #[serde(default)]
    pub(crate) changed_by: String,
    #[serde(default)]
    pub(crate) changed_epoch: i64,
}

fn default_mode() -> String {
    "OFF".to_string()
}

impl Default for AutoPilotMode {
    fn default() -> Self {
        Self {
            mode: default_mode(),
            changed_by: "operator".to_string(),
            changed_epoch: 0,
        }
    }
}

fn state_path() -> PathBuf {
    config_dir().join("auto_pilot.json")
}

pub(crate) fn load() -> AutoPilotMode {
    std::fs::read_to_string(state_path())
        .ok()
        .and_then(|text| serde_json::from_str::<AutoPilotMode>(&text).ok())
        .map(|mut value| {
            value.mode = normalize(&value.mode);
            value
        })
        .unwrap_or_default()
}

fn normalize(raw: &str) -> String {
    match raw.trim().to_uppercase().as_str() {
        "ON" | "AUTO" | "AUTO PILOT" => "AUTO".to_string(),
        _ => "OFF".to_string(),
    }
}

pub(crate) fn is_on() -> bool {
    load().mode == "AUTO"
}

fn save(value: &AutoPilotMode) -> Result<(), String> {
    let dir = config_dir();
    std::fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
    let body = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
    let tmp = dir.join("auto_pilot.json.tmp");
    std::fs::write(&tmp, body).map_err(|error| error.to_string())?;
    std::fs::rename(&tmp, state_path()).map_err(|error| error.to_string())
}

fn envelope(status: StatusCode, body: Value) -> Response {
    (status, Json(body)).into_response()
}

fn payload(value: &AutoPilotMode) -> Value {
    json!({
        "mode": value.mode,
        "on": value.mode == "AUTO",
        "changed_by": value.changed_by,
        "changed_epoch": value.changed_epoch,
    })
}

/// GET /api/auto-pilot-mode
pub(crate) async fn get_mode() -> Response {
    Json(payload(&load())).into_response()
}

/// POST /api/auto-pilot-mode — `{"mode":"auto"|"off","by":"operator"}`
pub(crate) async fn post_mode(Json(body): Json<Value>) -> Response {
    let Some(raw) = body.get("mode").and_then(Value::as_str) else {
        return envelope(
            StatusCode::BAD_REQUEST,
            json!({"error": "mode wajib diisi (auto atau off)"}),
        );
    };
    let mode = normalize(raw);
    let by = body
        .get("by")
        .and_then(Value::as_str)
        .unwrap_or("operator")
        .trim()
        .chars()
        .take(64)
        .collect::<String>();
    let value = AutoPilotMode {
        mode,
        changed_by: if by.is_empty() { "operator".to_string() } else { by },
        changed_epoch: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0),
    };
    match save(&value) {
        Ok(()) => {
            crate::api::events::publish(serde_json::json!({
                "type": "auto_pilot_mode",
                "mode": value.mode,
                "on": value.mode == "AUTO",
            }));
            Json(payload(&value)).into_response()
        }
        Err(error) => envelope(
            StatusCode::SERVICE_UNAVAILABLE,
            json!({"error": format!("tidak bisa menulis config/auto_pilot.json: {error}")}),
        ),
    }
}
