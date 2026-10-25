//! Tulis situs Playground lewat HTTP: post, media, rebuild.
//!
//! Aturan tetap milik Python (`tools/site/publish.py`): pindai secret, validasi,
//! sha256, dan build. Modul ini hanya batas HTTP + batas waktu proses — pola yang
//! sama dengan `worker_context.rs` — supaya console, MCP, dan CLI tidak punya dua
//! kebenaran. Badan request ditulis ke berkas sementara di `data/site-index/inbox`
//! lalu dibaca skrip; daemon tidak menafsirkan isi post.
use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::{json, Value};
use std::path::PathBuf;

use crate::paths::{data_dir, site_publish_path};

fn inbox_file(kind: &str) -> Result<PathBuf, Response> {
    let dir = data_dir().join("site-index").join("inbox");
    std::fs::create_dir_all(&dir).map_err(|error| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"ok": false, "error": format!("inbox situs tidak bisa dibuat: {error}")})),
        )
            .into_response()
    })?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    Ok(dir.join(format!("{kind}-{}-{stamp}.json", std::process::id())))
}

fn failure(status: StatusCode, error: String) -> Response {
    (status, Json(json!({"ok": false, "error": error}))).into_response()
}

async fn run_site(args: Vec<String>) -> Response {
    let script = site_publish_path();
    let result = tokio::task::spawn_blocking(move || {
        crate::api::autopilot::run_script(&script, &args)
    })
    .await;
    match result {
        Ok(Ok(text)) => {
            // publish.py sudah memutuskan hasilnya; daemon tidak menilai ulang. Post
            // yang ditolak tetap JSON yang sah, jadi status HTTP-nya 422 — bukan 200.
            let rejected = serde_json::from_str::<Value>(&text)
                .ok()
                .and_then(|value| value.get("ok").and_then(Value::as_bool))
                == Some(false);
            let status = if rejected {
                StatusCode::UNPROCESSABLE_ENTITY
            } else {
                StatusCode::OK
            };
            (
                status,
                [(axum::http::header::CONTENT_TYPE, "application/json")],
                axum::body::Body::from(text),
            )
                .into_response()
        }
        Ok(Err(error)) => failure(StatusCode::SERVICE_UNAVAILABLE, error),
        Err(error) => failure(
            StatusCode::SERVICE_UNAVAILABLE,
            format!("task situs berhenti: {error}"),
        ),
    }
}

/// Tulis badan request ke inbox lalu jalankan `publish.py <subcommand> --request`.
async fn publish_call(subcommand: &str, body: Value) -> Response {
    let inbox = match inbox_file(subcommand) {
        Ok(path) => path,
        Err(response) => return response,
    };
    let encoded = match serde_json::to_vec_pretty(&body) {
        Ok(bytes) => bytes,
        Err(error) => return failure(StatusCode::BAD_REQUEST, format!("request tidak valid: {error}")),
    };
    if let Err(error) = std::fs::write(&inbox, encoded) {
        return failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("request situs tidak bisa ditulis: {error}"),
        );
    }
    let cleanup = inbox.clone();
    let response = run_site(vec![
        subcommand.to_string(),
        "--request".to_string(),
        inbox.display().to_string(),
    ])
    .await;
    let _ = std::fs::remove_file(&cleanup);
    response
}

/// POST /api/site/posts — objek post langsung, atau {"post": {...}, "public": true}.
pub(crate) async fn post_post(Json(body): Json<Value>) -> Response {
    publish_call("post", body).await
}

/// POST /api/site/media — salin media + sha256 + post showcase/generation.
pub(crate) async fn post_media(Json(body): Json<Value>) -> Response {
    publish_call("media", body).await
}

/// POST /api/site/rebuild — build ulang situs; tidak menerima badan bermakna.
pub(crate) async fn post_rebuild(Json(_body): Json<Value>) -> Response {
    run_site(vec!["rebuild".to_string()]).await
}
