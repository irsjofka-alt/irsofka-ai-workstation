//! Konteks stasiun adaptif (Tiered + Intent-based Context).
//!
//! Keresahan yang dijawab modul ini:
//! 1. Brute-force context injection memboroskan ribuan token per giliran (turn).
//! 2. Dokumen yang dipotong mentah ([… dipotong …]) membingungkan model.
//! 3. Prefix cache penyedia (Gemini/Claude) terganggu bila konteks statis tercampur
//!    dengan log usang atau handoff sesi sebelumnya.
//!
//! Arsitektur:
//! - Tier 0 (Core Anchor): Identitas mesin, operator, batasan keselamatan mutlak,
//!   dan pointer direktori penting. Selalu stabil dan ringkas (~600-800 karakter).
//! - Agentic Pointers (Lazy Retrieval): Pointer berkas (arsitektur, memori, skills)
//!   menggantikan dump berkas raksasa; agent membaca via tool saat diperlukan.
//! - Tier 1 (Intent-Based Rules): Aturan spesifik (site publication, checklist model,
//!   rate limits, routing) dan handoff hanya disertakan saat relevan dengan prompt tugas.
//!
//! Ukuran dibatasi `STATION_CONTEXT_CHARS` (default 3.500). `STATION_CONTEXT_PACK=0`
//! mematikan paket ini untuk run tertentu.

use crate::compress;
use crate::paths::station_dir;
use std::fs;
use std::path::Path;

