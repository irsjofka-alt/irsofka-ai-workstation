//! Riwayat percakapan untuk prompt: ringkasan + N pesan terakhir.
//!
//! Dua lapis:
//! 1. Ringkasan bergulir disimpan per percakapan (`summary`, `summary_covered_id`).
//! 2. Sisa pesan setelah ringkasan dikompres deterministik, lalu N pesan terakhir
//!    dikirim utuh.
//!
//! Model lokal hanya dipakai bila guard sumber daya mengizinkan; bila tidak,
//! ringkasan deterministik tetap menjaga konteks tanpa memakai VRAM.

use crate::compress;
use crate::resources;
use crate::store::{StoredMessage, Store};
use std::sync::{Arc, OnceLock};

const DEFAULT_KEEP: usize = 6;
const DEFAULT_HISTORY_CHARS: usize = 6_000;
const DEFAULT_SUMMARY_TRIGGER: usize = 12;
const DEFAULT_SUMMARY_CHARS: usize = 1_600;
const SUMMARY_SOURCE_CHARS: usize = 12_000;

fn env_usize(key: &str, default: usize) -> usize {
    std::env::var(key)
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(default)
}

fn keep_count() -> usize {
    env_usize("STATION_HISTORY_KEEP", DEFAULT_KEEP).clamp(2, 20)
}

fn role_label(role: &str) -> &'static str {
    match role {
        "user" => "Pengguna",
        "waker" => "Worker",
        "tool" => "Alat",
        _ => "Sistem",
    }
}

/// Menghapus prefix konteks UI konsol stasiun jika ada, agar riwayat percakapan
/// tetap murni dan tidak memboroskan token dengan snapshot status yang sudah usang.
pub(crate) fn clean_user_content(content: &str) -> &str {
    if let Some(start) = content.find("[KONTEKS AI STATION]") {
        if let Some(end) = content[start..].find("[/KONTEKS]") {
            let after = &content[start + end + "[/KONTEKS]".len()..];
            let trimmed = after.trim_start();
            if let Some(question_idx) = trimmed.find("Pertanyaan operator:\n") {
                return trimmed[question_idx + "Pertanyaan operator:\n".len()..].trim();
            }
            if let Some(question_idx) = trimmed.find("Pertanyaan operator:") {
                return trimmed[question_idx + "Pertanyaan operator:".len()..].trim();
            }
            return trimmed;
        }
    }
    content.trim()
}

fn render_message(message: &StoredMessage, max_chars: usize) -> Option<String> {
    let clean = if message.role == "user" {
        clean_user_content(&message.content)
    } else {
        message.content.trim()
    };
    let body = compress::compact(clean, max_chars);
    if body.trim().is_empty() {
        return None;
    }
    Some(format!("[{}] {}", role_label(&message.role), body))
}

/// Blok riwayat siap tempel. Kosong bila percakapan belum punya pesan.
pub(crate) async fn build(store: &Store, conversation: Option<&str>) -> String {
    let Some(conversation_id) = conversation else {
        return String::new();
    };
    let keep = keep_count();
    let max_chars = env_usize("STATION_HISTORY_CHARS", DEFAULT_HISTORY_CHARS).clamp(1_000, 40_000);

    let summary = store.conversation_summary(conversation_id).await;
    let covered_id = summary.as_ref().map(|state| state.covered_id).unwrap_or(0);
    let summary_text = summary.map(|state| state.summary).unwrap_or_default();

    // 80 pesan terakhir cukup untuk 6 utuh + puluhan pesan ringkas.
    let messages = store.conversation_messages(conversation_id, 80).await;
    if messages.is_empty() && summary_text.trim().is_empty() {
        return String::new();
    }

    let split = messages.len().saturating_sub(keep);
    let (older, recent) = messages.split_at(split);

    let header = "Riwayat percakapan sebelumnya (konteks, jangan diulang):\n";
    let mut used = header.chars().count();

    // Ringkasan adalah bagian paling padat; jangan sampai terpotong oleh pesan lama.
    let mut summary_block = String::new();
    if !summary_text.trim().is_empty() {
        let cap = 1_200.min(max_chars / 5);
        summary_block.push_str("Ringkasan:\n");
        summary_block.push_str(&compress::compact(&summary_text, cap));
        summary_block.push('\n');
        used += summary_block.chars().count();
    }

    // N pesan terakhir mendapat porsi terbesar, tetapi tetap dibatasi supaya
    // seluruh blok muat dalam anggaran.
    let recent_budget = max_chars.saturating_sub(used) * 3 / 5;
    let per_recent = (recent_budget / recent.len().max(1)).clamp(120, 1_800);
    let recent_lines: Vec<String> = recent
        .iter()
        .filter_map(|message| render_message(message, per_recent))
        .collect();
    if !recent_lines.is_empty() {
        used += "\nPesan terakhir:\n".chars().count();
        used += recent_lines
            .iter()
            .map(|line| line.chars().count() + 1)
            .sum::<usize>();
    }

    // Sisa anggaran dipakai untuk pesan lama, dari yang paling dekat dulu.
    let mut older_lines: Vec<String> = Vec::new();
    if used < max_chars {
        let mut older_used = "\nPesan lama (ringkas):\n".chars().count();
        for message in older.iter().rev() {
            if message.id <= covered_id {
                continue;
            }
            let budget = if message.role == "tool" { 240 } else { 360 };
            let Some(line) = render_message(message, budget) else {
                continue;
            };
            let cost = line.chars().count() + 1;
            if used + older_used + cost > max_chars {
                break;
            }
            older_used += cost;
            older_lines.push(line);
            if older_lines.len() >= 12 {
                break;
            }
        }
        older_lines.reverse();
        if !older_lines.is_empty() {
            used += older_used;
        }
    }

    let mut out = String::with_capacity(used + 64);
    out.push_str(header);
    out.push_str(&summary_block);
    if !older_lines.is_empty() {
        out.push_str("\nPesan lama (ringkas):\n");
        for line in older_lines {
            out.push_str(&line);
            out.push('\n');
        }
    }
    if !recent_lines.is_empty() {
        out.push_str("\nPesan terakhir:\n");
        for line in recent_lines {
            out.push_str(&line);
            out.push('\n');
        }
    }

    if out.chars().count() > max_chars {
        // Pengaman terakhir; seharusnya tidak tercapai karena anggaran di atas.
        return compress::tail(&out, max_chars);
    }
    out
}

