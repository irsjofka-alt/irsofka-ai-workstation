//! HTTP P2.3: jalankan eval dan baca hasilnya.
//!
//! Satu eval = satu putaran dataset pada satu engine/model. Skor tidak dikarang:
//! hanya pemeriksaan deterministik di `config/eval_tasks.json` yang menentukan lulus.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::eval::StartRequest;

pub(crate) async fn tasks() -> Response {
    match crate::eval::load_tasks() {
        Ok(tasks) => {
            let items: Vec<Value> = tasks
                .iter()
                .map(|task| {
                    json!({
                        "id": task.id,
                        "prompt": task.prompt,
                        "checks": task.checks,
                        "requires_permission": task.requires_permission,
                    })
                })
                .collect();
            Json(json!({ "tasks": items, "count": items.len() })).into_response()
        }
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn start(
    State(state): State<AppState>,
    Json(body): Json<StartRequest>,
) -> Response {
    match crate::eval::start(&state, body).await {
        Ok(id) => (
            StatusCode::CREATED,
            Json(json!({ "eval_run_id": id })),
        )
            .into_response(),
        Err(error) => (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn list(State(state): State<AppState>) -> Json<Value> {
    let runs = state.store.list_eval_runs(20).await;
    let count = runs.as_array().map(Vec::len).unwrap_or(0);
    Json(json!({ "runs": runs, "count": count }))
}

pub(crate) async fn get(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.get_eval_run(&id).await {
        Some(run) => Json(run).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("eval \"{id}\" tidak ada") })),
        )
            .into_response(),
    }
}
