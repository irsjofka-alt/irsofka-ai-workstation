//! HTTP untuk automation: daftar, buat, ubah, hapus, jalankan sekarang.
//!
//! Semua validasi jadwal dilakukan di sini memakai `cron::next_after`, sehingga
//! ekspresi yang tidak masuk akal ditolak saat disimpan — bukan berubah menjadi
//! automation yang diam selamanya.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use chrono::Utc;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::automations::{payload, Automation};
use crate::cron;
use crate::runs::StartParams;

const VALID_OVERLAP: [&str; 3] = ["skip", "queue", "replace"];
const VALID_KIND: [&str; 4] = ["prompt", "command", "autopilot", "autopilot-done"];

#[derive(Deserialize)]
pub(crate) struct CreateAutomation {
    pub(crate) waker_id: String,
    pub(crate) name: String,
    pub(crate) cron: String,
    #[serde(default)]
    pub(crate) timezone: Option<String>,
    #[serde(default)]
    pub(crate) overlap_policy: Option<String>,
    #[serde(default)]
    pub(crate) prompt: Option<String>,
    #[serde(default)]
    pub(crate) kind: Option<String>,
    #[serde(default)]
    pub(crate) command: Option<String>,
    #[serde(default)]
    pub(crate) enabled: Option<bool>,
}

#[derive(Deserialize)]
pub(crate) struct PatchAutomation {
    #[serde(default)]
    pub(crate) waker_id: Option<String>,
    #[serde(default)]
    pub(crate) name: Option<String>,
    #[serde(default)]
    pub(crate) cron: Option<String>,
    #[serde(default)]
    pub(crate) timezone: Option<String>,
    #[serde(default)]
    pub(crate) overlap_policy: Option<String>,
    #[serde(default)]
    pub(crate) prompt: Option<String>,
    #[serde(default)]
    pub(crate) kind: Option<String>,
    #[serde(default)]
    pub(crate) command: Option<String>,
    #[serde(default)]
    pub(crate) enabled: Option<bool>,
}

fn bad(message: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": message.into() }))).into_response()
}

fn not_found(message: impl Into<String>) -> Response {
    (StatusCode::NOT_FOUND, Json(json!({ "error": message.into() }))).into_response()
}

fn unavailable(message: impl Into<String>) -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(json!({ "error": message.into() })),
    )
        .into_response()
}

pub(crate) async fn list(State(state): State<AppState>) -> Json<Value> {
    let items = state.store.list_automations().await.unwrap_or_default();
    let mut out = Vec::with_capacity(items.len());
    for automation in &items {
        out.push(payload(&state, automation).await);
    }
    Json(json!({
        "automations": out,
        "count": out.len(),
        "backend": state.store.backend,
    }))
}

pub(crate) async fn create(
    State(state): State<AppState>,
    Json(body): Json<CreateAutomation>,
) -> Response {
    let name = body.name.trim().to_string();
    if name.is_empty() {
        return bad("nama automation tidak boleh kosong");
    }
    let waker_exists = {
        let items = state.wakers.read().await;
        items.iter().any(|waker| waker.id == body.waker_id)
    };
    if !waker_exists {
        return bad(format!("waker \"{}\" tidak ada", body.waker_id));
    }
    let kind = body.kind.unwrap_or_else(|| "prompt".to_string());
    if !VALID_KIND.contains(&kind.as_str()) {
        return bad(format!(
            "kind \"{kind}\" tidak dikenal (pilih {})",
            VALID_KIND.join(", ")
        ));
    }
    let command = body.command.filter(|value| !value.trim().is_empty());
    if kind == "command" && command.is_none() {
        return bad("command wajib diisi untuk kind=command");
    }
    let overlap = body
        .overlap_policy
        .unwrap_or_else(|| "skip".to_string());
    if !VALID_OVERLAP.contains(&overlap.as_str()) {
        return bad(format!(
            "overlap_policy \"{overlap}\" tidak dikenal (pilih {})",
            VALID_OVERLAP.join(", ")
        ));
    }
    let timezone = body.timezone.unwrap_or_else(|| "local".to_string());
    let next = match cron::next_after(&body.cron, Utc::now(), &timezone) {
        Ok(next) => next,
        Err(error) => return bad(format!("cron tidak sah: {error}")),
    };
    let enabled = body.enabled.unwrap_or(true);

    let automation = Automation {
        id: Automation::new_id(),
        waker_id: body.waker_id,
        name,
        cron: body.cron,
        timezone,
        enabled,
        overlap_policy: overlap,
        prompt: body.prompt.filter(|value| !value.trim().is_empty()),
        kind,
        command,
        next_run_at: if enabled {
            Some(next.to_rfc3339())
        } else {
            None
        },
        last_run_at: None,
    };

    if let Err(error) = state.store.upsert_automation(&automation).await {
        return unavailable(error);
    }
    crate::api::events::publish(json!({
        "type": "automation", "action": "created", "id": automation.id,
    }));
    (
        StatusCode::CREATED,
        Json(payload(&state, &automation).await),
    )
        .into_response()
}