static SUMMARY_LOCK: OnceLock<Arc<tokio::sync::Mutex<()>>> = OnceLock::new();

/// Dipanggil setelah run selesai. Hanya bekerja bila pesan yang belum tercakup
/// melewati ambang; N pesan terakhir sengaja tidak diringkas.
pub(crate) async fn maybe_summarize(store: &Store, conversation_id: &str) {
    let lock = SUMMARY_LOCK.get_or_init(|| Arc::new(tokio::sync::Mutex::new(())));
    let _guard = lock.lock().await;

    let trigger = env_usize("STATION_SUMMARY_TRIGGER", DEFAULT_SUMMARY_TRIGGER).clamp(4, 200);
    let keep = keep_count();

    let Some(state) = store.conversation_summary(conversation_id).await else {
        // Tanpa ringkasan sebelumnya, covered_id = 0.
        summarize_from(store, conversation_id, 0, String::new(), trigger, keep).await;
        return;
    };
    summarize_from(
        store,
        conversation_id,
        state.covered_id,
        state.summary,
        trigger,
        keep,
    )
    .await;
}

async fn summarize_from(
    store: &Store,
    conversation_id: &str,
    covered_id: i64,
    old_summary: String,
    trigger: usize,
    keep: usize,
) {
    let messages = store.messages_after(conversation_id, covered_id).await;
    if messages.len() <= trigger {
        return;
    }
    let split = messages.len().saturating_sub(keep);
    let to_cover = &messages[..split];
    let Some(last) = to_cover.last() else {
        return;
    };
    let last_id = last.id;

    let prompt = summary_prompt(&old_summary, to_cover);
    let (summary, model) = match resources::generate_summary(&prompt).await {
        Ok((text, model)) => match sanitize_summary(&text) {
            Some(text) => (text, model),
            None => {
                eprintln!("ℹ ringkasan model tidak layak dipakai; memakai kompresi deterministik");
                (
                    deterministic_summary(&old_summary, to_cover),
                    "deterministic".to_string(),
                )
            }
        },
        Err(reason) => {
            eprintln!("ℹ ringkasan model lokal dilewati ({reason}); memakai kompresi deterministik");
            (
                deterministic_summary(&old_summary, to_cover),
                "deterministic".to_string(),
            )
        }
    };

    let max_chars = env_usize("STATION_SUMMARY_CHARS", DEFAULT_SUMMARY_CHARS).clamp(400, 8_000);
    let summary = compress::compact(&summary, max_chars);
    store
        .set_conversation_summary(conversation_id, &summary, last_id, &model)
        .await;
}

fn summary_prompt(old_summary: &str, messages: &[StoredMessage]) -> String {
    let mut out = String::new();
    out.push_str(
        "Ringkas percakapan berikut dalam maksimal 180 kata. \
         Pertahankan keputusan, fakta, angka, nama, preferensi, dan tugas yang belum selesai.\n\n",
    );
    if !old_summary.trim().is_empty() {
        out.push_str("Ringkasan sebelumnya:\n");
        out.push_str(&compress::compact(old_summary, 1_500));
        out.push_str("\n\n");
    }
    out.push_str("Data:\n");
    let mut body = String::new();
    for message in messages {
        let Some(line) = render_message(message, 600) else {
            continue;
        };
        if body.chars().count() + line.chars().count() > SUMMARY_SOURCE_CHARS {
            break;
        }
        body.push_str(&line);
        body.push('\n');
    }
    out.push_str(&body);
    out.push_str("\nRingkasan:");
    out
}

