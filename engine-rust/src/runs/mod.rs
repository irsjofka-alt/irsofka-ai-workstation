//! Run engine headless.
//!
//! Satu run = satu proses anak, dibaca baris demi baris, dan setiap baris menjadi
//! event yang bisa dipolling console. Tidak ada pane, tidak ada ketikan: yang
//! menentukan perintah adalah `template` di `config/engines.json`, bukan ingatan kode.
//!
//! Sesi disimpan di memori. Riwayat lintas-restart belum ada di tahap ini; bentuk
//! `RunRecord` sudah sama dengan tabel `waker_run` supaya pemindahan ke database
//! nanti tidak mengubah kontrak HTTP.

use chrono::Utc;
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::{Mutex, RwLock, Semaphore};

use crate::adapters::{EngineKind, EngineRegistry, EngineSpec};
use crate::api::providers::Lane;
use crate::paths::engines_registry_path;
use crate::store::Store;

#[derive(Debug, Clone, Serialize)]
pub(crate) struct RunEvent {
    pub(crate) seq: u64,
    pub(crate) at: String,
    /// `started` | `output` | `stderr` | `status`
    pub(crate) kind: String,
    pub(crate) text: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct RunRecord {
    pub(crate) id: String,
    pub(crate) waker_id: String,
    pub(crate) engine_key: String,
    pub(crate) model: Option<String>,
    pub(crate) reasoning_effort: Option<String>,
    pub(crate) context_window: Option<u64>,
    /// Pesan pengguna apa adanya (tanpa system prompt), untuk riwayat chat.
    pub(crate) content: String,
    /// Prompt lengkap yang benar-benar dikirim ke engine.
    pub(crate) prompt: String,
    pub(crate) workspace: String,
    /// `running` | `succeeded` | `failed`
    pub(crate) status: String,
    pub(crate) output: String,
    pub(crate) input_tokens: Option<i64>,
    pub(crate) output_tokens: Option<i64>,
    pub(crate) cache_read_tokens: Option<i64>,
    pub(crate) error: Option<String>,
    pub(crate) exit_code: Option<i32>,
    pub(crate) started_at: String,
    pub(crate) finished_at: Option<String>,
    pub(crate) events: Vec<RunEvent>,
    /// Nomor urut event berikutnya. Terpisah dari `events` supaya event yang
    /// dibuang dari memori tidak membuat nomor di database bertabrakan.
    #[serde(skip)]
    pub(crate) next_seq: u64,
    /// Kapan engine terakhir menunjukkan tanda kehidupan. Batas run dihitung
    /// dari sini, bukan dari waktu mulai, supaya pekerjaan yang masih mengalir
    /// tidak dipotong.
    #[serde(skip)]
    pub(crate) last_activity: Instant,
}

#[derive(Clone)]
pub(crate) struct StartParams {
    pub(crate) waker_id: String,
    pub(crate) engine_key: String,
    pub(crate) model: Option<String>,
    pub(crate) reasoning_effort: Option<String>,
    pub(crate) context_window: Option<u64>,
    pub(crate) prompt: String,
    pub(crate) system_prompt: String,
    pub(crate) workspace: String,
    pub(crate) answer_style: Option<String>,
    pub(crate) max_output_tokens: Option<i64>,
    /// Automation berdiri sendiri; ia tidak perlu membawa riwayat obrolan.
    pub(crate) include_history: bool,
    /// Hanya automation yang boleh memakai cache hasil identik. Chat/IM tidak,
    /// karena jawaban percakapan tidak boleh dipakai ulang untuk konteks lain.
    pub(crate) allow_cache: bool,
    /// Sisa kandidat ketika `engine_key` adalah nama grup peran; dipakai untuk
    /// melanjutkan ke model berikutnya bila yang ini gagal.
    pub(crate) fallback_engines: Vec<String>,
    /// Failover tidak boleh menyimpan pesan pengguna dua kali di percakapan.
    pub(crate) save_message: bool,
}

/// Batas memori per run: event dan keluaran tidak boleh tumbuh tanpa ujung.
const MAX_EVENTS: usize = 4_000;
const MAX_OUTPUT_BYTES: usize = 2 * 1024 * 1024;

#[derive(Clone)]
pub(crate) struct RunManager {
    inner: Arc<RwLock<HashMap<String, Arc<Mutex<RunRecord>>>>>,
    store: Store,
    /// Slot eksekusi paralel. Agen yang menunggu tetap `queued`, bukan menumpuk proses.
    permits: Arc<Semaphore>,
    max_parallel: usize,
    /// Jumlah run selesai yang disimpan di memori (database tetap menyimpan semua).
    max_keep: usize,
    /// Batas *menganggur*: run dipotong setelah selama ini tidak ada event,
    /// bukan setelah selama ini berjalan.
    idle_timeout: Duration,
    /// Penanda batal per run + PID process group untuk membunuh proses yang berjalan.
    cancel_flags: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
    pids: Arc<Mutex<HashMap<String, u32>>>,
}

impl RunManager {
    pub(crate) fn new(store: Store) -> Self {
        let max_parallel = std::env::var("STATION_MAX_PARALLEL")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(4)
            .max(1);
        let max_keep = std::env::var("STATION_KEEP_RUNS")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(60)
            .max(1);
        // Nama variabel lama dipertahankan, tetapi artinya kini batas
        // menganggur, bukan batas total.
        let idle_secs = std::env::var("STATION_RUN_TIMEOUT_SECS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(1_800)
            .max(2);

        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            store,
            permits: Arc::new(Semaphore::new(max_parallel)),
            max_parallel,
            max_keep,
            idle_timeout: Duration::from_secs(idle_secs),
            cancel_flags: Arc::new(Mutex::new(HashMap::new())),
            pids: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Membatalkan run. Proses CLI dibunuh lewat process group; Ollama berhenti
    /// pada fragmen berikutnya (curl ikut mati karena `kill_on_drop`).
    pub(crate) async fn cancel(&self, run_id: &str) -> bool {
        let flag = self.cancel_flags.lock().await.get(run_id).cloned();
        let pid = self.pids.lock().await.get(run_id).copied();
        if flag.is_none() && pid.is_none() {
            return false;
        }
        if let Some(flag) = &flag {
            flag.store(true, Ordering::SeqCst);
        }
        if let Some(pid) = pid {
            kill_process_group(Some(pid));
        }
        true
    }

    /// (paralel, simpan-di-memori, timeout detik) — ditampilkan di console.
    pub(crate) fn limits(&self) -> (usize, usize, u64) {
        (self.max_parallel, self.max_keep, self.idle_timeout.as_secs())
    }

    /// Membuang run selesai tertua ketika melewati `max_keep`. Run yang masih
    /// berjalan/queued tidak pernah disentuh.
    async fn prune(&self) {
        // Cache kedaluwarsa dibuang bersama prune memori; tidak ada timer terpisah.
        self.store.cache_prune(cache_ttl_secs()).await;
        let mut finished: Vec<(String, String)> = Vec::new();
        {
            let map = self.inner.read().await;
            for (key, handle) in map.iter() {
                if let Ok(record) = handle.try_lock() {
                    if record.status != "running" && record.status != "queued" {
                        let at = record
                            .finished_at
                            .clone()
                            .unwrap_or_else(|| record.started_at.clone());
                        finished.push((key.clone(), at));
                    }
                }
            }
        }
        if finished.len() <= self.max_keep {
            return;
        }
        finished.sort_by(|a, b| a.1.cmp(&b.1));
        let remove = finished.len() - self.max_keep;
        let mut map = self.inner.write().await;
        for (key, _) in finished.into_iter().take(remove) {
            map.remove(&key);
        }
    }

    /// Mengisi memori dari database saat start, supaya riwayat tidak hilang
    /// setiap daemon di-restart.
    pub(crate) async fn seed(&self, runs: Vec<RunRecord>) {
        let mut map = self.inner.write().await;
        for mut run in runs {
            run.output = display_output(&run.output);
            map.insert(run.id.clone(), Arc::new(Mutex::new(run)));
        }
    }

    pub(crate) async fn get(&self, id: &str) -> Option<RunRecord> {
        let handle = {
            let map = self.inner.read().await;
            map.get(id).cloned()
        };
        match handle {
            Some(record) => Some(record.lock().await.clone()),
            None => None,
        }
    }

    /// Ringkasan tanpa event, terbaru lebih dulu.
    pub(crate) async fn list(&self, waker_id: Option<&str>) -> Vec<RunRecord> {
        let handles: Vec<Arc<Mutex<RunRecord>>> = {
            let map = self.inner.read().await;
            map.values().cloned().collect()
        };
        let mut out = Vec::with_capacity(handles.len());
        for handle in handles {
            let mut snapshot = handle.lock().await.clone();
            if let Some(wanted) = waker_id {
                if snapshot.waker_id != wanted {
                    continue;
                }
            }
            snapshot.events.clear();
            out.push(snapshot);
        }
        out.sort_by(|a, b| b.started_at.cmp(&a.started_at));
        out
    }

    /// Failover memanggil `start` lagi dari dalam task yang di-spawn, sehingga
    /// future-nya rekursif. Tipe `Pin<Box<dyn Future + Send>>` memutus inferensi
    /// itu secara eksplisit; tanpa ini rustc tidak bisa membuktikan `Send`.
    pub(crate) fn start(
        &self,
        params: StartParams,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<String, String>> + Send + '_>,
    > {
        Box::pin(self.start_inner(params))
    }

    async fn start_inner(&self, params: StartParams) -> Result<String, String> {
        let mut params = params;
        // `auto` diselesaikan di sini, bukan di UI: keputusan harus memakai meter
        // kuota terbaru dan tercatat di event run supaya bisa diaudit.
        let mut routing_note: Option<String> = None;
        if params.engine_key == "auto" {
            let suggestion = crate::quota::suggest(&self.store, None).await?;
            let engine_key = suggestion
                .get("engine_key")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string);
            let Some(engine_key) = engine_key else {
                return Err(format!(
                    "routing otomatis tidak menemukan engine: {}",
                    suggestion
                        .get("reason")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("alasan tidak dilaporkan")
                ));
            };
            let model = suggestion
                .get("model")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string);
            routing_note = Some(format!(
                "auto → {engine_key} ({})",
                suggestion
                    .get("reason")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("")
            ));
            params.engine_key = engine_key;
            if params.model.as_deref().unwrap_or("").is_empty() {
                params.model = model;
            }
        }
        let registry = EngineRegistry::load(engines_registry_path()).map_err(|e| e.to_string())?;
        // Nama peran (mis. `decider`) bukan engine: ia adalah daftar model berurutan.
        // Yang pertama dipakai sekarang, sisanya menjadi cadangan saat run gagal.
        if params.fallback_engines.is_empty() {
            if let Some(candidates) = registry.group(&params.engine_key).cloned() {
                if let Some(first) = candidates.first().cloned() {
                    let rest: Vec<String> = candidates.into_iter().skip(1).collect();
                    if routing_note.is_none() {
                        routing_note = Some(format!(
                            "grup {} → {}{}",
                            params.engine_key,
                            first,
                            if rest.is_empty() {
                                String::new()
                            } else {
                                format!(" (cadangan: {})", rest.join(" → "))
                            }
                        ));
                    }
                    params.engine_key = first;
                    params.fallback_engines = rest;
                }
            }
        }
        let spec = registry
            .require(&params.engine_key)
            .map_err(|e| e.to_string())?;

        // Urutan sengaja stabil untuk prefix cache: system (termasuk gaya) →
        // pengetahuan → pesan pengguna. Tidak ada timestamp di bagian awal.
        let mut system = params.system_prompt.trim().to_string();
        match params.answer_style.as_deref().unwrap_or("normal") {
            "concise" => {
                if !system.is_empty() {
                    system.push_str("\n\n");
                }
                system.push_str(
                    "Gaya jawaban: ringkas. Maksimal tiga kalimat kecuali diminta detail.",
                );
            }
            "detailed" => {
                if !system.is_empty() {
                    system.push_str("\n\n");
                }
                system.push_str("Gaya jawaban: jelas dan bertahap; sebutkan asumsi penting.");
            }
            _ => {}
        }
        if let Some(limit) = params.max_output_tokens {
            if !system.is_empty() {
                system.push_str("\n\n");
            }
            system.push_str(&format!("Batas keluaran: sekitar {limit} token."));
        }

        // Kontrak, peta folder, dan pekerjaan terakhir dari brain ikut ke setiap
        // run secara adaptif berdasarkan intent prompt.
        let context_pack = crate::context::pack_for(&params.prompt);
        if !context_pack.trim().is_empty() {
            if !system.is_empty() {
                system.push_str("\n\n");
            }
            system.push_str(&context_pack);
        }

        // Percakapan dibuat lebih awal supaya riwayat bisa dibaca sebelum pesan
        // pengguna yang sekarang disimpan (pesan itu tidak boleh muncul dua kali).
        let conversation = self.store.ensure_conversation(&params.waker_id).await;

        let knowledge = crate::knowledge::context(
            &self.store,
            &params.waker_id,
            &params.prompt,
            3,
        )
        .await;
        let history = if params.include_history {
            crate::history::build(&self.store, conversation.as_deref()).await
        } else {
            String::new()
        };
        // Qoder/Antigravity punya runtime MCP sendiri. Menyuntikkan protokol
        // JSON ke prompt mereka membuat model memanggil tool bawaan yang di
        // mode headless bisa ditolak. Untuk engine itu, MCP diatur di
        // pengaturan engine; protokol prompt dipakai engine tanpa tool bawaan.
        let native_mcp = params.engine_key == "qoder"
            || spec.binary.as_deref() == Some("agy")
            || spec.tab.as_deref() == Some("antigravity");
        let tool_index = if native_mcp {
            String::new()
        } else {
            crate::mcp::prompt_index(&self.store, &params.waker_id).await
        };

        let mut prompt = String::new();
        if !system.is_empty() {
            prompt.push_str(&system);
            prompt.push_str("\n\n");
        }
        // Indeks tool stabil dan murah; detail skema tidak ikut sampai dipanggil.
        // Ditempatkan sebelum knowledge & riwayat agar prefix caching penyedia
        // (Gemini/Claude/OpenAI) tetap utuh dan menghasilkan cache hit lintas putaran.
        if !tool_index.is_empty() {
            prompt.push_str(&tool_index);
            prompt.push_str("\n\n");
        }
        if !knowledge.is_empty() {
            prompt.push_str("Konteks pengetahuan yang mungkin relevan:\n");
            prompt.push_str(&knowledge);
            prompt.push_str("\n\n");
        }
        if !history.is_empty() {
            prompt.push_str(&history);
            prompt.push_str("\n\n");
        }
        prompt.push_str(&params.prompt);
        // Ollama berbicara lewat HTTP, bukan CLI. Ia punya jalur sendiri supaya
        // "banyak agen" tidak berarti "banyak proses terminal".
        let is_ollama = spec.kind == EngineKind::Ollama;
        // Dua jenis ini berbicara lewat HTTP dan tidak butuh perintah CLI;
        // `build_command` akan menolaknya bila dipaksa masuk jalur CLI.
        let is_http = is_ollama || spec.kind == EngineKind::OpenAi;
        let (program, args) = if is_http {
            (format!("{} http", spec.kind.as_str()), Vec::new())
        } else {
            build_command(
                spec,
                params.model.as_deref(),
                params.reasoning_effort.as_deref(),
                params.context_window,
                params.max_output_tokens,
                &prompt,
                &params.workspace,
            )?
        };
        let run_model = params
            .model
            .clone()
            .or_else(|| spec.model.clone())
            .unwrap_or_default();

        let workspace = if std::path::Path::new(&params.workspace).is_dir() {
            params.workspace.clone()
        } else {
            crate::paths::home_dir().to_string_lossy().to_string()
        };

        // Cache hanya untuk Ollama: jalur HTTP tidak memanggil tool/shell, jadi
        // hasilnya tidak bergantung pada efek samping. Engine CLI bisa menjalankan
        // tool, sehingga hasil identik belum tentu aman dipakai ulang.
        let cache_key = if params.allow_cache && spec.kind == EngineKind::Ollama {
            Some(result_cache_key(&params.engine_key, &run_model, &prompt))
        } else {
            None
        };

        let id = new_run_id();
        let started = Utc::now().to_rfc3339();
        let record = RunRecord {
            id: id.clone(),
            waker_id: params.waker_id.clone(),
            engine_key: params.engine_key.clone(),
            model: params.model.clone().or_else(|| spec.model.clone()),
            reasoning_effort: params.reasoning_effort.clone(),
            context_window: params.context_window,
            content: params.prompt.clone(),
            prompt: prompt.clone(),
            workspace: workspace.clone(),
            status: "queued".to_string(),
            output: String::new(),
            input_tokens: None,
            output_tokens: None,
            cache_read_tokens: None,
            error: None,
            exit_code: None,
            started_at: started.clone(),
            finished_at: None,
            events: {
                let mut initial = vec![RunEvent {
                    seq: 0,
                    at: started.clone(),
                    kind: "started".to_string(),
                    text: format!(
                        "{program}{}{}",
                        if args.is_empty() { "" } else { " " },
                        args.join(" ")
                    ),
                }];
                if let Some(note) = routing_note.clone() {
                    initial.push(RunEvent {
                        seq: 1,
                        at: started.clone(),
                        kind: "routing".to_string(),
                        text: note,
                    });
                }
                initial
            },
            next_seq: if routing_note.is_some() { 2 } else { 1 },
            last_activity: Instant::now(),
        };

        let conversation_ref = conversation.as_deref();
        let _ = self
            .store
            .start_run(&record, conversation_ref, "queued")
            .await;
        if let Some(started_event) = record.events.first() {
            self.store.append_event(&record.id, started_event).await;
        }
        if let Some(note) = routing_note.as_deref() {
            let event = RunEvent {
                seq: 1,
                at: record.started_at.clone(),
                kind: "routing".to_string(),
                text: note.to_string(),
            };
            self.store.append_event(&record.id, &event).await;
        }
        if params.save_message {
            if let Some(chat) = conversation.as_deref() {
                let saved_content = crate::history::clean_user_content(&params.prompt);
                self.store
                    .save_message(chat, "user", saved_content, Some(&id))
                    .await;
            }
        }

        let handle = Arc::new(Mutex::new(record));
        {
            let mut map = self.inner.write().await;
            map.insert(id.clone(), handle.clone());
            // Batas keras agar peta tidak tumbuh tanpa ujung, terlepas dari prune.
            if map.len() > 400 {
                if let Some(oldest) = map
                    .iter()
                    .min_by_key(|(_, record)| {
                        record
                            .try_lock()
                            .map(|r| r.started_at.clone())
                            .unwrap_or_default()
                    })
                    .map(|(key, _)| key.clone())
                {
                    map.remove(&oldest);
                }
            }
        }

        publish_run_state(&handle).await;

        let manager = self.clone();
        let fallbacks_for_task = params.fallback_engines.clone();
        let params_for_failover = params.clone();
        let store = self.store.clone();
        let run_id = id.clone();
        let prompt_for_task = prompt.clone();
        let workspace_for_task = workspace.clone();
        let cache_key_for_task = cache_key.clone();
        let spec_for_task = spec.clone();
        let model_opt_for_task = params.model.clone();
        let effort_for_task = params.reasoning_effort.clone();
        let context_for_task = params.context_window;
        let max_tokens_for_task = params.max_output_tokens;
        let waker_for_task = params.waker_id.clone();

        // Cache dicek setelah pesan pengguna tersimpan: percakapan tetap utuh,
        // hanya eksekusi engine yang dilewati.
        if let Some(key) = cache_key.as_deref() {
            if let Some(cached) = self.store.cache_lookup(key, cache_ttl_secs()).await {
                let snapshot = {
                    let mut record = handle.lock().await;
                    record.status = "succeeded".to_string();
                    record.exit_code = Some(0);
                    record.output = cached.output;
                    record.input_tokens = cached.input_tokens;
                    record.output_tokens = cached.output_tokens;
                    record.finished_at = Some(Utc::now().to_rfc3339());
                    let at = record.finished_at.clone().unwrap_or_default();
                    let seq = record.next_seq;
                    record.next_seq += 1;
                    record.events.push(RunEvent {
                        seq,
                        at: at.clone(),
                        kind: "cache".to_string(),
                        text: "hasil dipakai ulang dari cache; engine tidak dipanggil"
                            .to_string(),
                    });
                    let seq = record.next_seq;
                    record.next_seq += 1;
                    record.events.push(RunEvent {
                        seq,
                        at,
                        kind: "status".to_string(),
                        text: "succeeded".to_string(),
                    });
                    record.clone()
                };
                for event in snapshot
                    .events
                    .iter()
                    .filter(|event| event.kind == "cache" || event.kind == "status")
                {
                    self.store.append_event(&snapshot.id, event).await;
                }
                // Cache hit tidak melewati jalur spawn yang biasanya mengisi
                // started_at; isi di sini supaya trace punya waktu mulai.
                self.store
                    .mark_run_running(&snapshot.id, &snapshot.started_at)
                    .await;
                self.store.finish_run(&snapshot).await;
                publish_run_state(&handle).await;
                if let Some(chat) = conversation.as_deref() {
                    self.store
                        .save_message(chat, "waker", &snapshot.output, Some(&snapshot.id))
                        .await;
                }
                self.prune().await;
                return Ok(id);
            }
        }

        tokio::spawn(async move {
            // Slot eksekusi: agen yang menunggu tetap `queued`, bukan menumpuk proses.
            let permit = manager.permits.clone().acquire_owned().await;
            let cancel_flag = Arc::new(AtomicBool::new(false));
            manager
                .cancel_flags
                .lock()
                .await
                .insert(run_id.clone(), cancel_flag.clone());
            let started_at = Utc::now().to_rfc3339();
            {
                let mut record = handle.lock().await;
                record.status = "running".to_string();
                record.started_at = started_at.clone();
                // Menunggu slot tidak dihitung menganggur.
                record.last_activity = Instant::now();
            }
            let _ = store.mark_run_running(&run_id, &started_at).await;
            publish_run_state(&handle).await;

            // Satu run bisa terdiri atas beberapa percobaan bila model meminta
            // tool MCP. Batasnya menganggur: setiap event yang dicatat
            // `push_event` memundurkan batas, jadi run yang masih bekerja boleh
            // berjalan lebih lama.
            let mut current_prompt = prompt_for_task.clone();
            let mut rounds = 0usize;
            let mut tools_enabled = true;
            let mut used_tools = false;
            let mut last_tool_note: Option<String> = None;
            let result = loop {
                {
                    let mut record = handle.lock().await;
                    record.output.clear();
                }
                if handle.lock().await.last_activity.elapsed() >= manager.idle_timeout {
                    break Err(idle_message(manager.idle_timeout.as_secs()));
                }
                let attempt = run_attempt(
                    &manager,
                    &store,
                    &handle,
                    &spec_for_task,
                    is_ollama,
                    &run_model,
                    model_opt_for_task.as_deref(),
                    effort_for_task.as_deref(),
                    context_for_task,
                    max_tokens_for_task,
                    &current_prompt,
                    &workspace_for_task,
                    &run_id,
                    &cancel_flag,
                    manager.idle_timeout,
                )
                .await;
                if cancel_flag.load(Ordering::SeqCst) {
                    break attempt;
                }
                if attempt.is_err() || !tools_enabled {
                    break attempt;
                }
                let output = {
                    let record = handle.lock().await;
                    record.output.clone()
                };
                // agy/qoder dapat membungkus jawaban dalam amplop JSON; cari
                // panggilan tool pada teks yang sudah dibuka.
                let candidate = unwrap_output(&output);
                let Some(call) = crate::mcp::parse_tool_call(&candidate) else {
                    break attempt;
                };
                if rounds >= mcp_max_rounds() {
                    // Satu percobaan terakhir tanpa tool supaya jawaban tetap ada.
                    tools_enabled = false;
                    current_prompt.push_str(
                        "\n\nJangan memanggil tool lagi. Jawab dengan informasi yang sudah ada.",
                    );
                    continue;
                }
                rounds += 1;
                used_tools = true;
                let decision =
                    crate::mcp::permission_for(&store, &waker_for_task, &call.server, &call.tool)
                        .await;
                let (tool_text, tool_error) = if decision == "allow" {
                    match crate::mcp::call_tool(&call.server, &call.tool, &call.arguments).await {
                        Ok(result) => (result.text, result.is_error),
                        Err(error) => (error, true),
                    }
                } else if decision == "ask" {
                    // `ask` bukan penolakan: permintaan dicatat ke inbox operator.
                    // Putusannya menjadi izin worker untuk tool itu, bukan sekali pakai.
                    let approval_id = {
                        let pending = store.list_approvals(Some("pending")).await;
                        let already = pending
                            .as_array()
                            .map(|items| {
                                items.iter().any(|item| {
                                    let payload = &item["payload"];
                                    payload["kind"].as_str() == Some("mcp-tool")
                                        && payload["waker_id"].as_str()
                                            == Some(waker_for_task.as_str())
                                        && payload["server"].as_str()
                                            == Some(call.server.as_str())
                                        && payload["tool"].as_str() == Some(call.tool.as_str())
                                })
                            })
                            .unwrap_or(false);
                        if already {
                            None
                        } else {
                            let payload = serde_json::json!({
                                "kind": "mcp-tool",
                                "waker_id": waker_for_task,
                                "server": call.server,
                                "tool": call.tool,
                                "arguments": call.arguments,
                                "run_id": run_id,
                            });
                            match store.create_approval("mcp", &payload).await {
                                Ok(id) => Some(id),
                                Err(error) => {
                                    push_event(
                                        &store,
                                        &handle,
                                        "tool",
                                        format!("gagal mencatat persetujuan: {error}"),
                                    )
                                    .await;
                                    None
                                }
                            }
                        }
                    };
                    let note = match approval_id {
                        Some(id) => format!("menunggu persetujuan operator (id {id})"),
                        None => "sudah menunggu persetujuan operator".to_string(),
                    };
                    (
                        format!(
                            "Tool {}.{} tidak dijalankan: izin worker \"ask\", permintaan \
                             dicatat ke inbox persetujuan dan {}. Setujui lewat halaman \
                             Persetujuan; putusannya berlaku sebagai izin mcp:{}:{}.",
                            call.server, call.tool, note, call.server, call.tool
                        ),
                        true,
                    )
                } else {
                    (
                        format!(
                            "Tool {}.{} tidak dijalankan: izin worker adalah \"{}\". \
                             Operator dapat mengizinkannya sebagai mcp:{}:{}.",
                            call.server, call.tool, decision, call.server, call.tool
                        ),
                        true,
                    )
                };
                // Izin selain `allow` mengakhiri putaran tool: model diberi tahu
                // sekali, lalu diminta menjawab dengan informasi yang ada.
                if decision != "allow" {
                    tools_enabled = false;
                }
                let summary = crate::compress::compact(&tool_text, 700);
                last_tool_note = Some(format!(
                    "{}.{} ({}) → {}\n{}",
                    call.server,
                    call.tool,
                    call.arguments,
                    if tool_error { "gagal" } else { "ok" },
                    summary
                ));
                push_event(
                    &store,
                    &handle,
                    "tool",
                    last_tool_note.clone().unwrap_or_default(),
                )
                .await;
                let arguments = serde_json::to_string(&call.arguments).unwrap_or_default();
                let closing = if tool_error {
                    "Jangan memanggil tool itu lagi. Jawab sekarang dengan informasi yang ada; \
                     jika akses ditolak, katakan dengan jelas."
                } else {
                    "Lanjutkan jawaban akhir. Jangan memanggil tool yang sama lagi."
                };
                current_prompt.push_str(&format!(
                    "\n\n[Panggilan tool]\n{{\"mcp_call\":{{\"server\":\"{}\",\"tool\":\"{}\",\"arguments\":{}}}}}\n\
                     [Hasil tool {}.{}]\n{}\n\n{}",
                    call.server,
                    call.tool,
                    arguments,
                    call.server,
                    call.tool,
                    crate::compress::compact(&tool_text, 4_000),
                    closing
                ));
            };

            // Model kecil kadang berhenti tanpa jawaban setelah tool. Jangan
            // tampilkan pesan kosong: sertakan hasil tool terakhir agar operator
            // tetap tahu apa yang terjadi.
            if used_tools && result.is_ok() {
                let mut record = handle.lock().await;
                if record.output.trim().is_empty() {
                    if let Some(note) = last_tool_note.as_deref() {
                        record.output = format!(
                            "Model tidak menghasilkan jawaban akhir setelah tool dijalankan.\n\n{note}"
                        );
                    }
                }
            }

            let was_cancelled = cancel_flag.load(Ordering::SeqCst);
            manager.pids.lock().await.remove(&run_id);
            manager.cancel_flags.lock().await.remove(&run_id);
            // Hasil yang memakai tool tidak di-cache: tool bisa mengembalikan data
            // waktu-nyata (VRAM, screenshot) yang tidak boleh dipakai ulang.
            let final_cache_key = if used_tools {
                None
            } else {
                cache_key_for_task.as_deref()
            };
            let status = finalize(
                &store,
                &handle,
                conversation.as_deref(),
                result,
                was_cancelled,
                final_cache_key,
            )
            .await;
            // Failover: grup peran mencoba model berikutnya hanya setelah yang ini
            // benar-benar gagal. Setiap percobaan tetap menjadi run sendiri, jadi
            // riwayat menunjukkan model mana yang menjawab dan mana yang tidak.
            if status == "failed" && !was_cancelled {
                if let Some((next, rest)) = fallbacks_for_task.split_first() {
                    let mut next_params = params_for_failover.clone();
                    next_params.engine_key = next.clone();
                    next_params.fallback_engines = rest.to_vec();
                    next_params.save_message = false;
                    let note = match manager.start(next_params).await {
                        Ok(next_run) => format!(
                            "{} gagal; lanjut ke {} (run {})",
                            params_for_failover.engine_key, next, next_run
                        ),
                        Err(error) => format!(
                            "{} gagal; {} tidak bisa dijalankan: {error}",
                            params_for_failover.engine_key, next
                        ),
                    };
                    push_event(&store, &handle, "failover", note).await;
                }
            }
            // Ringkasan bergulir hanya untuk run yang berhasil; run gagal tetap
            // tersimpan sebagai pesan dan akan tercakup pada ringkasan berikutnya.
            if status == "succeeded" {
                if let Some(chat) = conversation.as_deref() {
                    let summary_store = store.clone();
                    let chat = chat.to_string();
                    tokio::spawn(async move {
                        crate::history::maybe_summarize(&summary_store, &chat).await;
                    });
                }
            }
            manager.prune().await;
            drop(permit);
        });

        Ok(id)
    }
}

/// Seberapa sering batas menganggur diperiksa. Satu timer sepanjang batas
/// tidak tahu apakah engine masih bekerja, jadi pemeriksaan dibuat berkala.
const IDLE_POLL: Duration = Duration::from_secs(2);

enum Bounded<T> {
    Done(Result<T, String>),
    Idle,
}

/// Menjalankan future sampai selesai atau sampai tidak ada event baru selama
/// `idle`. `push_event` memperbarui `last_activity`, jadi aliran token yang
/// masih berjalan tidak dihitung sebagai menganggur.
async fn run_until_idle<F, T>(
    handle: &Arc<Mutex<RunRecord>>,
    idle: Duration,
    future: F,
) -> Bounded<T>
where
    F: std::future::Future<Output = Result<T, String>>,
{
    let mut future = Box::pin(future);
    loop {
        tokio::select! {
            result = &mut future => return Bounded::Done(result),
            _ = tokio::time::sleep(IDLE_POLL) => {
                if handle.lock().await.last_activity.elapsed() >= idle {
                    return Bounded::Idle;
                }
            }
        }
    }
}

fn idle_message(secs: u64) -> String {
    format!("tidak ada aktivitas dari engine selama {secs} detik")
}

/// Satu percobaan eksekusi engine. Untuk CLI, proses dijalankan per percobaan
/// supaya hasil tool MCP bisa dikirim balik sebagai prompt lanjutan.
#[allow(clippy::too_many_arguments)]
async fn run_attempt(
    manager: &RunManager,
    store: &Store,
    handle: &Arc<Mutex<RunRecord>>,
    spec: &EngineSpec,
    is_ollama: bool,
    model: &str,
    model_opt: Option<&str>,
    effort: Option<&str>,
    context_window: Option<u64>,
    max_tokens: Option<i64>,
    prompt: &str,
    workspace: &str,
    run_id: &str,
    cancel_flag: &Arc<AtomicBool>,
    idle: Duration,
) -> Result<i32, String> {
    if spec.kind == EngineKind::OpenAi {
        let mut attempt = 0;
        loop {
            match run_until_idle(
                handle,
                idle,
                run_openai(
                    store,
                    handle,
                    manager,
                    run_id,
                    spec,
                    model,
                    prompt,
                    max_tokens,
                    cancel_flag,
                ),
            )
            .await
            {
                Bounded::Done(Ok(code)) => return Ok(code),
                Bounded::Done(Err(error)) => {
                    if attempt < 2 && error.starts_with("retryable:") {
                        attempt += 1;
                        let message = error.trim_start_matches("retryable:").to_string();
                        push_event(
                            store,
                            handle,
                            "status",
                            format!("retry {attempt}/2: {message}"),
                        )
                        .await;
                        // Retry pertama langsung: lane yang kena 429 sudah
                        // cooldown, jadi acquire_lane memilih lane lain. Retry
                        // berikutnya baru beri jeda backoff.
                        if attempt > 1 {
                            tokio::time::sleep(Duration::from_secs(2 * attempt as u64)).await;
                        }
                        continue;
                    }
                    return Err(error.trim_start_matches("retryable:").to_string());
                }
                Bounded::Idle => return Err(idle_message(idle.as_secs())),
            }
        }
    }

    if is_ollama {
        return match run_until_idle(
            handle,
            idle,
            run_ollama(store, handle, model, prompt, max_tokens, cancel_flag),
        )
        .await
        {
            Bounded::Done(result) => result,
            Bounded::Idle => Err(idle_message(idle.as_secs())),
        };
    }

    let (program, args) = build_command(
        spec,
        model_opt,
        effort,
        context_window,
        max_tokens,
        prompt,
        workspace,
    )?;
    let stream_json = spec.key == "qoder" || args.iter().any(|arg| arg == "stream-json");
    let stream_error: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let mut command = Command::new(&program);
    command
        .args(&args)
        .current_dir(workspace)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // Process group sendiri: kill bisa menjangkau cucu proses
        // (mis. `sh -c "…; sleep 8"`), bukan hanya shell-nya.
        .process_group(0)
        // Anak mati bersama task; tidak ada proses standby yang tertinggal.
        .kill_on_drop(true);

    let mut process = match command.spawn() {
        Ok(process) => process,
        Err(error) => return Err(format!("gagal menjalankan {program}: {error}")),
    };
    let pid = process.id();
    if let Some(pid) = pid {
        manager.pids.lock().await.insert(run_id.to_string(), pid);
    }

    let mut readers = Vec::new();
    if let Some(out) = process.stdout.take() {
        let handle = handle.clone();
        let store = store.clone();
        let stream_error = stream_error.clone();
        readers.push(tokio::spawn(async move {
            let mut lines = BufReader::new(out).lines();
            let mut tool_names: HashMap<String, String> = HashMap::new();
            let mut last_context: Option<f64> = None;
            let is_agy = program == "agy" || args.iter().any(|arg| arg == "agy");
            while let Ok(Some(line)) = lines.next_line().await {
                if stream_json {
                    let error = if is_agy || line.trim().starts_with("{\"event\":") {
                        stream_agy_line(&store, &handle, &line, &mut tool_names).await
                    } else {
                        stream_qoder_line(&store, &handle, &line, &mut tool_names, &mut last_context).await
                    };
                    if let Some(error) = error {
                        let mut slot = stream_error.lock().await;
                        if slot.is_none() {
                            *slot = Some(error);
                        }
                    }
                } else {
                    push_event(&store, &handle, "output", strip_ansi(&line)).await;
                }
            }
        }));
    }
    if let Some(err) = process.stderr.take() {
        let handle = handle.clone();
        let store = store.clone();
        readers.push(tokio::spawn(async move {
            let mut lines = BufReader::new(err).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                push_event(&store, &handle, "stderr", strip_ansi(&line)).await;
            }
        }));
    }

