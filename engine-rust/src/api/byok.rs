//! Kolam BYOK Gemini di layar Station: status slot dan satu jalur tulis, tanpa pernah
//! menampilkan kunci.
//!
//! Pembagian kerja diwarisi dari F8.3 dan dari `api/autopilot.rs`: **Python memegang berkas
//! kunci** (`tools/byok.py`), Rust hanya memegang HTTP, batas waktu proses, dan izin berkas.
//! Angka mana pun yang tampil di layar adalah byte yang dikeluarkan `byok.py station` —
//! mengurainya lalu menyerialisasikannya kembali di sini akan membuat Station dan CLI punya
//! dua definisi "apa yang diketahui tentang kolam ini", dan kontrak §12 melarang itu.
//!
//! Tiga hal yang membuat file ini ada bentuknya sekarang:
//!
//! 1. **Kunci tidak pernah lewat argv.** `ps` memperlihatkan argumen proses ke semua pengguna di
//!    mesin yang sama, dan kunci yang lewat sana tercatat di log proses. Jadi POST menulis body
//!    ke berkas sementara dengan mode 0600 lebih dulu, dan yang dikirim ke anak proses hanyalah
//!    PATH-nya. `byok.py set` menghapus berkas itu sendiri begitu dibacanya — kalau tidak, kita
//!    baru saja memindahkan kunci dari satu tempat ke tempat yang lebih bocor.
//! 2. **Yang dibaca layar hanyalah sidik jari.** `slots` mengembalikan panjang, bentuk (`AIza`
//!    atau bukan), dan 12 karakter pertama sha256. Itu cukup untuk mengenali "slot ini berisi
//!    kunci yang sama dengan yang tadi" tanpa menyalin rahasianya ke DOM, ke log browser, atau
//!    ke screenshot yang dikirim ke engine lain.
//! 3. **Tersedia ≠ terpakai.** Layar menampilkan peta kapasitas hasil UKUR (`capabilities`),
//!    lengkap dengan status DITOLAK UKUR. `lyra` 404 di semua kunci dan `gemini-4-argon` 429 di
//!    semua kunci; menulis keduanya sebagai "tersedia" adalah cara paling cepat membuat orang
//!    merancang pipeline di atas jalur yang tidak ada.

use axum::body::Body;
use axum::extract::Json;
use axum::http::header::CONTENT_TYPE;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::Value;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::Command;

use crate::paths::{home_dir, python_bin, station_dir};

fn byok_script() -> PathBuf {
    station_dir().join("tools/byok.py")
}

fn raw_json(status: StatusCode, text: String) -> Response {
    (status, [(CONTENT_TYPE, "application/json")], Body::from(text)).into_response()
}

/// Panggil `byok.py` dengan argumen tersusun sebagai array — tidak ada string yang pernah
/// masuk shell, jadi tidak ada cara bagi isi request untuk menyisipkan perintah.
fn run_byok(args: &[String]) -> Result<String, String> {
    let script = byok_script();
    if !script.exists() {
        return Err(format!("{} tidak ada", script.display()));
    }
    // Path-nya diikat lebih dulu: `script.display().to_string().as_str()` membuat sementara yang
    // mati di akhir statement, dan vektornya akan memegang gantungan yang sudah tidak ada.
    let script_arg = script.display().to_string();
    let mut argv: Vec<&str> = vec![script_arg.as_str()];
    argv.extend(args.iter().map(String::as_str));
    let out = Command::new(python_bin())
        .args(&argv)
        .current_dir(station_dir())
        .output()
        .map_err(|e| format!("gagal menjalankan byok.py: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().chars().take(400).collect());
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// GET /api/byok — apa yang diketahui sistem tentang kolamnya, tanpa satu byte kunci pun.
pub(crate) async fn get_state() -> Response {
    match run_byok(&["station".into()]) {
        Ok(text) => raw_json(StatusCode::OK, text),
        Err(why) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"ok": false, "why": why})),
        )
            .into_response(),
    }
}

/// POST /api/byok — tulis satu slot, atau ganti daftar sekaligus, atau ganti model.
///
/// Body yang diterima hanya bentuk data: `{"slot": 3, "key": "..."}`,
/// `{"keys": ["...", "..."]}`, atau `{"model": "gemini-3.8-flash"`. Kunci ditulis ke berkas
/// sementara 0600 di bawah `$HOME/.ai-station` dan hanya path-nya yang menjadi argumen.
pub(crate) async fn post_state(Json(body): Json<Value>) -> Response {
    if !body.is_object() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "why": "body harus sebuah objek"})),
        )
            .into_response();
    }
    let payload = body.to_string();
    let tmp = home_dir().join(".ai-station").join("config").join(format!(
        ".byok-incoming-{}.json",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    let mut f = match std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
    {
        Ok(f) => f,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "why": format!("gagal membuat berkas sementara: {e}")})),
            )
                .into_response()
        }
    };
    if let Err(e) = f.write_all(payload.as_bytes()) {
        let _ = std::fs::remove_file(&tmp);
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"ok": false, "why": format!("gagal menulis: {e}")})),
        )
            .into_response();
    }
    drop(f);
    let args = vec!["set".into(), "--from-file".into(), tmp.display().to_string()];
    match run_byok(&args) {
        Ok(text) => raw_json(StatusCode::OK, text),
        Err(why) => {
            // Anak proses gagal SEBELUM sempat menghapus berkasnya: jangan tinggalkan kunci
            // tergeletak di disk.
            let _ = std::fs::remove_file(&tmp);
            (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"ok": false, "why": why})),
            )
                .into_response()
        }
    }
}
