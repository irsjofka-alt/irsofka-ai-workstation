//! Penampil memori station (read-only).
//!
//! Console hanya membaca `brain/memory`. Tidak ada jalur tulis di sini: MEMORY.md
//! tetap milik tool station (dream/lifecycle), supaya dua penulis tidak saling
//! menimpa. Path divalidasi agar tidak bisa keluar dari direktori memori.

use axum::extract::Query;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use crate::paths::station_dir;

#[derive(Deserialize)]
pub(crate) struct FilesQuery {
    #[serde(default)]
    pub(crate) scope: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct FileQuery {
    pub(crate) path: String,
}

fn memory_root() -> PathBuf {
    station_dir().join("brain").join("memory")
}

pub(crate) async fn scopes() -> Json<Value> {
    let root = memory_root();
    let mut items = Vec::new();

    let root_files = read_dir_files(&root);
    items.push(json!({
        "scope": "root",
        "files": root_files.len(),
        "bytes": root_files.iter().map(|(_, meta)| meta.0).sum::<u64>(),
        "updated": root_files.iter().map(|(_, meta)| meta.1).max().map(|at| fmt_time(at)),
    }));

    if let Ok(entries) = fs::read_dir(&root) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            let (files, bytes, updated) = dir_stats(&path, 0);
            items.push(json!({
                "scope": name,
                "files": files,
                "bytes": bytes,
                "updated": updated.map(fmt_time),
            }));
        }
    }
    items.sort_by(|a, b| {
        let left = a.get("files").and_then(Value::as_i64).unwrap_or(0);
        let right = b.get("files").and_then(Value::as_i64).unwrap_or(0);
        right.cmp(&left)
    });
    Json(json!({ "scopes": items }))
}

pub(crate) async fn files(Query(query): Query<FilesQuery>) -> Json<Value> {
    let root = memory_root();
    let scope = query.scope.unwrap_or_else(|| "root".to_string());
    let dir = if scope == "root" {
        root.clone()
    } else {
        if scope.contains("..") || scope.contains('/') {
            return Json(json!({ "files": [], "error": "scope tidak sah" }));
        }
        root.join(&scope)
    };
    if !dir.is_dir() {
        return Json(json!({ "files": [], "error": "scope tidak ditemukan" }));
    }

    let mut entries: Vec<(String, u64, SystemTime)> = fs::read_dir(&dir)
        .map(|items| {
            items
                .flatten()
                .filter(|entry| entry.path().is_file())
                .filter_map(|entry| {
                    let meta = entry.metadata().ok()?;
                    Some((
                        entry.file_name().to_string_lossy().to_string(),
                        meta.len(),
                        meta.modified().unwrap_or(SystemTime::UNIX_EPOCH),
                    ))
                })
                .collect()
        })
        .unwrap_or_default();
    entries.sort_by(|a, b| b.2.cmp(&a.2));
    entries.truncate(300);

    let files: Vec<Value> = entries
        .into_iter()
        .map(|(name, size, modified)| {
            json!({
                "name": name,
                "path": if scope == "root" { name.clone() } else { format!("{scope}/{name}") },
                "bytes": size,
                "modified": fmt_time(modified),
            })
        })
        .collect();
    Json(json!({ "scope": scope, "files": files }))
}

pub(crate) async fn file(Query(query): Query<FileQuery>) -> Response {
    let Some(path) = safe_path(&query.path) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "path tidak sah" })),
        )
            .into_response();
    };
    let meta = fs::metadata(&path).ok();
    match fs::read_to_string(&path) {
        Ok(content) => {
            let truncated = content.chars().count() > 200_000;
            let content: String = content.chars().take(200_000).collect();
            let bytes = meta.as_ref().map(|m| m.len()).unwrap_or(0);
            let modified = meta.and_then(|m| m.modified().ok()).map(fmt_time);
            Json(json!({
                "path": query.path,
                "bytes": bytes,
                "modified": modified,
                "truncated": truncated,
                "content": content,
            }))
            .into_response()
        }
        Err(_) => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "berkas tidak terbaca" })),
        )
            .into_response(),
    }
}

fn safe_path(relative: &str) -> Option<PathBuf> {
    if relative.contains("..") {
        return None;
    }
    let base = memory_root().canonicalize().ok()?;
    let full = base.join(relative).canonicalize().ok()?;
    if !full.starts_with(&base) || !full.is_file() {
        return None;
    }
    Some(full)
}

fn read_dir_files(dir: &Path) -> Vec<(String, (u64, SystemTime))> {
    fs::read_dir(dir)
        .map(|items| {
            items
                .flatten()
                .filter(|entry| entry.path().is_file())
                .filter_map(|entry| {
                    let meta = entry.metadata().ok()?;
                    Some((
                        entry.file_name().to_string_lossy().to_string(),
                        (
                            meta.len(),
                            meta.modified().unwrap_or(SystemTime::UNIX_EPOCH),
                        ),
                    ))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn dir_stats(dir: &Path, depth: usize) -> (u64, u64, Option<SystemTime>) {
    if depth > 3 {
        return (0, 0, None);
    }
    let mut files = 0u64;
    let mut bytes = 0u64;
    let mut updated: Option<SystemTime> = None;
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let (sub_files, sub_bytes, sub_updated) = dir_stats(&path, depth + 1);
                files += sub_files;
                bytes += sub_bytes;
                if let (Some(current), Some(candidate)) = (updated, sub_updated) {
                    if candidate > current {
                        updated = Some(candidate);
                    }
                } else if sub_updated.is_some() {
                    updated = sub_updated;
                }
            } else if let Ok(meta) = entry.metadata() {
                files += 1;
                bytes += meta.len();
                if let Ok(modified) = meta.modified() {
                    if updated.map(|current| modified > current).unwrap_or(true) {
                        updated = Some(modified);
                    }
                }
            }
        }
    }
    (files, bytes, updated)
}

fn fmt_time(at: SystemTime) -> String {
    chrono::DateTime::<chrono::Utc>::from(at).to_rfc3339()
}