fn budget() -> usize {
    std::env::var("STATION_CONTEXT_CHARS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(3_500)
        .clamp(500, 20_000)
}

fn enabled() -> bool {
    std::env::var("STATION_CONTEXT_PACK")
        .map(|value| value != "0" && !value.eq_ignore_ascii_case("false"))
        .unwrap_or(true)
}

fn read(path: &Path) -> Option<String> {
    fs::read_to_string(path).ok().filter(|text| !text.trim().is_empty())
}

/// Menambahkan satu bagian selama anggaran masih ada.
fn section(out: &mut String, remaining: &mut usize, title: &str, body: &str, want: usize) {
    if *remaining <= title.len() + 60 {
        return;
    }
    let allowance = want.min(remaining.saturating_sub(title.len() + 2));
    let body = compress::compact(body, allowance);
    if body.trim().is_empty() {
        return;
    }
    out.push_str(title);
    out.push('\n');
    out.push_str(&body);
    out.push_str("\n\n");
    *remaining = remaining.saturating_sub(title.len() + body.chars().count() + 2);
}

fn latest_handoff(projects: &Path) -> Option<String> {
    let mut names: Vec<String> = fs::read_dir(projects)
        .ok()?
        .flatten()
        .filter_map(|entry| entry.file_name().to_str().map(str::to_string))
        .filter(|name| name.starts_with("handoff_auto_") && name.ends_with(".md"))
        .collect();
    names.sort();
    let name = names.pop()?;
    let text = fs::read_to_string(projects.join(name)).ok()?;
    Some(compress::tail(&text, 800))
}

#[derive(Default)]
struct PromptIntent {
    needs_site: bool,
    needs_model_checklist: bool,
    needs_routing: bool,
    needs_rate_limits: bool,
    needs_handoff: bool,
    needs_arch: bool,
}

fn detect_intent(prompt: &str) -> PromptIntent {
    let p = prompt.to_lowercase();
    PromptIntent {
        needs_site: p.contains("site")
            || p.contains("publish")
            || p.contains("hugo")
            || p.contains("publikasi")
            || p.contains("artikel")
            || p.contains("blog"),
        needs_model_checklist: p.contains("model")
            || p.contains("endpoint")
            || p.contains("provider")
            || p.contains("rpm")
            || p.contains("lane")
            || p.contains("checklist")
            || p.contains("tambah model"),
        needs_routing: p.contains("routing")
            || p.contains("decider")
            || p.contains("verifier")
            || p.contains("resolver")
            || p.contains("matrix"),
        needs_rate_limits: p.contains("rate limit")
            || p.contains("rate_limit")
            || p.contains("kuota")
            || p.contains("quota")
            || p.contains("throttle")
            || p.contains("429"),
        needs_handoff: p.contains("lanjutkan")
            || p.contains("resume")
            || p.contains("handoff")
            || p.contains("pekerjaan terakhir")
            || p.contains("status tugas")
            || p.contains("recovery"),
        needs_arch: p.contains("arsitektur")
            || p.contains("architecture")
            || p.contains("struktur folder")
            || p.contains("file map")
            || p.contains("peta folder"),
    }
}

/// Paket konteks umum (default tanpa filter intent).
pub(crate) fn pack() -> String {
    pack_for("")
}

/// Paket konteks kontekstual berdasarkan intent pesan pengguna.
pub(crate) fn pack_for(prompt: &str) -> String {
    if !enabled() {
        return String::new();
    }
    let base = station_dir();
    let brain = base.join("brain");
    if !brain.is_dir() {
        return String::new();
    }

    let mut remaining = budget();
    let mut out = String::new();
    out.push_str("## Konteks stasiun (dimuat otomatis; ikuti, jangan diabaikan)\n");
    remaining = remaining.saturating_sub(80);
    out.push_str(
        "Konteks stasiun dimuat adaptif. Baca berkas spesifik via tool bila memerlukan detail lengkap.\n\n",
    );
    remaining = remaining.saturating_sub(100);

    // Tier 0: Core Anchor (Identitas, Mesin, Batasan Keselamatan Mutlak)
    let core_anchor = "\
- **Operator**: irsofka | **OS**: Pop!_OS 24.04 LTS (COSMIC Wayland, x86_64)
- **Hardware**: Intel i7-12700F, 32GB RAM, NVIDIA RTX 3060 12GB VRAM
- **Direktori Utama**: `~/.ai-station` (Brain: `~/.ai-station/brain`, Runtime/Caches: `~/runtime`)
- **Batasan Keselamatan Mutlak**: DILARANG menjalankan perintah destruktif (`rm -rf`, `git push --force`, drop database, ubah izin sistem) tanpa persetujuan operator. Jangan mencetak/menyalin API key atau kredensial.
- **Prinsip Kerja**: Mandiri, tenang, presisi, berorientasi bukti (cantumkan bukti perintah). State persisten disimpan di database PostgreSQL `irsofka_ai_workstation` dan `~/.ai-station`. Jangan gunakan `$HOME` sebagai dumping ground.";
    section(&mut out, &mut remaining, "### Identitas & Batasan Inti", core_anchor, 800);

    // Tier 0: Agentic Resource Index (Lazy retrieval pointers)
    let pointers = "\
- Arsitektur Berkas: `~/.ai-station/brain/memory/projects/ARCHITECTURE.md`
- Basis Data & Memori: `~/.ai-station/brain/memory/MEMORY.md` (PostgreSQL `irsofka_ai_workstation`)
- Proyek Aktif: `~/.ai-station/brain/memory/projects/active_projects.md`
- Skills: `~/.ai-station/brain/skills/`";
    section(&mut out, &mut remaining, "### Indeks Sumber Daya (Baca via Tool)", pointers, 400);

    let intent = detect_intent(prompt);
    let rules_dir = brain.join("rules");

    // Tier 1: Aturan Berbasis Intent
    let mut loaded_any_rule = false;
    if intent.needs_site {
        if let Some(text) = read(&rules_dir.join("site_publication.md")) {
            section(&mut out, &mut remaining, "### Aturan: Publikasi Web", &text, 1_400);
            loaded_any_rule = true;
        }
    }
    if intent.needs_model_checklist {
        if let Some(text) = read(&rules_dir.join("model_integration_checklist.md")) {
            section(&mut out, &mut remaining, "### Aturan: Integrasi Model Baru", &text, 1_200);
            loaded_any_rule = true;
        }
    }
    if intent.needs_routing {
        if let Some(text) = read(&rules_dir.join("model_routing_rules.md")) {
            section(&mut out, &mut remaining, "### Aturan: Routing Mesin", &text, 1_000);
            loaded_any_rule = true;
        }
    }
    if intent.needs_rate_limits {
        if let Some(text) = read(&rules_dir.join("rate_limits.md")) {
            section(&mut out, &mut remaining, "### Aturan: Rate Limits", &text, 800);
            loaded_any_rule = true;
        }
    }

    // Jika prompt umum dan tidak ada rule spesifik yang dimuat, berikan ringkasan aturan yang ada
    if !loaded_any_rule && remaining > 150 {
        let available_rules = "\
Aturan spesifik tersedia di `~/.ai-station/brain/rules/` (dimuat otomatis saat relevan atau dapat dibaca langsung):
- `safety_boundaries.md` (Batasan keselamatan detail)
- `site_publication.md` (Aturan publikasi web & artikel)
- `model_integration_checklist.md` (Checklist integrasi model/endpoint)
- `model_routing_rules.md` (Matriks keputusan model)
- `rate_limits.md` (Batas pemanggilan API & RPM)";
        section(&mut out, &mut remaining, "### Aturan Tambahan", available_rules, 450);
    }

    // Tier 1: Peta Arsitektur Berkas jika diminta
    if intent.needs_arch {
        if let Some(text) = read(&brain.join("memory/projects/ARCHITECTURE.md")) {
            section(&mut out, &mut remaining, "### Peta Folder & Arsitektur", &text, 1_500);
        }
    }

    // Tier 1: Handoff hanya jika relevan (resume/lanjutkan/status)
    if intent.needs_handoff {
        let projects = brain.join("memory/projects");
        if let Some(text) = latest_handoff(&projects) {
            section(&mut out, &mut remaining, "### Handoff Terakhir", &text, 800);
        }
    }

    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paket_kosong_saat_dimatikan() {
        std::env::set_var("STATION_CONTEXT_PACK", "0");
        assert!(pack().is_empty());
        assert!(pack_for("halo").is_empty());
        std::env::remove_var("STATION_CONTEXT_PACK");
    }

    #[test]
    fn batas_ukuran_dihormati() {
        std::env::set_var("STATION_CONTEXT_CHARS", "1000");
        let pack = pack();
        assert!(pack.chars().count() <= 1_600, "paket melebihi batas: {}", pack.chars().count());
        std::env::remove_var("STATION_CONTEXT_CHARS");
    }

    #[test]
    fn pack_for_chat_is_compact() {
        let pack = pack_for("halo apa kabar?");
        assert!(pack.chars().count() < 1_800, "chat biasa terlalu boros: {}", pack.chars().count());
        assert!(!pack.contains("Aturan: Publikasi Web"));
        assert!(!pack.contains("Aturan: Integrasi Model Baru"));
        assert!(pack.contains("Identitas & Batasan Inti"));
    }

    #[test]
    fn pack_for_site_includes_site_rules() {
        let pack = pack_for("tolong publikasikan artikel ke site sekarang");
        assert!(pack.contains("Aturan: Publikasi Web"));
    }

    #[test]
    fn pack_for_model_includes_checklist() {
        let pack = pack_for("tolong periksa checklist integrasi model dan endpoint baru");
        assert!(pack.contains("Aturan: Integrasi Model Baru"));
    }
}
