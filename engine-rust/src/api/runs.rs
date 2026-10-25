//! HTTP untuk run engine: kirim pesan ke waker, baca status run, daftar riwayat.
//!
//! Endpoint pesan sengaja mengembalikan `run_id` segera, bukan menunggu proses
//! selesai: console mempolling `/api/runs/:id` dan menampilkan keluaran bertahap.

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::json;

use crate::api::AppState;
use crate::runs::StartParams;

#[derive(Deserialize)]
pub(crate) struct MessagePayload {
    pub(crate) content: String,
}

#[derive(Deserialize)]
pub(crate) struct RunQuery {
    #[serde(default)]
    pub(crate) waker: Option<String>,
}

pub(crate) async fn post_message(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(payload): Json<MessagePayload>,
) -> Response {
    let waker = {
        let items = state.wakers.read().await;
        items.iter().find(|w| w.id == id || w.slug == id).cloned()
    };
    let Some(waker) = waker else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("waker \"{id}\" tidak ada") })),
        )
            .into_response();
    };
    if waker.status != "active" {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("waker \"{}\" sedang dijeda", waker.name) })),
        )
            .into_response();
    }
    let content = payload.content.trim();
    if content.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "pesan kosong" })),
        )
            .into_response();
    }

    let params = StartParams {
        waker_id: waker.id.clone(),
        engine_key: waker.engine_key.clone(),
        model: waker.model.clone(),
        reasoning_effort: waker.reasoning_effort.clone(),
        context_window: waker.context_window,
        prompt: content.to_string(),
        system_prompt: waker.system_prompt.clone(),
        workspace: waker.workspace.clone(),
        answer_style: Some(waker.answer_style.clone()),
        max_output_tokens: waker.max_output_tokens,
        include_history: true,
        allow_cache: false,
        fallback_engines: Vec::new(),
        save_message: true,
    };

    if crate::approvals::needs_approval(&waker) {
        let payload = crate::approvals::params_payload(&params, None);
        return match state.store.create_approval("chat", &payload).await {
            Ok(approval_id) => (
                StatusCode::ACCEPTED,
                Json(json!({
                    "approval_id": approval_id,
                    "status": "pending_approval",
                    "waker_id": waker.id,
                })),
            )
                .into_response(),
            Err(message) => (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({ "error": message })),
            )
                .into_response(),
        };
    }

    match state.runs.start(params).await {
        Ok(run_id) => (
            StatusCode::CREATED,
            Json(json!({ "run_id": run_id, "waker_id": waker.id })),
        )
            .into_response(),
        Err(message) => (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": message })),
        )
            .into_response(),
    }
}

pub(crate) async fn cancel(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    if state.runs.cancel(&id).await {
        Json(json!({ "cancelled": id })).into_response()
    } else {
        (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("run \"{id}\" tidak aktif") })),
        )
            .into_response()
    }
}

pub(crate) async fn get_run(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.runs.get(&id).await {
        Some(record) => Json(json!(record)).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("run \"{id}\" tidak ada") })),
        )
            .into_response(),
    }
}

pub(crate) async fn list_runs(
    State(state): State<AppState>,
    Query(query): Query<RunQuery>,
) -> Json<serde_json::Value> {
    let runs = state.runs.list(query.waker.as_deref()).await;
    Json(json!({ "runs": runs, "count": runs.len() }))
}
