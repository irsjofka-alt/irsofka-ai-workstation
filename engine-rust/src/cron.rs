//! Penjadwal cron lima kolom: menit jam tanggal bulan hari.
//!
//! Ditulis sendiri, bukan mengambil crate, karena aturan yang dipakai station hanya
//! butuh lima kolom dan semantik Vixie yang sederhana: `*`, `*/n`, `a`, `a-b`,
//! `a-b/n`, dan daftar berkoma. Tanggal dan hari sama-sama dibatasi berarti **atau**
//! (bukan dan), sesuai cron klasik.
//!
//! Zona waktu: `UTC` dihitung apa adanya; nama lain memakai zona lokal mesin.
//! Nama IANA belum diterjemahkan per zona — itu ditunda sampai ada kebutuhan nyata,
//! dan console menampilkan zona yang dipakai agar tidak menipu.

use chrono::{DateTime, Datelike, Duration, Local, NaiveDateTime, TimeZone, Timelike, Utc};
use std::process::Command;

#[derive(Debug, Clone)]
pub(crate) struct Cron {
    minutes: Vec<bool>,
    hours: Vec<bool>,
    days: Vec<bool>,
    months: Vec<bool>,
    weekdays: Vec<bool>,
    dom_restricted: bool,
    dow_restricted: bool,
}

impl Cron {
    pub(crate) fn parse(expr: &str) -> Result<Self, String> {
        let fields: Vec<&str> = expr.split_whitespace().collect();
        if fields.len() != 5 {
            return Err("cron harus 5 kolom: menit jam tanggal bulan hari".to_string());
        }
        Ok(Self {
            minutes: parse_field(fields[0], 0, 59)?,
            hours: parse_field(fields[1], 0, 23)?,
            days: parse_field(fields[2], 1, 31)?,
            months: parse_field(fields[3], 1, 12)?,
            weekdays: parse_field(fields[4], 0, 6)?,
            dom_restricted: fields[2] != "*",
            dow_restricted: fields[4] != "*",
        })
    }

    fn matches(&self, at: &NaiveDateTime) -> bool {
        if !self.minutes[at.minute() as usize] || !self.hours[at.hour() as usize] {
            return false;
        }
        // Kolom bulan diindeks 1..=12, sama seperti tanggal.
        if !self.months[at.month() as usize] {
            return false;
        }
        let dom = self.days[at.day() as usize];
        let dow = self.weekdays[at.weekday().num_days_from_sunday() as usize];
        match (self.dom_restricted, self.dow_restricted) {
            (true, true) => dom || dow,
            (true, false) => dom,
            (false, true) => dow,
            (false, false) => true,
        }
    }
}

fn parse_field(spec: &str, min: u32, max: u32) -> Result<Vec<bool>, String> {
    let mut set = vec![false; (max + 1) as usize];
    if spec.trim().is_empty() {
        return Err("kolom cron kosong".to_string());
    }

    for part in spec.split(',') {
        let (range, step) = match part.split_once('/') {
            Some((range, step)) => {
                let step: u32 = step
                    .parse()
                    .map_err(|_| format!("langkah \"{step}\" bukan angka"))?;
                if step == 0 {
                    return Err("langkah cron tidak boleh 0".to_string());
                }
                (range, step)
            }
            None => (part, 1),
        };

        let (start, end) = if range == "*" {
            (min, max)
        } else if let Some((a, b)) = range.split_once('-') {
            let start: u32 = a
                .parse()
                .map_err(|_| format!("nilai \"{a}\" bukan angka"))?;
            let end: u32 = b
                .parse()
                .map_err(|_| format!("nilai \"{b}\" bukan angka"))?;
            (start, end)
        } else {
            let value: u32 = range
                .parse()
                .map_err(|_| format!("nilai \"{range}\" bukan angka"))?;
            // `a/n` berarti dari a sampai batas atas.
            (value, if part.contains('/') { max } else { value })
        };

        if start < min || end > max || start > end {
            return Err(format!(
                "rentang {start}-{end} di luar batas {min}-{max}"
            ));
        }
        let mut value = start;
        while value <= end {
            set[value as usize] = true;
            value += step;
        }
    }
    Ok(set)
}

/// Jadwal berikutnya setelah `from`. `timezone` hanya membedakan `UTC` dari lokal.
pub(crate) fn next_after(
    expr: &str,
    from: DateTime<Utc>,
    timezone: &str,
) -> Result<DateTime<Utc>, String> {
    let cron = Cron::parse(expr)?;
    let start = wall_time(from, timezone)?;
    let mut cursor = start
        .with_second(0)
        .and_then(|value| value.with_nanosecond(0))
        .unwrap_or(start)
        + Duration::minutes(1);

    // Batas dua tahun mencegah ekspresi mustahil (mis. 31 Februari) berputar selamanya.
    for _ in 0..(366 * 24 * 60 * 2) {
        if cron.matches(&cursor) {
            return to_utc(cursor, timezone);
        }
        cursor += Duration::minutes(1);
    }
    Err("tidak ada jadwal berikutnya dalam dua tahun".to_string())
}

