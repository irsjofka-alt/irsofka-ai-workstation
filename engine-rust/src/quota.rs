//! P3.3 — routing model menurut sisa kuota.
//!
//! Angka diambil dari tabel `engine_quota` (meter F8.2) dan ambangnya dari blok
//! `quota_gate` di `config/engines.json`. Modul ini tidak menjalankan probe sendiri
//! dan tidak menaksir: jendela yang tidak dilaporkan ditandai `unsupported`, bukan
//! dianggap penuh atau kosong.
//!
//! Aturan yang dipakai saat memilih engine untuk `engine_key = "auto"`:
//! 1. hanya engine yang punya `quota_gate` dan punya meter (`usage.engine`);
//! 2. setiap jendela di `quota_gate.windows` harus dilaporkan meter;
//! 3. sisa terkecil di antara jendela itu harus ≥ `quota_gate.min_fraction`
//!    (atau `STATION_QUOTA_MIN_FRACTION` bila gate tidak menyebut angkanya);
//! 4. di antara kandidat yang lolos, sisa terkecil terbesar menang.
//!
//! Engine tanpa meter (mis. Ollama) tidak pernah dipilih otomatis: konsumsinya VRAM,
//! bukan kuota, dan pagarnya ada di `config/resource_policy.json`.

use serde_json::{json, Value};
use std::fs;

use crate::paths::engines_registry_path;
use crate::store::Store;

fn default_min_fraction() -> f64 {
    std::env::var("STATION_QUOTA_MIN_FRACTION")
        .ok()
        .and_then(|value| value.parse::<f64>().ok())
        .unwrap_or(0.2)
        .clamp(0.0, 1.0)
}

/// Kelompok scope meter yang relevan untuk sebuah model. Meter Antigravity
/// melaporkan beberapa kelompok sekaligus ("Gemini Models", "Gemini 4 Argon",
/// "Claude and GPT models"); memakai kelompok yang salah membuat keputusan
/// routing terbaca benar padahal angkanya milik model lain.
fn scope_hint(model: &str) -> Option<&'static str> {
    let model = model.to_lowercase();
    if model.contains("argon") {
        Some("argon")
    } else if model.contains("claude") || model.contains("opus") || model.contains("sonnet") {
        Some("claude")
    } else if model.contains("gemini") {
        Some("gemini")
    } else {
        None
    }
}

fn scope_matches(scope: &str, hint: Option<&str>) -> bool {
    let scope = scope.to_lowercase();
    match hint {
        Some("argon") => scope.contains("argon"),
        Some("claude") => scope.contains("claude"),
        Some("gemini") => scope.contains("gemini") && !scope.contains("argon"),
        Some(_) => true,
        None => true,
    }
}

fn read_registry() -> Result<Value, String> {
    let path = engines_registry_path();
    let text = fs::read_to_string(&path)
        .map_err(|error| format!("config/engines.json tidak terbaca: {error}"))?;
    serde_json::from_str(&text).map_err(|error| format!("config/engines.json tidak sah: {error}"))
}

fn fraction_of(row: &Value) -> Option<f64> {
    row.get("remaining_fraction").and_then(Value::as_f64)
}

fn state_of(row: &Value) -> &str {
    row.get("state").and_then(Value::as_str).unwrap_or("")
}

