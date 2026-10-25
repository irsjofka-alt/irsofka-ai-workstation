//! WakerFlow: alur berurutan atas langkah prompt, tool, branch, dan approval.
//!
//! Satu langkah prompt/tool = satu eksekusi yang ditunggu sampai selesai. Langkah
//! `approval` menghentikan alur sampai manusia memutuskan; setelah itu alur
//! dilanjutkan dari langkah berikutnya. `branch` melompat berdasarkan keluaran
//! langkah sebelumnya. `max_retries` mengulang langkah yang gagal sebelum alur
//! dinyatakan gagal.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::process::Stdio;
use std::time::Duration;

use crate::api::AppState;
use crate::runs::StartParams;

#[derive(Debug, Clone, Serialize)]
pub(crate) struct Workflow {
    pub(crate) id: String,
    pub(crate) waker_id: String,
    pub(crate) name: String,
    pub(crate) version: i32,
    pub(crate) steps: Vec<WorkflowStep>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct WorkflowStep {
    pub(crate) ord: i32,
    pub(crate) name: String,
    /// `prompt` | `tool` | `branch` | `approval`
    pub(crate) kind: String,
    pub(crate) prompt: Option<String>,
    pub(crate) command: Option<String>,
    pub(crate) branch_contains: Option<String>,
    pub(crate) branch_goto: Option<i32>,
    pub(crate) max_retries: i32,
    pub(crate) on_failure: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct WorkflowRun {
    pub(crate) id: String,
    pub(crate) workflow_id: String,
    pub(crate) status: String,
    pub(crate) current_step: i32,
    pub(crate) log: Value,
    pub(crate) error: Option<String>,
    pub(crate) started_at: Option<String>,
    pub(crate) finished_at: Option<String>,
}

impl WorkflowRun {
    pub(crate) fn new_id() -> String {
        format!("wfr_{:x}", nanos())
    }
}

pub(crate) fn nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

pub(crate) async fn start(state: &AppState, workflow: Workflow) -> Result<String, String> {
    let run = WorkflowRun {
        id: WorkflowRun::new_id(),
        workflow_id: workflow.id.clone(),
        status: "queued".to_string(),
        current_step: 0,
        log: json!([]),
        error: None,
        started_at: None,
        finished_at: None,
    };
    state.store.create_workflow_run(&run).await?;
    let state = state.clone();
    let run_id = run.id.clone();
    tokio::spawn(async move {
        execute(state, run_id, 0).await;
    });
    Ok(run.id)
}

pub(crate) async fn on_decision(state: &AppState, run_id: &str, approved: bool) {
    if !approved {
        if let Some(mut run) = state.store.get_workflow_run(run_id).await {
            run.status = "cancelled".to_string();
            run.error = Some("ditolak pada langkah persetujuan".to_string());
            run.finished_at = Some(now());
            state.store.update_workflow_run(&run).await;
        }
        return;
    }
    let Some(run) = state.store.get_workflow_run(run_id).await else {
        return;
    };
    let next = (run.current_step + 1).max(0);
    let state = state.clone();
    let run_id = run_id.to_string();
    tokio::spawn(async move {
        execute(state, run_id, next).await;
    });
}

async fn execute(state: AppState, run_id: String, start_index: i32) {
    let Some(mut run) = state.store.get_workflow_run(&run_id).await else {
        return;
    };
    let Some(workflow) = state.store.get_workflow(&run.workflow_id).await else {
        fail(&state, &mut run, "definisi workflow hilang").await;
        return;
    };
    let Some(waker) = ({
        let items = state.wakers.read().await;
        items.iter().find(|w| w.id == workflow.waker_id).cloned()
    }) else {
        fail(&state, &mut run, "worker pemilik workflow tidak ada").await;
        return;
    };

    if run.started_at.is_none() {
        run.started_at = Some(now());
    }
    run.status = "running".to_string();
    state.store.update_workflow_run(&run).await;

    let steps = workflow.steps.clone();
    let mut last_output = String::new();
    // Setelah approval, proses baru dimulai: pulihkan konteks dari log supaya
    // langkah berikutnya tidak kehilangan keluaran langkah sebelumnya.
    let mut last_context = resume_context(&run.log);
    let mut index = start_index;

    while index < steps.len() as i32 {
        if cancelled(&state, &run.id).await {
            return;
        }
        let step = steps[index as usize].clone();
        run.current_step = index;
        state.store.update_workflow_run(&run).await;

        match step.kind.as_str() {
            "approval" => {
                let payload = json!({
                    "workflow_run_id": run.id,
                    "step": index,
                    "waker_id": workflow.waker_id,
                    "engine_key": waker.engine_key,
                    "model": waker.model,
                    "prompt": step.prompt.clone().unwrap_or_else(|| step.name.clone()),
                });
                match state.store.create_approval("workflow", &payload).await {
                    Ok(_) => {
                        run.status = "waiting_approval".to_string();
                        push_log(&mut run, index, &step.name, "waiting", "");
                        state.store.update_workflow_run(&run).await;
                    }
                    Err(error) => fail(&state, &mut run, &error).await,
                }
                return;
            }
            "branch" => {
                if let Some(needle) = step.branch_contains.as_deref() {
                    if last_output.contains(needle) {
                        if let Some(goto) = step.branch_goto {
                            if goto >= 1 && goto <= steps.len() as i32 {
                                push_log(
                                    &mut run,
                                    index,
                                    &step.name,
                                    "jump",
                                    &format!("mengandung \"{needle}\" → langkah {goto}"),
                                );
                                state.store.update_workflow_run(&run).await;
                                // Langsung lompat; `continue` melewati increment di bawah,
                                // supaya langkah tujuan benar-benar dieksekusi.
                                index = goto - 1;
                                continue;
                            }
                        }
                    }
                }
                push_log(&mut run, index, &step.name, "continue", "kondisi tidak terpenuhi");
                state.store.update_workflow_run(&run).await;
            }
            "tool" => {
                let command = step
                    .command
                    .clone()
                    .filter(|value| !value.trim().is_empty())
                    .unwrap_or_else(|| step.name.clone());
                let decision = state
                    .store
                    .get_permission(&waker.id, "shell")
                    .await
                    .unwrap_or_else(|| "allow".to_string());
                match decision.as_str() {
                    "deny" => {
                        fail(
                            &state,
                            &mut run,
                            "langkah tool diblokir oleh izin worker (shell: deny)",
                        )
                        .await;
                        return;
                    }
                    "ask" => {
                        let payload = json!({
                            "workflow_run_id": run.id,
                            "step": index,
                            "kind": "tool",
                            "waker_id": workflow.waker_id,
                            "command": command,
                            "workspace": waker.workspace,
                            "prompt": format!("Tool: {command}"),
                        });
                        match state.store.create_approval("workflow-tool", &payload).await {
                            Ok(_) => {
                                run.status = "waiting_approval".to_string();
                                push_log(
                                    &mut run,
                                    index,
                                    &step.name,
                                    "waiting",
                                    "menunggu izin tool",
                                );
                                state.store.update_workflow_run(&run).await;
                            }
                            Err(error) => fail(&state, &mut run, &error).await,
                        }
                        return;
                    }
                    _ => {}
                }
                match with_retries(&state, &step, || run_tool(&command, &waker.workspace)).await {
                    Ok(output) => {
                        let (log_text, context) =
                            prepare_step_output(&run.id, index, &step.name, &output);
                        last_output = output;
                        last_context = context;
                        push_log(&mut run, index, &step.name, "succeeded", &log_text);
                        state.store.update_workflow_run(&run).await;
                    }
                    Err(error) => {
                        fail(&state, &mut run, &error).await;
                        return;
                    }
                }
            }
            _ => {
                let base = step
                    .prompt
                    .clone()
                    .filter(|value| !value.trim().is_empty())
                    .unwrap_or_else(|| step.name.clone());
                // Langkah berikutnya menerima ringkasan (+ path artifact), bukan
                // seluruh keluaran panjang.
                let prompt = if last_context.trim().is_empty() {
                    base
                } else {
                    format!("{last_context}\n\n{base}")
                };
                let outcome = with_retries(&state, &step, || {
                    run_prompt(&state, &waker, &prompt)
                })
                .await;
                match outcome {
                    Ok(output) => {
                        let (log_text, context) =
                            prepare_step_output(&run.id, index, &step.name, &output);
                        last_output = output;
                        last_context = context;
                        push_log(&mut run, index, &step.name, "succeeded", &log_text);
                        state.store.update_workflow_run(&run).await;
                    }
                    Err(error) => {
                        fail(&state, &mut run, &error).await;
                        return;
                    }
                }
            }
        }

        if cancelled(&state, &run.id).await {
            return;
        }
        index += 1;
    }

    run.status = "succeeded".to_string();
    run.finished_at = Some(now());
    state.store.update_workflow_run(&run).await;
}

pub(crate) async fn run_prompt(
    state: &AppState,
    waker: &crate::api::wakers::Waker,
    prompt: &str,
) -> Result<String, String> {
    run_prompt_with_run(state, waker, prompt)
        .await
        .map(|(_, output)| output)
}

pub(crate) async fn run_prompt_with_run(
    state: &AppState,
    waker: &crate::api::wakers::Waker,
    prompt: &str,
) -> Result<(String, String), String> {
    let run_id = state
        .runs
        .start(StartParams {
            waker_id: waker.id.clone(),
            engine_key: waker.engine_key.clone(),
            model: waker.model.clone(),
            reasoning_effort: waker.reasoning_effort.clone(),
            context_window: waker.context_window,
            prompt: prompt.to_string(),
            system_prompt: waker.system_prompt.clone(),
            workspace: waker.workspace.clone(),
            answer_style: Some(waker.answer_style.clone()),
            max_output_tokens: waker.max_output_tokens,
            include_history: false,
            allow_cache: false,
            fallback_engines: Vec::new(),
            save_message: true,
        })
        .await?;

    for _ in 0..(60 * 60 * 2) {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let Some(record) = state.runs.get(&run_id).await else {
            return Err("run langkah hilang dari memori".to_string());
        };
        match record.status.as_str() {
            "succeeded" => return Ok((run_id, record.output)),
            "failed" => {
                return Err(record
                    .error
                    .unwrap_or_else(|| "langkah gagal".to_string()))
            }
            _ => {}
        }
    }
    Err("langkah melewati batas waktu".to_string())
}

/// Menjalankan perintah shell di workspace worker. Keluaran dibatasi 2 MB,
/// batas waktu 5 menit, dan seluruh process group dibunuh saat timeout.
pub(crate) async fn run_tool(command: &str, workspace: &str) -> Result<String, String> {
    let mut child = tokio::process::Command::new("sh")
        .arg("-c")
        .arg(format!("{command} 2>&1"))
        .current_dir(workspace)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .process_group(0)
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| format!("gagal menjalankan tool: {error}"))?;

    let pid = child.id();
    let stdout = child.stdout.take();
    let reader = tokio::spawn(async move {
        use tokio::io::AsyncReadExt;
        let mut text = String::new();
        if let Some(mut out) = stdout {
            let mut buffer = vec![0u8; 8192];
            loop {
                match out.read(&mut buffer).await {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        text.push_str(&String::from_utf8_lossy(&buffer[..n]));
                        if text.len() > 2 * 1024 * 1024 {
                            break;
                        }
                    }
                }
            }
        }
        text
    });

