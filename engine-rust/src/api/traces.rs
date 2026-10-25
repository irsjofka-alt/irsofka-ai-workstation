//! HTTP untuk trace dan ringkasan papan kerja.

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;

#[derive(Deserialize)]
pub(crate) struct TraceQuery {
    #[serde(default)]
    pub(crate) status: Option<String>,
    #[serde(default)]
    pub(crate) waker: Option<String>,
    #[serde(default)]
    pub(crate) limit: Option<i64>,
}

pub(crate) async fn list(
    State(state): State<AppState>,
    Query(query): Query<TraceQuery>,
) -> Json<Value> {
    let limit = query.limit.unwrap_or(100).clamp(1, 500);
    let status = query.status.as_deref().filter(|value| !value.is_empty());
    let waker = query.waker.as_deref().filter(|value| !value.is_empty());
    let runs = state.store.trace_runs(limit, status, waker).await;
    let count = runs.as_array().map(Vec::len).unwrap_or(0);
    Json(json!({ "traces": runs, "count": count }))
}

pub(crate) async fn summary(State(state): State<AppState>) -> Json<Value> {
    Json(state.store.trace_summary().await)
}

pub(crate) async fn get(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.get_run_trace(&id).await {
        Some(trace) => Json(trace).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("trace \"{id}\" tidak ada") })),
        )
            .into_response(),
    }
}
