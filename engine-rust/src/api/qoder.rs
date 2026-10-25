//! Login akun engine Qoder dari Console.
//!
//! `qoder login` adalah alur interaktif (membuka peramban dan menunggu). Handler ini
//! menjalankannya sebagai proses anak, menampung baris keluarannya (termasuk URL),
//! lalu memeriksa hasil sebenarnya lewat `qoder status -o json` — bukan menyimpulkan
//! dari exit code.
//!
//! `qoder logout` tidak tercantum di `--help`, tetapi benar-benar ada sebagai
//! subcommand; handler ini memakainya apa adanya.
//!
//! Identitas akun tetap disamarkan seperti di `engineinfo::redact_account`: yang
//! dikirim ke console hanya status, versi, dan sidik akun (hash) supaya dua akun
//! bisa dibedakan tanpa membocorkan email ke engine yang bisa `curl localhost`.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use std::process::Stdio;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader};

use crate::probe::run_capture;

const MAX_LINES: usize = 200;
const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);

struct LoginJob {
    running: bool,
    status: String,
    lines: Vec<String>,
    started_at: Option<String>,
    finished_at: Option<String>,
}

static JOB: OnceLock<Mutex<LoginJob>> = OnceLock::new();

fn job() -> &'static Mutex<LoginJob> {
    JOB.get_or_init(|| {
        Mutex::new(LoginJob {
            running: false,
            status: "idle".to_string(),
            lines: Vec::new(),
            started_at: None,
            finished_at: None,
        })
    })
}

fn fnv_hex(text: &str) -> String {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in text.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{:08x}", (hash ^ (hash >> 32)) as u32)
}

/// `qoder status -o json`, dipangkas ke hal yang aman ditampilkan.
fn account_snapshot() -> Value {
    let parsed: Value = run_capture("qoder", &["status", "-o", "json"])
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_else(|| json!({ "logged_in": false }));
    let logged_in = parsed
        .get("logged_in")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    // Sidik akun membuat operator bisa membedakan dua akun tanpa email ikut terkirim.
    let fingerprint = parsed
        .get("user_id")
        .or_else(|| parsed.get("email"))
        .and_then(Value::as_str)
        .map(fnv_hex);
    json!({
        "logged_in": logged_in,
        "version": parsed.get("version").cloned().unwrap_or(Value::Null),
        "allow_byok": parsed.get("allow_byok").cloned().unwrap_or(Value::Null),
        "fingerprint": fingerprint,
        "identity_redacted": true,
        "checked_at": chrono::Utc::now().to_rfc3339(),
    })
}

pub(crate) async fn account() -> Json<Value> {
    Json(json!({ "qoder": account_snapshot() }))
}

pub(crate) async fn login_status() -> Json<Value> {
    let snapshot = job().lock().map(|state| {
        (
            state.running,
            state.status.clone(),
            state.lines.clone(),
            state.started_at.clone(),
            state.finished_at.clone(),
        )
    });
    match snapshot {
        Ok((running, status, lines, started_at, finished_at)) => Json(json!({
            "running": running,
            "status": status,
            "lines": lines,
            "started_at": started_at,
            "finished_at": finished_at,
        })),
        Err(_) => Json(json!({ "running": false, "status": "unknown", "lines": [] })),
    }
}

fn push_line(line: String) {
    if line.is_empty() {
        return;
    }
    if let Ok(mut state) = job().lock() {
        state.lines.push(line);
        if state.lines.len() > MAX_LINES {
            let drop = state.lines.len() - MAX_LINES;
            state.lines.drain(0..drop);
        }
    }
}

/// Membuang escape ANSI/spinner supaya yang tampil di console bisa dibaca.
fn clean_line(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    let mut chars = line.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '\u{1b}' {
            if chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        if ch == '\r' {
            continue;
        }
        out.push(ch);
    }
    out.trim_end().to_string()
}

async fn pump<R>(reader: R)
where
    R: tokio::io::AsyncRead + Unpin,
{
    let mut lines = BufReader::new(reader).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let cleaned = clean_line(&line);
        if !cleaned.is_empty() {
            push_line(cleaned);
        }
    }
}

