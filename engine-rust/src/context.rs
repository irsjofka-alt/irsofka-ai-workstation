//! Konteks stasiun yang selalu ikut ke prompt.
//!
//! Keresahan yang dijawab modul ini: setelah context di-refresh, agent lupa
//! struktur folder, kontrak, dan pekerjaan yang sedang berjalan, lalu mulai dari
//! nol. Paket ini dibaca dari brain setiap run dengan urutan yang stabil
//! (identitas → kontrak → peta folder → pekerjaan terakhir → memori → skill),
//! sehingga prefix cache tetap berguna dan isinya berubah hanya ketika berkasnya
//! berubah.
//!
//! Ukuran dibatasi `STATION_CONTEXT_CHARS` (default 6000). `STATION_CONTEXT_PACK=0`
//! mematikan paket ini untuk run tertentu.

use crate::compress;
use crate::paths::station_dir;
use std::fs;
use std::path::Path;

fn budget() -> usize {
    std::env::var("STATION_CONTEXT_CHARS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(8_000)
        .clamp(1_000, 20_000)
}

fn enabled() -> bool {
    std::env::var("STATION_CONTEXT_PACK")
        .map(|value| value != "0" && !value.eq_ignore_ascii_case("false"))
        .unwrap_or(true)
}

fn read(path: &Path) -> Option<String> {
    fs::read_to_string(path).ok().filter(|text| !text.trim().is_empty())
}

/// Menambahkan satu bagian selama anggaran masih ada. Bagian awal (identitas,
/// kontrak, peta folder) lebih penting daripada bagian akhir, jadi yang dipotong
/// adalah bagian belakang — bukan awal teks seperti pemotongan total.
fn section(out: &mut String, remaining: &mut usize, title: &str, body: &str, want: usize) {
    if *remaining <= title.len() + 120 {
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
    Some(compress::tail(&text, 1_600))
}

/// Paket konteks siap tempel. Kosong bila dimatikan atau brain tidak ada.
pub(crate) fn pack() -> String {
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
    remaining = remaining.saturating_sub(200);
    out.push_str(
        "Bagian ini dibaca dari brain workstation. Aturan, struktur folder, dan pekerjaan \
         terakhir berlaku untuk jawaban ini; jangan mengarang lokasi berkas atau memulai \
         dari nol.\n\n",
    );

    if let Some(text) = read(&brain.join("IDENTITY.md")) {
        section(&mut out, &mut remaining, "### Identitas & mesin", &text, 1_100);
    }
    if let Some(text) = read(&brain.join("rules/WORKSTATION_SHARED.md")) {
        section(&mut out, &mut remaining, "### Kontrak kerja (ringkas)", &text, 1_300);
    }
    let rules_dir = brain.join("rules");
    if let Ok(entries) = fs::read_dir(&rules_dir) {
        let mut names: Vec<String> = entries
            .flatten()
            .filter_map(|entry| entry.file_name().to_str().map(str::to_string))
            .filter(|name| name.ends_with(".md") && name != "WORKSTATION_SHARED.md")
            .collect();
        names.sort();
        // Isi aturan dibaca, bukan hanya namanya: pekerja yang butuh aturan
        // spesifik tidak akan membuka berkasnya sendiri di tengah tugas.
        if !names.is_empty() && *&remaining > 260 {
            out.push_str("### Aturan spesifik (ringkas)\n");
            remaining = remaining.saturating_sub(80);
            for name in names {
                if remaining <= 200 {
                    break;
                }
                // Checklist integrasi mesin harus utuh: ia rujukan wajib saat ada
                // model/endpoint baru, jadi diberi ruang lebih dari aturan lain.
                let allowance = if name == "model_integration_checklist.md" {
                    900
                } else {
                    320
                };
                if let Some(text) = read(&rules_dir.join(&name)) {
                    section(&mut out, &mut remaining, &format!("#### {name}"), &text, allowance);
                }
            }
        }
    }
    if let Some(text) = read(&brain.join("memory/projects/ARCHITECTURE.md")) {
        section(&mut out, &mut remaining, "### Peta folder (jangan menebak path)", &text, 1_900);
    }
    let projects = brain.join("memory/projects");
    if let Some(text) = read(&projects.join("active_projects.md")) {
        section(&mut out, &mut remaining, "### Proyek aktif", &text, 500);
    }
    if let Some(text) = latest_handoff(&projects) {
        section(&mut out, &mut remaining, "### Pekerjaan terakhir sebelum konteks di-refresh", &text, 1_500);
    }
    if let Some(text) = read(&brain.join("memory/MEMORY.md")) {
        section(&mut out, &mut remaining, "### Indeks memori", &text, 700);
    }
    if let Some(text) = read(&brain.join("skills/SKILLS.md")) {
        section(&mut out, &mut remaining, "### Skill yang tersedia", &text, 700);
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
        std::env::remove_var("STATION_CONTEXT_PACK");
    }

    #[test]
    fn batas_ukuran_dihormati() {
        std::env::set_var("STATION_CONTEXT_CHARS", "1000");
        let pack = pack();
        assert!(pack.chars().count() <= 1_600, "paket melebihi batas: {}", pack.chars().count());
        std::env::remove_var("STATION_CONTEXT_CHARS");
    }
}
