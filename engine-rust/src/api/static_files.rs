//! Menyajikan gui.html dan aset statis yang menyertainya.
//!
//! Tidak ada build step dan tidak ada CDN: satu berkas HTML dibaca dari disk setiap kali
//! diminta, dengan fallback ke salinan yang ikut ter-compile kalau berkasnya hilang.
//! Header anti-cache di sini bukan hiasan — tanpa itu WebKit boleh menyajikan hasil lama
//! dan tombol "Refresh UI" terlihat berhasil padahal yang termuat JavaScript lama.
use axum::extract::{Path, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use serde_json::Value;
use std::fs;

use crate::api::{wakers, AppState};
use crate::paths::{assets_dir, console_dir, gui_path, site_dir, station_path};

pub(crate) async fn serve_gui() -> Response {
    let html = fs::read_to_string(gui_path())
        .or_else(|_| Ok::<String, std::io::Error>(include_str!("../gui.html").to_string()))
        .unwrap_or_else(|_| "<h1>gui.html tidak ditemukan</h1>".to_string());
    Response::builder()
        .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-store, must-revalidate")
        .body(axum::body::Body::from(html))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

/// Console aplikasi (pengganti bertahap gui.html). Berkas dibaca dari disk setiap
/// permintaan supaya suntingan CSS/JS langsung terlihat tanpa build ulang.
///
/// Data awal disuntikkan ke halaman (`window.__BOOT__`) alih-alih ditunggu lewat
/// fetch: paint pertama sudah berisi daftar waker, dan halaman tidak berkedip
/// "memuat" hanya untuk menampilkan hal yang sudah diketahui daemon.
pub(crate) async fn serve_console(State(state): State<AppState>) -> Response {
    let html = fs::read_to_string(console_dir().join("index.html"))
        .or_else(|_| Ok::<String, std::io::Error>(include_str!("../console/index.html").to_string()))
        .unwrap_or_else(|_| "<h1>console/index.html tidak ditemukan</h1>".to_string());

    let wakers_value = {
        let items = state.wakers.read().await;
        serde_json::to_value(&*items).unwrap_or(Value::Array(Vec::new()))
    };
    let engines = wakers::engines_payload();
    // Riwayat terbaru ikut di bootstrap: paint pertama halaman Chat sudah berisi
    // percakapan, bukan ruang kosong yang berubah sesudah fetch.
    let mut recent_runs = state.runs.list(None).await;
    recent_runs.truncate(20);
    let runs_value = serde_json::to_value(&recent_runs).unwrap_or(Value::Array(Vec::new()));
    let (parallel, keep, timeout_s) = state.runs.limits();
    let boot = serde_json::json!({
        "wakers": wakers_value,
        "engines": engines.get("engines").cloned().unwrap_or(Value::Array(Vec::new())),
        "skipped": engines.get("skipped").cloned().unwrap_or(Value::Array(Vec::new())),
        "enginesError": engines.get("error").cloned().unwrap_or(Value::Null),
        "runs": runs_value,
        "backend": state.store.backend,
        "limits": {
            "parallel": parallel,
            "keep": keep,
            "timeout_s": timeout_s,
        },
    });
    let boot_json = serde_json::to_string(&boot)
        .unwrap_or_else(|_| "null".to_string())
        // `<` di-escape supaya isi prompt pengguna tidak bisa menutup tag script.
        .replace('<', "\\u003c");
    let html = html.replace(
        "<!--BOOT-->",
        &format!("<script>window.__BOOT__={boot_json};</script>"),
    );

    Response::builder()
        .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-store, must-revalidate")
        .body(axum::body::Body::from(html))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

pub(crate) async fn serve_console_asset(Path(rel): Path<String>) -> Response {
    if rel.contains("..") {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let full = console_dir().join(&rel);
    match fs::read(&full) {
        Ok(bytes) => {
            let ctype = match full.extension().and_then(|e| e.to_str()).unwrap_or("") {
                "js" => "text/javascript; charset=utf-8",
                "css" => "text/css; charset=utf-8",
                "svg" => "image/svg+xml",
                "png" => "image/png",
                "woff2" => "font/woff2",
                "json" => "application/json; charset=utf-8",
                _ => "application/octet-stream",
            };
            Response::builder()
                .header(header::CONTENT_TYPE, ctype)
                .header(header::CACHE_CONTROL, "no-store")
                .body(axum::body::Body::from(bytes))
                .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
        }
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

pub(crate) async fn serve_station() -> Response {
    match fs::read_to_string(station_path()) {
        Ok(html) => Response::builder()
            .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
            .header(header::CACHE_CONTROL, "no-store")
            .body(axum::body::Body::from(html))
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response()),
        Err(_) => (StatusCode::NOT_FOUND, "station.html belum terpasang").into_response(),
    }
}

/// Halaman depan situs Playground. HTML selalu no-store: build ulang harus langsung
/// terlihat, dan WebKitGTK dikenal menyajikan halaman lama tanpa header ini.
pub(crate) async fn serve_site_index() -> Response {
    serve_site_file("index.html")
}

/// Aset situs (HTML, CSS, media). Path traversal ditolak sebelum menyentuh disk.
pub(crate) async fn serve_site_asset(Path(rel): Path<String>) -> Response {
    if rel.is_empty() || rel.contains("..") || rel.starts_with('/') {
        return StatusCode::BAD_REQUEST.into_response();
    }
    serve_site_file(&rel)
}

fn serve_site_file(rel: &str) -> Response {
    let full = site_dir().join(rel);
    match fs::read(&full) {
        Ok(bytes) => {
            let ext = full.extension().and_then(|e| e.to_str()).unwrap_or("");
            let ctype = match ext {
                "html" => "text/html; charset=utf-8",
                "css" => "text/css; charset=utf-8",
                "js" => "text/javascript; charset=utf-8",
                "json" => "application/json; charset=utf-8",
                "xml" => "application/xml; charset=utf-8",
                "svg" => "image/svg+xml",
                "png" => "image/png",
                "jpg" | "jpeg" => "image/jpeg",
                "webp" => "image/webp",
                "gif" => "image/gif",
                "avif" => "image/avif",
                "mp4" => "video/mp4",
                "webm" => "video/webm",
                "glb" => "model/gltf-binary",
                "gltf" => "model/gltf+json",
                "woff2" => "font/woff2",
                _ => "application/octet-stream",
            };
            let cache = if matches!(ext, "html" | "json" | "xml") {
                "no-store, must-revalidate"
            } else {
                "public, max-age=300"
            };
            Response::builder()
                .header(header::CONTENT_TYPE, ctype)
                .header(header::CACHE_CONTROL, cache)
                .body(axum::body::Body::from(bytes))
                .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
        }
        Err(_) if rel == "index.html" => (
            StatusCode::NOT_FOUND,
            "site belum dibangun — jalankan: python3 tools/site/build.py",
        )
            .into_response(),
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

pub(crate) async fn serve_asset(Path(rel): Path<String>) -> Response {
    if rel.contains("..") {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let full = assets_dir().join(&rel);
    match fs::read(&full) {
        Ok(bytes) => {
            let ctype = match full.extension().and_then(|e| e.to_str()).unwrap_or("") {
                "js" => "text/javascript; charset=utf-8",
                "css" => "text/css; charset=utf-8",
                "woff2" => "font/woff2",
                "png" => "image/png",
                "svg" => "image/svg+xml",
                _ => "application/octet-stream",
            };
            Response::builder()
                .header(header::CONTENT_TYPE, ctype)
                .header(header::CACHE_CONTROL, "public, max-age=3600")
                .body(axum::body::Body::from(bytes))
                .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
        }
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}
