//! Automation: jadwal yang menjalankan waker tanpa manusia.
//!
//! Satu automation menyimpan kapan ia harus berjalan (`next_run_at`) dan apa yang
//! harus dikirim (prompt). Penjadwal adalah loop di daemon, bukan cron sistem:
//! dengan begitu riwayat, status, dan kebijakan overlap hidup di tempat yang sama
//! dengan run-nya.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::api::AppState;
use crate::cron;
use crate::runs::StartParams;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct Automation {
    pub(crate) id: String,
    pub(crate) waker_id: String,
    pub(crate) name: String,
    pub(crate) cron: String,
    pub(crate) timezone: String,
    pub(crate) enabled: bool,
    pub(crate) overlap_policy: String,
    pub(crate) prompt: Option<String>,
    /// `prompt` menjalankan engine; `command` menjalankan perintah deterministik.
    #[serde(default = "default_kind")]
    pub(crate) kind: String,
    #[serde(default)]
    pub(crate) command: Option<String>,
    pub(crate) next_run_at: Option<String>,
    pub(crate) last_run_at: Option<String>,
}

fn default_kind() -> String {
    "prompt".to_string()
}

impl Automation {
    pub(crate) fn new_id() -> String {
        format!("auto_{:x}", nanos())
    }
}

pub(crate) fn nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

/// Satu detak penjadwal: jalankan yang sudah jatuh tempo, lalu majukan jadwalnya.
pub(crate) async fn tick(state: &AppState) {
    let now = Utc::now();
    let due = state.store.due_automations(now).await;
    for automation in due {
        fire(state, automation, now).await;
    }
}

async fn fire(state: &AppState, automation: Automation, now: DateTime<Utc>) {
    // Biarkan console memuat ulang kartu automation saat tick berjalan; tanpa
    // ini Auto Pilot hanya terlihat berubah saat poll 10 detik berikutnya.
    crate::api::events::publish(json!({
        "type": "automation", "action": "fire", "id": automation.id,
    }));
    // Auto Pilot Task adalah automation, tetapi putarannya tunduk pada satu sakelar
    // global. OFF berarti tidak ada putaran baru; jadwal tetap maju supaya saat
    // dinyalakan lagi task tidak menumpuk burst dari slot yang tertinggal.
    if automation.kind == "autopilot" && !crate::api::auto_pilot::is_on() {
        let _ = state
            .store
            .record_automation_run(&automation.id, None, now, "skipped")
            .await;
        advance(state, &automation, now).await;
        return;
    }

    // Auto Pilot memakai arti "worker sibuk" yang lebih luas daripada automation
    // biasa: run obrolan atau task lain pada worker yang sama ikut dihitung.
    // Idle = langsung kirim; sibuk = skip / queue / replace sesuai pilihan task.
    if automation.kind == "autopilot" {
        let busy = state
            .runs
            .list(Some(&automation.waker_id))
            .await
            .into_iter()
            .find(|run| run.status == "running" || run.status == "queued");
        if let Some(run) = busy {
            match automation.overlap_policy.as_str() {
                "replace" => {
                    let _ = state.runs.cancel(&run.id).await;
                }
                "queue" => {}
                _ => {
                    let _ = state
                        .store
                        .record_automation_run(&automation.id, None, now, "skipped")
                        .await;
                    advance(state, &automation, now).await;
                    return;
                }
            }
        }
    }

    let waker = {
        let items = state.wakers.read().await;
        items.iter().find(|w| w.id == automation.waker_id).cloned()
    };

    let Some(waker) = waker else {
        let _ = state
            .store
            .record_automation_run(&automation.id, None, now, "failed")
            .await;
        advance(state, &automation, now).await;
        return;
    };

    // Command jobs are station work, not worker work: a paused worker must not
    // stop backups, and no engine/quota is involved.
    if automation.kind != "command" && waker.status != "active" {
        let _ = state
            .store
            .record_automation_run(&automation.id, None, now, "skipped")
            .await;
        advance(state, &automation, now).await;
        return;
    }

    if state.store.automation_is_running(&automation.id).await {
        match automation.overlap_policy.as_str() {
            "replace" => {
                if let Some(run_id) = state
                    .store
                    .running_run_for_automation(&automation.id)
                    .await
                {
                    let _ = state.runs.cancel(&run_id).await;
                }
            }
            "queue" => {}
            _ => {
                let _ = state
                    .store
                    .record_automation_run(&automation.id, None, now, "skipped")
                    .await;
                advance(state, &automation, now).await;
                return;
            }
        }
    }

    if automation.kind == "command" {
        let Some(command) = automation
            .command
            .clone()
            .filter(|value| !value.trim().is_empty())
        else {
            eprintln!("⚠ automation {}: kind=command tanpa command", automation.name);
            let _ = state
                .store
                .record_automation_run(&automation.id, None, now, "failed")
                .await;
            advance(state, &automation, now).await;
            return;
        };
        run_command(state, &automation, command, waker.workspace.clone(), now).await;
        advance(state, &automation, now).await;
        return;
    }

    let prompt = automation
        .prompt
        .clone()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "Jalankan tugas terjadwal ini.".to_string());

    let params = StartParams {
        waker_id: waker.id.clone(),
        engine_key: waker.engine_key.clone(),
        model: waker.model.clone(),
        reasoning_effort: waker.reasoning_effort.clone(),
        context_window: waker.context_window,
        prompt,
        system_prompt: waker.system_prompt.clone(),
        workspace: waker.workspace.clone(),
        answer_style: Some(waker.answer_style.clone()),
        max_output_tokens: waker.max_output_tokens,
        include_history: false,
        allow_cache: true,
        fallback_engines: Vec::new(),
        save_message: true,
    };

    if crate::approvals::needs_approval(&waker) {
        // Automation yang butuh izin tidak berjalan malam-malam tanpa manusia.
        // Baris automation_run dibuat lebih dulu agar keputusan bisa menautkannya.
        let arun = state
            .store
            .record_automation_run(&automation.id, None, now, "queued")
            .await
            .ok()
            .filter(|id| !id.is_empty());
        let payload = crate::approvals::params_payload(&params, arun.as_deref());
        if let Err(error) = state.store.create_approval("automation", &payload).await {
            eprintln!("⚠ automation {}: {error}", automation.name);
            if let Some(arun) = arun.as_deref() {
                state
                    .store
                    .update_automation_run(arun, None, "failed")
                    .await;
            }
        }
    } else {
        match state.runs.start(params).await {
            Ok(run_id) => {
                record_started_run(state, &automation.id, &run_id, now).await;
            }
            Err(error) => {
                eprintln!("⚠ automation {} gagal berangkat: {error}", automation.name);
                let _ = state
                    .store
                    .record_automation_run(&automation.id, None, now, "failed")
                    .await;
            }
        }
    }

    advance(state, &automation, now).await;
}

