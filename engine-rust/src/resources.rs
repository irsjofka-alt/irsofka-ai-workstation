//! Penjaga sumber daya GPU.
//!
//! Model lokal (ringkasan, embedding) hanya boleh berjalan bila VRAM cukup dan
//! tidak ada render yang sedang memakai GPU. Pemeriksaan dilakukan sebelum setiap
//! pemakaian, lalu hasilnya dikembalikan ke console supaya operator tahu kenapa
//! sebuah pekerjaan memakai fallback deterministik.

use serde::{Deserialize, Serialize};
use std::process::Stdio;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct ResourcePolicy {
    #[serde(default)]
    pub(crate) gpu: GpuPolicy,
    #[serde(default)]
    pub(crate) models: ModelsPolicy,
    #[serde(default)]
    pub(crate) fallback: FallbackPolicy,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct GpuPolicy {
    #[serde(default = "default_min_free_llm")]
    pub(crate) min_free_llm_gb: f32,
    #[serde(default = "default_min_free_embedding")]
    pub(crate) min_free_embedding_gb: f32,
    #[serde(default = "default_busy_processes")]
    pub(crate) busy_processes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct ModelsPolicy {
    #[serde(default)]
    pub(crate) summary: SummaryModelPolicy,
    #[serde(default)]
    pub(crate) summary_quality: QualityModelPolicy,
    #[serde(default)]
    pub(crate) embedding: EmbeddingModelPolicy,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct SummaryModelPolicy {
    #[serde(default = "default_summary_model")]
    pub(crate) model: String,
    #[serde(default = "default_keep_alive")]
    pub(crate) keep_alive: String,
    #[serde(default = "default_num_ctx")]
    pub(crate) num_ctx: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct QualityModelPolicy {
    #[serde(default = "default_quality_model")]
    pub(crate) model: String,
    #[serde(default = "default_quality_free")]
    pub(crate) min_free_gb: f32,
    #[serde(default)]
    pub(crate) enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct EmbeddingModelPolicy {
    #[serde(default = "default_embedding_model")]
    pub(crate) model: String,
    #[serde(default = "default_keep_alive")]
    pub(crate) keep_alive: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct FallbackPolicy {
    #[serde(default = "default_summary_fallback")]
    pub(crate) summary: String,
    #[serde(default = "default_embedding_fallback")]
    pub(crate) embedding: String,
}

fn default_min_free_llm() -> f32 {
    2.0
}
fn default_min_free_embedding() -> f32 {
    1.0
}
fn default_busy_processes() -> Vec<String> {
    vec![
        "comfyui-desktop".to_string(),
        "ComfyUI".to_string(),
        "UnrealEditor".to_string(),
    ]
}
fn default_summary_model() -> String {
    "tev1:0.8b".to_string()
}
fn default_quality_model() -> String {
    "qwen3.5:4b".to_string()
}
fn default_keep_alive() -> String {
    "0s".to_string()
}
fn default_num_ctx() -> u32 {
    4096
}
fn default_quality_free() -> f32 {
    5.0
}
fn default_embedding_model() -> String {
    "nomic-embed-text".to_string()
}
fn default_summary_fallback() -> String {
    "deterministic".to_string()
}
fn default_embedding_fallback() -> String {
    "keyword".to_string()
}

impl Default for GpuPolicy {
    fn default() -> Self {
        Self {
            min_free_llm_gb: default_min_free_llm(),
            min_free_embedding_gb: default_min_free_embedding(),
            busy_processes: default_busy_processes(),
        }
    }
}

impl Default for SummaryModelPolicy {
    fn default() -> Self {
        Self {
            model: default_summary_model(),
            keep_alive: default_keep_alive(),
            num_ctx: default_num_ctx(),
        }
    }
}

impl Default for QualityModelPolicy {
    fn default() -> Self {
        Self {
            model: default_quality_model(),
            min_free_gb: default_quality_free(),
            enabled: false,
        }
    }
}

impl Default for EmbeddingModelPolicy {
    fn default() -> Self {
        Self {
            model: default_embedding_model(),
            keep_alive: default_keep_alive(),
        }
    }
}

impl Default for FallbackPolicy {
    fn default() -> Self {
        Self {
            summary: default_summary_fallback(),
            embedding: default_embedding_fallback(),
        }
    }
}

impl Default for ModelsPolicy {
    fn default() -> Self {
        Self {
            summary: SummaryModelPolicy::default(),
            summary_quality: QualityModelPolicy::default(),
            embedding: EmbeddingModelPolicy::default(),
        }
    }
}

impl Default for ResourcePolicy {
    fn default() -> Self {
        Self {
            gpu: GpuPolicy::default(),
            models: ModelsPolicy::default(),
            fallback: FallbackPolicy::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct LoadedModel {
    pub(crate) name: String,
    pub(crate) size: String,
    pub(crate) processor: String,
    pub(crate) until: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct Snapshot {
    pub(crate) gpu_available: bool,
    pub(crate) gpu_name: String,
    pub(crate) used_mb: u64,
    pub(crate) total_mb: u64,
    pub(crate) free_mb: u64,
    pub(crate) loaded_models: Vec<LoadedModel>,
    pub(crate) busy_processes: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct Decision {
    pub(crate) summary_allowed: bool,
    pub(crate) summary_model: String,
    pub(crate) summary_reason: String,
    pub(crate) embedding_allowed: bool,
    pub(crate) embedding_reason: String,
}

static CACHE: OnceLock<tokio::sync::Mutex<Option<(Instant, Snapshot)>>> = OnceLock::new();

pub(crate) fn policy() -> (ResourcePolicy, String) {
    let path = crate::paths::config_dir().join("resource_policy.json");
    match std::fs::read_to_string(&path) {
        Ok(text) => match serde_json::from_str::<ResourcePolicy>(&text) {
            Ok(policy) => (policy, path.display().to_string()),
            Err(error) => (
                ResourcePolicy::default(),
                format!("default (resource_policy.json tidak valid: {error})"),
            ),
        },
        Err(_) => (
            ResourcePolicy::default(),
            "default (resource_policy.json belum ada)".to_string(),
        ),
    }
}

/// Snapshot GPU dipakai bersama oleh guard ringkasan dan embedding. Cache 3 detik
/// cukup untuk mencegah `nvidia-smi`/`ps` dipanggil berkali-kali dalam satu run.
pub(crate) async fn snapshot() -> Snapshot {
    let cache = CACHE.get_or_init(|| tokio::sync::Mutex::new(None));
    let mut guard = cache.lock().await;
    if let Some((at, snap)) = guard.as_ref() {
        if at.elapsed() < Duration::from_secs(3) {
            return snap.clone();
        }
    }
    let snap = read_snapshot().await;
    *guard = Some((Instant::now(), snap.clone()));
    snap
}

async fn read_snapshot() -> Snapshot {
    let (policy, _) = policy();
    let mut snap = Snapshot {
        gpu_available: false,
        gpu_name: String::new(),
        used_mb: 0,
        total_mb: 0,
        free_mb: 0,
        loaded_models: Vec::new(),
        busy_processes: Vec::new(),
    };

    if let Ok(output) = tokio::process::Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .await
    {
        if output.status.success() {
            if let Some(line) = String::from_utf8_lossy(&output.stdout).lines().next() {
                let parts: Vec<&str> = line.split(',').map(str::trim).collect();
                if parts.len() >= 3 {
                    snap.gpu_available = true;
                    snap.gpu_name = parts[0].to_string();
                    snap.used_mb = parts[1].parse().unwrap_or(0);
                    snap.total_mb = parts[2].parse().unwrap_or(0);
                    snap.free_mb = snap.total_mb.saturating_sub(snap.used_mb);
                }
            }
        }
    }

    if let Ok(output) = tokio::process::Command::new("ps")
        .args(["-eo", "args="])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .await
    {
        let text = String::from_utf8_lossy(&output.stdout).to_lowercase();
        for name in &policy.gpu.busy_processes {
            let needle = name.to_lowercase();
            if text.contains(&needle) {
                snap.busy_processes.push(name.clone());
            }
        }
        snap.busy_processes.truncate(6);
    }

    if let Ok(output) = tokio::process::Command::new("ollama")
        .arg("ps")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .await
    {
        if output.status.success() {
            for line in String::from_utf8_lossy(&output.stdout).lines().skip(1) {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() < 6 {
                    continue;
                }
                snap.loaded_models.push(LoadedModel {
                    name: parts[0].to_string(),
                    size: format!("{} {}", parts[2], parts[3]),
                    processor: parts[4].to_string(),
                    until: parts[6..].join(" "),
                });
            }
        }
    }

    snap
}

pub(crate) fn decide(policy: &ResourcePolicy, snap: &Snapshot) -> Decision {
    let free_gb = snap.free_mb as f32 / 1024.0;
    let busy = !snap.busy_processes.is_empty();

    let (summary_allowed, summary_reason) = if !snap.gpu_available {
        (
            false,
            "nvidia-smi tidak tersedia; guard memakai mode konservatif".to_string(),
        )
    } else if busy {
        (
            false,
            format!(
                "proses render aktif: {}",
                snap.busy_processes.join(", ")
            ),
        )
    } else if free_gb < policy.gpu.min_free_llm_gb {
        (
            false,
            format!(
                "VRAM bebas {free_gb:.1} GB < {:.1} GB",
                policy.gpu.min_free_llm_gb
            ),
        )
    } else {
        (true, format!("VRAM bebas {free_gb:.1} GB"))
    };

    let summary_model = if summary_allowed
        && policy.models.summary_quality.enabled
        && free_gb >= policy.models.summary_quality.min_free_gb
    {
        policy.models.summary_quality.model.clone()
    } else {
        policy.models.summary.model.clone()
    };

    let (embedding_allowed, embedding_reason) = if !snap.gpu_available {
        (false, "nvidia-smi tidak tersedia; pakai kata kunci".to_string())
    } else if busy {
        (
            false,
            format!(
                "proses render aktif: {}",
                snap.busy_processes.join(", ")
            ),
        )
    } else if free_gb < policy.gpu.min_free_embedding_gb {
        (
            false,
            format!(
                "VRAM bebas {free_gb:.1} GB < {:.1} GB",
                policy.gpu.min_free_embedding_gb
            ),
        )
    } else {
        (true, format!("VRAM bebas {free_gb:.1} GB"))
    };

    Decision {
        summary_allowed,
        summary_model,
        summary_reason,
        embedding_allowed,
        embedding_reason,
    }
}

pub(crate) async fn embedding_allowed() -> bool {
    let (policy, _) = policy();
    let snap = snapshot().await;
    decide(&policy, &snap).embedding_allowed
}

/// Ringkasan memakai `keep_alive=0s` supaya model langsung keluar dari VRAM.
/// Bila guard menolak, pemanggil memakai kompresi deterministik.
pub(crate) async fn generate_summary(prompt: &str) -> Result<(String, String), String> {
    let (policy, _) = policy();
    let snap = snapshot().await;
    let decision = decide(&policy, &snap);
    if !decision.summary_allowed {
        return Err(decision.summary_reason);
    }

    let url = std::env::var("OLLAMA_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:11434/api/generate".to_string());
    let body = serde_json::json!({
        "model": decision.summary_model,
        "system": "Anda perangkum percakapan. Tulis ringkasan padat dalam bahasa Indonesia. \
                   Jangan menulis proses berpikir dan jangan mengulang instruksi.",
        "prompt": prompt,
        "stream": false,
        "keep_alive": policy.models.summary.keep_alive,
        // tev1/qwen bisa menghabiskan seluruh anggaran token untuk "thinking";
        // untuk ringkasan kita minta jawaban langsung.
        "think": false,
        "options": {
            "num_ctx": policy.models.summary.num_ctx,
            "num_predict": 420,
            "temperature": 0.2
        }
    })
    .to_string();

    let output = tokio::time::timeout(
        Duration::from_secs(150),
        tokio::process::Command::new("curl")
            .args([
                "-sS",
                "--max-time",
                "140",
                "-H",
                "content-type: application/json",
                "--data-binary",
                &body,
                &url,
            ])
            .stdin(Stdio::null())
            .stderr(Stdio::piped())
            .output(),
    )
    .await
    .map_err(|_| "ringkasan melewati batas waktu 150 detik".to_string())?
    .map_err(|error| format!("gagal memanggil Ollama: {error}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("curl gagal: {}", stderr.trim()));
    }
    let value: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("jawaban Ollama bukan JSON: {error}"))?;
    if let Some(error) = value.get("error").and_then(|v| v.as_str()) {
        return Err(format!("Ollama: {error}"));
    }
    let text = value
        .get("response")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .trim()
        .to_string();
    if text.is_empty() {
        return Err("ringkasan kosong".to_string());
    }
    Ok((text, decision.summary_model))
}

pub(crate) async fn report() -> serde_json::Value {
    let (policy, source) = policy();
    let snap = snapshot().await;
    let decision = decide(&policy, &snap);
    serde_json::json!({
        "gpu": {
            "available": snap.gpu_available,
            "name": snap.gpu_name,
            "used_mb": snap.used_mb,
            "total_mb": snap.total_mb,
            "free_mb": snap.free_mb,
        },
        "loaded_models": snap.loaded_models,
        "busy_processes": snap.busy_processes,
        "policy": policy,
        "policy_source": source,
        "decision": decision,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshot_with(free_mb: u64, busy: Vec<&str>) -> Snapshot {
        Snapshot {
            gpu_available: true,
            gpu_name: "test".to_string(),
            used_mb: 12_288 - free_mb,
            total_mb: 12_288,
            free_mb,
            loaded_models: Vec::new(),
            busy_processes: busy.into_iter().map(str::to_string).collect(),
        }
    }

    #[test]
    fn guard_blocks_when_render_is_running() {
        let policy = ResourcePolicy::default();
        let decision = decide(&policy, &snapshot_with(10_000, vec!["comfyui-desktop"]));
        assert!(!decision.summary_allowed);
        assert!(!decision.embedding_allowed);
        assert!(decision.summary_reason.contains("render"));
    }

    #[test]
    fn guard_allows_small_model_when_vram_is_free() {
        let policy = ResourcePolicy::default();
        let decision = decide(&policy, &snapshot_with(10_000, vec![]));
        assert!(decision.summary_allowed);
        assert_eq!(decision.summary_model, "tev1:0.8b");
    }

    #[test]
    fn guard_blocks_when_vram_is_low() {
        let policy = ResourcePolicy::default();
        let decision = decide(&policy, &snapshot_with(800, vec![]));
        assert!(!decision.summary_allowed);
        assert!(!decision.embedding_allowed);
    }
}