pub(crate) async fn patch(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<PatchAutomation>,
) -> Response {
    let Some(mut automation) = state.store.get_automation(&id).await else {
        return not_found(format!("automation \"{id}\" tidak ada"));
    };

    if let Some(waker_id) = body.waker_id {
        let exists = {
            let items = state.wakers.read().await;
            items.iter().any(|waker| waker.id == waker_id)
        };
        if !exists {
            return bad(format!("waker \"{waker_id}\" tidak ada"));
        }
        automation.waker_id = waker_id;
    }
    if let Some(name) = body.name {
        let trimmed = name.trim().to_string();
        if trimmed.is_empty() {
            return bad("nama automation tidak boleh kosong");
        }
        automation.name = trimmed;
    }
    if let Some(cron_expr) = body.cron {
        automation.cron = cron_expr;
    }
    if let Some(timezone) = body.timezone {
        automation.timezone = timezone;
    }
    if let Some(overlap) = body.overlap_policy {
        if !VALID_OVERLAP.contains(&overlap.as_str()) {
            return bad(format!("overlap_policy \"{overlap}\" tidak dikenal"));
        }
        automation.overlap_policy = overlap;
    }
    if let Some(prompt) = body.prompt {
        automation.prompt = if prompt.trim().is_empty() {
            None
        } else {
            Some(prompt)
        };
    }
    if let Some(kind) = body.kind {
        if !VALID_KIND.contains(&kind.as_str()) {
            return bad(format!("kind \"{kind}\" tidak dikenal"));
        }
        automation.kind = kind;
    }
    if let Some(command) = body.command {
        automation.command = if command.trim().is_empty() {
            None
        } else {
            Some(command)
        };
    }
    if automation.kind == "command" && automation.command.is_none() {
        return bad("command wajib diisi untuk kind=command");
    }
    if let Some(enabled) = body.enabled {
        automation.enabled = enabled;
    }

    if automation.enabled {
        match cron::next_after(&automation.cron, Utc::now(), &automation.timezone) {
            Ok(next) => automation.next_run_at = Some(next.to_rfc3339()),
            Err(error) => return bad(format!("cron tidak sah: {error}")),
        }
    } else {
        automation.next_run_at = None;
    }

    if let Err(error) = state.store.upsert_automation(&automation).await {
        return unavailable(error);
    }
    crate::api::events::publish(json!({
        "type": "automation", "action": "updated", "id": automation.id,
    }));
    Json(payload(&state, &automation).await).into_response()
}

pub(crate) async fn remove(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.delete_automation(&id).await {
        Ok(()) => {
            crate::api::events::publish(json!({
                "type": "automation", "action": "deleted", "id": id,
            }));
            Json(json!({ "deleted": id })).into_response()
        }
        Err(error) => unavailable(error),
    }
}

pub(crate) async fn run_now(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    let Some(automation) = state.store.get_automation(&id).await else {
        return not_found(format!("automation \"{id}\" tidak ada"));
    };
    let waker = {
        let items = state.wakers.read().await;
        items
            .iter()
            .find(|waker| waker.id == automation.waker_id)
            .cloned()
    };
    let Some(waker) = waker else {
        return bad("waker pemilik automation ini sudah tidak ada");
    };
    if automation.kind != "command" && waker.status != "active" {
        return bad(format!("waker \"{}\" sedang dijeda", waker.name));
    }

    crate::api::events::publish(json!({
        "type": "automation", "action": "run_requested", "id": automation.id,
    }));
    if automation.kind == "command" {
        let Some(command) = automation
            .command
            .clone()
            .filter(|value| !value.trim().is_empty())
        else {
            return bad("command kosong");
        };
        crate::automations::run_command(
            &state,
            &automation,
            command,
            waker.workspace.clone(),
            Utc::now(),
        )
        .await;
        return (
            StatusCode::CREATED,
            Json(json!({ "kind": "command", "status": "started" })),
        )
            .into_response();
    }

    let prompt = automation
        .prompt
        .clone()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "Jalankan tugas terjadwal ini.".to_string());

    let params = StartParams {
        waker_id: waker.id.clone(),
        engine_key: waker.engine_key.clone(),
        model: waker.model.clone(),
        reasoning_effort: waker.reasoning_effort.clone(),
        context_window: waker.context_window,
        prompt,
        system_prompt: waker.system_prompt.clone(),
        workspace: waker.workspace.clone(),
        answer_style: Some(waker.answer_style.clone()),
        max_output_tokens: waker.max_output_tokens,
        include_history: false,
        allow_cache: true,
        fallback_engines: Vec::new(),
        save_message: true,
    };

    if crate::approvals::needs_approval(&waker) {
        let payload = crate::approvals::params_payload(&params, None);
        return match state.store.create_approval("manual", &payload).await {
            Ok(approval_id) => (
                StatusCode::ACCEPTED,
                Json(json!({ "approval_id": approval_id, "status": "pending_approval" })),
            )
                .into_response(),
            Err(message) => unavailable(message),
        };
    }

    match state.runs.start(params).await {
        Ok(run_id) => {
            crate::automations::record_started_run(
                &state,
                &automation.id,
                &run_id,
                Utc::now(),
            )
            .await;
            (StatusCode::CREATED, Json(json!({ "run_id": run_id }))).into_response()
        }
        Err(error) => bad(error),
    }
}

pub(crate) async fn runs(State(state): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    Json(json!({ "runs": state.store.automation_runs(&id, 20).await }))
}