    let wait = loop {
        tokio::select! {
            status = process.wait() => break Some(status),
            _ = tokio::time::sleep(IDLE_POLL) => {
                if handle.lock().await.last_activity.elapsed() >= idle {
                    break None;
                }
            }
        }
    };
    if wait.is_none() {
        kill_process_group(pid);
        let _ = process.kill().await;
    }
    // Pembaca harus selesai sebelum percobaan berikutnya mengosongkan output.
    for reader in readers {
        let _ = reader.await;
    }
    manager.pids.lock().await.remove(run_id);

    match wait {
        Some(Ok(exit)) if exit.success() => {
            if let Some(error) = stream_error.lock().await.take() {
                Err(error)
            } else {
                Ok(exit.code().unwrap_or(0))
            }
        }
        Some(Ok(exit)) => Err(format!(
            "proses keluar dengan kode {}",
            exit.code().unwrap_or(-1)
        )),
        Some(Err(error)) => Err(error.to_string()),
        None => Err(idle_message(idle.as_secs())),
    }
}

fn mcp_max_rounds() -> usize {
    std::env::var("STATION_MCP_MAX_ROUNDS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(3)
        .clamp(1, 8)
}

fn ollama_think() -> bool {
    std::env::var("STATION_OLLAMA_THINK")
        .map(|value| matches!(value.as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(false)
}

/// Membunuh seluruh process group anak. Tanpa ini, `sh -c "…; sleep 8"` yang
/// dibunuh hanya meninggalkan `sleep` sebagai proses yatim yang tetap berjalan.
fn unwrap_output(output: &str) -> String {
    let trimmed = output.trim();
    if !trimmed.starts_with('{') {
        return output.to_string();
    }
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) {
        for key in ["result", "response", "text", "content", "message"] {
            if let Some(text) = value.get(key).and_then(|item| item.as_str()) {
                return text.to_string();
            }
        }
    }
    output.to_string()
}

/// Riwayat yang tersimpan sebelum `finalize` mengekstrak amplop masih memuat
/// JSON mentah (`conversation_id`, `response`). Normalisasi saat dibaca supaya
/// chat lama menampilkan jawaban, bukan ID internal; isi database tidak diubah.
pub(crate) fn display_output(output: &str) -> String {
    unwrap_output(output)
}

fn kill_process_group(pid: Option<u32>) {
    let Some(pid) = pid else {
        return;
    };
    // killpg langsung: pid negatif berarti "seluruh process group".
    unsafe {
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
}

/// Menutup run: status akhir, event penutup, lalu pesan waker ke percakapan.
async fn finalize(
    store: &Store,
    handle: &Arc<Mutex<RunRecord>>,
    conversation: Option<&str>,
    result: Result<i32, String>,
    cancelled: bool,
    cache_key: Option<&str>,
) -> String {
    let snapshot = {
        let mut record = handle.lock().await;
        if cancelled {
            record.status = "cancelled".to_string();
            record.exit_code = None;
            record.error = Some("dibatalkan oleh operator".to_string());
        } else {
            match result {
                Ok(code) if code == 0 => {
                    record.status = "succeeded".to_string();
                    record.exit_code = Some(code);
                    record.error = None;
                }
                Ok(code) => {
                    record.status = "failed".to_string();
                    record.exit_code = Some(code);
                    record.error = Some(format!("proses keluar dengan kode {code}"));
                }
                Err(message) => {
                    record.status = "failed".to_string();
                    record.error = Some(message);
                }
            }
        }
        // Amplop JSON membawa `usage`; ambil angkanya sebelum teks diekstrak.
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(record.output.trim()) {
            if let Some(usage) = value.get("usage") {
                record.input_tokens = usage.get("input_tokens").and_then(|v| v.as_i64());
                record.output_tokens = usage.get("output_tokens").and_then(|v| v.as_i64());
                record.cache_read_tokens =
                    usage.get("cache_read_tokens").and_then(|v| v.as_i64());
            }
        }
        // Antigravity/Claude CLI bisa mengeluarkan amplop JSON; yang ditampilkan
        // ke chat/IM adalah teks jawabannya, bukan JSON mentah.
        record.output = unwrap_output(&record.output);
        // Jawaban kosong bukan keberhasilan: operator harus melihat bahwa engine
        // tidak menghasilkan apa pun, bukan gelembung chat kosong.
        if record.status == "succeeded" && record.output.trim().is_empty() {
            record.status = "failed".to_string();
            record.exit_code = None;
            record.error = Some("engine tidak menghasilkan keluaran".to_string());
        }
        record.finished_at = Some(Utc::now().to_rfc3339());
        let seq = record.next_seq;
        record.next_seq += 1;
        let at = record.finished_at.clone().unwrap_or_default();
        let text = record.status.clone();
        record.events.push(RunEvent {
            seq,
            at,
            kind: "status".to_string(),
            text,
        });
        record.clone()
    };

    if let Some(status_event) = snapshot.events.last() {
        store.append_event(&snapshot.id, status_event).await;
    }
    store.finish_run(&snapshot).await;
    publish_run_state(handle).await;
    if let Some(chat) = conversation {
        store
            .save_message(chat, "waker", &snapshot.output, Some(&snapshot.id))
            .await;
    }
    // Hanya hasil sukses yang tidak kosong yang layak dipakai ulang.
    if let Some(key) = cache_key {
        if snapshot.status == "succeeded" && !snapshot.output.trim().is_empty() {
            store
                .cache_store(
                    key,
                    &snapshot.engine_key,
                    snapshot.model.as_deref(),
                    &snapshot.output,
                    snapshot.input_tokens,
                    snapshot.output_tokens,
                )
                .await;
        }
    }
    snapshot.status
}

/// Ollama lewat HTTP. `curl` hanya dipakai sebagai pipa transport (`-N`), bukan
/// sebagai engine: yang menjawab tetap proses Ollama di port 11434.
async fn run_ollama(
    store: &Store,
    handle: &Arc<Mutex<RunRecord>>,
    model: &str,
    prompt: &str,
    max_tokens: Option<i64>,
    cancelled: &Arc<AtomicBool>,
) -> Result<i32, String> {
    if model.trim().is_empty() {
        return Err("model Ollama kosong: isi model pada waker (mis. qwen3.5:9b)".to_string());
    }
    let url = std::env::var("OLLAMA_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:11434/api/generate".to_string());
    let body = serde_json::json!({
        "model": model,
        "prompt": prompt,
        "stream": true,
        // Model reasoning lokal bisa menghabiskan seluruh konteks untuk
        // "thinking" dan mengembalikan response kosong. Default: jawab langsung;
        // operator dapat mengaktifkan thinking lewat STATION_OLLAMA_THINK=1.
        "think": ollama_think(),
        // Ollama memakai `num_predict`, bukan nama flag CLI. Batas dari worker
        // diteruskan di sini supaya `max_output_tokens` benar-benar berlaku.
        "options": match max_tokens {
            Some(limit) => serde_json::json!({ "num_predict": limit }),
            None => serde_json::json!({}),
        },
    })
    .to_string();

    let mut child = tokio::process::Command::new("curl")
        .args([
            "-sN",
            "--max-time",
            "900",
            &url,
            "-H",
            "content-type: application/json",
            "--data-binary",
            &body,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("gagal menjalankan transport HTTP: {e}"))?;

    let mut api_error: Option<String> = None;
    if let Some(out) = child.stdout.take() {
        let mut lines = BufReader::new(out).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if cancelled.load(Ordering::Relaxed) {
                return Err("dibatalkan oleh operator".to_string());
            }
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            match serde_json::from_str::<serde_json::Value>(trimmed) {
                Ok(value) => {
                    if let Some(text) = value.get("response").and_then(|v| v.as_str()) {
                        if !text.is_empty() {
                            push_event(store, handle, "delta", text.to_string()).await;
                        }
                    }
                    if let Some(text) = value.get("thinking").and_then(|v| v.as_str()) {
                        if !text.is_empty() {
                            // Ditampilkan di panel Proses, tidak masuk jawaban akhir.
                            push_event(store, handle, "thinking", text.to_string()).await;
                        }
                    }
                    if let Some(error) = value.get("error").and_then(|v| v.as_str()) {
                        api_error = Some(error.to_string());
                        push_event(store, handle, "stderr", error.to_string()).await;
                    }
                }
                Err(_) => push_event(store, handle, "output", trimmed.to_string()).await,
            }
        }
    }
    if let Some(err) = child.stderr.take() {
        let mut lines = BufReader::new(err).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if !line.trim().is_empty() {
                push_event(store, handle, "stderr", line).await;
            }
        }
    }

    let status = child.wait().await.map_err(|e| e.to_string())?;
    if let Some(error) = api_error {
        return Err(format!("Ollama: {error}"));
    }
    Ok(status.code().unwrap_or(-1))
}

/// Berkas sementara yang selalu dihapus saat keluar, termasuk lewat `return`
/// karena pembatalan atau timeout — bukan hanya di jalur sukses.
struct TempBody(std::path::PathBuf);

impl Drop for TempBody {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

const DEFAULT_OPENAI_RPM: u64 = 35;
const RATE_WINDOW: Duration = Duration::from_secs(60);

struct LaneGate {
    next_at: Instant,
    recent: VecDeque<Instant>,
    /// Diisi saat provider menjawab 429/503; lane ini dilewati sampai waktunya
    /// habis supaya retry langsung memakai lane lain.
    cooldown_until: Option<Instant>,
}

/// Gerbang per lane (provider + kunci). Satu lane tidak boleh menembak lebih
/// cepat dari `60_000 / rpm` milidetik dan tidak boleh melebihi `rpm` request
/// dalam 60 detik. Dua lane boleh bergantian, jadi dua kunci Nvidia = 80 RPM
/// total tanpa melewati 40 RPM per kunci.
static LANE_GATES: OnceLock<Mutex<HashMap<String, LaneGate>>> = OnceLock::new();

fn provider_interval(rpm: u64) -> Duration {
    if rpm == 0 {
        return Duration::ZERO;
    }
    Duration::from_millis((60_000 / rpm).max(1))
}

fn retryable_message(message: &str) -> bool {
    let lower = message.to_lowercase();
    [
        "429",
        "too many requests",
        "502",
        "503",
        "504",
        "overload",
        "temporarily",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "rate limit",
    ]
    .iter()
    .any(|needle| lower.contains(needle))
}

/// Sidik kunci hanya untuk kunci state di memori; kunci asli tidak pernah
/// disimpan di map, event, atau log.
fn key_fingerprint(key: &str) -> String {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in key.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{:08x}", (hash ^ (hash >> 32)) as u32)
}

async fn acquire_lane(
    provider: &str,
    lanes: &[Lane],
    rpm: u64,
    cancelled: &AtomicBool,
) -> Result<Lane, String> {
    if lanes.is_empty() {
        return Err("tidak ada lane kredensial untuk provider ini".to_string());
    }
    if rpm == 0 {
        return Ok(lanes[0].clone());
    }
    let interval = provider_interval(rpm);
    loop {
        let now = Instant::now();
        let mut best_wait: Option<Duration> = None;
        {
            let gates = LANE_GATES.get_or_init(|| Mutex::new(HashMap::new()));
            let mut map = gates.lock().await;
            for lane in lanes {
                // Kuota menempel pada kredensial, bukan posisi lane: menukar
                // urutan lane tidak boleh mereset jendela rate limit.
                let gate_key = format!("{provider}:{}", key_fingerprint(&lane.key));
                let gate = map.entry(gate_key).or_insert_with(|| LaneGate {
                    next_at: now,
                    recent: VecDeque::new(),
                    cooldown_until: None,
                });
                let cooldown_wait = gate
                    .cooldown_until
                    .map(|until| until.saturating_duration_since(now))
                    .unwrap_or(Duration::ZERO);
                if !cooldown_wait.is_zero() {
                    best_wait = Some(
                        best_wait
                            .map(|current| current.min(cooldown_wait))
                            .unwrap_or(cooldown_wait),
                    );
                    continue;
                }
                gate.recent
                    .retain(|at| now.duration_since(*at) < RATE_WINDOW);
                let spacing_ok = gate.next_at <= now;
                let capacity_ok = (gate.recent.len() as u64) < rpm;
                if spacing_ok && capacity_ok {
                    gate.recent.push_back(now);
                    gate.next_at = now + interval;
                    return Ok(lane.clone());
                }
                let wait_spacing = gate.next_at.saturating_duration_since(now);
                // Jendela 60 detik hanya relevan saat kapasitas lane benar-benar
                // penuh. Lane yang masih punya slot cukup menunggu jarak antar
                // request, bukan ikut menunggu jendela penuh.
                let wait = if capacity_ok {
                    wait_spacing
                } else {
                    let wait_window = gate
                        .recent
                        .front()
                        .map(|at| (*at + RATE_WINDOW).saturating_duration_since(now))
                        .unwrap_or(Duration::ZERO);
                    wait_spacing.max(wait_window)
                };
                best_wait = Some(best_wait.map(|current| current.min(wait)).unwrap_or(wait));
            }
        }
        let wait = best_wait
            .unwrap_or(Duration::from_millis(50))
            .max(Duration::from_millis(50));
        let mut left = wait;
        while !left.is_zero() {
            if cancelled.load(Ordering::Relaxed) {
                return Err("dibatalkan saat menunggu rate limit".to_string());
            }
            let step = left.min(Duration::from_millis(250));
            tokio::time::sleep(step).await;
            left -= step;
        }
    }
}

async fn mark_lane_cooldown(provider: &str, key_fp: &str, duration: Duration) {
    let gates = LANE_GATES.get_or_init(|| Mutex::new(HashMap::new()));
    let mut map = gates.lock().await;
    let gate_key = format!("{provider}:{key_fp}");
    let gate = map.entry(gate_key).or_insert_with(|| LaneGate {
        next_at: Instant::now(),
        recent: VecDeque::new(),
        cooldown_until: None,
    });
    let until = Instant::now() + duration;
    if gate
        .cooldown_until
        .map(|current| until > current)
        .unwrap_or(true)
    {
        gate.cooldown_until = Some(until);
    }
}

/// Ubah error provider menjadi hasil yang bisa diretrying. Hanya 429/503 yang
/// belum menghasilkan teks yang di-retry; kalau sudah ada output, mengulang
/// akan menduplikasi jawaban di depan operator.
async fn openai_failure(
    store: &Store,
    handle: &Arc<Mutex<RunRecord>>,
    provider: &str,
    lane_fp: &str,
    lane_label: &str,
    message: String,
    had_output: bool,
) -> String {
    if !had_output && retryable_message(&message) {
        mark_lane_cooldown(provider, lane_fp, Duration::from_secs(5)).await;
        push_event(
            store,
            handle,
            "status",
            format!("{provider} · {lane_label}: {message}; cooldown 5s lalu pindah lane"),
        )
        .await;
        format!("retryable:{provider}: {message}")
    } else {
        format!("{provider}: {message}")
    }
}

/// Endpoint OpenAI-compatible: SSE `data:` dengan delta teks.
/// Kunci dikirim lewat config curl di stdin, body lewat berkas 0600 sementara,
/// jadi tidak ada rahasia yang muncul di argv proses.
async fn run_openai(
    store: &Store,
    handle: &Arc<Mutex<RunRecord>>,
    manager: &RunManager,
    run_id: &str,
    spec: &EngineSpec,
    model: &str,
    prompt: &str,
    max_tokens: Option<i64>,
    cancelled: &Arc<AtomicBool>,
) -> Result<i32, String> {
    let provider = spec
        .raw
        .get("provider")
        .and_then(|value| value.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if provider.is_empty() {
        return Err(format!(
            "engine \"{}\" berjenis openai butuh field \"provider\" di config/engines.json",
            spec.key
        ));
    }
    let rpm = spec
        .raw
        .get("rpm_per_key")
        .or_else(|| spec.raw.get("rpm"))
        .and_then(|value| value.as_u64())
        .unwrap_or(DEFAULT_OPENAI_RPM);
    let lanes = crate::api::providers::lanes_for(&provider)?;
    let lane = acquire_lane(&provider, &lanes, rpm, cancelled).await?;
    let lane_fp = key_fingerprint(&lane.key);
    let lane_label = lane.label.clone();
    push_event(
        store,
        handle,
        "routing",
        format!("{provider} · {lane_label}"),
    )
    .await;
    let base = lane.base;
    let key = lane.key;
    let url = format!("{}/chat/completions", base.trim_end_matches('/'));
    // `stream_options.include_usage` membuat potongan terakhir membawa token
    // nyata; tanpa itu badge context hanya bisa menebak.
    let mut payload = serde_json::Map::new();
    payload.insert("model".into(), serde_json::json!(model));
    payload.insert("messages".into(), serde_json::json!([{ "role": "user", "content": prompt }]));
    payload.insert("stream".into(), serde_json::json!(true));
    payload.insert(
        "stream_options".into(),
        serde_json::json!({ "include_usage": true }),
    );
    if let Some(limit) = max_tokens {
        payload.insert("max_tokens".into(), serde_json::json!(limit));
    }
    let body = serde_json::Value::Object(payload).to_string();

    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let body_path = std::env::temp_dir().join(format!("station-openai-{nanos}.json"));
    {
        use std::io::Write as _;
        use std::os::unix::fs::OpenOptionsExt as _;
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .mode(0o600)
            .open(&body_path)
            .map_err(|error| format!("{provider}: body tidak bisa dibuat: {error}"))?;
        file.write_all(body.as_bytes())
            .map_err(|error| format!("{provider}: body tidak bisa ditulis: {error}"))?;
    }
    // Guard ini menghapus body pada setiap jalur keluar, termasuk pembatalan.
    let _body = TempBody(body_path.clone());

    let escaped = key.replace('\\', "\\\\").replace('"', "\\\"");
    let config = format!(
        "url = \"{url}\"\nheader = \"Authorization: Bearer {escaped}\"\nheader = \"content-type: application/json\"\ndata-binary = \"@{}\"\nsilent\n",
        body_path.display()
    );
    let mut child = tokio::process::Command::new("curl")
        // `-S` tetap menampilkan pesan galat meski `silent` menekan progres.
        .args(["-sSN", "--max-time", "900", "--config", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // Process group sendiri supaya Stop bisa membunuh curl yang menggantung
        // (endpoint diam tanpa satu baris pun).
        .process_group(0)
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| format!("curl tidak bisa dijalankan: {error}"))?;
    if let Some(pid) = child.id() {
        manager.pids.lock().await.insert(run_id.to_string(), pid);
    }
    if let Some(mut stdin) = child.stdin.take() {
        use tokio::io::AsyncWriteExt as _;
        let _ = stdin.write_all(config.as_bytes()).await;
        drop(stdin);
    }
    // Berkas body baru boleh dihapus setelah curl selesai membacanya.

    // stderr dibaca di task terpisah supaya curl tidak macet ketika pipa penuh
    // sementara stdout masih dialirkan.
    let stderr_task = child.stderr.take().map(|err| {
        tokio::spawn(async move {
            let mut lines = BufReader::new(err).lines();
            let mut collected = Vec::new();
            while let Ok(Some(line)) = lines.next_line().await {
                if !line.trim().is_empty() {
                    collected.push(line);
                }
            }
            collected
        })
    });

    let mut usage_in = None;
    let mut usage_out = None;
    let mut api_error: Option<String> = None;
    let mut saw_text = false;
    let mut saw_thinking = false;
    // Galat HTTP dari endpoint ini datang sebagai JSON biasa, bukan event SSE.
    let mut raw_tail = String::new();
    if let Some(out) = child.stdout.take() {
        let mut lines = BufReader::new(out).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if cancelled.load(Ordering::Relaxed) {
                manager.pids.lock().await.remove(run_id);
                return Err("dibatalkan oleh operator".to_string());
            }
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            let Some(payload) = trimmed.strip_prefix("data:") else {
                if raw_tail.len() < 2048 {
                    raw_tail.push_str(trimmed);
                    raw_tail.push('\n');
                }
                continue;
            };
            let payload = payload.trim();
            if payload == "[DONE]" {
                break;
            }
            let Ok(value) = serde_json::from_str::<serde_json::Value>(payload) else {
                continue;
            };
            if let Some(error) = value.get("error") {
                let message = error.as_str().map(str::to_string).or_else(|| {
                    error
                        .get("message")
                        .and_then(|v| v.as_str())
                        .map(str::to_string)
                });
                if let Some(message) = message {
                    api_error = Some(message);
                    continue;
                }
            }
            if let Some(text) = value
                .pointer("/choices/0/delta/content")
                .and_then(|v| v.as_str())
            {
                if !text.is_empty() {
                    saw_text = true;
                    push_event(store, handle, "delta", text.to_string()).await;
                }
            }
            if let Some(text) = value
                .pointer("/choices/0/delta/reasoning_content")
                .and_then(|v| v.as_str())
            {
                if !text.is_empty() {
                    saw_thinking = true;
                    push_event(store, handle, "thinking", text.to_string()).await;
                }
            }
            if let Some(usage) = value.get("usage") {
                usage_in = usage.get("prompt_tokens").and_then(|v| v.as_i64());
                usage_out = usage.get("completion_tokens").and_then(|v| v.as_i64());
            }
        }
    }
    if let Some(task) = stderr_task {
        for line in task.await.unwrap_or_default() {
            push_event(store, handle, "stderr", line).await;
        }
    }
    let status = child.wait().await.map_err(|error| error.to_string())?;
    manager.pids.lock().await.remove(run_id);
    let _ = std::fs::remove_file(&body_path);
    {
        let mut record = handle.lock().await;
        if usage_in.is_some() {
            record.input_tokens = usage_in;
        }
        if usage_out.is_some() {
            record.output_tokens = usage_out;
        }
    }
    if let Some(error) = api_error {
        return Err(openai_failure(
            store,
            handle,
            &provider,
            &lane_fp,
            &lane_label,
            error,
            saw_text || saw_thinking,
        )
        .await);
    }
    if !status.success() {
        let message = if raw_tail.trim().is_empty() {
            format!("curl keluar dengan kode {:?}", status.code())
        } else {
            format!(
                "curl keluar dengan kode {:?}: {}",
                status.code(),
                raw_tail.trim()
            )
        };
        return Err(openai_failure(
            store,
            handle,
            &provider,
            &lane_fp,
            &lane_label,
            message,
            saw_text || saw_thinking,
        )
        .await);
    }
    if !saw_text && !saw_thinking && usage_in.is_none() {
        let detail = serde_json::from_str::<serde_json::Value>(raw_tail.trim())
            .ok()
            .and_then(|value| {
                value
                    .pointer("/error/message")
                    .or_else(|| value.get("detail"))
                    .or_else(|| value.get("error"))
                    .and_then(|v| v.as_str().map(str::to_string))
            })
            .unwrap_or_else(|| raw_tail.trim().chars().take(300).collect());
        let message = if detail.is_empty() {
            "endpoint tidak mengirim isi".to_string()
        } else {
            detail
        };
        return Err(openai_failure(
            store,
            handle,
            &provider,
            &lane_fp,
            &lane_label,
            message,
            false,
        )
        .await);
    }
    Ok(0)
}

/// Buang escape ANSI (warna, cursor) dari keluaran CLI. Cline dan beberapa
/// CLI lain tetap mengirim kode warna meski stdout bukan terminal.
fn strip_ansi(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '\x1b' {
            out.push(ch);
            continue;
        }
        if chars.peek() == Some(&'[') {
            chars.next();
            // CSI berakhir pada byte di rentang '@'..='~'.
            for code in chars.by_ref() {
                if ('@'..='~').contains(&code) {
                    break;
                }
            }
        } else {
            // Escape dua karakter (mis. ESC c) — buang pasangannya.
            let _ = chars.next();
        }
    }
    out
}

async fn push_event(store: &Store, handle: &Arc<Mutex<RunRecord>>, kind: &str, text: String) {
    let (run_id, waker_id, event) = {
        let mut record = handle.lock().await;
        let seq = record.next_seq;
        record.next_seq += 1;
        // Satu event = engine masih bekerja; batas menganggur mundur.
        record.last_activity = Instant::now();

        let has_room = record.output.len() < MAX_OUTPUT_BYTES;
        if has_room {
            if kind == "output" {
                if !record.output.is_empty() {
                    record.output.push('\n');
                }
                record.output.push_str(&text);
            } else if kind == "delta" {
                // Fragmen stream HTTP menyambung tanpa pemisah baris.
                record.output.push_str(&text);
            }
        }

        let event = RunEvent {
            seq,
            at: Utc::now().to_rfc3339(),
            kind: kind.to_string(),
            text,
        };
        // Memori dibatasi; database tetap menerima seluruh event.
        if record.events.len() < MAX_EVENTS {
            record.events.push(event.clone());
        }
        (record.id.clone(), record.waker_id.clone(), event)
    };
    store.append_event(&run_id, &event).await;
    crate::api::events::publish(serde_json::json!({
        "type": "run_event",
        "run_id": run_id,
        "waker_id": waker_id,
        "event": &event,
    }));
}

/// Menerbitkan keadaan run (bukan hanya event) supaya console tahu kapan run
/// mulai, selesai, gagal, atau dibatalkan tanpa menebak dari event terakhir.
async fn publish_run_state(handle: &Arc<Mutex<RunRecord>>) {
    let record = handle.lock().await;
    crate::api::events::publish(serde_json::json!({
        "type": "run_state",
        "run_id": record.id,
        "waker_id": record.waker_id,
        "status": record.status,
        "output": record.output,
        "error": record.error,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
    }));
}

fn short_text(text: &str, limit: usize) -> String {
    let clean = text.replace('\n', " · ").replace('\r', " ");
    let mut out: String = clean.chars().take(limit).collect();
    if clean.chars().count() > limit {
        out.push('…');
    }
    out.trim().to_string()
}

fn tool_input_summary(name: &str, input: &serde_json::Value) -> String {
    let key = match name {
        "Bash" | "run_command" => "CommandLine",
        "Read" | "Edit" | "Write" | "NotebookEdit" | "read_file" | "write_file" | "edit_file" => {
            "file_path"
        }
        "Glob" => "pattern",
        "Grep" | "grep_search" => "pattern",
        "WebFetch" => "url",
        "WebSearch" | "search_web" => "query",
        "Task" | "Agent" => "description",
        _ => "path",
    };
    let raw = input
        .get(key)
        .and_then(serde_json::Value::as_str)
        .or_else(|| input.get("command").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("CommandLine").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("file_path").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("Path").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("path").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("description").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("query").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("Prompt").and_then(serde_json::Value::as_str))
        .or_else(|| input.get("prompt").and_then(serde_json::Value::as_str))
        .map(str::to_string)
        .unwrap_or_else(|| serde_json::to_string(input).unwrap_or_default());
    short_text(&raw, 150)
}

/// Antigravity CLI (agy) `--output-format stream-json` → event yang bisa ditampilkan sebagai
/// Deep Thinking, View Steps, dan status tool secara live. Satu baris = satu objek JSON (NDJSON).
///
/// Event kinds yang dihasilkan: `status`, `thinking`, `delta`, `tool_started`,
/// `tool_finished`, `result`, `stderr`. `delta` tetap menjadi isi jawaban.
async fn stream_agy_line(
    store: &Store,
    handle: &Arc<Mutex<RunRecord>>,
    line: &str,
    tool_names: &mut HashMap<String, String>,
) -> Option<String> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return None;
    }
    let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) else {
        push_event(store, handle, "stderr", short_text(trimmed, 500)).await;
        return None;
    };
    let event = value.get("event").and_then(serde_json::Value::as_str).unwrap_or("");
    match event {
        "init" => {
            if let Some(init) = value.get("init") {
                let model = init.get("model").and_then(serde_json::Value::as_str).unwrap_or("?");
                let tools = init
                    .get("tools")
                    .and_then(serde_json::Value::as_array)
                    .map(|items| items.len())
                    .unwrap_or(0);
                let permission = init
                    .get("permission_mode")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("?");
                push_event(
                    store,
                    handle,
                    "status",
                    format!("init · model={model} · tools={tools} · permission={permission}"),
                )
                .await;
            }
            None
        }
        "step_update" => {
            let Some(step) = value.get("step_update") else {
                return None;
            };
            let step_type = step.get("step_type").and_then(serde_json::Value::as_str).unwrap_or("");
            let state = step.get("state").and_then(serde_json::Value::as_str).unwrap_or("");

            // 1. Fragmen thinking/reasoning (bisa thinking_delta atau thinking)
            if let Some(thinking) = step
                .get("thinking_delta")
                .or_else(|| step.get("thinking"))
                .and_then(serde_json::Value::as_str)
            {
                if !thinking.trim().is_empty() {
                    push_event(store, handle, "thinking", thinking.to_string()).await;
                }
            }

            match step_type {
                "agent_response" => {
                    if let Some(delta) = step.get("text_delta").and_then(serde_json::Value::as_str) {
                        if !delta.is_empty() {
                            push_event(store, handle, "delta", delta.to_string()).await;
                        }
                    }
                    if state == "DONE" {
                        if let Some(usage) = step.get("usage") {
                            let mut record = handle.lock().await;
                            if let Some(inp) = usage.get("input_tokens").and_then(|v| v.as_i64()) {
                                record.input_tokens = Some(inp);
                            }
                            if let Some(out) = usage.get("output_tokens").and_then(|v| v.as_i64()) {
                                record.output_tokens = Some(out);
                            }
                            if let Some(cache) = usage.get("cache_read_tokens").and_then(|v| v.as_i64()) {
                                record.cache_read_tokens = Some(cache);
                            }
                        }
                    }
                }
                "tool" => {
                    let name = step
                        .get("tool_name")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("Tool");
                    let tool_id = step
                        .get("step_index")
                        .and_then(serde_json::Value::as_i64)
                        .map(|i| i.to_string())
                        .unwrap_or_default();
                    if !tool_id.is_empty() {
                        tool_names.insert(tool_id.clone(), name.to_string());
                    }

                    if state == "ACTIVE" {
                        let params = step
                            .pointer("/tool_info/parameters")
                            .cloned()
                            .unwrap_or(serde_json::Value::Null);
                        let summary = tool_input_summary(name, &params);
                        push_event(
                            store,
                            handle,
                            "tool_started",
                            format!("{name} · {summary}"),
                        )
                        .await;
                    } else if state == "DONE" {
                        let duration = step
                            .get("duration_seconds")
                            .and_then(serde_json::Value::as_f64)
                            .unwrap_or(0.0);
                        let output = step
                            .pointer("/tool_info/output")
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("");
                        let detail = if !output.trim().is_empty() {
                            format!("{} ({:.2}s)", short_text(output, 180), duration)
                        } else {
                            format!("({:.2}s)", duration)
                        };
                        push_event(
                            store,
                            handle,
                            "tool_finished",
                            format!("completed {name} · {detail}"),
                        )
                        .await;
                    }
                }
                _ => {}
            }
            None
        }
        "result" => {
            let Some(res) = value.get("result") else {
                return None;
            };
            let status = res.get("status").and_then(serde_json::Value::as_str).unwrap_or("");
            let is_error = status == "ERROR" || res.get("error").is_some();
            let duration = res.get("duration_seconds").and_then(serde_json::Value::as_f64).unwrap_or(0.0);
            let turns = res.get("num_turns").and_then(serde_json::Value::as_u64).unwrap_or(1);

            if let Some(usage) = res.get("usage") {
                let mut record = handle.lock().await;
                if let Some(inp) = usage.get("input_tokens").and_then(|v| v.as_i64()) {
                    record.input_tokens = Some(inp);
                }
                if let Some(out) = usage.get("output_tokens").and_then(|v| v.as_i64()) {
                    record.output_tokens = Some(out);
                }
                if let Some(cache) = usage.get("cache_read_tokens").and_then(|v| v.as_i64()) {
                    record.cache_read_tokens = Some(cache);
                }
            }

            if is_error {
                let error = res
                    .get("error")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("antigravity stream error");
                push_event(store, handle, "status", format!("error · {}", short_text(error, 300)))
                    .await;
                Some(error.to_string())
            } else {
                let resp = res.get("response").and_then(serde_json::Value::as_str).unwrap_or("");
                if !resp.trim().is_empty() {
                    let empty = {
                        let record = handle.lock().await;
                        record.output.trim().is_empty()
                    };
                    if empty {
                        push_event(store, handle, "delta", resp.to_string()).await;
                    }
                }
                push_event(
                    store,
                    handle,
                    "result",
                    format!("selesai · {turns} turn · {:.0} ms", duration * 1000.0),
                )
                .await;
                None
            }
        }
        _ => None,
    }
}

/// Qoder `--output-format stream-json` → event yang bisa ditampilkan sebagai
/// Deep Thinking, View Steps, dan status tool. Satu baris = satu objek JSON.
///
/// Event kinds yang dihasilkan: `status`, `thinking`, `delta`, `tool_started`,
/// `tool_finished`, `context`, `result`, `stderr`. `delta` tetap menjadi isi
/// jawaban; sisanya adalah jejak proses yang tidak boleh dianggap jawaban.
async fn stream_qoder_line(
    store: &Store,
    handle: &Arc<Mutex<RunRecord>>,
    line: &str,
    tool_names: &mut HashMap<String, String>,
    last_context: &mut Option<f64>,
) -> Option<String> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return None;
    }
    let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) else {
        push_event(store, handle, "stderr", short_text(trimmed, 500)).await;
        return None;
    };
    let kind = value.get("type").and_then(serde_json::Value::as_str).unwrap_or("");
    match kind {
        "system" => {
            let subtype = value.get("subtype").and_then(serde_json::Value::as_str).unwrap_or("");
            if subtype == "init" {
                let model = value
                    .get("model")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("?");
                let tools = value
                    .get("tools")
                    .and_then(serde_json::Value::as_array)
                    .map(|items| items.len())
                    .unwrap_or(0);
                let mcp = value
                    .get("mcp_servers")
                    .and_then(serde_json::Value::as_array)
                    .map(|items| items.len())
                    .unwrap_or(0);
                let permission = value
                    .get("permissionMode")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("?");
                push_event(
                    store,
                    handle,
                    "status",
                    format!("init · model={model} · tools={tools} · mcp={mcp} · permission={permission}"),
                )
                .await;
            }
            None
        }
        "assistant" => {
            let Some(content) = value
                .pointer("/message/content")
                .and_then(serde_json::Value::as_array)
            else {
                return None;
            };
            for block in content {
                match block.get("type").and_then(serde_json::Value::as_str).unwrap_or("") {
                    "thinking" => {
                        if let Some(text) = block.get("thinking").and_then(serde_json::Value::as_str) {
                            if !text.trim().is_empty() {
                                push_event(store, handle, "thinking", text.to_string()).await;
                            }
                        }
                    }
                    "text" => {
                        if let Some(text) = block.get("text").and_then(serde_json::Value::as_str) {
                            if !text.is_empty() {
                                push_event(store, handle, "delta", text.to_string()).await;
                            }
                        }
                    }
                    "tool_use" => {
                        let id = block
                            .get("id")
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("")
                            .to_string();
                        let name = block
                            .get("name")
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("Tool")
                            .to_string();
                        let input = block
                            .get("input")
                            .cloned()
                            .unwrap_or(serde_json::Value::Null);
                        let summary = tool_input_summary(&name, &input);
                        if !id.is_empty() {
                            tool_names.insert(id, name.clone());
                        }
                        push_event(
                            store,
                            handle,
                            "tool_started",
                            format!("{name} · {summary}"),
                        )
                        .await;
                    }
                    _ => {}
                }
            }
            if let Some(ratio) = value
                .pointer("/message/usage/context_usage_ratio")
                .and_then(serde_json::Value::as_f64)
            {
                let changed = last_context
                    .map(|previous| (previous - ratio).abs() >= 0.0005)
                    .unwrap_or(true);
                if changed {
                    *last_context = Some(ratio);
                    push_event(
                        store,
                        handle,
                        "context",
                        format!("context {:.1}%", ratio * 100.0),
                    )
                    .await;
                }
            }
            None
        }
        "user" => {
            let Some(content) = value
                .pointer("/message/content")
                .and_then(serde_json::Value::as_array)
            else {
                return None;
            };
            for block in content {
                if block.get("type").and_then(serde_json::Value::as_str) != Some("tool_result") {
                    continue;
                }
                let id = block
                    .get("tool_use_id")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("");
                let name = tool_names
                    .remove(id)
                    .unwrap_or_else(|| "Tool".to_string());
                let result = value.get("tool_use_result").cloned().unwrap_or(serde_json::Value::Null);
                let exit = result.get("exitCode").and_then(serde_json::Value::as_i64);
                let completed = result
                    .get("kind")
                    .and_then(serde_json::Value::as_str)
                    .map(|kind| kind == "completed")
                    .or_else(|| exit.map(|code| code == 0))
                    .unwrap_or(true);
                let status = if completed { "completed" } else { "failed" };
                let detail = if let Some(code) = exit {
                    let stdout = result
                        .get("stdout")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("");
                    format!("exit {code} · {}", short_text(stdout, 180))
                } else if let Some(file) = result.get("file") {
                    let path = file
                        .get("filePath")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("");
                    let lines = file
                        .get("numLines")
                        .and_then(serde_json::Value::as_i64)
                        .unwrap_or(0);
                    format!("{path} · {lines} lines")
                } else {
                    block
                        .get("content")
                        .and_then(serde_json::Value::as_str)
                        .map(|text| short_text(text, 180))
                        .unwrap_or_default()
                };
                push_event(
                    store,
                    handle,
                    "tool_finished",
                    format!("{status} {name} · {detail}"),
                )
                .await;
            }
            None
        }
        "result" => {
            let is_error = value
                .get("is_error")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            let text = value
                .get("result")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            if !is_error && !text.trim().is_empty() {
                let empty = {
                    let record = handle.lock().await;
                    record.output.trim().is_empty()
                };
                if empty {
                    push_event(store, handle, "delta", text.to_string()).await;
                }
            }
            let turns = value
                .get("num_turns")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0);
            let duration = value
                .get("duration_ms")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0);
            let ratio = value
                .pointer("/usage/context_usage_ratio")
                .and_then(serde_json::Value::as_f64)
                .or_else(|| {
                    value
                        .get("context_usage_ratio")
                        .and_then(serde_json::Value::as_f64)
                });
            if let Some(ratio) = ratio {
                let changed = last_context
                    .map(|previous| (previous - ratio).abs() >= 0.0005)
                    .unwrap_or(true);
                if changed {
                    *last_context = Some(ratio);
                    push_event(
                        store,
                        handle,
                        "context",
                        format!("context {:.1}%", ratio * 100.0),
                    )
                    .await;
                }
            }
            if is_error {
                let error = if text.trim().is_empty() {
                    "qoder result error".to_string()
                } else {
                    text.to_string()
                };
                push_event(store, handle, "status", format!("error · {}", short_text(&error, 300)))
                    .await;
                Some(error)
            } else {
                push_event(
                    store,
                    handle,
                    "result",
                    format!("selesai · {turns} turn · {duration} ms"),
                )
                .await;
                None
            }
        }
        _ => None,
    }
}

