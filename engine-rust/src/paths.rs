//! Setiap jalur berkas yang dibaca atau ditulis workstation, dihitung ulang dari `$HOME`.
//!
//! Tidak ada satu pun yang di-cache di `static`: daemon harus tetap benar ketika direktori
//! workstation dipindah atau dijalankan dengan akun lain. Satu jalur absolut yang ikut
//! ter-compile akan membawa home directory pemilik mesin ke dalam setiap clone repo publik.
use std::path::PathBuf;

pub(crate) const FALLBACK_HOST: &str = "127.0.0.1";
pub(crate) const DEFAULT_PORT: u16 = 8999;

/// Alamat bind. Default tetap localhost; LAN hanya aktif bila operator memilihnya
/// lewat `STATION_BIND_HOST` (mis. `0.0.0.0`) dan sebaiknya bersama token.
pub(crate) fn bind_host() -> String {
    std::env::var("STATION_BIND_HOST")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| FALLBACK_HOST.to_string())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AuthMode {
    None,
    Token,
    Sudo,
}

/// `STATION_AUTH=sudo` memakai password akun mesin (diverifikasi lewat sudo).
/// Selain itu, `STATION_ACCESS_TOKEN` mengaktifkan mode token.
pub(crate) fn auth_mode() -> AuthMode {
    if std::env::var("STATION_AUTH")
        .map(|value| value.eq_ignore_ascii_case("sudo"))
        .unwrap_or(false)
    {
        AuthMode::Sudo
    } else if access_token().is_some() {
        AuthMode::Token
    } else {
        AuthMode::None
    }
}

pub(crate) fn access_token() -> Option<String> {
    std::env::var("STATION_ACCESS_TOKEN")
        .ok()
        .filter(|value| !value.trim().is_empty())
}

