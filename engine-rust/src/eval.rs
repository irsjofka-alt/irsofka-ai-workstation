//! P2.3 — eval harness deterministik.
//!
//! Dataset hidup di `config/eval_tasks.json`: setiap tugas punya `prompt` dan daftar
//! `checks` yang bisa diputuskan tanpa model penilai (`contains`, `max_chars`,
//! `json_fields`, `no_tool`, `tool_called`, …). Satu putaran eval menjalankan tugas
//! berurutan lewat `RunManager` yang sama dengan chat/automation, lalu menyimpan
//! keluaran, hasil tiap pemeriksaan, dan token per tugas.
//!
//! Tugas yang butuh izin tool yang belum `allow` **dilewati** dan dicatat sebagai
//! `skipped` — tidak dihitung gagal, tetapi juga tidak menaikkan skor. Skor adalah
//! `passed/total` atas tugas yang benar-benar dinilai.

use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::time::{Duration, Instant};

use crate::api::wakers::Waker;
use crate::api::AppState;
use crate::runs::{RunEvent, RunRecord, StartParams};

#[derive(Debug, Clone)]
pub(crate) struct TaskSpec {
    pub(crate) id: String,
    pub(crate) prompt: String,
    pub(crate) checks: Vec<Value>,
    pub(crate) requires_permission: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct StartRequest {
    #[serde(default)]
    pub(crate) waker_id: Option<String>,
    #[serde(default)]
    pub(crate) engine_key: Option<String>,
    #[serde(default)]
    pub(crate) model: Option<String>,
    #[serde(default)]
    pub(crate) label: Option<String>,
}

pub(crate) fn load_tasks() -> Result<Vec<TaskSpec>, String> {
    let path = crate::paths::station_dir().join("config/eval_tasks.json");
    let text = fs::read_to_string(&path)
        .map_err(|error| format!("{} tidak terbaca: {error}", path.display()))?;
    let document: Value = serde_json::from_str(&text)
        .map_err(|error| format!("{} tidak sah: {error}", path.display()))?;
    let items = document
        .get("tasks")
        .and_then(Value::as_array)
        .ok_or_else(|| "config/eval_tasks.json tidak punya array tasks".to_string())?;
    if items.is_empty() {
        return Err("dataset eval kosong".to_string());
    }
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let id = item
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| "ada task tanpa id".to_string())?
            .to_string();
        let prompt = item
            .get("prompt")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("task {id} tanpa prompt"))?
            .to_string();
        let checks = item
            .get("checks")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        if checks.is_empty() {
            return Err(format!("task {id} tidak punya checks"));
        }
        out.push(TaskSpec {
            id,
            prompt,
            checks,
            requires_permission: item
                .get("requires_permission")
                .and_then(Value::as_str)
                .map(str::to_string),
        });
    }
    Ok(out)
}

async fn resolve_waker(state: &AppState, id: Option<&str>) -> Result<Waker, String> {
    let items = state.wakers.read().await;
    match id {
        Some(id) => items
            .iter()
            .find(|waker| waker.id == id || waker.slug == id)
            .cloned()
            .ok_or_else(|| format!("worker \"{id}\" tidak ada")),
        None => items
            .iter()
            .find(|waker| waker.status == "active")
            .cloned()
            .ok_or_else(|| "tidak ada worker aktif untuk eval".to_string()),
    }
}

pub(crate) async fn start(state: &AppState, request: StartRequest) -> Result<String, String> {
    let waker = resolve_waker(state, request.waker_id.as_deref()).await?;
    let tasks = load_tasks()?;
    let engine_key = request
        .engine_key
        .unwrap_or_else(|| waker.engine_key.clone());
    // Model default diambil dari registry untuk engine yang dieval, bukan dari
    // worker — worker bisa saja memakai engine lain, dan mengirim id model engine
    // lain ke CLI hanya menghasilkan kegagalan yang tidak mengukur apa pun.
    let model = match request.model {
        Some(model) => Some(model),
        None => {
            let registry = crate::adapters::EngineRegistry::load(
                crate::paths::engines_registry_path(),
            )
            .map_err(|error| error.to_string())?;
            registry
                .get(&engine_key)
                .and_then(|spec| spec.model.clone())
                .or_else(|| waker.model.clone())
        }
    };
    let id = format!("eval_{:x}", crate::approvals::nanos());
    state
        .store
        .create_eval_run(
            &id,
            request.label.as_deref(),
            &waker.id,
            &engine_key,
            model.as_deref(),
        )
        .await?;
    let state_for_task = state.clone();
    let run_id = id.clone();
    tokio::spawn(async move {
        execute(state_for_task, run_id, waker, engine_key, model, tasks).await;
    });
    Ok(id)
}

