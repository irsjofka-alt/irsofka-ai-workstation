//! Pembaca `config/engines.json`.
//!
//! Registry adalah kontrak yang sudah dipakai `cross_verify.py`, `local_llm.py`, dan
//! Treasury. Modul ini tidak menggantikannya — ia hanya memberi daemon akses bertipe
//! ke isi yang sama, sehingga menambah mesin tetap cukup dengan menyunting satu berkas.
//!
//! Prinsip pemuatan: satu entri rusak tidak boleh menjatuhkan seluruh registry.
//! Entri yang gagal dicatat di `skipped` dan dilaporkan oleh `/api/engines`.

use crate::adapters::types::EngineKind;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QuotaGate {
    #[serde(default)]
    pub engine: Option<String>,
    #[serde(default)]
    pub scope: Option<String>,
    #[serde(default)]
    pub windows: Vec<String>,
    #[serde(default)]
    pub min_fraction: Option<f64>,
    #[serde(default)]
    pub threshold_meaning: Option<String>,
}

/// Satu mesin sebagaimana dinyatakan registry. `raw` menyimpan blok yang belum
/// bertipe (`usage`, catatan, kunci masa depan) supaya console bisa menampilkannya
/// tanpa kehilangan informasi.
#[derive(Debug, Clone, Serialize)]
pub struct EngineSpec {
    pub key: String,
    pub kind: EngineKind,
    pub role: Option<String>,
    pub model: Option<String>,
    pub binary: Option<String>,
    pub tab: Option<String>,
    pub template: Option<Vec<String>>,
    /// Argumen pembatas keluaran yang benar-benar dikenal CLI ini, mis.
    /// `["--max-output-tokens", "{value}"]`. Kosong = engine tidak mendukungnya,
    /// dan batas dari worker tidak dikirim (bukan dikarang).
    pub max_tokens_arg: Option<Vec<String>>,
    pub quota_gate: Option<QuotaGate>,
    pub raw: Value,
}

#[derive(Debug)]
pub enum RegistryError {
    Io { path: PathBuf, message: String },
    Json { path: PathBuf, message: String },
    Shape { path: PathBuf, message: String },
}

impl std::fmt::Display for RegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io { path, message } => {
                write!(f, "tidak bisa membaca {}: {message}", path.display())
            }
            Self::Json { path, message } => {
                write!(f, "JSON tidak sah di {}: {message}", path.display())
            }
            Self::Shape { path, message } => {
                write!(f, "bentuk registry tidak sesuai di {}: {message}", path.display())
            }
        }
    }
}

impl std::error::Error for RegistryError {}

#[derive(Debug)]
pub struct EngineRegistry {
    engines: BTreeMap<String, EngineSpec>,
    /// Peran → daftar kunci engine berurutan. Urutannya adalah urutan percobaan
    /// saat sebuah run memakai nama peran (mis. `decider`) dan mesin pertama gagal.
    groups: BTreeMap<String, Vec<String>>,
    skipped: Vec<(String, String)>,
    source: PathBuf,
}

impl EngineRegistry {
    pub fn load(path: impl AsRef<Path>) -> Result<Self, RegistryError> {
        let path = path.as_ref();
        let text = std::fs::read_to_string(path).map_err(|e| RegistryError::Io {
            path: path.to_path_buf(),
            message: e.to_string(),
        })?;
        Self::from_json(&text, path)
    }

    pub fn from_json(text: &str, source: &Path) -> Result<Self, RegistryError> {
        let root: Value = serde_json::from_str(text).map_err(|e| RegistryError::Json {
            path: source.to_path_buf(),
            message: e.to_string(),
        })?;

        let engines_value = root.get("engines").ok_or_else(|| RegistryError::Shape {
            path: source.to_path_buf(),
            message: "kunci \"engines\" tidak ada".into(),
        })?;

        let map = engines_value.as_object().ok_or_else(|| RegistryError::Shape {
            path: source.to_path_buf(),
            message: "\"engines\" bukan object".into(),
        })?;

        let groups = root
            .get("role_groups")
            .and_then(Value::as_object)
            .map(|items| {
                items
                    .iter()
                    .filter(|(name, _)| !name.starts_with('_'))
                    .filter_map(|(name, list)| {
                        let keys: Vec<String> = list
                            .as_array()?
                            .iter()
                            .filter_map(Value::as_str)
                            .filter(|key| map.contains_key(*key))
                            .map(str::to_string)
                            .collect();
                        (!keys.is_empty()).then(|| (name.clone(), keys))
                    })
                    .collect()
            })
            .unwrap_or_default();

        let mut engines = BTreeMap::new();
        let mut skipped = Vec::new();

        for (key, value) in map {
            match EngineSpec::from_value(key, value) {
                Ok(spec) => {
                    engines.insert(key.clone(), spec);
                }
                Err(message) => skipped.push((key.clone(), message)),
            }
        }

        // Override model dari console diterapkan di sini supaya runner, API, dan
        // pemilih model melihat nilai yang sama.
        if let Some(overrides) = load_model_overrides() {
            for (key, model) in overrides {
                if let Some(spec) = engines.get_mut(&key) {
                    spec.model = Some(model);
                }
            }
        }

        Ok(Self {
            engines,
            groups,
            skipped,
            source: source.to_path_buf(),
        })
    }

    pub fn group(&self, name: &str) -> Option<&Vec<String>> {
        self.groups.get(name)
    }

    pub fn groups(&self) -> &BTreeMap<String, Vec<String>> {
        &self.groups
    }

