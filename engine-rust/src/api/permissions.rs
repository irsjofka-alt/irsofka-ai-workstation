//! HTTP untuk izin per-tool per worker.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;

const VALID: [&str; 3] = ["allow", "ask", "deny"];

#[derive(Deserialize)]
pub(crate) struct SetPermission {
    pub(crate) tool: String,
    pub(crate) decision: String,
}

fn bad(message: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": message.into() }))).into_response()
}

pub(crate) async fn list(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Json<Value> {
    Json(json!({ "permissions": state.store.list_permissions(&id).await }))
}

pub(crate) async fn set(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<SetPermission>,
) -> Response {
    let tool = body.tool.trim().to_string();
    if tool.is_empty() {
        return bad("nama tool tidak boleh kosong");
    }
    if !VALID.contains(&body.decision.as_str()) {
        return bad(format!(
            "keputusan \"{}\" tidak dikenal (pilih allow, ask, deny)",
            body.decision
        ));
    }
    let exists = {
        let items = state.wakers.read().await;
        items.iter().any(|waker| waker.id == id)
    };
    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("worker \"{id}\" tidak ada") })),
        )
            .into_response();
    }
    let permission_id = format!("perm_{:x}", crate::approvals::nanos());
    match state
        .store
        .set_permission(&permission_id, &id, &tool, &body.decision)
        .await
    {
        Ok(()) => Json(json!({ "tool": tool, "decision": body.decision })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn remove(
    State(state): State<AppState>,
    Path((id, tool)): Path<(String, String)>,
) -> Response {
    match state.store.delete_permission(&id, &tool).await {
        Ok(()) => Json(json!({ "deleted": tool })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}