async fn execute(
    state: AppState,
    eval_id: String,
    waker: Waker,
    engine_key: String,
    model: Option<String>,
    tasks: Vec<TaskSpec>,
) {
    let permissions = permission_map(&state.store.list_permissions(&waker.id).await);
    let mut passed = 0i32;
    let mut total = 0i32;
    let mut skipped = 0i32;
    let mut tokens_in = 0i64;
    let mut tokens_out = 0i64;
    let mut any_tokens = false;

    for task in &tasks {
        if let Some(required) = task.requires_permission.as_deref() {
            if effective_decision(&permissions, required) != "allow" {
                skipped += 1;
                state
                    .store
                    .insert_eval_case(
                        &case_id(),
                        &eval_id,
                        &task.id,
                        "skipped",
                        false,
                        &json!([]),
                        None,
                        Some("izin tool belum allow; tugas dilewati, bukan dihitung gagal"),
                        None,
                        None,
                        None,
                        0,
                    )
                    .await;
                continue;
            }
        }

        total += 1;
        let started = Instant::now();
        let params = StartParams {
            waker_id: waker.id.clone(),
            engine_key: engine_key.clone(),
            model: model.clone(),
            reasoning_effort: waker.reasoning_effort.clone(),
            context_window: waker.context_window,
            prompt: task.prompt.clone(),
            system_prompt: "Ikuti instruksi pengguna persis. Jawab singkat; jangan menambah penjelasan bila tidak diminta.".to_string(),
            workspace: waker.workspace.clone(),
            answer_style: Some("normal".to_string()),
            max_output_tokens: Some(256),
            include_history: false,
            allow_cache: false,
            fallback_engines: Vec::new(),
            save_message: true,
        };
        let case_id = case_id();
        match state.runs.start(params).await {
            Err(error) => {
                state
                    .store
                    .insert_eval_case(
                        &case_id,
                        &eval_id,
                        &task.id,
                        "failed",
                        false,
                        &json!([]),
                        None,
                        Some(&error),
                        None,
                        None,
                        None,
                        started.elapsed().as_millis() as i64,
                    )
                    .await;
            }
            Ok(run_id) => match wait_run(&state, &run_id, Duration::from_secs(180)).await {
                Err(error) => {
                    state
                        .store
                        .insert_eval_case(
                            &case_id,
                            &eval_id,
                            &task.id,
                            "failed",
                            false,
                            &json!([]),
                            None,
                            Some(&error),
                            Some(&run_id),
                            None,
                            None,
                            started.elapsed().as_millis() as i64,
                        )
                        .await;
                }
                Ok(record) => {
                    if let Some(value) = record.input_tokens {
                        tokens_in += value;
                        any_tokens = true;
                    }
                    if let Some(value) = record.output_tokens {
                        tokens_out += value;
                        any_tokens = true;
                    }
                    let (ok, checks) = evaluate_checks(&task.checks, &record.output, &record.events);
                    if ok {
                        passed += 1;
                    }
                    let output = truncate(&record.output, 2_000);
                    let reason = record.error.clone();
                    state
                        .store
                        .insert_eval_case(
                            &case_id,
                            &eval_id,
                            &task.id,
                            if ok { "passed" } else { "failed" },
                            ok,
                            &checks,
                            Some(&output),
                            reason.as_deref(),
                            Some(&run_id),
                            record.input_tokens,
                            record.output_tokens,
                            started.elapsed().as_millis() as i64,
                        )
                        .await;
                }
            },
        }
    }

    let score = if total > 0 {
        passed as f64 / total as f64
    } else {
        0.0
    };
    let input_tokens = if any_tokens { Some(tokens_in) } else { None };
    let output_tokens = if any_tokens { Some(tokens_out) } else { None };
    state
        .store
        .finish_eval_run(
            &eval_id,
            "finished",
            passed,
            total,
            skipped,
            score,
            input_tokens,
            output_tokens,
            None,
        )
        .await;
}