/// Menjalankan `kind=command` di latar belakang agar satu tick tidak tertahan
/// selama perintah berjalan; status ditulis ke baris automation_run yang sama.
pub(crate) async fn run_command(
    state: &AppState,
    automation: &Automation,
    command: String,
    workspace: String,
    now: DateTime<Utc>,
) {
    let arun = state
        .store
        .record_automation_run(&automation.id, None, now, "running")
        .await
        .ok();
    let id = automation.id.clone();
    let name = automation.name.clone();
    let state = state.clone();
    tokio::spawn(async move {
        let result = crate::workflows::run_tool(&command, &workspace).await;
        let (status, body) = match result {
            Ok(output) => ("succeeded", output),
            Err(error) => ("failed", error),
        };
        write_command_log(&id, now, &status, &body);
        if let Some(arun) = arun.as_deref() {
            state.store.update_automation_run(arun, None, status).await;
        }
        if status == "failed" {
            eprintln!("⚠ automation {name}: command gagal");
        }
        crate::api::events::publish(json!({
            "type": "automation", "action": "finished", "id": id, "status": status,
        }));
    });
}

/// Keluaran command disimpan sebagai berkas, bukan di database: bisa besar dan
/// hanya dibaca saat operator ingin memeriksa.
fn write_command_log(automation_id: &str, at: DateTime<Utc>, status: &str, body: &str) {
    let dir = crate::paths::data_dir().join("automation-runs");
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let name = format!("{automation_id}-{}-{status}.log", at.format("%Y%m%dT%H%M%SZ"));
    let _ = std::fs::write(dir.join(name), body);
}

/// Mencatat automation run setelah `start`. Run cache bisa selesai sebelum baris
/// `waker_automation_run` dibuat, jadi status akhirnya disalin dari run record.
pub(crate) async fn record_started_run(
    state: &AppState,
    automation_id: &str,
    run_id: &str,
    at: DateTime<Utc>,
) {
    let Ok(arun) = state
        .store
        .record_automation_run(automation_id, Some(run_id), at, "running")
        .await
    else {
        return;
    };
    if arun.is_empty() {
        return;
    }
    if let Some(record) = state.runs.get(run_id).await {
        if record.status != "running" && record.status != "queued" {
            state
                .store
                .update_automation_run(&arun, Some(run_id), &record.status)
                .await;
        }
    }
}

async fn advance(state: &AppState, automation: &Automation, now: DateTime<Utc>) {
    match cron::next_after(&automation.cron, now, &automation.timezone) {
        Ok(next) => {
            let _ = state.store.mark_automation(&automation.id, next).await;
        }
        Err(error) => {
            eprintln!(
                "⚠ automation {} dimatikan: {error}",
                automation.name
            );
            let _ = state.store.disable_automation(&automation.id).await;
        }
    }
}

/// Bentuk JSON untuk console: automation + nama waker + status run terakhir.
pub(crate) async fn payload(state: &AppState, automation: &Automation) -> Value {
    let waker = {
        let items = state.wakers.read().await;
        items
            .iter()
            .find(|w| w.id == automation.waker_id)
            .map(|w| (w.name.clone(), w.status.clone()))
    };
    let last = state.store.last_automation_run(&automation.id).await;
    json!({
        "id": automation.id,
        "waker_id": automation.waker_id,
        "waker_name": waker.as_ref().map(|(name, _)| name.clone()),
        "waker_status": waker.as_ref().map(|(_, status)| status.clone()),
        "name": automation.name,
        "cron": automation.cron,
        "timezone": automation.timezone,
        "enabled": automation.enabled,
        "overlap_policy": automation.overlap_policy,
        "prompt": automation.prompt,
        "kind": automation.kind,
        "command": automation.command,
        "running": state.store.automation_is_running(&automation.id).await,
        "next_run_at": automation.next_run_at,
        "last_run_at": automation.last_run_at,
        "last_status": last,
    })
}