/// Satu baris keputusan per engine yang punya meter.
fn evaluate(key: &str, spec: &Value, meters: &[Value]) -> Value {
    let role = spec.get("role").and_then(Value::as_str).unwrap_or("");
    let model = spec
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let usage_engine = spec
        .get("usage")
        .and_then(|usage| usage.get("engine"))
        .and_then(Value::as_str);
    let gate = spec.get("quota_gate");
    // Gate boleh menunjuk meter dan kelompok scope-nya sendiri (mis. claude-opus
    // memakai grup "Claude and GPT models" milik meter antigravity walau blok
    // `usage`-nya tidak ada). Bila gate menyebut scope, nama itu yang dipakai —
    // tidak ada tebakan dari nama model.
    let meter_engine = gate
        .and_then(|gate| gate.get("engine"))
        .and_then(Value::as_str)
        .or(usage_engine);
    let gate_scope = gate
        .and_then(|gate| gate.get("scope"))
        .and_then(Value::as_str);
    let gate_windows: Vec<String> = gate
        .and_then(|gate| gate.get("windows"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let min_fraction = gate
        .and_then(|gate| gate.get("min_fraction"))
        .and_then(Value::as_f64)
        .unwrap_or_else(default_min_fraction)
        .clamp(0.0, 1.0);

    let base = json!({
        "engine_key": key,
        "role": role,
        "model": model,
        "meter_engine": meter_engine,
        "gate_scope": gate_scope,
        "gate_windows": gate_windows,
        "min_fraction": min_fraction,
    });

    if gate.is_none() || gate_windows.is_empty() {
        return merge(
            base,
            "ungated",
            "tidak ada quota_gate, jadi tidak ada ambang yang bisa dipakai".to_string(),
            None,
            Vec::new(),
        );
    }
    let Some(meter_engine) = meter_engine else {
        return merge(
            base,
            "unmetered",
            "gate ini tidak menyebut meter dan engine-nya tidak punya blok usage".to_string(),
            None,
            Vec::new(),
        );
    };

    let hint = gate_scope.or_else(|| scope_hint(&model));
    let rows: Vec<&Value> = meters
        .iter()
        .filter(|row| {
            row.get("engine").and_then(Value::as_str) == Some(meter_engine)
                && row
                    .get("scope")
                    .and_then(Value::as_str)
                    .map(|scope| match gate_scope {
                        Some(exact) => scope.eq_ignore_ascii_case(exact),
                        None => scope_matches(scope, hint),
                    })
                    .unwrap_or(false)
        })
        .collect();

    let mut report = Vec::new();
    let mut missing = Vec::new();
    let mut fractions: Vec<f64> = Vec::new();
    for window in &gate_windows {
        let row = rows
            .iter()
            .find(|row| row.get("window").and_then(Value::as_str) == Some(window.as_str()));
        match row {
            Some(row) if state_of(row) == "REPORTED" && fraction_of(row).is_some() => {
                let fraction = fraction_of(row).unwrap_or(0.0);
                fractions.push(fraction);
                report.push(json!({
                    "window": window,
                    "scope": row.get("scope"),
                    "remaining_fraction": fraction,
                    "reset_at": row.get("reset_at"),
                }));
            }
            Some(row) => missing.push(format!("{window} ({})", state_of(row))),
            None => missing.push(format!("{window} (tidak dilaporkan)")),
        }
    }

    let worst = fractions.iter().cloned().fold(None::<f64>, |acc, value| {
        Some(match acc {
            Some(current) if current < value => current,
            _ => value,
        })
    });

    if !missing.is_empty() {
        return merge(
            base,
            "unsupported",
            format!("meter belum melaporkan jendela: {}", missing.join(", ")),
            worst,
            report,
        );
    }
    let worst_value = worst.unwrap_or(0.0);
    if worst_value < min_fraction {
        merge(
            base,
            "low",
            format!(
                "sisa {:.1}% di bawah ambang {:.0}%",
                worst_value * 100.0,
                min_fraction * 100.0
            ),
            worst,
            report,
        )
    } else {
        merge(
            base,
            "ok",
            format!(
                "sisa terkecil {:.1}% ≥ ambang {:.0}%",
                worst_value * 100.0,
                min_fraction * 100.0
            ),
            worst,
            report,
        )
    }
}

fn merge(
    mut base: Value,
    status: &str,
    reason: String,
    worst: Option<f64>,
    windows: Vec<Value>,
) -> Value {
    let object = base.as_object_mut().expect("base selalu objek");
    object.insert("status".into(), json!(status));
    object.insert("reason".into(), json!(reason));
    object.insert("worst_remaining".into(), json!(worst));
    object.insert("windows".into(), json!(windows));
    object.insert(
        "selectable".into(),
        json!(status == "ok" && object["gate_windows"].as_array().map(|w| !w.is_empty()).unwrap_or(false)),
    );
    base
}

/// Seluruh engine yang bisa dijalankan headless, beserta keputusan kuotanya.
pub(crate) async fn snapshot(store: &Store) -> Result<Vec<Value>, String> {
    let registry = read_registry()?;
    let meters = store.latest_quota().await;
    let engines = registry
        .get("engines")
        .and_then(Value::as_object)
        .ok_or_else(|| "config/engines.json tidak punya objek engines".to_string())?;
    let mut out: Vec<Value> = engines
        .iter()
        .map(|(key, spec)| evaluate(key, spec, &meters))
        .collect();
    out.sort_by(|a, b| {
        a["engine_key"]
            .as_str()
            .unwrap_or("")
            .cmp(b["engine_key"].as_str().unwrap_or(""))
    });
    Ok(out)
}

/// Pilih engine untuk `auto`. `role` menyaring (builder/reviewer/decider) bila diisi.
pub(crate) async fn suggest(store: &Store, role: Option<&str>) -> Result<Value, String> {
    let candidates = snapshot(store).await?;
    let role = role.map(str::trim).filter(|value| !value.is_empty());
    let mut eligible: Vec<&Value> = candidates
        .iter()
        .filter(|candidate| candidate["selectable"].as_bool().unwrap_or(false))
        .filter(|candidate| match role {
            Some(role) => candidate["role"].as_str() == Some(role),
            None => true,
        })
        .collect();
    eligible.sort_by(|a, b| {
        let left = a["worst_remaining"].as_f64().unwrap_or(-1.0);
        let right = b["worst_remaining"].as_f64().unwrap_or(-1.0);
        right
            .partial_cmp(&left)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let chosen = eligible.first().map(|candidate| (*candidate).clone());
    let reason = match &chosen {
        Some(candidate) => candidate["reason"]
            .as_str()
            .unwrap_or("")
            .to_string(),
        None => {
            let measured = candidates
                .iter()
                .filter(|candidate| candidate["status"].as_str() != Some("unmetered"))
                .count();
            if measured == 0 {
                "tidak ada engine dengan meter kuota terbaca".to_string()
            } else {
                format!(
                    "tidak ada kandidat yang lolos ambang (role={})",
                    role.unwrap_or("apa saja")
                )
            }
        }
    };
    Ok(json!({
        "engine_key": chosen.as_ref().map(|candidate| candidate["engine_key"].clone()),
        "model": chosen.as_ref().map(|candidate| candidate["model"].clone()),
        "role": role,
        "reason": reason,
        "candidates": candidates,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(scope: &str, window: &str, fraction: Option<f64>, state: &str) -> Value {
        json!({
            "engine": "antigravity",
            "scope": scope,
            "window": window,
            "state": state,
            "remaining_fraction": fraction,
            "reset_at": null,
        })
    }

    #[test]
    fn scope_mengikuti_keluarga_model() {
        assert_eq!(scope_hint("gemini-3.8-flash-high"), Some("gemini"));
        assert_eq!(scope_hint("claude-opus-5-5-high"), Some("claude"));
        assert_eq!(scope_hint("gemini-4-argon-high"), Some("argon"));
        assert_eq!(scope_hint("qwen3.5:9b"), None);
        assert!(scope_matches("Gemini Models", Some("gemini")));
        assert!(!scope_matches("Gemini 4 Argon", Some("gemini")));
        assert!(scope_matches("Claude and GPT models", Some("claude")));
    }

    #[test]
    fn gate_lolos_memakai_sisa_terkecil() {
        let spec = json!({
            "role": "reviewer",
            "model": "gemini-3.8-flash-high",
            "run_path": "agy_headless",
            "usage": { "engine": "antigravity" },
            "quota_gate": { "windows": ["5h", "weekly"], "min_fraction": 0.2 }
        });
        let meters = vec![
            row("Gemini Models", "5h", Some(0.8), "REPORTED"),
            row("Gemini Models", "weekly", Some(0.35), "REPORTED"),
        ];
        let decision = evaluate("gemini", &spec, &meters);
        assert_eq!(decision["status"], "ok");
        assert_eq!(decision["worst_remaining"], 0.35);
        assert_eq!(decision["selectable"], true);
    }

    #[test]
    fn jendela_hilang_tidak_dianggap_nol_atau_penuh() {
        let spec = json!({
            "role": "reviewer",
            "model": "gemini-3.8-flash-high",
            "run_path": "agy_headless",
            "usage": { "engine": "antigravity" },
            "quota_gate": { "windows": ["5h", "weekly"], "min_fraction": 0.2 }
        });
        let meters = vec![row("Gemini Models", "5h", Some(0.9), "REPORTED")];
        let decision = evaluate("gemini", &spec, &meters);
        assert_eq!(decision["status"], "unsupported");
        assert_eq!(decision["selectable"], false);
    }

    #[test]
    fn di_bawah_ambang_tidak_dipilih() {
        let spec = json!({
            "role": "reviewer",
            "model": "gemini-3.8-flash-high",
            "run_path": "agy_headless",
            "usage": { "engine": "antigravity" },
            "quota_gate": { "windows": ["weekly"], "min_fraction": 0.2 }
        });
        let meters = vec![row("Gemini Models", "weekly", Some(0.05), "REPORTED")];
        let decision = evaluate("gemini", &spec, &meters);
        assert_eq!(decision["status"], "low");
        assert_eq!(decision["selectable"], false);
    }

    #[test]
    fn gate_scope_dipakai_tanpa_blok_usage() {
        let spec = json!({
            "role": "decider",
            "model": "claude-opus-5-5-high",
            "quota_gate": {
                "engine": "antigravity",
                "scope": "Claude and GPT models",
                "windows": ["5h", "weekly"],
                "min_fraction": 0.2
            }
        });
        let meters = vec![
            row("Claude and GPT models", "5h", Some(0.9), "REPORTED"),
            row("Claude and GPT models", "weekly", Some(0.4), "REPORTED"),
            row("Gemini Models", "weekly", Some(0.05), "REPORTED"),
        ];
        let decision = evaluate("claude-opus", &spec, &meters);
        assert_eq!(decision["status"], "ok");
        assert_eq!(decision["worst_remaining"], 0.4);
        assert_eq!(decision["selectable"], true);
    }

    #[test]
    fn scope_model_lain_tidak_ikut_menilai() {
        let spec = json!({
            "role": "reviewer",
            "model": "gemini-3.8-flash-high",
            "run_path": "agy_headless",
            "usage": { "engine": "antigravity" },
            "quota_gate": { "windows": ["weekly"], "min_fraction": 0.2 }
        });
        let meters = vec![
            row("Gemini Models", "weekly", Some(0.5), "REPORTED"),
            row("Gemini 4 Argon", "weekly", Some(0.0), "REPORTED"),
        ];
        let decision = evaluate("gemini", &spec, &meters);
        assert_eq!(decision["status"], "ok");
    }
}
