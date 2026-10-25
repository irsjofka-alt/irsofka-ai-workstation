//! HTTP untuk basis pengetahuan lokal (kata kunci).

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;

#[derive(Deserialize)]
pub(crate) struct CreateKnowledge {
    pub(crate) title: String,
    pub(crate) content: String,
    #[serde(default)]
    pub(crate) tags: Option<String>,
    #[serde(default)]
    pub(crate) waker_id: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct SearchKnowledge {
    pub(crate) query: String,
    #[serde(default)]
    pub(crate) waker_id: Option<String>,
    #[serde(default)]
    pub(crate) limit: Option<usize>,
}

fn bad(message: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": message.into() }))).into_response()
}

pub(crate) async fn list(State(state): State<AppState>) -> Json<Value> {
    let items = state.store.list_knowledge().await;
    let count = items.as_array().map(Vec::len).unwrap_or(0);
    Json(json!({ "knowledge": items, "count": count }))
}

pub(crate) async fn create(
    State(state): State<AppState>,
    Json(body): Json<CreateKnowledge>,
) -> Response {
    let title = body.title.trim().to_string();
    let content = body.content.trim().to_string();
    if title.is_empty() {
        return bad("judul tidak boleh kosong");
    }
    if content.is_empty() {
        return bad("isi tidak boleh kosong");
    }
    if let Some(waker_id) = body.waker_id.as_deref() {
        let exists = {
            let items = state.wakers.read().await;
            items.iter().any(|waker| waker.id == waker_id)
        };
        if !exists {
            return bad(format!("worker \"{waker_id}\" tidak ada"));
        }
    }

    let id = format!("kn_{:x}", crate::approvals::nanos());
    let embedding = crate::embeddings::embed(&content).await;
    match state
        .store
        .add_knowledge(
            &id,
            body.waker_id.as_deref(),
            &title,
            &content,
            body.tags.unwrap_or_default().trim(),
            embedding.as_deref(),
        )
        .await
    {
        Ok(()) => (
            StatusCode::CREATED,
            Json(json!({ "id": id, "title": title })),
        )
            .into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn remove(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.delete_knowledge(&id).await {
        Ok(()) => Json(json!({ "deleted": id })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn search(
    State(state): State<AppState>,
    Json(body): Json<SearchKnowledge>,
) -> Json<Value> {
    let query = body.query.trim();
    if query.is_empty() {
        return Json(json!({ "results": [] }));
    }
    let scope = body.waker_id.unwrap_or_default();
    let limit = body.limit.unwrap_or(5).clamp(1, 20);
    let (hits, mode) = crate::knowledge::search(&state.store, &scope, query, limit).await;
    Json(json!({
        "mode": mode,
        "results": hits
            .into_iter()
            .map(|(title, snippet)| json!({ "title": title, "snippet": snippet }))
            .collect::<Vec<_>>(),
    }))
}

/// Membuat embedding untuk entri lama yang belum punya vektor.
pub(crate) async fn reindex(State(state): State<AppState>) -> Json<Value> {
    let missing = state.store.knowledge_missing_embeddings().await;
    let mut embedded = 0;
    for (id, content) in &missing {
        if let Some(vector) = crate::embeddings::embed(content).await {
            state.store.set_knowledge_embedding(id, &vector).await;
            embedded += 1;
        }
    }
    Json(json!({
        "requested": missing.len(),
        "embedded": embedded,
        "mode": if embedded > 0 { "vector" } else { "keyword" },
    }))
}