async fn wait_run(state: &AppState, run_id: &str, timeout: Duration) -> Result<RunRecord, String> {
    let deadline = Instant::now() + timeout;
    loop {
        tokio::time::sleep(Duration::from_millis(500)).await;
        if Instant::now() > deadline {
            return Err("eval melewati batas waktu".to_string());
        }
        let Some(record) = state.runs.get(run_id).await else {
            return Err("run eval hilang dari memori".to_string());
        };
        if record.status != "running" && record.status != "queued" {
            return Ok(record);
        }
    }
}

fn permission_map(value: &Value) -> HashMap<String, String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let tool = item.get("tool").and_then(Value::as_str)?;
                    let decision = item.get("decision").and_then(Value::as_str)?;
                    Some((tool.to_string(), decision.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Urutan yang sama dengan gerbang run: tool spesifik → server → gerbang umum.
fn effective_decision(permissions: &HashMap<String, String>, required: &str) -> String {
    let mut keys = vec![required.to_string()];
    if let Some((server, _)) = required.rsplit_once(':') {
        keys.push(server.to_string());
    }
    if let Some(head) = required.split(':').next() {
        keys.push(head.to_string());
    }
    for key in keys {
        if let Some(decision) = permissions.get(&key) {
            return decision.clone();
        }
    }
    "ask".to_string()
}

fn truncate(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    text.chars().take(max).collect()
}

fn parse_json_object(output: &str) -> Option<Value> {
    let trimmed = output.trim();
    if let Ok(value) = serde_json::from_str::<Value>(trimmed) {
        if value.is_object() {
            return Some(value);
        }
    }
    let start = trimmed.find('{')?;
    let end = trimmed.rfind('}')?;
    if end <= start {
        return None;
    }
    let value: Value = serde_json::from_str(&trimmed[start..=end]).ok()?;
    value.is_object().then_some(value)
}

fn evaluate_checks(checks: &[Value], output: &str, events: &[RunEvent]) -> (bool, Value) {
    let mut results = Vec::with_capacity(checks.len());
    let mut all = true;
    for check in checks {
        let kind = check.get("kind").and_then(Value::as_str).unwrap_or("");
        let (ok, detail) = match kind {
            "contains" => {
                let needle = check.get("value").and_then(Value::as_str).unwrap_or("");
                (
                    output.to_lowercase().contains(&needle.to_lowercase()),
                    format!("mengandung \"{needle}\""),
                )
            }
            "not_contains" => {
                let needle = check.get("value").and_then(Value::as_str).unwrap_or("");
                (
                    !output.to_lowercase().contains(&needle.to_lowercase()),
                    format!("tidak mengandung \"{needle}\""),
                )
            }
            "contains_compact" => {
                let needle = check.get("value").and_then(Value::as_str).unwrap_or("");
                let compact = |text: &str| {
                    text.chars()
                        .filter(|c| !c.is_whitespace())
                        .collect::<String>()
                        .to_lowercase()
                };
                (
                    compact(output).contains(&compact(needle)),
                    format!("mengandung \"{needle}\" (spasi diabaikan)"),
                )
            }
            "max_chars" => {
                let limit = check.get("value").and_then(Value::as_u64).unwrap_or(0) as usize;
                let used = output.trim().chars().count();
                (used <= limit, format!("{used} dari maksimum {limit} karakter"))
            }
            "json_fields" => {
                let fields: Vec<String> = check
                    .get("fields")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_string)
                            .collect()
                    })
                    .unwrap_or_default();
                match parse_json_object(output) {
                    Some(value) => {
                        let missing: Vec<&String> = fields
                            .iter()
                            .filter(|field| value.get(field.as_str()).is_none())
                            .collect();
                        (
                            missing.is_empty(),
                            if missing.is_empty() {
                                format!("JSON punya field: {}", fields.join(", "))
                            } else {
                                format!("JSON kehilangan field: {missing:?}")
                            },
                        )
                    }
                    None => (false, "keluaran bukan objek JSON".to_string()),
                }
            }
            "no_tool" => {
                let called = events.iter().any(|event| event.kind == "tool");
                (
                    !called,
                    if called {
                        "engine memanggil tool padahal dilarang".to_string()
                    } else {
                        "tidak ada panggilan tool".to_string()
                    },
                )
            }
            "tool_called" => {
                let server = check.get("server").and_then(Value::as_str).unwrap_or("");
                let tool = check.get("tool").and_then(Value::as_str).unwrap_or("");
                let marker = format!("{server}.{tool}");
                let called = events
                    .iter()
                    .filter(|event| event.kind == "tool")
                    .any(|event| event.text.contains(&marker) && event.text.contains("→ ok"));
                (
                    called,
                    if called {
                        format!("{marker} dipanggil dan berhasil")
                    } else {
                        format!("{marker} tidak dipanggil / gagal")
                    },
                )
            }
            other => (false, format!("pemeriksaan \"{other}\" tidak dikenal")),
        };
        all &= ok;
        results.push(json!({ "kind": kind, "passed": ok, "detail": detail }));
    }
    (all, Value::Array(results))
}