/// Waktu dinding (wall clock) di zona yang diminta. Nama IANA diselesaikan oleh
/// tzdata sistem lewat `date`, sehingga tidak perlu crate tambahan.
fn wall_time(from: DateTime<Utc>, timezone: &str) -> Result<NaiveDateTime, String> {
    match timezone {
        "UTC" => Ok(from.naive_utc()),
        "local" | "" => Ok(from.with_timezone(&Local).naive_local()),
        name => {
            // `date` diam-diam jatuh ke UTC untuk nama zona yang tidak dikenal,
            // jadi keberadaan berkas tzdata diperiksa lebih dulu.
            if !zone_exists(name) {
                return Err(format!("zona waktu \"{name}\" tidak dikenal"));
            }
            let output = Command::new("date")
                .env("TZ", name)
                .args([
                    "-d",
                    &format!("@{}", from.timestamp()),
                    "+%Y-%m-%dT%H:%M:%S",
                ])
                .output()
                .map_err(|error| format!("zona waktu tidak bisa dibaca: {error}"))?;
            if !output.status.success() {
                return Err(format!("zona waktu \"{name}\" tidak dikenal"));
            }
            let text = String::from_utf8_lossy(&output.stdout);
            NaiveDateTime::parse_from_str(text.trim(), "%Y-%m-%dT%H:%M:%S")
                .map_err(|error| format!("zona waktu \"{name}\": {error}"))
        }
    }
}

fn zone_exists(name: &str) -> bool {
    if name.contains("..") || name.starts_with('/') {
        return false;
    }
    std::path::Path::new(&format!("/usr/share/zoneinfo/{name}")).is_file()
}

fn to_utc(naive: NaiveDateTime, timezone: &str) -> Result<DateTime<Utc>, String> {
    match timezone {
        "UTC" => Ok(Utc.from_utc_datetime(&naive)),
        "local" | "" => Local
            .from_local_datetime(&naive)
            .earliest()
            .map(|at| at.with_timezone(&Utc))
            .ok_or_else(|| "waktu lokal ambigu (DST)".to_string()),
        name => {
            let output = Command::new("date")
                .env("TZ", name)
                .args([
                    "-d",
                    &naive.format("%Y-%m-%d %H:%M:%S").to_string(),
                    "+%s",
                ])
                .output()
                .map_err(|error| format!("zona waktu tidak bisa dihitung: {error}"))?;
            if !output.status.success() {
                return Err(format!("zona waktu \"{name}\" tidak dikenal"));
            }
            let epoch: i64 = String::from_utf8_lossy(&output.stdout)
                .trim()
                .parse()
                .map_err(|_| "hasil konversi zona waktu tidak sah".to_string())?;
            Utc.timestamp_opt(epoch, 0)
                .single()
                .ok_or_else(|| "epoch tidak sah".to_string())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(value: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(value).unwrap().with_timezone(&Utc)
    }

    #[test]
    fn setiap_lima_belas_menit() {
        let next = next_after("*/15 * * * *", at("2026-01-01T00:02:00Z"), "UTC").unwrap();
        assert_eq!(next.to_rfc3339(), "2026-01-01T00:15:00+00:00");
    }

    #[test]
    fn hari_kerja_jam_sembilan() {
        // Jumat 10:00 UTC -> Senin 09:00 UTC.
        let next = next_after("0 9 * * 1-5", at("2026-01-02T10:00:00Z"), "UTC").unwrap();
        assert_eq!(next.to_rfc3339(), "2026-01-05T09:00:00+00:00");
    }

    #[test]
    fn tanggal_satu_setiap_bulan() {
        let next = next_after("0 0 1 * *", at("2026-01-15T00:00:00Z"), "UTC").unwrap();
        assert_eq!(next.to_rfc3339(), "2026-02-01T00:00:00+00:00");
    }

    #[test]
    fn ekspresi_salah_ditolak() {
        assert!(Cron::parse("* * *").is_err());
        assert!(Cron::parse("61 * * * *").is_err());
        assert!(Cron::parse("*/0 * * * *").is_err());
        assert!(Cron::parse("a * * * *").is_err());
    }

    #[test]
    fn zona_iana_dihormati() {
        // 09:00 di Asia/Jakarta = 02:00 UTC.
        let next = next_after("0 9 * * *", at("2026-01-01T00:00:00Z"), "Asia/Jakarta").unwrap();
        assert_eq!(next.to_rfc3339(), "2026-01-01T02:00:00+00:00");
        assert!(next_after("0 9 * * *", at("2026-01-01T00:00:00Z"), "Bukan/Zona").is_err());
    }

    #[test]
    fn daftar_dan_rentang() {
        let next = next_after("0 8,12 * * *", at("2026-01-01T09:00:00Z"), "UTC").unwrap();
        assert_eq!(next.to_rfc3339(), "2026-01-01T12:00:00+00:00");
        let next = next_after("30 6 * * *", at("2026-01-01T06:31:00Z"), "UTC").unwrap();
        assert_eq!(next.to_rfc3339(), "2026-01-02T06:30:00+00:00");
    }
}