pub(crate) fn station_port() -> u16 {
    std::env::var("STATION_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_PORT)
}

pub(crate) fn home_dir() -> PathBuf {
    // $HOME praktis selalu terisi (systemd user maupun shell mengisinya). Fallback-nya
    // tidak boleh berupa nama orang tertentu — hasil clone tidak boleh membawa home
    // directory milik mesin saya. Urutannya: $HOME, lalu /home/$USER, lalu direktori kerja.
    if let Ok(h) = std::env::var("HOME") {
        if !h.is_empty() {
            return PathBuf::from(h);
        }
    }
    if let Ok(u) = std::env::var("USER") {
        if !u.is_empty() {
            return PathBuf::from(format!("/home/{u}"));
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("/"))
}

pub(crate) fn station_dir() -> PathBuf {
    home_dir().join(".ai-station")
}

pub(crate) fn config_dir() -> PathBuf {
    station_dir().join("config")
}

pub(crate) fn profiles_path() -> PathBuf {
    config_dir().join("cli_profiles.json")
}

pub(crate) fn runner_path() -> PathBuf {
    config_dir().join("run_tab.sh")
}

/// Penanda sekali-pakai untuk tab tertentu: kalau ada, penyalakan berikutnya tidak
/// memakai `--continue`. Ditulis oleh endpoint New Session, dihapus sendiri oleh
/// run_tab.sh pada saat dibacanya. Tidak ditulis ke cli_profiles.json — menyalakan
/// sesi baru bukan perubahan preferensi permanen.
pub(crate) fn fresh_marker_path(tab: &str) -> PathBuf {
    config_dir().join(format!("fresh.{tab}"))
}

pub(crate) fn assets_dir() -> PathBuf {
    station_dir().join("engine-rust").join("assets")
}

/// Data milik lapisan produk (waker, dsb). Sengaja terpisah dari `brain/` yang
/// menyimpan jejak audit: console boleh menulis di sini tanpa menyentuh sejarah.
pub(crate) fn data_dir() -> PathBuf {
    station_dir().join("data")
}

pub(crate) fn wakers_path() -> PathBuf {
    data_dir().join("wakers.json")
}

/// Situs Playground hasil `tools/site/build.py`, disajikan di `/site`.
pub(crate) fn site_dir() -> PathBuf {
    station_dir().join("site")
}

/// Console baru — statis, tanpa build step, disajikan di `/console`.
pub(crate) fn console_dir() -> PathBuf {
    station_dir().join("engine-rust").join("src").join("console")
}

pub(crate) fn gui_path() -> PathBuf {
    station_dir().join("engine-rust").join("src").join("gui.html")
}

pub(crate) fn spool_path() -> PathBuf {
    station_dir().join("logs").join("station_events.jsonl")
}

pub(crate) fn ingestor_path() -> PathBuf {
    station_dir().join("tools").join("session_ingestor.py")
}

/// Penjaga data induk (slot & registry). Validasi milik Python, berkas milik Rust:
/// yang mana pun yang memegang keduanya, salah satu akan menulis tanpa diperiksa.
pub(crate) fn master_data_path() -> PathBuf {
    station_dir().join("tools").join("master_data.py")
}

/// Pemilik kosakata tugas — klaim, lease, bukti, antrean manusia, sakelar Autopilot.
/// Angka pemutus hidup di sana, tidak di sini: menyalinnya ke Rust berarti punya dua
/// angka yang boleh berbeda, dan kontrak §12 melarang dua definisi untuk satu kata.
pub(crate) fn work_order_path() -> PathBuf {
    station_dir().join("tools").join("work_order.py")
}

/// Context pack untuk command `worker load` / `worker recovery`.
pub(crate) fn worker_context_path() -> PathBuf {
    station_dir().join("tools").join("worker_context.py")
}

/// Pintu tulis situs Playground. Nama subcommand dan aturannya milik skrip;
/// Rust hanya menyediakan batas HTTP dan batas waktu.
pub(crate) fn site_publish_path() -> PathBuf {
    station_dir().join("tools").join("site").join("publish.py")
}

/// Tangan drainer (F10.3). Jawabannya tinggal di `work_order.py`; yang di sini cuma jalurnya,
/// karena mengirim teks ke pane punya satu pemilik dan itu bukan modul HTTP.
pub(crate) fn drainer_path() -> PathBuf {
    station_dir().join("tools").join("drainer.py")
}

/// Interpreter untuk setiap proses anak daemon.
///
/// Unit systemd tidak mewarisi PATH selebar shell, jadi nama telanjang `python3` bisa tidak
/// ketemu justru pada saat ia paling dibutuhkan. Dicari di satu tempat supaya dua modul tidak
/// saling berbeda pada instalasi berikutnya.
pub(crate) fn python_bin() -> PathBuf {
    for candidate in ["/usr/bin/python3", "/usr/local/bin/python3"] {
        if PathBuf::from(candidate).exists() {
            return PathBuf::from(candidate);
        }
    }
    PathBuf::from("python3")
}

pub(crate) fn engines_registry_path() -> PathBuf {
    config_dir().join("engines.json")
}

/// Override model default per engine. Dipisah dari registry supaya console bisa
/// mengubah pilihan tanpa menulis ulang berkas yang disunting tangan.
pub(crate) fn engine_overrides_path() -> PathBuf {
    config_dir().join("engine_overrides.json")
}

pub(crate) fn slots_path() -> PathBuf {
    config_dir().join("slots.yaml")
}

/// Cadangan setiap tulisan data induk. Bukan biner: satu registry yang salah disimpan
/// membuat semua keberangkatan mesin berikutnya ditolak, dan korbannya adalah sesi yang
/// sedang berjalan. `station_backup.sh` tidak menutup lubang sekecil ini.
pub(crate) fn master_backups_dir() -> PathBuf {
    config_dir().join("backups")
}

/// Halaman Station: muka dari semua catatan, dibaca manusia — bukan tempat mengetik.
pub(crate) fn station_path() -> PathBuf {
    station_dir().join("engine-rust").join("src").join("station.html")
}

pub(crate) fn tabs_log_dir() -> PathBuf {
    station_dir().join("logs").join("tabs")
}

/// Socket tmux milik workstation. Terpisah dari socket default supaya pane AI tidak
/// pernah berbagi server dengan sesi tmux manual pemilik mesin.
pub(crate) fn tmux_socket() -> String {
    std::env::var("STATION_TMUX_SOCKET").unwrap_or_else(|_| "irsofka".to_string())
}

pub(crate) fn tmux_session_name(tab: &str) -> String {
    format!("station-{}", tab)
}