pub(crate) async fn login() -> Response {
    if let Ok(state) = job().lock() {
        if state.running {
            return (
                StatusCode::CONFLICT,
                Json(json!({ "error": "login sedang berjalan" })),
            )
                .into_response();
        }
    }
    let before = account_snapshot();
    if before["logged_in"].as_bool().unwrap_or(false) {
        return (
            StatusCode::CONFLICT,
            Json(json!({
                "error": "sudah login; logout dulu untuk berganti akun",
                "qoder": before,
            })),
        )
            .into_response();
    }

    {
        let mut state = match job().lock() {
            Ok(state) => state,
            Err(_) => {
                return (
                    StatusCode::SERVICE_UNAVAILABLE,
                    Json(json!({ "error": "status login tidak terbaca" })),
                )
                    .into_response()
            }
        };
        state.running = true;
        state.status = "running".to_string();
        state.lines.clear();
        state.started_at = Some(chrono::Utc::now().to_rfc3339());
        state.finished_at = None;
    }

    let mut child = match tokio::process::Command::new("qoder")
        .arg("login")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(error) => {
            if let Ok(mut state) = job().lock() {
                state.running = false;
                state.status = "failed".to_string();
                state.lines.push(format!("gagal menjalankan `qoder login`: {error}"));
                state.finished_at = Some(chrono::Utc::now().to_rfc3339());
            }
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({ "error": format!("gagal menjalankan `qoder login`: {error}") })),
            )
                .into_response();
        }
    };
    if let Some(stdout) = child.stdout.take() {
        tokio::spawn(pump(stdout));
    }
    if let Some(stderr) = child.stderr.take() {
        tokio::spawn(pump(stderr));
    }
    tokio::spawn(async move {
        let outcome = tokio::time::timeout(LOGIN_TIMEOUT, child.wait()).await;
        let status = match outcome {
            Ok(Ok(exit)) => {
                let after = account_snapshot();
                if after["logged_in"].as_bool().unwrap_or(false) {
                    "succeeded".to_string()
                } else {
                    format!(
                        "failed (exit {}); status akun tetap belum login",
                        exit.code().unwrap_or(-1)
                    )
                }
            }
            Ok(Err(error)) => format!("failed: {error}"),
            Err(_) => {
                let _ = child.kill().await;
                "failed: login melewati batas waktu".to_string()
            }
        };
        if let Ok(mut state) = job().lock() {
            state.running = false;
            state.status = status;
            state.finished_at = Some(chrono::Utc::now().to_rfc3339());
        }
    });

    (
        StatusCode::ACCEPTED,
        Json(json!({ "started": true, "note": "Ikuti alur di panel; login selesai bila status akun berubah" })),
    )
        .into_response()
}

pub(crate) async fn logout() -> Response {
    if let Ok(state) = job().lock() {
        if state.running {
            return (
                StatusCode::CONFLICT,
                Json(json!({ "error": "selesaikan login yang sedang berjalan dulu" })),
            )
                .into_response();
        }
    }
    let output = tokio::time::timeout(
        Duration::from_secs(60),
        tokio::process::Command::new("qoder")
            .arg("logout")
            .stdin(Stdio::null())
            .output(),
    )
    .await;
    let (cli_ok, detail) = match output {        Ok(Ok(result)) => {
            let mut text = String::from_utf8_lossy(&result.stdout).trim().to_string();
            let err = String::from_utf8_lossy(&result.stderr).trim().to_string();
            if !err.is_empty() {
                text = if text.is_empty() {
                    err
                } else {
                    format!("{text}\n{err}")
                };
            }
            (result.status.success(), text)
        }
        Ok(Err(error)) => (false, format!("gagal menjalankan `qoder logout`: {error}")),
        Err(_) => (false, "logout melewati batas waktu".to_string()),
    };
    let after = account_snapshot();
    let still_logged_in = after["logged_in"].as_bool().unwrap_or(false);
    // Logout bersifat idempoten: bila akun memang sudah tidak aktif, hasilnya
    // berhasil walau CLI menjawab "Not logged in" dengan exit code bukan nol.
    let success = cli_ok || !still_logged_in;
    let success = success && !still_logged_in;
    (
        if success {
            StatusCode::OK
        } else {
            StatusCode::BAD_GATEWAY
        },
        Json(json!({
            "ok": success,
            "output": detail,
            "qoder": after,
        })),
    )
        .into_response()
}