#[allow(dead_code)]
fn compose_prompt(system: &str, user: &str) -> String {
    let system = system.trim();
    if system.is_empty() {
        user.to_string()
    } else {
        format!("{system}\n\n{user}")
    }
}

/// Menyusun argv dari template registry. Elemen pertama template adalah program.
///
/// Engine `pane` tidak punya template karena hidup di tmux; untuk qoder ada jalur
/// headless resmi (`-p`), dan hanya jalur itu yang dipakai di sini — bukan menebak
/// argumen CLI lain.
fn build_command(
    spec: &EngineSpec,
    model: Option<&str>,
    effort: Option<&str>,
    context_window: Option<u64>,
    max_tokens: Option<i64>,
    prompt: &str,
    cwd: &str,
) -> Result<(String, Vec<String>), String> {
    let model = model.or(spec.model.as_deref()).unwrap_or("");
    let effort = effort.unwrap_or("");
    let context = context_window.map(|n| n.to_string()).unwrap_or_default();
    let max_tokens_value = max_tokens.map(|n| n.to_string()).unwrap_or_default();

    if let Some(template) = &spec.template {
        if template.is_empty() {
            return Err(format!("engine \"{}\" punya template kosong", spec.key));
        }
        let mut argv: Vec<String> = template
            .iter()
            .map(|part| {
                part.replace("{model}", model)
                    .replace("{prompt}", prompt)
                    .replace("{cwd}", cwd)
                    .replace("{effort}", effort)
                    .replace("{context}", &context)
                    .replace("{max_tokens}", &max_tokens_value)
            })
            .collect();
        // Batas keluaran hanya diteruskan bila registry menyatakan argumen yang
        // benar-benar dikenal CLI itu. Tidak ada flag yang dikarang di sini.
        if let Some(limit) = max_tokens {
            if let Some(extra) = &spec.max_tokens_arg {
                let value = limit.to_string();
                for part in extra {
                    argv.push(part.replace("{value}", &value));
                }
            }
        }
        let program = argv[0].clone();
        return Ok((program, argv[1..].to_vec()));
    }

    // Engine qoder di registry berjenis `pane` dan tidak selalu mengisi `binary`,
    // jadi nama kunci ikut dikenali sebagai jalur headless resmi qoder.
    if spec.binary.as_deref() == Some("qoder") || spec.key == "qoder" {
        let mut args = vec!["-p".to_string(), prompt.to_string()];
        if !model.is_empty() {
            args.push("-m".to_string());
            args.push(model.to_string());
        }
        // Qoder CLI mengenal tiga hal ini secara eksplisit; meneruskannya berarti
        // pilihan di console benar-benar sampai ke model, bukan sekadar hiasan.
        if !effort.is_empty() {
            args.push("--reasoning-effort".to_string());
            args.push(effort.to_string());
        }
        if !context.is_empty() {
            args.push("--context-window".to_string());
            args.push(context);
        }
        // `qoder --help` menyebut flag ini apa adanya, jadi aman diteruskan.
        if let Some(limit) = max_tokens {
            args.push("--max-output-tokens".to_string());
            args.push(limit.to_string());
        }
        args.push("--cwd".to_string());
        args.push(cwd.to_string());
        // Headless tidak punya meja untuk menyetujui tool. `auto` mengizinkan
        // tool yang aman tanpa menggantung, sementara `accept_edits` dulu
        // membuat run Bash menunggu persetujuan yang tidak akan pernah datang.
        args.push("--permission-mode".to_string());
        args.push("auto".to_string());
        // Stream-json adalah satu-satunya jalur yang memberi thinking dan
        // tool_use/tool_result per langkah. Tanpa ini UI hanya melihat "started".
        args.push("--output-format".to_string());
        args.push("stream-json".to_string());
        // `qodercli` langsung; pembungkus `qoder` bisa membuka GUI bila
        // environment tidak lengkap dan tidak dipakai oleh daemon.
        return Ok(("qodercli".to_string(), args));
    }

    // Antigravity: entri `pane` yang memakai tab antigravity dijalankan lewat CLI
    // resmi `agy` — tab tmux hanya rumah lamanya, bukan cara eksekusinya.
    // Menghasilkan event stream `stream-json` agar reasoning, steps tool, dan jawaban mengalir live.
    if spec.binary.as_deref() == Some("agy") || spec.tab.as_deref() == Some("antigravity") {
        let mut args = Vec::new();
        if !model.is_empty() {
            args.push("--model".to_string());
            args.push(model.to_string());
        }
        if !effort.is_empty() {
            args.push("--effort".to_string());
            args.push(effort.to_string());
        }
        // Interactive worker engines configured with `permission_mode: skip`
        // or `auto_approve: true` bypass interactive prompts in headless mode.
        // Verifier profiles do not set this flag.
        if spec.raw.get("permission_mode").and_then(|v| v.as_str()) == Some("skip")
            || spec.raw.get("auto_approve").and_then(|v| v.as_bool()) == Some(true)
        {
            args.push("--dangerously-skip-permissions".to_string());
        }
        args.push("--print".to_string());
        args.push(prompt.to_string());
        args.push("--output-format".to_string());
        args.push("stream-json".to_string());
        return Ok(("agy".to_string(), args));
    }

    Err(format!(
        "engine \"{}\" ({}) belum punya jalur headless: tambahkan \"template\" di config/engines.json",
        spec.key,
        spec.kind.as_str()
    ))
}

