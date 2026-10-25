//! Katalog model milik QoderWake.
//!
//! Sumber kebenaran adalah `qoderwake models list --json`, bukan `config/engines.json`.
//! Registry station hanya tahu "mesin apa yang bisa dijalankan"; ia tidak tahu model
//! mana yang sedang tersedia, berapa context window-nya, atau thinking level apa yang
//! didukung. QoderWake tahu ketiganya, dan katalognya berubah tanpa kita menyentuh kode —
//! karena itu daftar ini dipungut ulang secara berkala, bukan ditulis tangan.
//!
//! Hasilnya dinormalisasi supaya console tidak perlu memahami bentuk mentah QoderWake:
//! `contexts` sudah terurut (200K -> 400K -> 1M), `efforts` sudah terurut dari ringan
//! ke berat, dan default ditandai.

use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tokio::process::Command;

use crate::paths::home_dir;

const TTL: Duration = Duration::from_secs(300);
const FETCH_TIMEOUT: Duration = Duration::from_secs(25);

/// Urutan effort dari yang paling ringan. Dipakai untuk mengurutkan `efforts`
/// apa pun urutan asli di katalog.
const EFFORT_ORDER: [&str; 6] = ["low", "medium", "high", "xhigh", "max", "auto"];

static CACHE: Mutex<Option<(Instant, Value)>> = Mutex::new(None);

/// Katalog ternormalisasi. `force` melewati cache (tombol "muat ulang").
pub(crate) async fn catalog(force: bool) -> Result<Value, String> {
    if !force {
        if let Some(value) = cached() {
            return Ok(value);
        }
    }
    let value = fetch().await?;
    store(value.clone());
    Ok(value)
}

fn cached() -> Option<Value> {
    let guard = CACHE.lock().ok()?;
    let (at, value) = guard.as_ref()?;
    if at.elapsed() < TTL {
        Some(value.clone())
    } else {
        None
    }
}

fn store(value: Value) {
    if let Ok(mut guard) = CACHE.lock() {
        *guard = Some((Instant::now(), value));
    }
}

async fn fetch() -> Result<Value, String> {
    let bin = qoderwake_bin();
    let output = tokio::time::timeout(
        FETCH_TIMEOUT,
        Command::new(&bin)
            .args(["models", "list", "--json"])
            .stdin(Stdio::null())
            .output(),
    )
    .await
    .map_err(|_| format!("{} tidak menjawab dalam {} detik", bin.display(), FETCH_TIMEOUT.as_secs()))?
    .map_err(|e| format!("gagal menjalankan {}: {e}", bin.display()))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let message = stderr.lines().next().unwrap_or("keluar tanpa pesan").trim();
        return Err(format!("qoderwake menolak permintaan: {message}"));
    }

    let raw: Vec<Value> = serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("keluaran katalog bukan JSON: {e}"))?;
    let models: Vec<Value> = raw.iter().map(compact).collect();

    Ok(json!({
        "source": "qoderwake",
        "fetched_at": chrono::Utc::now().to_rfc3339(),
        "count": models.len(),
        "models": models,
    }))
}

