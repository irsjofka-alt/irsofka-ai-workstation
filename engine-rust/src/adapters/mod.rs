//! Kontrak adapter engine — satu antarmuka untuk `pane`, `cli`, dan `ollama`.
//!
//! Inilah satu-satunya tempat di daemon yang boleh tahu bahwa mesin punya tiga
//! bentuk berbeda. Semua lapisan di atasnya (run engine, automation, workflow,
//! channel) hanya mengenal `RunRequest` dan aliran `RunEvent`.
//!
//! Aturan yang mengikat setiap implementasi:
//!
//! 1. **Satu run, satu handle.** `spawn` tidak boleh menunggu run selesai; ia
//!    mengembalikan `RunHandle` yang mengalirkan event sampai `Finished`/`Failed`.
//! 2. **Resume harus eksplisit.** Bila `RunRequest::session_ref` kosong, adapter
//!    wajib memulai sesi baru. Menebak sesi dari disk dilarang — itu membuat run
//!    yang tidak bisa direproduksi.
//! 3. **Kuota tak terukur bukan nol.** `usage` yang tidak bisa diukur mengembalikan
//!    `UsageSource::Unsupported`, bukan angka penuh.
//! 4. **Batal itu janji.** Setelah `cancel` sukses, tidak boleh ada event baru
//!    selain `Finished { status: Cancelled }`.

#![allow(dead_code)] // Kontrak ini dipakai bertahap; konsumen pertama adalah /api/wakers.

pub mod registry;
pub mod types;

#[allow(unused_imports)] // Re-export adalah bagian kontrak; konsumennya menyusul di M1.
pub use registry::{EngineRegistry, EngineSpec, QuotaGate, RegistryError};
pub use types::*;

use tokio::sync::mpsc::Receiver;

/// Pegangan satu eksekusi yang sedang berjalan.
pub struct RunHandle {
    pub run_id: String,
    pub events: Receiver<RunEvent>,
}

impl RunHandle {
    pub fn new(run_id: impl Into<String>, events: Receiver<RunEvent>) -> Self {
        Self {
            run_id: run_id.into(),
            events,
        }
    }
}

#[derive(Debug)]
pub enum AdapterError {
    /// Engine tidak ada di registry — kesalahan konfigurasi, bukan kesalahan run.
    UnknownEngine(String),
    /// Engine ada, tetapi tidak bisa melakukan yang diminta (mis. resume pada pane).
    Unsupported {
        engine: String,
        capability: &'static str,
    },
    /// Ditolak sebelum dijalankan. `window` dan `remaining` dilaporkan apa adanya
    /// supaya console bisa menjelaskan, bukan sekadar menampilkan "gagal".
    QuotaExhausted {
        engine: String,
        window: String,
        remaining: Option<f64>,
    },
    Spawn(String),
    Io(String),
}

impl std::fmt::Display for AdapterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownEngine(engine) => write!(f, "engine \"{engine}\" tidak terdaftar"),
            Self::Unsupported { engine, capability } => {
                write!(f, "engine \"{engine}\" tidak mendukung {capability}")
            }
            Self::QuotaExhausted {
                engine,
                window,
                remaining,
            } => match remaining {
                Some(r) => write!(
                    f,
                    "kuota \"{engine}\" jendela {window} tinggal {:.0}%",
                    r * 100.0
                ),
                None => write!(f, "kuota \"{engine}\" jendela {window} habis"),
            },
            Self::Spawn(message) => write!(f, "gagal menjalankan engine: {message}"),
            Self::Io(message) => write!(f, "kesalahan I/O: {message}"),
        }
    }
}

impl std::error::Error for AdapterError {}

/// Jalur eksekusi headless sebuah engine. `kind` di registry hanya menunjukkan
/// bentuk lamanya (tab/CLI/lokal); yang menentukan bisa-tidaknya dijalankan adalah
/// hasil resolusi ini.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunPath {
    /// Argv dari `template` di registry.
    Template,
    /// Jalur headless resmi qoder (`-p`).
    Qoder,
    /// Antigravity CLI (`agy --print`), termasuk entri pane yang memakai tab antigravity.
    Agy,
    /// HTTP Ollama.
    Ollama,
    /// HTTP OpenAI-compatible (Nvidia integrate.api.nvidia.com).
    OpenAi,
    /// Tidak ada jalur headless yang bisa dipertanggungjawabkan.
    Unsupported,
}

impl RunPath {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Template => "template",
            Self::Qoder => "qoder_headless",
            Self::Agy => "agy_headless",
            Self::Ollama => "ollama_http",
            Self::OpenAi => "openai_http",
            Self::Unsupported => "unsupported",
        }
    }
}

pub fn run_path(spec: &EngineSpec) -> RunPath {
    if spec.template.is_some() {
        RunPath::Template
    } else if spec.kind == EngineKind::Ollama {
        RunPath::Ollama
    } else if spec.kind == EngineKind::OpenAi {
        RunPath::OpenAi
    } else if spec.key == "qoder" || spec.binary.as_deref() == Some("qoder") {
        RunPath::Qoder
    } else if spec.binary.as_deref() == Some("agy")
        || spec.tab.as_deref() == Some("antigravity")
    {
        RunPath::Agy
    } else {
        RunPath::Unsupported
    }
}

/// Implementasi konkret per `kind`. Satu adapter boleh menangani banyak entri
/// registry (mis. `agy` melayani `claude-opus`, `claude-sonnet`, dan `gemini`).
pub trait EngineAdapter: Send + Sync {
    /// Kunci registry yang dilayani adapter ini.
    fn key(&self) -> &str;

    fn kind(&self) -> EngineKind;

    fn supports_resume(&self) -> bool {
        false
    }

    fn spawn(&self, request: RunRequest) -> Result<RunHandle, AdapterError>;

    fn cancel(&self, run_id: &str) -> Result<(), AdapterError>;

    fn usage(&self) -> Result<UsageSnapshot, AdapterError>;
}