fn new_run_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("run_{nanos:x}")
}

fn cache_ttl_secs() -> i64 {
    std::env::var("STATION_CACHE_TTL_SECS")
        .ok()
        .and_then(|value| value.parse::<i64>().ok())
        .unwrap_or(86_400)
        .clamp(60, 30 * 86_400)
}

/// FNV-1a 64-bit: stabil lintas build (berbeda dengan `DefaultHasher`), cukup
/// untuk kunci cache lokal. Prompt lengkap ikut di-hash, jadi perubahan system,
/// pengetahuan, atau riwayat otomatis menghasilkan kunci baru.
fn result_cache_key(engine_key: &str, model: &str, prompt: &str) -> String {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for part in [engine_key, model, prompt] {
        for byte in part.as_bytes() {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
        }
        hash ^= 0xff;
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{hash:016x}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::EngineKind;
    use serde_json::json;

    fn spec(kind: EngineKind, binary: Option<&str>, template: Option<Vec<&str>>) -> EngineSpec {
        EngineSpec {
            key: "uji".into(),
            kind,
            role: None,
            model: Some("m1".into()),
            binary: binary.map(str::to_string),
            tab: None,
            template: template.map(|t| t.into_iter().map(str::to_string).collect()),
            max_tokens_arg: None,
            quota_gate: None,
            raw: json!({}),
        }
    }

    #[test]
    fn template_mengganti_placeholder() {
        let s = spec(
            EngineKind::Cli,
            Some("agy"),
            Some(vec!["agy", "--model", "{model}", "--print", "{prompt}"]),
        );
        let (program, args) = build_command(&s, None, None, None, None, "halo", "/tmp").unwrap();
        assert_eq!(program, "agy");
        assert_eq!(args, vec!["--model", "m1", "--print", "halo"]);
    }

    #[test]
    fn qoder_pakai_jalur_headless() {
        let s = spec(EngineKind::Pane, Some("qoder"), None);
        let (program, args) =
            build_command(&s, Some("Qwen3.8-Flash"), Some("high"), Some(1_000_000), None, "halo", "/tmp")
                .unwrap();
        assert_eq!(program, "qodercli");
        assert_eq!(args[0], "-p");
        assert_eq!(args[1], "halo");
        assert!(args.contains(&"--cwd".to_string()));
        assert!(args.contains(&"--reasoning-effort".to_string()));
        assert!(args.contains(&"high".to_string()));
        assert!(args.contains(&"--context-window".to_string()));
        assert!(args.contains(&"1000000".to_string()));
        // Stream-json adalah jalur yang memberi thinking dan langkah tool.
        assert!(args.contains(&"--output-format".to_string()));
        assert!(args.contains(&"stream-json".to_string()));
        assert!(args.contains(&"--permission-mode".to_string()));
        assert!(args.contains(&"auto".to_string()));
    }

    #[test]
    fn batas_keluaran_diteruskan_hanya_bila_didukung() {
        let mut s = spec(
            EngineKind::Cli,
            Some("agy"),
            Some(vec!["agy", "--print", "{prompt}"]),
        );
        let (_, args) = build_command(&s, None, None, None, Some(64), "halo", "/tmp").unwrap();
        assert!(!args.iter().any(|arg| arg == "64"), "tanpa dukungan tidak menambah flag");

        s.max_tokens_arg = Some(vec!["--max-output-tokens".into(), "{value}".into()]);
        let (_, args) = build_command(&s, None, None, None, Some(64), "halo", "/tmp").unwrap();
        let pos = args
            .iter()
            .position(|arg| arg == "--max-output-tokens")
            .expect("flag batas ada");
        assert_eq!(args[pos + 1], "64");
    }

    #[test]
    fn qoder_meneruskan_max_output_tokens() {
        let s = spec(EngineKind::Pane, Some("qoder"), None);
        let (_, args) =
            build_command(&s, None, None, None, Some(128), "halo", "/tmp").unwrap();
        let pos = args
            .iter()
            .position(|arg| arg == "--max-output-tokens")
            .expect("flag batas qoder ada");
        assert_eq!(args[pos + 1], "128");
    }

    #[test]
    fn escape_ansi_dibuang_dari_keluaran_cli() {
        assert_eq!(strip_ansi("\x1b[36m[read_files]\x1b[0m ok"), "[read_files] ok");
        assert_eq!(strip_ansi("tanpa warna"), "tanpa warna");
    }

    #[test]
    fn amplop_json_diubah_menjadi_jawaban() {
        let raw = r#"{"conversation_id":"x","status":"SUCCESS","response":"ok\n"}"#;
        assert_eq!(display_output(raw), "ok\n");
        let plain = r#"{"ok": true}"#;
        assert_eq!(display_output(plain), plain);
    }

    #[test]
    fn pane_tanpa_jalur_headless_ditolak_terang() {
        let s = spec(EngineKind::Pane, Some("tmux-thing"), None);
        let err = build_command(&s, None, None, None, None, "x", "/tmp").unwrap_err();
        assert!(err.contains("belum punya jalur headless"));
    }

    #[test]
    fn system_prompt_dilebur() {
        assert_eq!(compose_prompt("", "hai"), "hai");
        assert_eq!(compose_prompt("S", "hai"), "S\n\nhai");
    }

    #[test]
    fn rpm_40_berarti_satu_setengah_detik() {
        assert_eq!(provider_interval(40), Duration::from_millis(1_500));
        assert_eq!(provider_interval(0), Duration::ZERO);
    }

    #[tokio::test]
    async fn dua_lane_memakai_lane_kedua_tanpa_menunggu() {
        let lanes = vec![
            Lane {
                base: "https://a".into(),
                key: "uji-kunci-a".into(),
                label: "lane-1".into(),
            },
            Lane {
                base: "https://b".into(),
                key: "uji-kunci-b".into(),
                label: "lane-2".into(),
            },
        ];
        let cancelled = AtomicBool::new(false);
        let started = Instant::now();
        let first = acquire_lane("uji-dua-lane", &lanes, 40, &cancelled)
            .await
            .unwrap();
        let second = acquire_lane("uji-dua-lane", &lanes, 40, &cancelled)
            .await
            .unwrap();
        assert!(started.elapsed() < Duration::from_millis(500));
        assert_ne!(first.label, second.label);
        let third_started = Instant::now();
        let _third = acquire_lane("uji-dua-lane", &lanes, 40, &cancelled)
            .await
            .unwrap();
        assert!(third_started.elapsed() >= Duration::from_millis(1_400));
    }

    #[tokio::test]
    async fn cooldown_mengalihkan_ke_lane_lain() {
        let lanes = vec![
            Lane {
                base: "https://a".into(),
                key: "uji-cooldown-a".into(),
                label: "lane-1".into(),
            },
            Lane {
                base: "https://b".into(),
                key: "uji-cooldown-b".into(),
                label: "lane-2".into(),
            },
        ];
        let cancelled = AtomicBool::new(false);
        let first = acquire_lane("uji-cooldown", &lanes, 40, &cancelled)
            .await
            .unwrap();
        mark_lane_cooldown(
            "uji-cooldown",
            &key_fingerprint(&first.key),
            Duration::from_secs(5),
        )
        .await;
        let second = acquire_lane("uji-cooldown", &lanes, 40, &cancelled)
            .await
            .unwrap();
        assert_ne!(first.label, second.label);
    }

    #[test]
    fn pesan_429_503_bisa_di_retry() {
        assert!(retryable_message("429 Too Many Requests"));
        assert!(retryable_message("Service temporarily overloaded"));
        assert!(retryable_message("503 Service Unavailable"));
        assert!(retryable_message("Service Unavailable"));
        assert!(retryable_message("Bad Gateway"));
        assert!(!retryable_message("Invalid API key"));
    }

    #[test]
    fn kunci_cache_stabil_dan_unik() {
        let a = result_cache_key("local", "tev1:0.8b", "prompt yang sama");
        let b = result_cache_key("local", "tev1:0.8b", "prompt yang sama");
        let c = result_cache_key("local", "tev1:0.8b", "prompt berbeda");
        assert_eq!(a, b);
        assert_ne!(a, c);
        assert_eq!(a.len(), 16);
    }

    #[test]
    fn agy_worker_pakai_stream_json() {
        let mut s = spec(EngineKind::Cli, Some("agy"), None);
        s.key = "gemini-worker".into();
        s.raw = json!({"permission_mode": "skip"});
        let (program, args) = build_command(
            &s,
            Some("gemini-3.8-flash-high"),
            Some("high"),
            None,
            None,
            "halo",
            "/tmp",
        )
        .unwrap();
        assert_eq!(program, "agy");
        assert!(args.contains(&"--model".to_string()));
        assert!(args.contains(&"gemini-3.8-flash-high".to_string()));
        assert!(args.contains(&"--effort".to_string()));
        assert!(args.contains(&"high".to_string()));
        assert!(args.contains(&"--dangerously-skip-permissions".to_string()));
        assert!(args.contains(&"--print".to_string()));
        assert!(args.contains(&"halo".to_string()));
        assert!(args.contains(&"--output-format".to_string()));
        assert!(args.contains(&"stream-json".to_string()));
    }

    #[tokio::test]
    async fn stream_agy_line_mengurai_event() {
        let store = Store::offline();
        let record = Arc::new(Mutex::new(RunRecord {
            id: "run_test".into(),
            waker_id: "wk_1".into(),
            engine_key: "gemini-worker".into(),
            model: Some("gemini-3.8-flash-high".into()),
            reasoning_effort: None,
            context_window: None,
            content: "test".into(),
            prompt: "test".into(),
            workspace: "/tmp".into(),
            status: "running".into(),
            output: String::new(),
            input_tokens: None,
            output_tokens: None,
            cache_read_tokens: None,
            error: None,
            exit_code: None,
            started_at: Utc::now().to_rfc3339(),
            finished_at: None,
            events: vec![],
            next_seq: 0,
            last_activity: Instant::now(),
        }));
        let mut tool_names: HashMap<String, String> = HashMap::new();

        // 1. Init
        let init_line = r#"{"event":"init","conversation_id":"abc","init":{"model":"gemini-3.8-flash-high","cwd":"/home/irsofka","tools":["run_command","read_file"],"permission_mode":"always-proceed"}}"#;
        let err = stream_agy_line(&store, &record, init_line, &mut tool_names).await;
        assert!(err.is_none());

        // 2. Thinking
        let think_line = r#"{"event":"step_update","step_update":{"conversation_id":"abc","step_index":1,"state":"ACTIVE","step_type":"agent_response","thinking_delta":"sedang menganalisis"}}"#;
        stream_agy_line(&store, &record, think_line, &mut tool_names).await;

        // 3. Tool started
        let tool_start = r#"{"event":"step_update","step_update":{"conversation_id":"abc","step_index":2,"state":"ACTIVE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"date"}}}}"#;
        stream_agy_line(&store, &record, tool_start, &mut tool_names).await;

        // 4. Tool finished
        let tool_done = r#"{"event":"step_update","step_update":{"conversation_id":"abc","step_index":2,"state":"DONE","step_type":"tool","tool_name":"run_command","duration_seconds":0.05,"tool_info":{"name":"run_command","parameters":{"CommandLine":"date"},"output":"Sun Oct 25 2026"}}}"#;
        stream_agy_line(&store, &record, tool_done, &mut tool_names).await;

        // 5. Agent response delta
        let delta_line = r#"{"event":"step_update","step_update":{"conversation_id":"abc","step_index":3,"state":"ACTIVE","step_type":"agent_response","text_delta":"Hasilnya adalah "}}"#;
        stream_agy_line(&store, &record, delta_line, &mut tool_names).await;

        // 6. Result
        let res_line = r#"{"event":"result","result":{"conversation_id":"abc","status":"SUCCESS","response":"Hasilnya adalah OK","duration_seconds":1.2,"num_turns":1,"usage":{"input_tokens":100,"output_tokens":20,"thinking_tokens":10,"cache_read_tokens":5,"total_tokens":125}}}"#;
        let res_err = stream_agy_line(&store, &record, res_line, &mut tool_names).await;
        assert!(res_err.is_none());

        let rec = record.lock().await;
        assert_eq!(rec.output, "Hasilnya adalah ");
        assert_eq!(rec.input_tokens, Some(100));
        assert_eq!(rec.output_tokens, Some(20));
        assert_eq!(rec.cache_read_tokens, Some(5));

        let kinds: Vec<&str> = rec.events.iter().map(|e| e.kind.as_str()).collect();
        assert_eq!(
            kinds,
            vec!["status", "thinking", "tool_started", "tool_finished", "delta", "result"]
        );
    }
}
