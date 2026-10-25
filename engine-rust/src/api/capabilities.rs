//! Kapabilitas & resource: skill, rules, MCP, dan mesin.
//!
//! Semua dibaca dari disk apa adanya. Tidak ada daftar yang ditulis tangan di
//! console, supaya isi halaman ini tidak pernah berbeda dari yang benar-benar ada.

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

use crate::api::AppState;
use crate::paths::station_dir;

/// Paket konteks yang benar-benar ditempel ke prompt worker; dibaca dari brain.
pub(crate) async fn context() -> Json<Value> {
    let text = crate::context::pack();
    Json(json!({ "chars": text.chars().count(), "text": text }))
}

pub(crate) async fn get_capabilities(State(_state): State<AppState>) -> Json<Value> {
    let base = station_dir();
    Json(json!({
        "skills": list_dirs(&base.join("brain/skills")),
        "rules": list_files(&base.join("brain/rules")),
        "mcp": list_dirs(&base.join("mcp")),
        "engines": crate::api::wakers::engines_payload(),
    }))
}

fn allowed_root(scope: &str) -> Option<PathBuf> {
    let base = station_dir();
    match scope {
        "skills" => Some(base.join("brain/skills")),
        "rules" => Some(base.join("brain/rules")),
        "mcp" => Some(base.join("mcp")),
        _ => None,
    }
}

#[derive(Deserialize)]
pub(crate) struct TreeQuery {
    pub(crate) scope: String,
    #[serde(default)]
    pub(crate) path: Option<String>,
}

/// Daftar berkas di bawah satu folder kemampuan. Path relatif saja; `..` ditolak.
pub(crate) async fn tree(Query(query): Query<TreeQuery>) -> Response {
    let Some(root) = allowed_root(&query.scope) else {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "scope tidak dikenal" }))).into_response();
    };
    let rel = query.path.unwrap_or_default();
    if rel.contains("..") || rel.starts_with('/') {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "path tidak sah" }))).into_response();
    }
    let target = root.join(&rel);
    // Symlink di dalam root bisa menunjuk ke luar; bandingkan path kanonik.
    let root_real = match root.canonicalize() {
        Ok(path) => path,
        Err(error) => {
            return (StatusCode::BAD_REQUEST, Json(json!({ "error": format!("scope tidak terbaca: {error}") }))).into_response()
        }
    };
    let target_real = match target.canonicalize() {
        Ok(path) => path,
        Err(error) => {
            return (StatusCode::NOT_FOUND, Json(json!({ "error": format!("folder tidak ada: {error}") }))).into_response()
        }
    };
    if !target_real.starts_with(&root_real) {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "path keluar dari scope" }))).into_response();
    }
    let mut files = Vec::new();
    // Path relatif terhadap AKAR scope, bukan folder yang dibuka: pemanggil
    // (console) memakai path itu untuk endpoint file yang juga berakar di scope.
    collect_files(&root_real, &target_real, &mut files, 0);
    Json(json!({ "scope": query.scope, "root": rel, "files": files })).into_response()
}

#[derive(Deserialize)]
pub(crate) struct FileQuery {
    pub(crate) scope: String,
    pub(crate) path: String,
}

pub(crate) async fn file(Query(query): Query<FileQuery>) -> Response {
    let Some(root) = allowed_root(&query.scope) else {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "scope tidak dikenal" }))).into_response();
    };
    if query.path.contains("..") || query.path.starts_with('/') {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "path tidak sah" }))).into_response();
    }
    let full = root.join(&query.path);
    if !full.is_file() {
        return (StatusCode::NOT_FOUND, Json(json!({ "error": "berkas tidak ada" }))).into_response();
    }
    let root_real = match root.canonicalize() {
        Ok(path) => path,
        Err(error) => {
            return (StatusCode::BAD_REQUEST, Json(json!({ "error": format!("scope tidak terbaca: {error}") }))).into_response()
        }
    };
    let full_real = match full.canonicalize() {
        Ok(path) => path,
        Err(error) => {
            return (StatusCode::NOT_FOUND, Json(json!({ "error": format!("berkas tidak ada: {error}") }))).into_response()
        }
    };
    if !full_real.starts_with(&root_real) {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "path keluar dari scope" }))).into_response();
    }
    match fs::read_to_string(&full_real) {
        Ok(content) => Json(json!({
            "scope": query.scope,
            "path": query.path,
            "bytes": content.len(),
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

fn collect_files(root: &Path, dir: &Path, out: &mut Vec<Value>, depth: usize) {
    if depth > 6 {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    let mut paths: Vec<PathBuf> = entries.flatten().map(|entry| entry.path()).collect();
    paths.sort();
    for path in paths {
        let Ok(rel) = path.strip_prefix(root) else {
            continue;
        };
        let rel = rel.to_string_lossy().to_string();
        if path.is_dir() {
            out.push(json!({ "path": rel, "kind": "dir" }));
            collect_files(root, &path, out, depth + 1);
        } else if !rel
            .split('/')
            .any(|part| part == "node_modules" || part == "__pycache__" || part == "venv")
        {
            let size = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            out.push(json!({ "path": rel, "kind": "file", "bytes": size }));
        }
    }
}

fn list_dirs(path: &Path) -> Vec<String> {
    let mut items: Vec<String> = fs::read_dir(path)
        .map(|entries| {
            entries
                .flatten()
                .filter(|entry| entry.path().is_dir())
                .filter_map(|entry| entry.file_name().to_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    items.sort();
    items
}

fn list_files(path: &Path) -> Vec<String> {
    let mut items: Vec<String> = fs::read_dir(path)
        .map(|entries| {
            entries
                .flatten()
                .filter(|entry| entry.path().is_file())
                .filter_map(|entry| entry.file_name().to_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    items.sort();
    items
}
