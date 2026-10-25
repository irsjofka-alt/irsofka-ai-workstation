//! Pencarian pengetahuan hibrida: vektor bila tersedia, kata kunci sebagai cadangan.
//!
//! Cosine dihitung di Rust; tidak ada database vektor. Bila Ollama tidak mendukung
//! embedding, hasilnya sama seperti sebelumnya — hanya mode yang berbeda.

use crate::embeddings;
use crate::store::Store;

/// (hasil, mode) — mode dipakai console supaya operator tahu apa yang sebenarnya
/// dipakai, bukan mengira selalu semantik.
pub(crate) async fn search(
    store: &Store,
    waker_id: &str,
    query: &str,
    limit: usize,
) -> (Vec<(String, String)>, &'static str) {
    // Maksimum tiga potongan: konteks yang tidak relevan lebih mahal daripada
    // konteks yang sedikit.
    let limit = limit.clamp(1, 3);
    let min_score = std::env::var("STATION_KNOWLEDGE_MIN_SCORE")
        .ok()
        .and_then(|value| value.parse::<f32>().ok())
        .unwrap_or(0.25);
    if let Some(query_vector) = embeddings::embed(query).await {
        let entries = store.knowledge_entries(waker_id).await;
        let mut scored: Vec<(f32, String, String)> = Vec::new();
        for (title, content, vector) in entries {
            let Some(vector) = vector else {
                continue;
            };
            if vector.len() != query_vector.len() {
                continue;
            }
            let score = cosine(&query_vector, &vector);
            if score >= min_score {
                scored.push((score, title, snippet(&content, query)));
            }
        }
        if !scored.is_empty() {
            scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
            let hits = scored
                .into_iter()
                .take(limit)
                .map(|(_, title, snippet)| (title, snippet))
                .collect();
            return (hits, "vector");
        }
    }
    (store.search_knowledge(waker_id, query, limit).await, "keyword")
}

pub(crate) async fn context(
    store: &Store,
    waker_id: &str,
    query: &str,
    limit: usize,
) -> String {
    let (hits, _) = search(store, waker_id, query, limit).await;
    hits.iter()
        .map(|(title, snippet)| format!("### {title}\n{snippet}"))
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let mut dot = 0.0f32;
    let mut norm_a = 0.0f32;
    let mut norm_b = 0.0f32;
    for (left, right) in a.iter().zip(b.iter()) {
        dot += left * right;
        norm_a += left * left;
        norm_b += right * right;
    }
    let denom = norm_a.sqrt() * norm_b.sqrt();
    if denom == 0.0 {
        0.0
    } else {
        dot / denom
    }
}

fn snippet(content: &str, query: &str) -> String {
    let lower = content.to_lowercase();
    let tokens: Vec<String> = query
        .split(|ch: char| !ch.is_alphanumeric())
        .map(|word| word.to_lowercase())
        .filter(|word| word.chars().count() >= 3)
        .collect();
    let found = tokens
        .iter()
        .filter_map(|token| lower.find(token.as_str()))
        .min()
        // Byte offset → posisi karakter; teks Indonesia bisa mengandung multibyte.
        .map(|byte| lower[..byte].chars().count());
    let chars: Vec<char> = content.chars().collect();
    let (start, end) = match found {
        Some(position) => (
            position.saturating_sub(120).min(chars.len()),
            (position + 680).min(chars.len()),
        ),
        None => (0, 800.min(chars.len())),
    };
    let mut text: String = chars[start..end].iter().collect();
    if start > 0 {
        text.insert(0, '…');
    }
    if end < chars.len() {
        text.push('…');
    }
    text
}