/// Model kecil kadang menulis proses berpikir, bukan ringkasan. Buang bagian itu
/// bila ada penanda jawaban akhir; kalau tidak ada, tolak supaya pemanggil memakai
/// ringkasan deterministik.
fn sanitize_summary(text: &str) -> Option<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return None;
    }
    let lower = trimmed.to_lowercase();
    if lower.starts_with("thinking process") || lower.contains("thinking process:") {
        for marker in [
            "**final answer:**",
            "final answer:",
            "**ringkasan:**",
            "ringkasan:",
        ] {
            if let Some(position) = lower.rfind(marker) {
                let rest = trimmed[position + marker.len()..].trim();
                if rest.chars().count() >= 60 && !rest.to_lowercase().contains("thinking process") {
                    return Some(rest.to_string());
                }
            }
        }
        return None;
    }
    // Terlalu pendek atau hanya mengulang instruksi: bukan ringkasan yang berguna.
    if trimmed.chars().count() < 60
        || lower.starts_with("tugas:")
        || lower.starts_with("ringkas riwayat")
        || lower.contains("ringkas riwayat kerja antara pengguna")
    {
        return None;
    }
    Some(trimmed.to_string())
}

/// Fallback tanpa model: ringkasan lama + kalimat kunci tiap pesan, dibatasi.
/// Membuang penanda ringkasan lama sebelum disisipkan lagi. Tanpa ini, setiap
/// siklus menambahkan "Ringkasan deterministik …" dan "Sebelumnya:" baru di atas
/// yang lama, sehingga anggaran 1.600 karakter habis untuk penanda, bukan isi.
fn strip_summary_markers(text: &str) -> String {
    text.replace("Ringkasan deterministik (model lokal tidak dipakai):", "")
        .replace("Ringkasan deterministik (model lokal tidak dipakai).", "")
        .replace("Sebelumnya:", "")
        .trim()
        .to_string()
}

fn deterministic_summary(old_summary: &str, messages: &[StoredMessage]) -> String {
    let mut out = String::new();
    out.push_str("Ringkasan deterministik (model lokal tidak dipakai):\n");
    let previous = strip_summary_markers(old_summary);
    if !previous.is_empty() {
        out.push_str("Sebelumnya: ");
        out.push_str(&compress::compact(&previous, 400));
        out.push('\n');
    }
    for message in messages {
        let Some(line) = render_message(message, 280) else {
            continue;
        };
        out.push_str(&line);
        out.push('\n');
    }
    compress::compact(&out, DEFAULT_SUMMARY_CHARS)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deterministic_summary_keeps_roles_and_bound() {
        let messages: Vec<StoredMessage> = (0..40)
            .map(|index| StoredMessage {
                id: index,
                role: if index % 2 == 0 { "user" } else { "waker" }.to_string(),
                content: format!("pesan {index} {}", "x".repeat(200)),
            })
            .collect();
        let summary = deterministic_summary("ringkasan lama", &messages);
        assert!(summary.contains("deterministik"));
        assert!(summary.chars().count() <= DEFAULT_SUMMARY_CHARS);
    }

    #[test]
    fn ringkasan_deterministik_tidak_bersarang() {
        let messages = vec![StoredMessage {
            id: 1,
            role: "user".to_string(),
            content: "halo".to_string(),
        }];
        let first = deterministic_summary("", &messages);
        let second = deterministic_summary(&first, &messages);
        assert_eq!(
            second.matches("Ringkasan deterministik").count(),
            1,
            "penanda tidak boleh berulang tiap siklus"
        );
        assert!(!second.contains("Sebelumnya: Ringkasan deterministik"));
        assert!(second.contains("Sebelumnya:"));
    }

    #[test]
    fn role_labels_are_stable() {
        assert_eq!(role_label("user"), "Pengguna");
        assert_eq!(role_label("waker"), "Worker");
        assert_eq!(role_label("tool"), "Alat");
    }

    #[test]
    fn rejects_thinking_without_final_answer() {
        let text = "Thinking Process:\n\n1. **Analyze the Request:**\n    * ...";
        assert!(sanitize_summary(text).is_none());
    }

    #[test]
    fn extracts_final_answer_after_thinking() {
        let answer = "Pengguna memilih warna biru dan angka tujuh; anggaran proyek Alpha ditetapkan 118 juta rupiah.";
        let text = format!("Thinking Process:\n\n1. Analyze...\n\n**Final Answer:**\n{answer}");
        let summary = sanitize_summary(&text).expect("final answer");
        assert_eq!(summary, answer);
    }

    #[test]
    fn clean_user_content_strips_console_context() {
        let raw = "[KONTEKS AI STATION]\nHalaman: Console (console)\nHuman Decide menunggu: 0\nWorker berjalan: 0\n[/KONTEKS]\n\nPertanyaan operator:\nhalo apa kabar?";
        assert_eq!(clean_user_content(raw), "halo apa kabar?");

        let raw_without_prefix = "[KONTEKS AI STATION]\nHalaman: Console\n[/KONTEKS]\ntolong bantu ini";
        assert_eq!(clean_user_content(raw_without_prefix), "tolong bantu ini");

        let regular = "pertanyaan biasa tanpa konteks UI";
        assert_eq!(clean_user_content(regular), "pertanyaan biasa tanpa konteks UI");
    }
}
