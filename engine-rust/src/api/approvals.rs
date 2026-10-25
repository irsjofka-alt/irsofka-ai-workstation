//! HTTP untuk inbox persetujuan.
//!
//! Keputusan hanya bisa diambil sekali: permintaan yang sudah `approved`/`denied`
//! tidak bisa diubah lagi, sehingga jejaknya tetap bisa dipercaya.

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::approvals::payload_to_params;

#[derive(Deserialize)]
pub(crate) struct ApprovalQuery {
    #[serde(default)]
    pub(crate) status: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct Decision {
    pub(crate) decision: String,
    #[serde(default)]
    pub(crate) note: Option<String>,
}

pub(crate) async fn list(
    State(state): State<AppState>,
    Query(query): Query<ApprovalQuery>,
) -> Json<Value> {
    let approvals = state.store.list_approvals(query.status.as_deref()).await;
    let count = approvals.as_array().map(Vec::len).unwrap_or(0);
    Json(json!({ "approvals": approvals, "count": count }))
}

pub(crate) async fn decide(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<Decision>,
) -> Response {
    let Some((status, payload)) = state.store.get_approval(&id).await else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("permintaan \"{id}\" tidak ada") })),
        )
            .into_response();
    };
    if status != "pending" {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("permintaan ini sudah diputuskan: {status}") })),
        )
            .into_response();
    }

    let approve = match body.decision.as_str() {
        "approved" | "approve" | "allow" => true,
        "denied" | "deny" | "reject" => false,
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": format!("keputusan \"{other}\" tidak dikenal") })),
            )
                .into_response()
        }
    };

    let automation_run_id = payload
        .get("automation_run_id")
        .and_then(Value::as_str)
        .map(str::to_string);

    // Persetujuan milik langkah WakerFlow: jangan menjalankan satu run lepas,
    // lanjutkan alurnya dari langkah berikutnya.
    if let Some(workflow_run_id) = payload.get("workflow_run_id").and_then(Value::as_str) {
        let status = if approve { "approved" } else { "denied" };
        state
            .store
            .decide_approval(&id, status, body.note.as_deref(), None)
            .await;

        // Langkah tool yang menunggu izin: jalankan perintahnya lebih dulu,
        // lalu lanjutkan alur. Menolak berarti alur dibatalkan.
        let kind = payload
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("approval");
        if approve && kind == "tool" {
            let command = payload
                .get("command")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let workspace = payload
                .get("workspace")
                .and_then(Value::as_str)
                .unwrap_or_default();
            match crate::workflows::run_tool(command, workspace).await {
                Ok(output) => {
                    crate::workflows::append_log(
                        &state,
                        workflow_run_id,
                        "tool",
                        "succeeded",
                        &output,
                    )
                    .await;
                }
                Err(error) => {
                    crate::workflows::fail_external(&state, workflow_run_id, &error).await;
                    return (
                        StatusCode::BAD_GATEWAY,
                        Json(json!({ "error": error })),
                    )
                        .into_response();
                }
            }
        }

        crate::workflows::on_decision(&state, workflow_run_id, approve).await;
        return Json(json!({
            "status": status,
            "workflow_run_id": workflow_run_id,
        }))
        .into_response();
    }

    // Permintaan izin tool MCP dari dalam run: putusannya menjadi izin worker
    // untuk tool itu (`mcp:<server>:<tool>`), supaya run berikutnya tidak
    // berhenti di gerbang yang sama.
    if payload.get("kind").and_then(Value::as_str) == Some("mcp-tool") {
        let status = if approve { "approved" } else { "denied" };
        let waker_id = payload
            .get("waker_id")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let server = payload
            .get("server")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let tool = payload
            .get("tool")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if waker_id.is_empty() || server.is_empty() || tool.is_empty() {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": "payload izin tool tidak lengkap" })),
            )
                .into_response();
        }
        let permission = format!("mcp:{server}:{tool}");
        let decision = if approve { "allow" } else { "deny" };
        let permission_id = format!("perm_{:x}", crate::approvals::nanos());
        if let Err(error) = state
            .store
            .set_permission(&permission_id, waker_id, &permission, decision)
            .await
        {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({ "error": error })),
            )
                .into_response();
        }
        state
            .store
            .decide_approval(&id, status, body.note.as_deref(), None)
            .await;
        return Json(json!({
            "status": status,
            "permission": permission,
            "decision": decision,
            "waker_id": waker_id,
        }))
        .into_response();
    }

    if !approve {
        state
            .store
            .decide_approval(&id, "denied", body.note.as_deref(), None)
            .await;
        if let Some(arun) = automation_run_id.as_deref() {
            state
                .store
                .update_automation_run(arun, None, "skipped")
                .await;
        }
        return Json(json!({ "status": "denied" })).into_response();
    }

    let params = payload_to_params(&payload);
    match state.runs.start(params).await {
        Ok(run_id) => {
            state
                .store
                .decide_approval(&id, "approved", body.note.as_deref(), Some(&run_id))
                .await;
            if let Some(arun) = automation_run_id.as_deref() {
                state
                    .store
                    .update_automation_run(arun, Some(&run_id), "running")
                    .await;
            }
            Json(json!({ "status": "approved", "run_id": run_id })).into_response()
        }
        Err(error) => (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}