/// Membuang detail yang tidak dipakai console, dan mengurutkan yang tersisa.
fn compact(model: &Value) -> Value {
    let contexts = model
        .get("context_config")
        .and_then(Value::as_object)
        .map(|map| {
            let mut items: Vec<(u64, String, bool)> = map
                .iter()
                .filter_map(|(key, entry)| {
                    let tokens = entry.get("token_count").and_then(Value::as_u64)?;
                    let default = entry
                        .get("is_default")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    Some((tokens, key.clone(), default))
                })
                .collect();
            items.sort_by_key(|(tokens, _, _)| *tokens);
            items
                .into_iter()
                .map(|(tokens, key, default)| {
                    json!({ "key": key, "tokens": tokens, "default": default })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let thinking = model.get("thinking_config").and_then(|t| t.get("enabled"));
    let thinking_enabled = thinking
        .and_then(|t| t.get("is_default"))
        .and_then(Value::as_bool)
        .unwrap_or(thinking.is_some());

    let mut efforts: Vec<String> = thinking
        .and_then(|t| t.get("efforts"))
        .and_then(Value::as_object)
        .map(|map| map.keys().cloned().collect())
        .unwrap_or_default();
    efforts.sort_by_key(|level| {
        EFFORT_ORDER
            .iter()
            .position(|known| known == level)
            .unwrap_or(EFFORT_ORDER.len())
    });
    let default_effort = thinking
        .and_then(|t| t.get("efforts"))
        .and_then(Value::as_object)
        .and_then(|map| {
            map.iter()
                .find(|(_, entry)| entry.get("is_default").and_then(Value::as_bool) == Some(true))
                .map(|(level, _)| level.clone())
        });

    json!({
        "id": model.get("id").cloned().unwrap_or(Value::Null),
        "name": model.get("name").cloned().unwrap_or(Value::Null),
        "display_name": model
            .get("displayName")
            .cloned()
            .or_else(|| model.get("name").cloned())
            .unwrap_or(Value::Null),
        "description": model.get("description").cloned().unwrap_or(Value::Null),
        "is_default": model.get("isDefault").and_then(Value::as_bool).unwrap_or(false),
        "is_enabled": model.get("isEnabled").and_then(Value::as_bool).unwrap_or(true),
        "is_free": model.get("isFree").and_then(Value::as_bool).unwrap_or(false),
        "is_new": model.get("isNew").and_then(Value::as_bool).unwrap_or(false),
        "price_factor": model.get("priceFactor").cloned().unwrap_or(Value::Null),
        "contexts": contexts,
        "thinking": thinking_enabled && !efforts.is_empty(),
        "efforts": efforts,
        "default_effort": default_effort,
    })
}

/// Mencari biner qoderwake: env, PATH, symlink umum, lalu generasi runtime terbaru.
/// Path generasi memakai huruf (A, B, ...) yang berubah saat upgrade, jadi tidak
/// boleh ditulis sebagai konstan.
fn qoderwake_bin() -> PathBuf {
    if let Ok(explicit) = std::env::var("QODERWAKE_BIN") {
        if !explicit.is_empty() {
            return PathBuf::from(explicit);
        }
    }
    if let Ok(path) = std::env::var("PATH") {
        for dir in path.split(':') {
            let candidate = PathBuf::from(dir).join("qoderwake");
            if candidate.is_file() {
                return candidate;
            }
        }
    }
    let home = home_dir();
    for candidate in [
        home.join(".local/bin/qoderwake"),
        home.join(".qoderwake/bin/qoderwake"),
    ] {
        if candidate.is_file() {
            return candidate;
        }
    }

    let generations = home.join(".qoderwake/runtime-generations");
    if let Ok(entries) = std::fs::read_dir(&generations) {
        let mut newest: Option<(std::time::SystemTime, PathBuf)> = None;
        for entry in entries.flatten() {
            let candidate = entry.path().join("qoderwake");
            if !candidate.is_file() {
                continue;
            }
            let modified = entry
                .metadata()
                .and_then(|meta| meta.modified())
                .unwrap_or(std::time::UNIX_EPOCH);
            if newest.as_ref().map(|(at, _)| modified > *at).unwrap_or(true) {
                newest = Some((modified, candidate));
            }
        }
        if let Some((_, path)) = newest {
            return path;
        }
    }

    PathBuf::from("qoderwake")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalisasi_context_dan_effort() {
        let raw = json!({
            "id": "qfmodel",
            "name": "Qwen3.8-Flash",
            "displayName": "Qwen3.8-Flash",
            "context_config": {
                "1M": { "token_count": 1000000 },
                "200K": { "token_count": 200000, "is_default": true },
                "400K": { "token_count": 400000 }
            },
            "thinking_config": {
                "enabled": {
                    "efforts": {
                        "xhigh": {}, "low": {}, "medium": { "is_default": true }
                    },
                    "is_default": true
                }
            }
        });
        let compact = compact(&raw);
        let contexts = compact["contexts"].as_array().unwrap();
        assert_eq!(contexts[0]["key"], "200K");
        assert_eq!(contexts[2]["key"], "1M");
        assert_eq!(contexts[0]["default"], true);
        let efforts = compact["efforts"].as_array().unwrap();
        assert_eq!(efforts, &vec![json!("low"), json!("medium"), json!("xhigh")]);
        assert_eq!(compact["default_effort"], "medium");
        assert_eq!(compact["thinking"], true);
    }

    #[test]
    fn model_tanpa_thinking_tetap_sah() {
        let raw = json!({
            "id": "fast",
            "name": "Fast",
            "context_config": { "200K": { "token_count": 200000, "is_default": true } }
        });
        let compact = compact(&raw);
        assert_eq!(compact["thinking"], false);
        assert_eq!(compact["efforts"].as_array().unwrap().len(), 0);
        assert_eq!(compact["contexts"][0]["tokens"], 200000);
    }
}
