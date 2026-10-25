//! Channel IM: pairing, pesan masuk, dan balasan keluar.
//!
//! Provider `local` menyimpan balasan di database sehingga alur bisa diuji tanpa
//! kredensial eksternal. Provider lain memakai webhook yang URL-nya dibaca dari
//! variabel lingkungan bernama `credentials_ref` — rahasianya tidak pernah masuk DB.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::runs::StartParams;

const VALID_PROVIDERS: [&str; 5] = ["local", "dingtalk", "feishu", "lark", "wecom"];

#[derive(Deserialize)]
pub(crate) struct CreateChannel {
    pub(crate) provider: String,
    pub(crate) name: String,
    #[serde(default)]
    pub(crate) credentials_ref: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct AddBinding {
    pub(crate) waker_id: String,
    pub(crate) external_chat_id: String,
}

#[derive(Deserialize)]
pub(crate) struct Inbound {
    pub(crate) external_chat_id: String,
    pub(crate) text: String,
}

fn bad(message: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({ "error": message.into() }))).into_response()
}

fn not_found(message: impl Into<String>) -> Response {
    (StatusCode::NOT_FOUND, Json(json!({ "error": message.into() }))).into_response()
}

fn conflict(message: impl Into<String>) -> Response {
    (StatusCode::CONFLICT, Json(json!({ "error": message.into() }))).into_response()
}

/// Kesiapan kredensial: hanya melaporkan NAMA variabel dan ada/tidaknya nilainya.
/// Nilai rahasia tidak pernah dikirim ke console.
pub(crate) async fn readiness(State(state): State<AppState>) -> Json<Value> {
    let channels = state.store.list_channels().await;
    let mut items = Vec::new();
    if let Some(list) = channels.as_array() {
        for channel in list {
            let provider = channel
                .get("provider")
                .and_then(Value::as_str)
                .unwrap_or("");
            let credentials_ref = channel
                .get("credentials_ref")
                .and_then(Value::as_str)
                .unwrap_or("");
            let ready = provider == "local"
                || (!credentials_ref.trim().is_empty()
                    && std::env::var(credentials_ref).is_ok());
            items.push(json!({
                "name": channel.get("name").cloned().unwrap_or(Value::Null),
                "provider": provider,
                "credentials_ref": credentials_ref,
                "ready": ready,
            }));
        }
    }

    Json(json!({
        "channels": items,
        "outbound": {
            "local": [],
            "dingtalk": ["DINGTALK_WEBHOOK_URL"],
            "feishu": ["FEISHU_WEBHOOK_URL"],
            "lark": ["LARK_WEBHOOK_URL"],
            "wecom": ["WECOM_WEBHOOK_URL"],
        },
        "inbound_app": {
            "dingtalk": ["AppKey", "AppSecret", "RobotCode (Stream Mode)"],
            "feishu": ["App ID", "App Secret", "Verification Token", "Encrypt Key"],
            "wecom": ["CorpID", "AgentID", "Secret", "Token", "EncodingAESKey"],
        },
        "note": "outbound dipakai kode sekarang; inbound_app belum diimplementasikan dan hanya checklist.",
    }))
}

pub(crate) async fn list(State(state): State<AppState>) -> Json<Value> {
    let channels = state.store.list_channels().await;
    let count = channels.as_array().map(Vec::len).unwrap_or(0);
    Json(json!({ "channels": channels, "count": count }))
}

