//! Status dan pembuka login Antigravity dari Console.
//!
//! `agy` tidak punya subcommand `login`/`logout`; OAuth berjalan implisit saat
//! CLI atau aplikasi Antigravity dimulai. Karena itu handler ini membaca status
//! dari log CLI terbaru (baris OAuth terakhir yang menang), lalu membuka
//! aplikasi Antigravity untuk alur OAuth ketika operator menekan Login.
//!
//! Identitas tidak pernah dikirim utuh: hanya email tersamar. Sumber kebenaran
//! status adalah log, bukan `agy models` (perintah itu tetap menjawab walau
//! sesi belum login).

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::SystemTime;

use crate::paths::home_dir;

fn log_dirs() -> Vec<PathBuf> {
    let engines = crate::paths::station_dir().join("engines");
    vec![
        engines.join("antigravity-cli/log"),
        engines.join("antigravity/log"),
    ]
}

fn newest_log() -> Option<PathBuf> {
    let mut best: Option<(SystemTime, PathBuf)> = None;
    for dir in log_dirs() {
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|ext| ext.to_str()) != Some("log") {
                continue;
            }
            let Ok(modified) = entry.metadata().and_then(|meta| meta.modified()) else {
                continue;
            };
            if best
                .as_ref()
                .map(|(time, _)| modified > *time)
                .unwrap_or(true)
            {
                best = Some((modified, path));
            }
        }
    }
    best.map(|(_, path)| path)
}

fn tail_lines(path: &Path, limit: usize) -> Vec<String> {
    let Ok(text) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let mut lines: Vec<String> = text
        .lines()
        .rev()
        .take(limit)
        .map(str::to_string)
        .collect();
    lines.reverse();
    lines
}

/// `i***@gmail.com` cukup untuk membedakan akun tanpa membocorkan alamat utuh.
fn mask_email(email: &str) -> String {
    let email = email.trim();
    let (local, domain) = email.split_once('@').unwrap_or((email, ""));
    let first = local.chars().next().unwrap_or('*');
    if domain.is_empty() {
        format!("{first}***")
    } else {
        format!("{first}***@{domain}")
    }
}

fn snapshot() -> Value {
    let Some(path) = newest_log() else {
        return json!({
            "logged_in": false,
            "account_hint": null,
            "status": "unknown",
            "checked_at": chrono::Utc::now().to_rfc3339(),
            "source": null,
        });
    };
    let mut logged_in = false;
    let mut account_hint: Option<String> = None;
    let mut status = "unknown";
    for line in tail_lines(&path, 400) {
        if line.contains("not logged into Antigravity") {
            logged_in = false;
            status = "logged_out";
            account_hint = None;
        } else if let Some(rest) = line
            .split("OAuth: authenticated successfully as ")
            .nth(1)
        {
            logged_in = true;
            status = "authenticated";
            let email = rest
                .split_whitespace()
                .next()
                .unwrap_or("")
                .trim_end_matches(',');
            if !email.is_empty() {
                account_hint = Some(mask_email(email));
            }
        }
    }
    json!({
        "logged_in": logged_in,
        "account_hint": account_hint,
        "status": status,
        "checked_at": chrono::Utc::now().to_rfc3339(),
        "source": path.file_name().and_then(|name| name.to_str()),
    })
}

pub(crate) async fn account() -> Json<Value> {
    Json(json!({ "antigravity": snapshot() }))
}

fn app_path() -> Option<PathBuf> {
    let home = home_dir();
    [
        home.join(".gemini/antigravity/bin/antigravity"),
        home.join("Apps/Antigravity-x64/antigravity"),
    ]
    .into_iter()
    .find(|path| path.exists())
}

pub(crate) async fn login() -> Response {
    let current = snapshot();
    let Some(app) = app_path() else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({
                "error": "aplikasi Antigravity tidak ditemukan",
                "antigravity": current,
            })),
        )
            .into_response();
    };
    match tokio::process::Command::new(&app)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(mut child) => {
            // Aplikasi berumur panjang; task ini menunggu dan menuai child saat
            // operator menutupnya, sehingga tidak meninggalkan zombie.
            tokio::spawn(async move {
                let _ = child.wait().await;
            });
            (
                StatusCode::ACCEPTED,
                Json(json!({
                    "started": true,
                    "note": "Jendela Antigravity dibuka. Selesaikan OAuth di sana; jika sudah login, pakai menu akun aplikasi untuk berganti akun.",
                    "antigravity": current,
                })),
            )
                .into_response()
        }
        Err(error) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({
                "error": format!("gagal membuka aplikasi Antigravity: {error}"),
                "antigravity": current,
            })),
        )
            .into_response(),
    }
}
