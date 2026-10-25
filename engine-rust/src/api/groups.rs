//! HTTP untuk grup multi-worker.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;

#[derive(Deserialize)]
pub(crate) struct CreateGroup {
    pub(crate) name: String,
    #[serde(default)]
    pub(crate) sop: Option<String>,
    pub(crate) members: Vec<String>,
}

#[derive(Deserialize)]
pub(crate) struct GroupMessage {
    pub(crate) content: String,
}

#[derive(Deserialize)]
pub(crate) struct AddTodo {
    pub(crate) title: String,
}

#[derive(Deserialize)]
pub(crate) struct TodoStatus {
    pub(crate) status: String,
}

#[derive(Deserialize)]
pub(crate) struct SetGoal {
    pub(crate) objective: String,
    #[serde(default)]
    pub(crate) status: Option<String>,
}

fn bad(message: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": message.into() }))).into_response()
}

fn not_found(message: impl Into<String>) -> Response {
    (StatusCode::NOT_FOUND, Json(json!({ "error": message.into() }))).into_response()
}

pub(crate) async fn list(State(state): State<AppState>) -> Json<Value> {
    let groups = state.store.list_groups().await;
    let count = groups.as_array().map(Vec::len).unwrap_or(0);
    Json(json!({ "groups": groups, "count": count }))
}

pub(crate) async fn create(
    State(state): State<AppState>,
    Json(body): Json<CreateGroup>,
) -> Response {
    let name = body.name.trim().to_string();
    if name.is_empty() {
        return bad("nama grup tidak boleh kosong");
    }
    if body.members.is_empty() {
        return bad("grup harus punya minimal satu worker");
    }
    let known: Vec<String> = {
        let items = state.wakers.read().await;
        items.iter().map(|waker| waker.id.clone()).collect()
    };
    for member in &body.members {
        if !known.contains(member) {
            return bad(format!("worker \"{member}\" tidak ada"));
        }
    }

    let id = format!("grp_{:x}", crate::approvals::nanos());
    match state
        .store
        .create_group(
            &id,
            &name,
            body.sop.unwrap_or_default().trim(),
            &body.members,
        )
        .await
    {
        Ok(()) => (
            StatusCode::CREATED,
            Json(json!({ "id": id, "name": name, "members": body.members.len() })),
        )
            .into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn get(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    let Some((name, sop, members)) = state.store.get_group(&id).await else {
        return not_found(format!("grup \"{id}\" tidak ada"));
    };
    let history = state.store.group_messages(&id).await;
    let goal = state.store.get_goal(&id).await.map(|(objective, status)| {
        json!({ "objective": objective, "status": status })
    });
    Json(json!({
        "id": id,
        "name": name,
        "sop": sop,
        "goal": goal,
        "todos": state.store.list_todos(&id).await,
        "members": members
            .iter()
            .map(|(waker_id, waker_name, role)| json!({
                "waker_id": waker_id,
                "waker_name": waker_name,
                "role": role,
            }))
            .collect::<Vec<_>>(),
        "messages": history
            .iter()
            .map(|(who, role, content)| json!({
                "who": who,
                "role": role,
                "content": content,
            }))
            .collect::<Vec<_>>(),
    }))
    .into_response()
}

pub(crate) async fn remove(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.delete_group(&id).await {
        Ok(()) => Json(json!({ "deleted": id })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn send(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<GroupMessage>,
) -> Response {
    if state.store.get_group(&id).await.is_none() {
        return not_found(format!("grup \"{id}\" tidak ada"));
    }
    let content = body.content.trim().to_string();
    if content.is_empty() {
        return bad("pesan kosong");
    }

    state
        .store
        .save_group_message(&id, None, "user", &content, None)
        .await;

    let state = state.clone();
    let group_id = id.clone();
    tokio::spawn(async move {
        crate::groups::dispatch(state, group_id, content).await;
    });

    (StatusCode::ACCEPTED, Json(json!({ "status": "started" }))).into_response()
}

pub(crate) async fn todos(State(state): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    Json(json!({ "todos": state.store.list_todos(&id).await }))
}

pub(crate) async fn add_todo(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<AddTodo>,
) -> Response {
    if state.store.get_group(&id).await.is_none() {
        return not_found(format!("grup \"{id}\" tidak ada"));
    }
    let title = body.title.trim().to_string();
    if title.is_empty() {
        return bad("judul todo tidak boleh kosong");
    }
    let todo_id = format!("todo_{:x}", crate::approvals::nanos());
    match state.store.add_todo(&todo_id, &id, &title).await {
        Ok(()) => (
            StatusCode::CREATED,
            Json(json!({ "id": todo_id, "title": title })),
        )
            .into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn patch_todo(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<TodoStatus>,
) -> Response {
    if !["open", "done", "dropped"].contains(&body.status.as_str()) {
        return bad("status todo harus open, done, atau dropped");
    }
    match state.store.set_todo_status(&id, &body.status).await {
        Ok(()) => Json(json!({ "status": body.status })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn remove_todo(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Response {
    match state.store.delete_todo(&id).await {
        Ok(()) => Json(json!({ "deleted": id })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn goal(State(state): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    let goal = state.store.get_goal(&id).await.map(|(objective, status)| {
        json!({ "objective": objective, "status": status })
    });
    Json(json!({ "goal": goal }))
}

pub(crate) async fn put_goal(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<SetGoal>,
) -> Response {
    if state.store.get_group(&id).await.is_none() {
        return not_found(format!("grup \"{id}\" tidak ada"));
    }
    let objective = body.objective.trim().to_string();
    if objective.is_empty() {
        return bad("objective tidak boleh kosong");
    }
    let status = body.status.unwrap_or_else(|| "active".to_string());
    if !["active", "achieved", "abandoned"].contains(&status.as_str()) {
        return bad("status goal harus active, achieved, atau abandoned");
    }
    match state.store.set_goal(&id, &objective, &status).await {
        Ok(()) => Json(json!({ "objective": objective, "status": status })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn messages(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Json<Value> {
    let history = state.store.group_messages(&id).await;
    Json(json!({
        "messages": history
            .iter()
            .map(|(who, role, content)| json!({
                "who": who,
                "role": role,
                "content": content,
            }))
            .collect::<Vec<_>>(),
    }))
}
