//! HTTP untuk WakerFlow: definisi alur, eksekusi, riwayat, dan penghentian.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::workflows::{nanos, Workflow, WorkflowStep};

#[derive(Deserialize)]
pub(crate) struct StepInput {
    pub(crate) name: String,
    #[serde(default)]
    pub(crate) kind: Option<String>,
    #[serde(default)]
    pub(crate) prompt: Option<String>,
    #[serde(default)]
    pub(crate) command: Option<String>,
    #[serde(default)]
    pub(crate) branch_contains: Option<String>,
    #[serde(default)]
    pub(crate) branch_goto: Option<i32>,
    #[serde(default)]
    pub(crate) max_retries: Option<i32>,
}

#[derive(Deserialize)]
pub(crate) struct CreateWorkflow {
    pub(crate) waker_id: String,
    pub(crate) name: String,
    pub(crate) steps: Vec<StepInput>,
}

#[derive(Deserialize)]
pub(crate) struct PatchWorkflow {
    #[serde(default)]
    pub(crate) name: Option<String>,
    #[serde(default)]
    pub(crate) steps: Option<Vec<StepInput>>,
}

#[derive(Deserialize)]
pub(crate) struct Rollback {
    pub(crate) version: i32,
}

fn bad(message: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": message.into() }))).into_response()
}

fn not_found(message: impl Into<String>) -> Response {
    (StatusCode::NOT_FOUND, Json(json!({ "error": message.into() }))).into_response()
}

pub(crate) async fn list(State(state): State<AppState>) -> Json<Value> {
    let items = state.store.list_workflows().await.unwrap_or_default();
    let mut out = Vec::with_capacity(items.len());
    for workflow in &items {
        let mut value = serde_json::to_value(workflow).unwrap_or_default();
        if let Some(object) = value.as_object_mut() {
            object.insert(
                "step_count".to_string(),
                json!(workflow.steps.len()),
            );
        }
        out.push(value);
    }
    Json(json!({ "workflows": out, "count": out.len(), "backend": state.store.backend }))
}