    pub fn get(&self, key: &str) -> Option<&EngineSpec> {
        self.engines.get(key)
    }

    pub fn require(&self, key: &str) -> Result<&EngineSpec, RegistryError> {
        self.get(key).ok_or_else(|| RegistryError::Shape {
            path: self.source.clone(),
            message: format!("engine \"{key}\" tidak terdaftar"),
        })
    }

    pub fn keys(&self) -> impl Iterator<Item = &str> {
        self.engines.keys().map(String::as_str)
    }

    pub fn iter(&self) -> impl Iterator<Item = &EngineSpec> {
        self.engines.values()
    }

    pub fn by_kind(&self, kind: EngineKind) -> Vec<&EngineSpec> {
        self.engines.values().filter(|e| e.kind == kind).collect()
    }

    pub fn len(&self) -> usize {
        self.engines.len()
    }

    pub fn is_empty(&self) -> bool {
        self.engines.is_empty()
    }

    /// Entri yang ditolak saat dimuat: (kunci, alasan). Ditampilkan di console,
    /// tidak disembunyikan — registry yang diam-diam kehilangan mesin lebih buruk
    /// daripada registry yang mengaku.
    pub fn skipped(&self) -> &[(String, String)] {
        &self.skipped
    }

    pub fn source(&self) -> &Path {
        &self.source
    }
}

fn load_model_overrides() -> Option<BTreeMap<String, String>> {
    let text = std::fs::read_to_string(crate::paths::engine_overrides_path()).ok()?;
    serde_json::from_str(&text).ok()
}

impl EngineSpec {
    fn from_value(key: &str, value: &Value) -> Result<Self, String> {
        let obj = value
            .as_object()
            .ok_or_else(|| "entri engine bukan object".to_string())?;

        let kind = match obj.get("kind").and_then(Value::as_str) {
            Some(k) => EngineKind::from_key(k),
            None => return Err("kunci \"kind\" tidak ada".into()),
        };
        if kind == EngineKind::Unknown {
            return Err("kind tidak dikenal (harus pane, cli, atau ollama)".into());
        }

        let as_string = |name: &str| obj.get(name).and_then(Value::as_str).map(str::to_string);

        let template = match obj.get("template") {
            Some(Value::Array(items)) => {
                let mut out = Vec::with_capacity(items.len());
                for item in items {
                    match item.as_str() {
                        Some(s) => out.push(s.to_string()),
                        None => return Err("\"template\" harus berisi string".into()),
                    }
                }
                Some(out)
            }
            Some(_) => return Err("\"template\" harus array".into()),
            None => None,
        };

        let max_tokens_arg = match obj.get("max_tokens_arg") {
            Some(Value::Array(items)) => {
                let mut out = Vec::new();
                for item in items {
                    match item.as_str() {
                        Some(s) => out.push(s.to_string()),
                        None => return Err("\"max_tokens_arg\" harus berisi string".into()),
                    }
                }
                Some(out)
            }
            Some(_) => return Err("\"max_tokens_arg\" harus array".into()),
            None => None,
        };

        let quota_gate = match obj.get("quota_gate") {
            Some(v) => Some(
                serde_json::from_value::<QuotaGate>(v.clone())
                    .map_err(|e| format!("quota_gate tidak sah: {e}"))?,
            ),
            None => None,
        };

        Ok(Self {
            key: key.to_string(),
            kind,
            role: as_string("role"),
            model: as_string("model"),
            binary: as_string("binary"),
            tab: as_string("tab"),
            template,
            max_tokens_arg,
            quota_gate,
            raw: value.clone(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn satu_entri_rusak_tidak_menjatuhkan_registry() {
        let text = r#"{
            "engines": {
                "baik": { "kind": "cli", "binary": "agy", "model": "m" },
                "tanpa_kind": { "binary": "x" },
                "aneh": { "kind": "quantum" }
            }
        }"#;
        let reg = EngineRegistry::from_json(text, Path::new("uji.json")).unwrap();
        assert_eq!(reg.len(), 1);
        assert_eq!(reg.skipped().len(), 2);
        assert!(reg.get("baik").is_some());
        assert_eq!(reg.get("baik").unwrap().kind, EngineKind::Cli);
    }

    #[test]
    fn quota_gate_dan_template_terbaca() {
        let text = r#"{
            "engines": {
                "claude": {
                    "kind": "cli",
                    "binary": "agy",
                    "template": ["agy", "--model", "{model}", "{prompt}"],
                    "quota_gate": { "windows": ["5h", "weekly"], "min_fraction": 0.2 }
                }
            }
        }"#;
        let reg = EngineRegistry::from_json(text, Path::new("uji.json")).unwrap();
        let spec = reg.require("claude").unwrap();
        assert_eq!(spec.template.as_ref().unwrap().len(), 4);
        assert_eq!(spec.quota_gate.as_ref().unwrap().windows, vec!["5h", "weekly"]);
    }

    #[test]
    fn jenis_mesin_dipisahkan() {
        let text = r#"{
            "engines": {
                "q": { "kind": "pane" },
                "c": { "kind": "cli" },
                "l": { "kind": "ollama" }
            }
        }"#;
        let reg = EngineRegistry::from_json(text, Path::new("uji.json")).unwrap();
        assert_eq!(reg.by_kind(EngineKind::Pane).len(), 1);
        assert_eq!(reg.by_kind(EngineKind::Cli).len(), 1);
        assert_eq!(reg.by_kind(EngineKind::Ollama).len(), 1);
    }
}
