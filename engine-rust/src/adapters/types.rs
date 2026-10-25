//! Tipe netral untuk semua lapisan di atas adapter.
//!
//! Tidak ada nama engine, format CLI, atau bentuk keluaran vendor di sini. Scheduler,
//! workflow, dan channel hanya berbicara dalam `RunRequest` dan `RunEvent`; adapter
//! yang menerjemahkannya ke `agy --print`, `tmux send-keys`, atau HTTP Ollama.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;

/// Tiga bentuk mesin yang dikenal registry, plus `Unknown` supaya entri baru di
/// `config/engines.json` tidak membuat seluruh registry gagal dimuat.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineKind {
    Pane,
    Cli,
    Ollama,
    /// Endpoint HTTP OpenAI-compatible (mis. Nvidia integrate.api.nvidia.com).
    OpenAi,
    Unknown,
}

impl EngineKind {
    pub fn from_key(key: &str) -> Self {
        match key {
            "pane" => Self::Pane,
            "cli" => Self::Cli,
            "ollama" => Self::Ollama,
            "openai" => Self::OpenAi,
            _ => Self::Unknown,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pane => "pane",
            Self::Cli => "cli",
            Self::Ollama => "ollama",
            Self::OpenAi => "openai",
            Self::Unknown => "unknown",
        }
    }
}

impl<'de> Deserialize<'de> for EngineKind {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Ok(Self::from_key(&String::deserialize(d)?))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Succeeded,
    Failed,
    Cancelled,
    TimedOut,
}

impl RunStatus {
    pub fn from_key(key: &str) -> Option<Self> {
        match key {
            "succeeded" => Some(Self::Succeeded),
            "failed" => Some(Self::Failed),
            "cancelled" => Some(Self::Cancelled),
            "timed_out" => Some(Self::TimedOut),
            _ => None,
        }
    }
}

impl<'de> Deserialize<'de> for RunStatus {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let key = String::deserialize(d)?;
        Self::from_key(&key).ok_or_else(|| serde::de::Error::custom(format!("status run tidak dikenal: {key}")))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputStream {
    Stdout,
    Stderr,
}

/// Satu permintaan eksekusi. `session_ref` adalah satu-satunya jalan resume:
/// bila kosong, adapter wajib memulai sesi baru, bukan menebak dari disk.
#[derive(Debug, Clone)]
pub struct RunRequest {
    pub run_id: String,
    pub waker_id: String,
    pub engine_key: String,
    pub model: Option<String>,
    pub prompt: String,
    pub system_prompt: Option<String>,
    pub workspace: PathBuf,
    pub session_ref: Option<String>,
    pub env: BTreeMap<String, String>,
    pub timeout_secs: Option<u64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TokenUsage {
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cost_usd: Option<f64>,
}

/// Apa yang dialirkan adapter ke run engine, dan dari sana ke SSE console.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum RunEvent {
    Started {
        pid: Option<u32>,
        at: DateTime<Utc>,
    },
    Output {
        stream: OutputStream,
        chunk: String,
    },
    ToolCall {
        name: String,
        input: serde_json::Value,
    },
    Usage {
        usage: TokenUsage,
    },
    Artifact {
        path: String,
        mime: Option<String>,
    },
    Finished {
        status: RunStatus,
        exit_code: Option<i32>,
        duration_ms: u64,
    },
    Failed {
        code: String,
        message: String,
        retryable: bool,
    },
}

/// Satu jendela kuota seperti dilaporkan `usage.probe` di registry.
/// `remaining_fraction` = sisa sebagai pecahan 0..1, bukan persen.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindowUsage {
    pub remaining_fraction: Option<f64>,
    pub reset_at: Option<DateTime<Utc>>,
    pub credits: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UsageSource {
    Probe,
    Unsupported,
}

/// Cuplikan kuota. `Unsupported` bukan nol — ia berarti "tidak diukur",
/// dan penjadwal dilarang memperlakukannya sebagai penuh.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageSnapshot {
    pub engine_key: String,
    pub windows: BTreeMap<String, WindowUsage>,
    pub measured_at: DateTime<Utc>,
    pub source: UsageSource,
}
