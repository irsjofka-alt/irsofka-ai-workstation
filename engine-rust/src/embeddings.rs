//! Embedding lokal lewat Ollama.
//!
//! Tidak ada panggilan cloud. Bila server tidak mendukung embedding (atau mati),
//! fungsi ini mengembalikan `None` dan pencarian jatuh ke kata kunci — fitur tetap
//! berjalan, hanya tanpa makna semantik.

use serde_json::Value;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::sync::OnceLock;

use crate::resources;

fn cache() -> &'static tokio::sync::Mutex<HashMap<u64, Vec<f32>>> {
    static CACHE: OnceLock<tokio::sync::Mutex<HashMap<u64, Vec<f32>>>> = OnceLock::new();
    CACHE.get_or_init(|| tokio::sync::Mutex::new(HashMap::new()))
}

fn cache_key(model: &str, text: &str) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    model.hash(&mut hasher);
    text.hash(&mut hasher);
    hasher.finish()
}

pub(crate) fn model() -> String {
    std::env::var("STATION_EMBED_MODEL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "qwen3.5:4b".to_string())
}

fn endpoint() -> String {
    std::env::var("STATION_EMBED_URL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "http://127.0.0.1:11434/api/embeddings".to_string())
}

pub(crate) async fn embed(text: &str) -> Option<Vec<f32>> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return None;
    }
    // GPU dipakai render/Unreal? Jangan rebutan VRAM; pencarian jatuh ke kata kunci.
    if !resources::embedding_allowed().await {
        return None;
    }

    let model = model();
    let key = cache_key(&model, trimmed);
    {
        let cached = cache().lock().await;
        if let Some(vector) = cached.get(&key) {
            return Some(vector.clone());
        }
    }

    let prompt: String = trimmed.chars().take(8_000).collect();
    let body = serde_json::json!({
        "model": model,
        "prompt": prompt,
        // Jangan biarkan model embedding tinggal di VRAM setelah satu pencarian.
        "keep_alive": "0s",
    })
    .to_string();

    let output = tokio::process::Command::new("curl")
        .args([
            "-sS",
            "--max-time",
            "20",
            "-H",
            "content-type: application/json",
            "--data-binary",
            &body,
            &endpoint(),
        ])
        .output()
        .await
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let value: Value = serde_json::from_slice(&output.stdout).ok()?;
    let array = value.get("embedding")?.as_array()?;
    let vector: Vec<f32> = array
        .iter()
        .filter_map(|item| item.as_f64().map(|number| number as f32))
        .collect();
    if vector.is_empty() {
        None
    } else {
        let mut cached = cache().lock().await;
        // Cache kecil dan terbatas: pertanyaan berulang tidak memanggil model lagi.
        if cached.len() >= 128 {
            cached.clear();
        }
        cached.insert(key, vector.clone());
        Some(vector)
    }
}