pub(crate) async fn create(
    State(state): State<AppState>,
    Json(body): Json<CreateWorkflow>,
) -> Response {
    let name = body.name.trim().to_string();
    if name.is_empty() {
        return bad("nama workflow tidak boleh kosong");
    }
    if body.steps.is_empty() {
        return bad("workflow harus punya minimal satu langkah");
    }
    let waker_exists = {
        let items = state.wakers.read().await;
        items.iter().any(|waker| waker.id == body.waker_id)
    };
    if !waker_exists {
        return bad(format!("worker \"{}\" tidak ada", body.waker_id));
    }

    let steps = match build_steps(&body.steps) {
        Ok(steps) => steps,
        Err(error) => return bad(error),
    };

    let workflow = Workflow {
        id: format!("wf_{:x}", nanos()),
        waker_id: body.waker_id,
        name,
        version: 1,
        steps,
    };

    match state.store.create_workflow(&workflow).await {
        Ok(()) => (StatusCode::CREATED, Json(json!(workflow))).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

fn build_steps(input: &[StepInput]) -> Result<Vec<WorkflowStep>, String> {
    let total = input.len() as i32;
    let mut steps = Vec::with_capacity(input.len());
    for (index, step) in input.iter().enumerate() {
        let kind = step
            .kind
            .clone()
            .unwrap_or_else(|| "prompt".to_string());
        if !["prompt", "tool", "branch", "approval"].contains(&kind.as_str()) {
            return Err(format!("jenis langkah \"{kind}\" tidak dikenal"));
        }
        let name = step.name.trim();
        if name.is_empty() {
            return Err(format!("nama langkah #{} kosong", index + 1));
        }
        let command = step
            .command
            .clone()
            .filter(|value| !value.trim().is_empty());
        if kind == "tool" && command.is_none() {
            return Err(format!("langkah tool #{} butuh perintah", index + 1));
        }
        let branch_goto = step.branch_goto.filter(|value| *value >= 1 && *value <= total);
        if kind == "branch" && (step.branch_contains.is_none() || branch_goto.is_none()) {
            return Err(format!(
                "langkah branch #{} butuh contains dan goto (1..{total})",
                index + 1
            ));
        }
        steps.push(WorkflowStep {
            ord: (index + 1) as i32,
            name: name.to_string(),
            kind,
            prompt: step.prompt.clone().filter(|value| !value.trim().is_empty()),
            command,
            branch_contains: step
                .branch_contains
                .clone()
                .filter(|value| !value.trim().is_empty()),
            branch_goto,
            max_retries: step.max_retries.unwrap_or(0).clamp(0, 5),
            on_failure: "stop".to_string(),
        });
    }
    Ok(steps)
}

pub(crate) async fn patch(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<PatchWorkflow>,
) -> Response {
    let Some(mut workflow) = state.store.get_workflow(&id).await else {
        return not_found(format!("workflow \"{id}\" tidak ada"));
    };
    if let Some(name) = body.name {
        let trimmed = name.trim().to_string();
        if trimmed.is_empty() {
            return bad("nama workflow tidak boleh kosong");
        }
        workflow.name = trimmed;
    }
    if let Some(steps) = body.steps {
        if steps.is_empty() {
            return bad("workflow harus punya minimal satu langkah");
        }
        workflow.steps = match build_steps(&steps) {
            Ok(steps) => steps,
            Err(error) => return bad(error),
        };
    }
    workflow.version += 1;
    match state.store.update_workflow(&workflow).await {
        Ok(()) => Json(json!(workflow)).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn versions(State(state): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    Json(json!({ "versions": state.store.list_versions(&id).await }))
}

pub(crate) async fn rollback(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<Rollback>,
) -> Response {
    let Some(current) = state.store.get_workflow(&id).await else {
        return not_found(format!("workflow \"{id}\" tidak ada"));
    };
    let Some((name, steps)) = state.store.version_snapshot(&id, body.version).await else {
        return not_found(format!("versi {} tidak ada", body.version));
    };
    if steps.is_empty() {
        return bad("versi itu tidak punya langkah");
    }
    let restored = Workflow {
        id: current.id,
        waker_id: current.waker_id,
        name,
        version: current.version + 1,
        steps,
    };
    match state.store.update_workflow(&restored).await {
        Ok(()) => Json(json!(restored)).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn retry_run(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    let Some(run) = state.store.get_workflow_run(&id).await else {
        return not_found(format!("workflow run \"{id}\" tidak ada"));
    };
    let Some(workflow) = state.store.get_workflow(&run.workflow_id).await else {
        return not_found("definisi workflow hilang");
    };
    match crate::workflows::start(&state, workflow).await {
        Ok(run_id) => (
            StatusCode::CREATED,
            Json(json!({ "workflow_run_id": run_id })),
        )
            .into_response(),
        Err(error) => bad(error),
    }
}

pub(crate) async fn get(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.get_workflow(&id).await {
        Some(workflow) => Json(json!(workflow)).into_response(),
        None => not_found(format!("workflow \"{id}\" tidak ada")),
    }
}

pub(crate) async fn remove(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.delete_workflow(&id).await {
        Ok(()) => Json(json!({ "deleted": id })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn run(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    let Some(workflow) = state.store.get_workflow(&id).await else {
        return not_found(format!("workflow \"{id}\" tidak ada"));
    };
    let waker_status = {
        let items = state.wakers.read().await;
        items
            .iter()
            .find(|waker| waker.id == workflow.waker_id)
            .map(|waker| waker.status.clone())
    };
    match waker_status {
        None => return bad("worker pemilik workflow tidak ada"),
        Some(status) if status != "active" => {
            return bad("worker sedang dijeda; aktifkan dulu")
        }
        Some(_) => {}
    }

    match crate::workflows::start(&state, workflow).await {
        Ok(run_id) => (
            StatusCode::CREATED,
            Json(json!({ "workflow_run_id": run_id })),
        )
            .into_response(),
        Err(error) => bad(error),
    }
}

pub(crate) async fn runs(State(state): State<AppState>, Path(id): Path<String>) -> Json<Value> {
    Json(json!({ "runs": state.store.list_workflow_runs(&id).await }))
}

pub(crate) async fn get_run(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.store.get_workflow_run(&id).await {
        Some(run) => Json(json!(run)).into_response(),
        None => not_found(format!("workflow run \"{id}\" tidak ada")),
    }
}

pub(crate) async fn terminate(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    let Some(mut run) = state.store.get_workflow_run(&id).await else {
        return not_found(format!("workflow run \"{id}\" tidak ada"));
    };
    if run.status == "succeeded" || run.status == "failed" || run.status == "cancelled" {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("run sudah selesai: {}", run.status) })),
        )
            .into_response();
    }
    run.status = "cancelled".to_string();
    run.error = Some("dihentikan oleh operator".to_string());
    run.finished_at = Some(chrono::Utc::now().to_rfc3339());
    state.store.update_workflow_run(&run).await;
    Json(json!({ "status": "cancelled" })).into_response()
}