    match tokio::time::timeout(Duration::from_secs(300), child.wait()).await {
        Ok(Ok(status)) => {
            let output = reader.await.unwrap_or_default();
            if status.success() {
                Ok(output)
            } else {
                Err(format!(
                    "tool keluar dengan kode {}: {}",
                    status.code().unwrap_or(-1),
                    output.chars().take(500).collect::<String>()
                ))
            }
        }
        Ok(Err(error)) => Err(error.to_string()),
        Err(_) => {
            kill_group(pid);
            let _ = child.kill().await;
            Err("tool melewati batas waktu 300 detik".to_string())
        }
    }
}

async fn with_retries<F, Fut>(state: &AppState, step: &WorkflowStep, mut attempt: F) -> Result<String, String>
where
    F: FnMut() -> Fut,
    Fut: std::future::Future<Output = Result<String, String>>,
{
    let mut tries = 0;
    loop {
        match attempt().await {
            Ok(output) => return Ok(output),
            Err(error) => {
                if tries >= step.max_retries.max(0) {
                    return Err(error);
                }
                tries += 1;
                let _ = state; // penanda: retry tetap tercatat lewat log langkah berikut
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        }
    }
}

async fn cancelled(state: &AppState, run_id: &str) -> bool {
    state
        .store
        .get_workflow_run(run_id)
        .await
        .map(|run| run.status == "cancelled")
        .unwrap_or(false)
}

async fn fail(state: &AppState, run: &mut WorkflowRun, error: &str) {
    run.status = "failed".to_string();
    run.error = Some(error.to_string());
    run.finished_at = Some(now());
    push_log(
        run,
        run.current_step,
        "gagal",
        "failed",
        error,
    );
    state.store.update_workflow_run(run).await;
}

/// Dipakai inbox persetujuan setelah tool yang menunggu izin dijalankan.
pub(crate) async fn append_log(
    state: &AppState,
    run_id: &str,
    name: &str,
    status: &str,
    text: &str,
) {
    if let Some(mut run) = state.store.get_workflow_run(run_id).await {
        let step = run.current_step;
        push_log(&mut run, step, name, status, text);
        state.store.update_workflow_run(&run).await;
    }
}

pub(crate) async fn fail_external(state: &AppState, run_id: &str, error: &str) {
    if let Some(mut run) = state.store.get_workflow_run(run_id).await {
        run.status = "failed".to_string();
        run.error = Some(error.to_string());
        run.finished_at = Some(now());
        let step = run.current_step;
        push_log(&mut run, step, "tool", "failed", error);
        state.store.update_workflow_run(&run).await;
    }
}

/// Keluaran langkah panjang disimpan sebagai artifact; langkah berikutnya hanya
/// menerima ringkasan dan path. Ringkasan tetap dibatasi agar prompt tidak
/// membawa ulang seluruh teks.
fn prepare_step_output(run_id: &str, step: i32, name: &str, output: &str) -> (String, String) {
    const CONTEXT_CHARS: usize = 2_000;
    const SUMMARY_CHARS: usize = 800;

    let count = output.chars().count();
    if count <= CONTEXT_CHARS {
        return (
            output.to_string(),
            format!("Keluaran langkah sebelumnya ({name}):\n{output}"),
        );
    }

    let summary = crate::compress::compact(output, SUMMARY_CHARS);
    let path = artifact_path(run_id, step, name);
    let saved = if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).is_ok() && std::fs::write(&path, output).is_ok()
    } else {
        false
    };

    if saved {
        (
            format!("{count} karakter; artifact: {}", path.display()),
            format!(
                "Keluaran langkah sebelumnya ({name}) disimpan di {}.\nRingkasan:\n{summary}",
                path.display()
            ),
        )
    } else {
        (
            crate::compress::compact(output, SUMMARY_CHARS),
            format!("Keluaran langkah sebelumnya ({name}):\n{summary}"),
        )
    }
}

