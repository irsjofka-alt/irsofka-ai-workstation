//! Gerbang persetujuan.
//!
//! Waker dengan `approval_mode = require` tidak pernah berjalan langsung. Permintaan
//! run disimpan sebagai baris `waker_approval` berisi parameter lengkap; menyetujui
//! berarti menjalankan parameter itu, bukan menjalankan sesuatu yang berbeda dari
//! yang ditampilkan.

use serde_json::{json, Value};

use crate::api::wakers::Waker;
use crate::runs::StartParams;

pub(crate) fn nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

pub(crate) fn needs_approval(waker: &Waker) -> bool {
    waker.approval_mode == "require"
}

pub(crate) fn params_payload(params: &StartParams, automation_run_id: Option<&str>) -> Value {
    json!({
        "waker_id": params.waker_id,
        "engine_key": params.engine_key,
        "model": params.model,
        "reasoning_effort": params.reasoning_effort,
        "context_window": params.context_window,
        "prompt": params.prompt,
        "system_prompt": params.system_prompt,
        "workspace": params.workspace,
        "answer_style": params.answer_style,
        "max_output_tokens": params.max_output_tokens,
        "include_history": params.include_history,
        "allow_cache": params.allow_cache,
        "automation_run_id": automation_run_id,
    })
}

pub(crate) fn payload_to_params(payload: &Value) -> StartParams {
    let text = |key: &str| {
        payload
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    StartParams {
        waker_id: text("waker_id"),
        engine_key: text("engine_key"),
        model: payload
            .get("model")
            .and_then(Value::as_str)
            .map(str::to_string),
        reasoning_effort: payload
            .get("reasoning_effort")
            .and_then(Value::as_str)
            .map(str::to_string),
        context_window: payload.get("context_window").and_then(Value::as_u64),
        prompt: text("prompt"),
        system_prompt: text("system_prompt"),
        workspace: text("workspace"),
        answer_style: payload
            .get("answer_style")
            .and_then(Value::as_str)
            .map(str::to_string),
        max_output_tokens: payload.get("max_output_tokens").and_then(Value::as_i64),
        // Payload lama tidak punya field ini; perlakukan sebagai percakapan.
        include_history: payload
            .get("include_history")
            .and_then(Value::as_bool)
            .unwrap_or(true),
        // Approval lama tidak punya field ini; cache hanya untuk automation baru.
        allow_cache: payload
            .get("allow_cache")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        fallback_engines: Vec::new(),
        save_message: true,
    }
}