pub(crate) async fn create(
    State(state): State<AppState>,
    Json(body): Json<CreateChannel>,
) -> Response {
    let provider = body.provider.trim().to_lowercase();
    if !VALID_PROVIDERS.contains(&provider.as_str()) {
        return bad(format!(
            "provider \"{provider}\" tidak dikenal (pilih {})",
            VALID_PROVIDERS.join(", ")
        ));
    }
    let name = body.name.trim().to_string();
    if name.is_empty() {
        return bad("nama channel tidak boleh kosong");
    }
    let credentials_ref = body.credentials_ref.unwrap_or_default();
    if provider != "local" && credentials_ref.trim().is_empty() {
        return bad("provider eksternal butuh nama variabel kredensial (credentials_ref)");
    }
    let id = format!("ch_{:x}", crate::approvals::nanos());
    match state
        .store
        .create_channel(&id, &provider, &name, credentials_ref.trim())
        .await
    {
        Ok(()) => (
            StatusCode::CREATED,
            Json(json!({ "id": id, "provider": provider, "name": name })),
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
    match state.store.delete_channel(&id).await {
        Ok(()) => Json(json!({ "deleted": id })).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn bindings(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Json<Value> {
    Json(json!({ "bindings": state.store.list_bindings(&id).await }))
}

pub(crate) async fn add_binding(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<AddBinding>,
) -> Response {
    let worker_exists = {
        let items = state.wakers.read().await;
        items.iter().any(|waker| waker.id == body.waker_id)
    };
    if !worker_exists {
        return bad("worker tidak ada");
    }
    if body.external_chat_id.trim().is_empty() {
        return bad("external_chat_id tidak boleh kosong");
    }
    let binding_id = format!("bind_{:x}", crate::approvals::nanos());
    match state
        .store
        .add_binding(&binding_id, &id, &body.waker_id, body.external_chat_id.trim())
        .await
    {
        Ok(()) => (StatusCode::CREATED, Json(json!({ "id": binding_id }))).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn remove_binding(
    State(state): State<AppState>,
    Path((_channel, binding)): Path<(String, String)>,
) -> Response {
    match state.store.delete_binding(&binding).await {
        Ok(()) => Json(json!({ "deleted": binding })).into_response(),
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
    Json(json!({ "messages": state.store.list_channel_messages(&id).await }))
}

#[derive(Deserialize)]
pub(crate) struct LarkIngress {
    pub(crate) chat_id: String,
    pub(crate) text: String,
    #[serde(default)]
    pub(crate) message_id: Option<String>,
}

/// Titik masuk dari bridge Lark (long connection). Menunggu worker selesai lalu
/// mengembalikan balasan; bridge yang mengirimkannya lewat API Lark.
pub(crate) async fn lark_ingress(
    State(state): State<AppState>,
    Json(body): Json<LarkIngress>,
) -> Response {
    let chat_id = body.chat_id.trim().to_string();
    let text = body.text.trim().to_string();
    if chat_id.is_empty() || text.is_empty() {
        return bad("chat_id dan text wajib diisi");
    }
    let Some((channel_id, waker_id)) = state.store.find_binding_any(&chat_id).await else {
        return conflict("chat ini belum dipasangkan dengan worker");
    };
    let waker = {
        let items = state.wakers.read().await;
        items.iter().find(|item| item.id == waker_id).cloned()
    };
    let Some(waker) = waker else {
        return conflict("worker pada binding ini sudah tidak ada");
    };
    if waker.status != "active" {
        return conflict(format!("worker \"{}\" sedang dijeda", waker.name));
    }

    state
        .store
        .log_channel_message(&channel_id, "in", &chat_id, Some(&waker.id), None, &text, "ok")
        .await;

    let params = StartParams {
        waker_id: waker.id.clone(),
        engine_key: waker.engine_key.clone(),
        model: waker.model.clone(),
        reasoning_effort: waker.reasoning_effort.clone(),
        context_window: waker.context_window,
        prompt: text,
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
        let mut payload = crate::approvals::params_payload(&params, None);
        if let Some(object) = payload.as_object_mut() {
            object.insert("channel_id".to_string(), json!(channel_id));
            object.insert("external_chat_id".to_string(), json!(chat_id));
        }
        return match state.store.create_approval("im", &payload).await {
            Ok(approval_id) => {
                let reply = "Permintaanmu menunggu persetujuan operator. Setelah disetujui, kirim ulang pesanmu.";
                state
                    .store
                    .log_channel_message(
                        &channel_id,
                        "out",
                        &chat_id,
                        Some(&waker.id),
                        None,
                        reply,
                        "ok",
                    )
                    .await;
                Json(json!({ "reply": reply, "approval_id": approval_id })).into_response()
            }
            Err(error) => (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({ "error": error })),
            )
                .into_response(),
        };
    }

    let run_id = match state.runs.start(params).await {
        Ok(run_id) => run_id,
        Err(error) => return bad(error),
    };

    for _ in 0..600 {
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        let Some(record) = state.runs.get(&run_id).await else {
            break;
        };
        match record.status.as_str() {
            "succeeded" => {
                let reply: String = record.output.chars().take(4_000).collect();
                state
                    .store
                    .log_channel_message(
                        &channel_id,
                        "out",
                        &chat_id,
                        Some(&waker.id),
                        Some(&run_id),
                        &reply,
                        "ok",
                    )
                    .await;
                return Json(json!({ "reply": reply, "run_id": run_id })).into_response();
            }
            "failed" | "cancelled" => {
                let reply = format!(
                    "Gagal menjalankan tugas: {}",
                    record.error.unwrap_or_default()
                );
                state
                    .store
                    .log_channel_message(
                        &channel_id,
                        "out",
                        &chat_id,
                        Some(&waker.id),
                        Some(&run_id),
                        &reply,
                        "ok",
                    )
                    .await;
                return Json(json!({ "reply": reply, "run_id": run_id })).into_response();
            }
            _ => {}
        }
    }

    (
        StatusCode::GATEWAY_TIMEOUT,
        Json(json!({ "error": "worker melewati batas waktu" })),
    )
        .into_response()
}

#[derive(Deserialize)]
pub(crate) struct TestSend {
    pub(crate) text: String,
}

/// Uji pengiriman tanpa menjalankan worker — memastikan webhook benar-benar hidup.
pub(crate) async fn test_send(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<TestSend>,
) -> Response {
    let Some((provider, _name, credentials_ref)) = state.store.get_channel(&id).await else {
        return not_found(format!("channel \"{id}\" tidak ada"));
    };
    let text = body.text.trim().to_string();
    if text.is_empty() {
        return bad("pesan kosong");
    }
    match deliver(&provider, &credentials_ref, &text).await {
        Ok(()) => {
            state
                .store
                .log_channel_message(&id, "out", "test", None, None, &text, "ok")
                .await;
            Json(json!({ "sent": true })).into_response()
        }
        Err(error) => {
            let status = format!("failed: {error}");
            state
                .store
                .log_channel_message(&id, "out", "test", None, None, &text, &status)
                .await;
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({ "error": error })),
            )
                .into_response()
        }
    }
}

pub(crate) async fn inbound(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<Inbound>,
) -> Response {
    let Some((provider, _name, credentials_ref)) = state.store.get_channel(&id).await else {
        return not_found(format!("channel \"{id}\" tidak ada"));
    };
    let chat_id = body.external_chat_id.trim().to_string();
    let Some(waker_id) = state.store.find_binding(&id, &chat_id).await else {
        return conflict("chat ini belum dipasangkan dengan worker");
    };
    let waker = {
        let items = state.wakers.read().await;
        items.iter().find(|w| w.id == waker_id).cloned()
    };
    let Some(waker) = waker else {
        return conflict("worker pada binding ini sudah tidak ada");
    };
    if waker.status != "active" {
        return conflict(format!("worker \"{}\" sedang dijeda", waker.name));
    }
    let text = body.text.trim().to_string();
    if text.is_empty() {
        return bad("pesan kosong");
    }

    state
        .store
        .log_channel_message(&id, "in", &chat_id, Some(&waker.id), None, &text, "ok")
        .await;

    let params = StartParams {
        waker_id: waker.id.clone(),
        engine_key: waker.engine_key.clone(),
        model: waker.model.clone(),
        reasoning_effort: waker.reasoning_effort.clone(),
        context_window: waker.context_window,
        prompt: text.clone(),
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
        let mut payload = crate::approvals::params_payload(&params, None);
        if let Some(object) = payload.as_object_mut() {
            object.insert("channel_id".to_string(), json!(id));
            object.insert("external_chat_id".to_string(), json!(chat_id));
        }
        return match state.store.create_approval("im", &payload).await {
            Ok(approval_id) => {
                let reply =
                    "Permintaanmu menunggu persetujuan operator. Setelah disetujui, kirim ulang pesanmu.";
                let _ = deliver(&provider, &credentials_ref, reply).await;
                state
                    .store
                    .log_channel_message(
                        &id,
                        "out",
                        &chat_id,
                        Some(&waker.id),
                        None,
                        reply,
                        "ok",
                    )
                    .await;
                (
                    StatusCode::ACCEPTED,
                    Json(json!({ "approval_id": approval_id, "status": "pending_approval" })),
                )
                    .into_response()
            }
            Err(error) => (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({ "error": error })),
            )
                .into_response(),
        };
    }

    let run_id = match state.runs.start(params).await {
        Ok(run_id) => run_id,
        Err(error) => return bad(error),
    };

    let state = state.clone();
    let channel_id = id.clone();
    let waker_id = waker.id.clone();
    let chat = chat_id.clone();
    let run = run_id.clone();
    tokio::spawn(async move {
        wait_and_reply(
            state,
            channel_id,
            chat,
            waker_id,
            run,
            provider,
            credentials_ref,
        )
        .await;
    });

    (
        StatusCode::ACCEPTED,
        Json(json!({ "run_id": run_id, "waker_id": waker.id })),
    )
        .into_response()
}

async fn wait_and_reply(
    state: AppState,
    channel_id: String,
    chat_id: String,
    waker_id: String,
    run_id: String,
    provider: String,
    credentials_ref: String,
) {
    let mut reply = None;
    for _ in 0..(60 * 60 * 2) {
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        let Some(record) = state.runs.get(&run_id).await else {
            break;
        };
        match record.status.as_str() {
            "succeeded" => {
                reply = Some(record.output);
                break;
            }
            "failed" => {
                reply = Some(format!(
                    "Gagal menjalankan tugas: {}",
                    record.error.unwrap_or_default()
                ));
                break;
            }
            _ => {}
        }
    }

    let Some(text) = reply else {
        return;
    };
    let text: String = text.chars().take(4_000).collect();
    let delivered = deliver(&provider, &credentials_ref, &text).await;
    let status = match &delivered {
        Ok(()) => "ok".to_string(),
        Err(error) => format!("failed: {error}"),
    };
    state
        .store
        .log_channel_message(
            &channel_id,
            "out",
            &chat_id,
            Some(&waker_id),
            Some(&run_id),
            &text,
            &status,
        )
        .await;
}

async fn deliver(provider: &str, credentials_ref: &str, text: &str) -> Result<(), String> {
    if provider == "local" {
        return Ok(());
    }
    if credentials_ref.trim().is_empty() {
        return Err("credentials_ref kosong".to_string());
    }
    let url = std::env::var(credentials_ref)
        .map_err(|_| format!("variabel lingkungan {credentials_ref} tidak diset"))?;
    let body = match provider {
        "dingtalk" => json!({ "msgtype": "text", "text": { "content": text } }),
        "feishu" | "lark" => json!({ "msg_type": "text", "content": { "text": text } }),
        "wecom" => json!({ "msgtype": "text", "text": { "content": text } }),
        other => return Err(format!("provider \"{other}\" belum punya pengirim")),
    }
    .to_string();

    let output = tokio::process::Command::new("curl")
        .args([
            "-sS",
            "--max-time",
            "10",
            "-H",
            "content-type: application/json",
            "--data-binary",
            &body,
            &url,
        ])
        .output()
        .await
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr)
            .lines()
            .next()
            .unwrap_or("pengiriman gagal")
            .to_string());
    }

    // curl sukses bukan berarti platform menerima. Lark/Feishu memakai `code`,
    // DingTalk memakai `StatusCode`. Nol berarti benar-benar terkirim.
    let body_text = String::from_utf8_lossy(&output.stdout);
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(&body_text) {
        if let Some(code) = value.get("code").and_then(serde_json::Value::as_i64) {
            if code != 0 {
                let message = value
                    .get("msg")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("tanpa pesan");
                return Err(format!("platform menolak (code {code}): {message}"));
            }
        }
        if let Some(code) = value.get("StatusCode").and_then(serde_json::Value::as_i64) {
            if code != 0 {
                let message = value
                    .get("errmsg")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("tanpa pesan");
                return Err(format!("platform menolak (StatusCode {code}): {message}"));
            }
        }
    }
    Ok(())
}