fn artifact_path(run_id: &str, step: i32, name: &str) -> std::path::PathBuf {
    let safe: String = name
        .chars()
        .map(|ch| {
            if ch.is_alphanumeric() {
                ch.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    let safe = safe.trim_matches('-');
    let safe: String = if safe.is_empty() {
        "step".to_string()
    } else {
        safe.chars().take(40).collect()
    };
    crate::paths::data_dir()
        .join("artifacts")
        .join(run_id)
        .join(format!("step-{step}-{safe}.md"))
}

/// Membangun kembali konteks langkah terakhir dari log workflow. Dipakai saat
/// alur dilanjutkan setelah approval; proses sebelumnya sudah tidak ada di memori.
fn resume_context(log: &Value) -> String {
    let Some(items) = log.as_array() else {
        return String::new();
    };
    let Some(entry) = items
        .iter()
        .rev()
        .find(|entry| entry.get("status").and_then(Value::as_str) == Some("succeeded"))
    else {
        return String::new();
    };
    let name = entry
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("langkah sebelumnya");
    let text = entry
        .get("text")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if let Some((_, path)) = text.split_once("; artifact: ") {
        let path = path.trim();
        if let Ok(full) = std::fs::read_to_string(path) {
            return format!(
                "Keluaran langkah sebelumnya ({name}) disimpan di {path}.\nRingkasan:\n{}",
                crate::compress::compact(&full, 800)
            );
        }
    }
    if text.trim().is_empty() {
        String::new()
    } else {
        format!("Keluaran langkah sebelumnya ({name}):\n{text}")
    }
}

fn push_log(run: &mut WorkflowRun, step: i32, name: &str, status: &str, text: &str) {
    let entry = json!({
        "step": step,
        "name": name,
        "status": status,
        "at": now(),
        "text": text.chars().take(2_000).collect::<String>(),
    });
    if let Some(items) = run.log.as_array_mut() {
        items.push(entry);
    } else {
        run.log = json!([entry]);
    }
}

fn kill_group(pid: Option<u32>) {
    let Some(pid) = pid else {
        return;
    };
    unsafe {
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
}

fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resume_context_memakai_langkah_sukses_terakhir() {
        let log = json!([
            {"name": "Lama", "status": "succeeded", "text": "hasil lama"},
            {"name": "Baru", "status": "succeeded", "text": "hasil baru"},
            {"name": "Gagal", "status": "failed", "text": "error"}
        ]);
        let context = resume_context(&log);
        assert!(context.contains("Baru"));
        assert!(context.contains("hasil baru"));
    }

    #[test]
    fn nama_artifact_tidak_bisa_keluar_direktori() {
        let path = artifact_path("wfr_abc", 2, "../../etc/passwd");
        let text = path.to_string_lossy();
        assert!(text.contains("/wfr_abc/step-2-"));
        assert!(!text.contains(".."));
    }
}
