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
    let head_raw: String = text.chars().take(head_budget).collect();
    let head = if let Some(last_ws) = head_raw.rfind(|c: char| c.is_whitespace() || c == '\n') {
        if last_ws > head_budget / 2 {
            head_raw[..last_ws].trim_end().to_string()
        } else {
            head_raw
        }
    } else {
        head_raw
    };
    let tail_start = count.saturating_sub(tail_budget);
    let tail_raw: String = text.chars().skip(tail_start).collect();
    let tail = if let Some(first_ws) = tail_raw.find(|c: char| c.is_whitespace() || c == '\n') {
        if first_ws < tail_budget / 2 {
            tail_raw[first_ws..].trim_start().to_string()
        } else {
            tail_raw
        }
    } else {
        tail_raw
    };
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
    let raw: String = text.chars().skip(start).collect();
    let clean = if let Some(first_nl) = raw.find('\n') {
        if first_nl < max_chars / 3 {
            raw[first_nl + 1..].trim_start().to_string()
        } else if let Some(first_ws) = raw.find(|c: char| c.is_whitespace()) {
            raw[first_ws..].trim_start().to_string()
        } else {
            raw
        }
    } else if let Some(first_ws) = raw.find(|c: char| c.is_whitespace()) {
        raw[first_ws..].trim_start().to_string()
    } else {
        raw
    };
    format!("[…]\n{clean}")
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
    fn compact_preserves_word_boundaries() {
        let text = "kata pertama kalimat ini cukup panjang dan akan dipotong dengan rapi di batas spasi bukan di tengah huruf";
        let out = compact(text, 50);
        assert!(out.contains("dipotong"));
        assert!(out.chars().count() <= 50);
        // Memastikan tidak memotong di tengah kata sebelum pemotongan
        assert!(!out.starts_with("kata pertama kalima\n"));
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

    #[test]
    fn tail_preserves_clean_boundary() {
        let text = "baris satu\nbaris dua ada kata panjang\nbaris tiga tujuan akhir";
        let out = tail(text, 35);
        assert!(out.starts_with("[…]"));
        assert!(out.contains("baris tiga tujuan akhir"));
        assert!(out.chars().count() <= 35);
    }
}
