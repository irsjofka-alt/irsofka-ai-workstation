//! Viewer: membaca berkas lokal untuk kolom ketiga console (read-only).
//!
//! Hanya berkas di bawah direktori home yang dilayani, dan berkas kredensial
//! ditolak secara eksplisit. Path dikanonikalisasi lebih dulu supaya symlink
//! tidak bisa dipakai keluar dari batas itu. Isi teks dibatasi 2 MB; gambar
//! disajikan lewat endpoint `raw` dengan tipe yang sudah dikenal.

use axum::extract::Query;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::json;
use std::path::{Path, PathBuf};

use crate::paths::home_dir;

const MAX_TEXT_BYTES: u64 = 2 * 1024 * 1024;
const MAX_IMAGE_BYTES: u64 = 24 * 1024 * 1024;

const TEXT_EXT: [&str; 21] = [
    "md", "markdown", "txt", "log", "json", "jsonl", "yaml", "yml", "toml", "csv", "tsv", "py",
    "js", "ts", "tsx", "jsx", "rs", "sh", "html", "css", "sql",
];
const IMAGE_EXT: [&str; 8] = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "avif"];

#[derive(Deserialize)]
pub(crate) struct PathQuery {
    pub(crate) path: String,
}

fn extension(path: &Path) -> String {
    path.extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_lowercase()
}

fn expand(raw: &str) -> Option<PathBuf> {
    let raw = raw.trim();
    if raw.is_empty() || raw.contains('\0') {
        return None;
    }
    if let Some(rest) = raw.strip_prefix("~/") {
        return Some(home_dir().join(rest));
    }
    let path = PathBuf::from(raw);
    if path.is_absolute() {
        Some(path)
    } else {
        // Laporan worker kadang menulis "home/irsofka/..."; perlakukan relatif
        // terhadap akar sistem berkas agar tetap bisa diklik.
        Some(PathBuf::from("/").join(path))
    }
}

fn denied(path: &Path) -> bool {
    let text = path.to_string_lossy().to_lowercase();
    const BLOCKED: [&str; 24] = [
        "/.ssh/",
        "/.gnupg/",
        "/.auth/",
        "/.config/qoder",
        "/.config/gcloud/",
        "/.config/gh/",
        "/.config/lark-bridge.env",
        "/.dws/",
        "/.qoderwake/.auth",
        "/.gemini/",
        "/.docker/",
        "/.kube/",
        "/.aws/",
        "/.netrc",
        "/.pgpass",
        "/.git-credentials",
        "/.npmrc",
        "/.git/",
        "config/db_local.json",
        "config/byok",
        "config/tripo",
        "id_rsa",
        "id_ed25519",
        "/.env",
    ];
    if BLOCKED.iter().any(|needle| text.contains(needle)) {
        return true;
    }
    let file = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("")
        .to_lowercase();
    // `.env`, `.env.local`, `byok.local.json`, `credentials.json`: nama yang
    // hampir selalu berisi rahasia walau ekstensinya terlihat biasa.
    if file == ".env" || file.starts_with(".env.") || file.ends_with(".env") {
        return true;
    }
    if file.starts_with("byok") || file.contains("credential") || file.contains("secret") {
        return true;
    }
    let ext = extension(path);
    matches!(ext.as_str(), "pem" | "key" | "p12" | "pfx")
}

fn resolve(raw: &str) -> Result<PathBuf, (StatusCode, Json<serde_json::Value>)> {
    let Some(candidate) = expand(raw) else {
        return Err((StatusCode::BAD_REQUEST, Json(json!({ "error": "path tidak sah" }))));
    };
    let Ok(canon) = candidate.canonicalize() else {
        return Err((StatusCode::NOT_FOUND, Json(json!({ "error": "berkas tidak ada" }))));
    };
    let home = home_dir()
        .canonicalize()
        .unwrap_or_else(|_| home_dir());
    if !canon.starts_with(&home) {
        return Err((StatusCode::FORBIDDEN, Json(json!({ "error": "di luar direktori home" }))));
    }
    if denied(&canon) {
        return Err((StatusCode::FORBIDDEN, Json(json!({ "error": "berkas ini tidak dilayani viewer" }))));
    }
    if !canon.is_file() {
        return Err((StatusCode::NOT_FOUND, Json(json!({ "error": "bukan berkas" }))));
    }
    Ok(canon)
}

fn encode_path(path: &str) -> String {
    let mut out = String::with_capacity(path.len());
    for byte in path.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

pub(crate) async fn view(Query(query): Query<PathQuery>) -> Response {
    let path = match resolve(&query.path) {
        Ok(path) => path,
        Err(response) => return response.into_response(),
    };
    let size = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
    let ext = extension(&path);
    if IMAGE_EXT.contains(&ext.as_str()) {
        if size > MAX_IMAGE_BYTES {
            return (StatusCode::PAYLOAD_TOO_LARGE, Json(json!({ "error": "gambar terlalu besar" }))).into_response();
        }
        return Json(json!({
            "kind": "image",
            "path": path.display().to_string(),
            "bytes": size,
            "url": format!("/api/files/raw?path={}", encode_path(&path.display().to_string())),
        }))
        .into_response();
    }
    if !TEXT_EXT.contains(&ext.as_str()) {
        return (
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            Json(json!({ "error": format!("tipe .{ext} belum bisa ditampilkan") })),
        )
            .into_response();
    }
    if size > MAX_TEXT_BYTES {
        return (StatusCode::PAYLOAD_TOO_LARGE, Json(json!({ "error": "berkas terlalu besar" }))).into_response();
    }
    match std::fs::read_to_string(&path) {
        Ok(content) => Json(json!({
            "kind": "text",
            "path": path.display().to_string(),
            "bytes": size,
            "content": content,
        }))
        .into_response(),
        Err(error) => (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": format!("tidak bisa dibaca sebagai teks: {error}") })),
        )
            .into_response(),
    }
}

pub(crate) async fn raw(Query(query): Query<PathQuery>) -> Response {
    let path = match resolve(&query.path) {
        Ok(path) => path,
        Err(response) => return response.into_response(),
    };
    let ext = extension(&path);
    if !IMAGE_EXT.contains(&ext.as_str()) {
        return (StatusCode::UNSUPPORTED_MEDIA_TYPE, Json(json!({ "error": "bukan gambar" }))).into_response();
    }
    let size = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
    if size > MAX_IMAGE_BYTES {
        return (StatusCode::PAYLOAD_TOO_LARGE, Json(json!({ "error": "gambar terlalu besar" }))).into_response();
    }
    let Ok(bytes) = std::fs::read(&path) else {
        return (StatusCode::NOT_FOUND, Json(json!({ "error": "berkas tidak terbaca" }))).into_response();
    };
    let content_type = match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "bmp" => "image/bmp",
        "avif" => "image/avif",
        _ => "application/octet-stream",
    };
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, content_type)
        .header(header::CACHE_CONTROL, "no-store")
        .body(axum::body::Body::from(bytes))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}
