//! Kompresi deterministik untuk riwayat percakapan.
//!
//! Tidak memanggil model. Saat GPU sibuk atau model lokal gagal, bagian ini tetap
//! memotong riwayat dengan aturan yang sama setiap kali — hasilnya bisa diprediksi
//! dan tidak menambah token.

/// Memotong teks panjang di tengah: awal dan akhir dipertahankan karena keputusan
/// dan kesimpulan biasanya ada di dua tempat itu.
pub(crate) fn compact(content: &str, max_chars: usize) -> String {
    let text = content.trim();
    if text.is_empty() || max_chars == 0 {
        return String::new();
    }
    let count = text.chars().count();
    if count <= max_chars {
        return text.to_string();
    }

    let head_budget = max_chars * 3 / 5;
    let tail_budget = max_chars / 4;
    let head: String = text.chars().take(head_budget).collect();
    let tail_start = count.saturating_sub(tail_budget);
    let tail: String = text.chars().skip(tail_start).collect();
    let mut out = format!("{head}\n[… dipotong …]\n{tail}");
    if out.chars().count() > max_chars {
        out = out.chars().take(max_chars).collect();
    }
    out
}

/// Mempertahankan ekor teks. Dipakai untuk anggaran total riwayat: pesan terbaru
/// lebih penting daripada pesan paling lama.
pub(crate) fn tail(content: &str, max_chars: usize) -> String {
    let text = content.trim();
    let count = text.chars().count();
    if count <= max_chars {
        return text.to_string();
    }
    let start = count.saturating_sub(max_chars.saturating_sub(8));
    let rest: String = text.chars().skip(start).collect();
    format!("[…]\n{rest}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_short_text_untouched() {
        assert_eq!(compact("halo", 20), "halo");
    }

    #[test]
    fn marks_and_bounds_long_text() {
        let text = "a".repeat(500);
        let out = compact(&text, 100);
        assert!(out.contains("dipotong"));
        assert!(out.chars().count() <= 100);
    }

    #[test]
    fn tail_keeps_the_end() {
        let mut text = String::new();
        for index in 0..50 {
            text.push_str(&format!("{index},"));
        }
        let out = tail(&text, 40);
        assert!(out.starts_with("[…]"));
        assert!(out.ends_with(','));
        assert!(out.chars().count() <= 40);
    }
}