fn case_id() -> String {
    format!("ecase_{:x}", crate::approvals::nanos())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(kind: &str, text: &str) -> RunEvent {
        RunEvent {
            seq: 0,
            at: String::new(),
            kind: kind.to_string(),
            text: text.to_string(),
        }
    }

    #[test]
    fn mengurai_json_dari_prosa() {
        let output = "Tentu: {\"ok\": true} — selesai.";
        let value = parse_json_object(output).expect("objek JSON");
        assert_eq!(value["ok"], json!(true));
    }

    #[test]
    fn json_fields_menolak_field_hilang() {
        let checks = vec![json!({"kind": "json_fields", "fields": ["ok"]})];
        let (ok, _) = evaluate_checks(&checks, "{\"lain\": 1}", &[]);
        assert!(!ok);
    }

    #[test]
    fn batas_karakter_dihitung_setelah_trim() {
        let checks = vec![json!({"kind": "max_chars", "value": 3})];
        assert!(evaluate_checks(&checks, "  abc \n", &[]).0);
        assert!(!evaluate_checks(&checks, "abcd", &[]).0);
    }

    #[test]
    fn contains_compact_mengabaikan_spasi() {
        let checks = vec![json!({"kind": "contains_compact", "value": "A,B,C"})];
        assert!(evaluate_checks(&checks, "A, B, C", &[]).0);
        assert!(!evaluate_checks(&checks, "A, B", &[]).0);
    }

    #[test]
    fn tool_called_butuh_keberhasilan() {
        let checks = vec![json!({"kind": "tool_called", "server": "s", "tool": "t"})];
        let failed = vec![event(
            "tool",
            "s.t ({}) → gagal\nTool s.t tidak dijalankan",
        )];
        let ok = vec![event("tool", "s.t ({}) → ok\nhasil")];
        assert!(!evaluate_checks(&checks, "x", &failed).0);
        assert!(evaluate_checks(&checks, "x", &ok).0);
    }

    #[test]
    fn izin_tool_mengikuti_urutan_gerbang() {
        let mut map = HashMap::new();
        map.insert("mcp".to_string(), "allow".to_string());
        assert_eq!(
            effective_decision(&map, "mcp:local-workstation:get_hardware_telemetry"),
            "allow"
        );
        map.insert("mcp:local-workstation".to_string(), "deny".to_string());
        assert_eq!(
            effective_decision(&map, "mcp:local-workstation:get_hardware_telemetry"),
            "deny"
        );
        map.remove("mcp");
        map.remove("mcp:local-workstation");
        assert_eq!(
            effective_decision(&map, "mcp:local-workstation:get_hardware_telemetry"),
            "ask"
        );
    }
}
