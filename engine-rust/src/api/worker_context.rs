//! `worker load` / `worker recovery` context pack.
//!
//! Python owns the facts (config, models, ledger, SQL, handoff). Rust owns the
//! HTTP boundary and the process timeout, the same split used by the
//! autopilot endpoints. The response is the JSON produced by
//! `tools/worker_context.py`, forwarded without re-serialising so the console
//! cannot drift from what the CLI prints.
use axum::body::Body;
use axum::extract::Json;
use axum::http::header::CONTENT_TYPE;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::{json, Value};

use crate::paths::worker_context_path;

pub(crate) async fn post_context(Json(body): Json<Value>) -> Response {
    let mode = body
        .get("mode")
        .and_then(Value::as_str)
        .unwrap_or("load")
        .trim()
        .to_lowercase();
    if mode != "load" && mode != "recovery" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "mode harus load atau recovery"})),
        )
            .into_response();
    }
    let limit = body
        .get("limit")
        .and_then(Value::as_u64)
        .unwrap_or(40)
        .clamp(5, 200);

    let script = worker_context_path();
    let args = vec![
        mode.clone(),
        "--limit".to_string(),
        limit.to_string(),
    ];
    let result = tokio::task::spawn_blocking(move || {
        if mode == "load" {
            crate::api::autopilot::run_script(&script, &["load".to_string()])
        } else {
            crate::api::autopilot::run_script(&script, &args)
        }
    })
    .await;

    match result {
        Ok(Ok(text)) => (
            StatusCode::OK,
            [(CONTENT_TYPE, "application/json")],
            Body::from(text),
        )
            .into_response(),
        Ok(Err(error)) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"ok": false, "error": error})),
        )
            .into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"ok": false, "error": format!("task context berhenti: {error}")})),
        )
            .into_response(),
    }
}
