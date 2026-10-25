//! Penyimpanan domain waker/run.
//!
//! PostgreSQL adalah backend utama. Bila tidak bisa tersambung, daemon tetap hidup
//! dengan berkas JSON — dan console melaporkan backend yang benar-benar dipakai,
//! bukan mengaku sudah menyimpan ke database.
//!
//! Semua id berasal dari console (`wk_…`, `run_…`, `conv_…`), karena itu kolomnya
//! TEXT, bukan UUID.

use chrono::{DateTime, Utc};
use std::sync::Arc;
use std::time::Instant;
use tokio_postgres::{Client, NoTls};

use crate::api::wakers::Waker;
use crate::automations::Automation;
use crate::runs::{RunEvent, RunRecord};
use crate::workflows::{Workflow, WorkflowRun, WorkflowStep};
use crate::save_state::pg_conn_str;

#[derive(Clone)]
pub(crate) struct Store {
    client: Option<Arc<Client>>,
    pub(crate) backend: &'static str,
}

/// Satu baris `waker_message` yang sudah dibaca untuk konteks prompt.
#[derive(Debug, Clone)]
pub(crate) struct StoredMessage {
    pub(crate) id: i64,
    pub(crate) role: String,
    pub(crate) content: String,
}

/// Ringkasan bergulir beserta batas pesan yang sudah tercakup.
#[derive(Debug, Clone)]
pub(crate) struct ConversationSummary {
    pub(crate) summary: String,
    pub(crate) covered_id: i64,
}

/// Hasil yang dipakai ulang dari cache. Token disimpan agar penghematannya terukur.
#[derive(Debug, Clone)]
pub(crate) struct CachedResult {
    pub(crate) output: String,
    pub(crate) input_tokens: Option<i64>,
    pub(crate) output_tokens: Option<i64>,
}

impl Store {
    #[cfg(test)]
    pub(crate) fn offline() -> Self {
        Self {
            client: None,
            backend: "JSON",
        }
    }

    pub(crate) async fn connect() -> Self {
        match tokio_postgres::connect(&pg_conn_str(), NoTls).await {
            Ok((client, connection)) => {
                tokio::spawn(async move {
                    let _ = connection.await;
                });
                let store = Self {
                    client: Some(Arc::new(client)),
                    backend: "POSTGRESQL",
                };
                if let Err(error) = store.ensure_schema().await {
                    eprintln!("⚠ skema waker tidak siap, jatuh ke JSON: {error}");
                    return Self {
                        client: None,
                        backend: "JSON",
                    };
                }
                store
            }
            Err(_) => Self {
                client: None,
                backend: "JSON",
            },
        }
    }

    fn client(&self) -> Option<&Client> {
        self.client.as_deref()
    }

    async fn ensure_schema(&self) -> Result<(), tokio_postgres::Error> {
        let Some(client) = self.client() else {
            return Ok(());
        };
        client
            .batch_execute(include_str!("../migrations/002_waker_console.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/003_waker_run_content.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/004_automation_schedule.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/005_approval_gate.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/006_approval_nullable_run.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/007_workflow_runs.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/008_workflow_versions.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/009_channels.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/010_groups.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/011_todos_goals.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/012_knowledge.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/013_knowledge_embedding.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/014_token_accounting.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/015_conversation_summary.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/016_result_cache.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/017_eval.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/018_automation_command.sql"))
            .await?;
        client
            .batch_execute(include_str!("../migrations/019_chat_availability.sql"))
            .await?;
        // Run tidak selamat dari restart daemon: proses anaknya sudah tidak ada.
        // Tanpa penutupan ini baris lama tetap "running" selamanya dan console
        // menampilkan pekerjaan hantu yang tidak bisa dibatalkan.
        if let Err(error) = client
            .execute(
                "UPDATE waker_run
                    SET status = 'failed',
                        error_message = COALESCE(error_message, 'interrupted by daemon restart'),
                        finished_at = now()
                  WHERE status IN ('running', 'queued')",
                &[],
            )
            .await
        {
            eprintln!("⚠ gagal menutup run yang terputus saat restart: {error}");
        }
        Ok(())
    }

    // ------------------------------------------------------------- trace

    pub(crate) async fn trace_runs(
        &self,
        limit: i64,
        status: Option<&str>,
        waker_id: Option<&str>,
    ) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT r.id, r.waker_id, COALESCE(w.name, r.waker_id) AS waker_name,
                        r.engine_key, r.model, r.status, r.started_at, r.finished_at,
                        r.exit_code, r.error_message, r.content,
                        r.input_tokens, r.output_tokens, r.cache_read_tokens,
                        (SELECT count(*) FROM waker_run_event e WHERE e.run_id = r.id) AS events
                 FROM waker_run r LEFT JOIN waker w ON w.id = r.waker_id
                 WHERE ($1::text IS NULL OR r.status = $1)
                   AND ($2::text IS NULL OR r.waker_id = $2)
                 ORDER BY r.created_at DESC LIMIT $3",
                &[&status, &waker_id, &limit],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    let started = row.get::<_, Option<DateTime<Utc>>>("started_at");
                    let finished = row.get::<_, Option<DateTime<Utc>>>("finished_at");
                    let duration_ms = match (started, finished) {
                        (Some(start), Some(end)) => {
                            Some((end - start).num_milliseconds())
                        }
                        _ => None,
                    };
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "waker_id": row.get::<_, String>("waker_id"),
                        "waker_name": row.get::<_, String>("waker_name"),
                        "engine_key": row.get::<_, String>("engine_key"),
                        "model": row.get::<_, Option<String>>("model"),
                        "status": row.get::<_, String>("status"),
                        "content": row.get::<_, String>("content"),
                        "exit_code": row.get::<_, Option<i32>>("exit_code"),
                        "error": row.get::<_, Option<String>>("error_message"),
                        "events": row.get::<_, i64>("events"),
                        "input_tokens": row.get::<_, Option<i64>>("input_tokens"),
                        "output_tokens": row.get::<_, Option<i64>>("output_tokens"),
                        "cache_read_tokens": row.get::<_, Option<i64>>("cache_read_tokens"),
                        "started_at": started.map(|at| at.to_rfc3339()),
                        "finished_at": finished.map(|at| at.to_rfc3339()),
                        "duration_ms": duration_ms,
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn get_run_trace(&self, id: &str) -> Option<serde_json::Value> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT r.id, r.waker_id, COALESCE(w.name, r.waker_id) AS waker_name,
                        r.engine_key, r.model, r.status, r.prompt, r.content,
                        r.started_at, r.finished_at, r.exit_code, r.error_message,
                        r.input_tokens, r.output_tokens, r.cache_read_tokens
                 FROM waker_run r LEFT JOIN waker w ON w.id = r.waker_id
                 WHERE r.id = $1",
                &[&id],
            )
            .await
            .ok()??;
        let events = client
            .query(
                "SELECT seq, kind, COALESCE(payload->>'text','') AS text, at
                 FROM waker_run_event WHERE run_id = $1 ORDER BY seq",
                &[&id],
            )
            .await
            .unwrap_or_default();
        Some(serde_json::json!({
            "id": row.get::<_, String>("id"),
            "waker_id": row.get::<_, String>("waker_id"),
            "waker_name": row.get::<_, String>("waker_name"),
            "engine_key": row.get::<_, String>("engine_key"),
            "model": row.get::<_, Option<String>>("model"),
            "status": row.get::<_, String>("status"),
            "content": row.get::<_, String>("content"),
            "prompt": row.get::<_, String>("prompt"),
            "exit_code": row.get::<_, Option<i32>>("exit_code"),
            "error": row.get::<_, Option<String>>("error_message"),
            "input_tokens": row.get::<_, Option<i64>>("input_tokens"),
            "output_tokens": row.get::<_, Option<i64>>("output_tokens"),
            "cache_read_tokens": row.get::<_, Option<i64>>("cache_read_tokens"),
            "started_at": row.get::<_, Option<DateTime<Utc>>>("started_at").map(|at| at.to_rfc3339()),
            "finished_at": row.get::<_, Option<DateTime<Utc>>>("finished_at").map(|at| at.to_rfc3339()),
            "events": events.iter().map(|event| serde_json::json!({
                "seq": event.get::<_, i32>("seq"),
                "kind": event.get::<_, String>("kind"),
                "text": event.get::<_, String>("text"),
                "at": event.get::<_, DateTime<Utc>>("at").to_rfc3339(),
            })).collect::<Vec<_>>(),
        }))
    }

    pub(crate) async fn trace_summary(&self) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!({});
        };
        let statuses = client
            .query("SELECT status, count(*) AS n FROM waker_run GROUP BY status", &[])
            .await
            .unwrap_or_default();
        let engines = client
            .query(
                "SELECT engine_key, count(*) AS n FROM waker_run
                 GROUP BY engine_key ORDER BY n DESC LIMIT 5",
                &[],
            )
            .await
            .unwrap_or_default();
        let recent: i64 = client
            .query_one(
                "SELECT count(*) AS n FROM waker_run WHERE created_at > now() - interval '24 hours'",
                &[],
            )
            .await
            .map(|row| row.get::<_, i64>("n"))
            .unwrap_or(0);
        let average: Option<f64> = client
            .query_one(
                "SELECT AVG(EXTRACT(EPOCH FROM (finished_at - started_at)))::float8 AS avg_s
                 FROM waker_run WHERE started_at IS NOT NULL AND finished_at IS NOT NULL",
                &[],
            )
            .await
            .ok()
            .and_then(|row| row.get::<_, Option<f64>>("avg_s"));
        // Token per worker: hanya run yang melaporkan `usage` yang dijumlahkan.
        // `token_reported_runs` membuat pembaca tahu berapa run yang angkanya nyata.
        let workers = client
            .query(
                "SELECT waker_id, count(*) AS n,
                        SUM(input_tokens)::bigint AS token_in,
                        SUM(output_tokens)::bigint AS token_out,
                        count(input_tokens) AS reported
                 FROM waker_run GROUP BY waker_id ORDER BY n DESC LIMIT 10",
                &[],
            )
            .await
            .unwrap_or_default();

        serde_json::json!({
            "by_status": statuses.iter().map(|row| serde_json::json!({
                "status": row.get::<_, String>("status"),
                "count": row.get::<_, i64>("n"),
            })).collect::<Vec<_>>(),
            "by_engine": engines.iter().map(|row| serde_json::json!({
                "engine": row.get::<_, String>("engine_key"),
                "count": row.get::<_, i64>("n"),
            })).collect::<Vec<_>>(),
            "by_waker": workers.iter().map(|row| serde_json::json!({
                "waker_id": row.get::<_, String>("waker_id"),
                "count": row.get::<_, i64>("n"),
                "input_tokens": row.get::<_, Option<i64>>("token_in"),
                "output_tokens": row.get::<_, Option<i64>>("token_out"),
                "token_reported_runs": row.get::<_, i64>("reported"),
            })).collect::<Vec<_>>(),
            "last_24h": recent,
            "avg_seconds": average,
        })
    }

    // -------------------------------------------------------- pengetahuan

    pub(crate) async fn list_knowledge(&self) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT k.id, k.title, k.tags, k.waker_id, k.created_at,
                        length(k.content) AS size, (k.embedding IS NOT NULL) AS has_embedding,
                        COALESCE(w.name, '') AS waker_name
                 FROM waker_knowledge k LEFT JOIN waker w ON w.id = k.waker_id
                 ORDER BY k.created_at DESC",
                &[],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "title": row.get::<_, String>("title"),
                        "tags": row.get::<_, String>("tags"),
                        "waker_id": row.get::<_, Option<String>>("waker_id"),
                        "waker_name": row.get::<_, String>("waker_name"),
                        "size": row.get::<_, i32>("size"),
                        "has_embedding": row.get::<_, bool>("has_embedding"),
                        "created_at": row.get::<_, DateTime<Utc>>("created_at").to_rfc3339(),
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn add_knowledge(
        &self,
        id: &str,
        waker_id: Option<&str>,
        title: &str,
        content: &str,
        tags: &str,
        embedding: Option<&[f32]>,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("pengetahuan memerlukan PostgreSQL".to_string());
        };
        let vector = embedding.map(|values| {
            serde_json::to_string(values).unwrap_or_else(|_| "[]".to_string())
        });
        client
            .execute(
                "INSERT INTO waker_knowledge (id, waker_id, title, content, tags, embedding)
                 VALUES ($1,$2,$3,$4,$5,$6::text::jsonb)",
                &[&id, &waker_id, &title, &content, &tags, &vector],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    /// (judul, isi, vektor) untuk pencarian semantik.
    pub(crate) async fn knowledge_entries(
        &self,
        waker_id: &str,
    ) -> Vec<(String, String, Option<Vec<f32>>)> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        let rows = client
            .query(
                "SELECT title, content, embedding::text AS embedding_text
                 FROM waker_knowledge
                 WHERE waker_id IS NULL OR waker_id = $1
                 ORDER BY created_at DESC LIMIT 500",
                &[&waker_id],
            )
            .await
            .unwrap_or_default();
        rows.iter()
            .map(|row| {
                let vector = row
                    .get::<_, Option<String>>("embedding_text")
                    .and_then(|text| serde_json::from_str::<Vec<f32>>(&text).ok());
                (
                    row.get::<_, String>("title"),
                    row.get::<_, String>("content"),
                    vector,
                )
            })
            .collect()
    }

    pub(crate) async fn knowledge_missing_embeddings(&self) -> Vec<(String, String)> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        let rows = client
            .query(
                "SELECT id, content FROM waker_knowledge WHERE embedding IS NULL LIMIT 200",
                &[],
            )
            .await
            .unwrap_or_default();
        rows.iter()
            .map(|row| {
                (
                    row.get::<_, String>("id"),
                    row.get::<_, String>("content"),
                )
            })
            .collect()
    }

    pub(crate) async fn set_knowledge_embedding(&self, id: &str, embedding: &[f32]) {
        let Some(client) = self.client() else {
            return;
        };
        let vector = serde_json::to_string(embedding).unwrap_or_else(|_| "[]".to_string());
        let _ = client
            .execute(
                "UPDATE waker_knowledge SET embedding = $2::text::jsonb WHERE id = $1",
                &[&id, &vector],
            )
            .await;
    }

    pub(crate) async fn delete_knowledge(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("pengetahuan memerlukan PostgreSQL".to_string());
        };
        client
            .execute("DELETE FROM waker_knowledge WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn search_knowledge(
        &self,
        waker_id: &str,
        query: &str,
        limit: usize,
    ) -> Vec<(String, String)> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        let tokens: Vec<String> = query
            .split(|ch: char| !ch.is_alphanumeric())
            .map(|word| word.to_lowercase())
            .filter(|word| word.chars().count() >= 3)
            .collect();
        if tokens.is_empty() {
            return Vec::new();
        }

        let rows = client
            .query(
                "SELECT title, content FROM waker_knowledge
                 WHERE waker_id IS NULL OR waker_id = $1
                 ORDER BY created_at DESC LIMIT 500",
                &[&waker_id],
            )
            .await
            .unwrap_or_default();

        let mut scored: Vec<(i64, String, String)> = Vec::new();
        for row in rows {
            let title: String = row.get("title");
            let content: String = row.get("content");
            let title_lower = title.to_lowercase();
            let content_lower = content.to_lowercase();
            let mut score = 0i64;
            for token in &tokens {
                if title_lower.contains(token) {
                    score += 3;
                }
                score += content_lower.matches(token.as_str()).count() as i64;
            }
            if score == 0 {
                continue;
            }
            let snippet = snippet_around(&content, &tokens);
            scored.push((score, title, snippet));
        }
        scored.sort_by(|a, b| b.0.cmp(&a.0));
        scored
            .into_iter()
            .take(limit)
            .map(|(_, title, snippet)| (title, snippet))
            .collect()
    }

    // ------------------------------------------------------- izin tool

    pub(crate) async fn list_permissions(&self, waker_id: &str) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT tool, decision FROM waker_permission
                 WHERE waker_id = $1 ORDER BY tool",
                &[&waker_id],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "tool": row.get::<_, String>("tool"),
                        "decision": row.get::<_, String>("decision"),
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn get_permission(&self, waker_id: &str, tool: &str) -> Option<String> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT decision FROM waker_permission WHERE waker_id = $1 AND tool = $2",
                &[&waker_id, &tool],
            )
            .await
            .ok()??;
        Some(row.get("decision"))
    }

    pub(crate) async fn set_permission(
        &self,
        id: &str,
        waker_id: &str,
        tool: &str,
        decision: &str,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("izin memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_permission (id, waker_id, tool, decision)
                 VALUES ($1,$2,$3,$4)
                 ON CONFLICT (waker_id, tool) DO UPDATE SET
                    decision = EXCLUDED.decision,
                    updated_at = now()",
                &[&id, &waker_id, &tool, &decision],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn delete_permission(
        &self,
        waker_id: &str,
        tool: &str,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("izin memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "DELETE FROM waker_permission WHERE waker_id = $1 AND tool = $2",
                &[&waker_id, &tool],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    // ---------------------------------------------------------- todo/goal

    pub(crate) async fn list_todos(&self, group_id: &str) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT t.id, t.title, t.status, t.waker_id, t.created_at,
                        COALESCE(w.name, '') AS waker_name
                 FROM waker_todo t LEFT JOIN waker w ON w.id = t.waker_id
                 WHERE t.group_id = $1
                 ORDER BY (t.status = 'open') DESC, t.created_at DESC",
                &[&group_id],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "title": row.get::<_, String>("title"),
                        "status": row.get::<_, String>("status"),
                        "waker_id": row.get::<_, Option<String>>("waker_id"),
                        "waker_name": row.get::<_, String>("waker_name"),
                        "created_at": row.get::<_, DateTime<Utc>>("created_at").to_rfc3339(),
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn add_todo(
        &self,
        id: &str,
        group_id: &str,
        title: &str,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("todo memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_todo (id, group_id, title) VALUES ($1,$2,$3)",
                &[&id, &group_id, &title],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn set_todo_status(&self, id: &str, status: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("todo memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "UPDATE waker_todo SET status = $2, updated_at = now() WHERE id = $1",
                &[&id, &status],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn delete_todo(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("todo memerlukan PostgreSQL".to_string());
        };
        client
            .execute("DELETE FROM waker_todo WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn get_goal(&self, group_id: &str) -> Option<(String, String)> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT objective, status FROM waker_goal WHERE group_id = $1",
                &[&group_id],
            )
            .await
            .ok()??;
        Some((row.get("objective"), row.get("status")))
    }

    pub(crate) async fn set_goal(
        &self,
        group_id: &str,
        objective: &str,
        status: &str,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("goal memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_goal (group_id, objective, status) VALUES ($1,$2,$3)
                 ON CONFLICT (group_id) DO UPDATE SET
                    objective = EXCLUDED.objective,
                    status = EXCLUDED.status,
                    updated_at = now()",
                &[&group_id, &objective, &status],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    // -------------------------------------------------------------- grup

    pub(crate) async fn list_groups(&self) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT g.id, g.name, g.sop, g.created_at,
                        (SELECT count(*) FROM waker_group_member m WHERE m.group_id = g.id) AS members
                 FROM waker_group g ORDER BY g.created_at DESC",
                &[],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "name": row.get::<_, String>("name"),
                        "sop": row.get::<_, String>("sop"),
                        "members": row.get::<_, i64>("members"),
                        "created_at": row.get::<_, DateTime<Utc>>("created_at").to_rfc3339(),
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn create_group(
        &self,
        id: &str,
        name: &str,
        sop: &str,
        members: &[String],
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("grup memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_group (id, name, sop) VALUES ($1,$2,$3)",
                &[&id, &name, &sop],
            )
            .await
            .map_err(|error| format!("{error:?}"))?;
        for (index, waker_id) in members.iter().enumerate() {
            client
                .execute(
                    "INSERT INTO waker_group_member (group_id, waker_id, role, ord)
                     VALUES ($1,$2,$3,$4)
                     ON CONFLICT (group_id, waker_id) DO NOTHING",
                    &[&id, &waker_id, &"member", &(index as i32)],
                )
                .await
                .map_err(|error| format!("{error:?}"))?;
        }
        Ok(())
    }

    /// (nama, sop, anggota [(waker_id, waker_name, role)]) terurut.
    pub(crate) async fn get_group(
        &self,
        id: &str,
    ) -> Option<(String, String, Vec<(String, String, String)>)> {
        let client = self.client()?;
        let row = client
            .query_opt("SELECT name, sop FROM waker_group WHERE id = $1", &[&id])
            .await
            .ok()??;
        let rows = client
            .query(
                "SELECT m.waker_id, m.role, COALESCE(w.name, m.waker_id) AS waker_name
                 FROM waker_group_member m
                 LEFT JOIN waker w ON w.id = m.waker_id
                 WHERE m.group_id = $1 ORDER BY m.ord",
                &[&id],
            )
            .await
            .unwrap_or_default();
        Some((
            row.get("name"),
            row.get("sop"),
            rows.iter()
                .map(|member| {
                    (
                        member.get::<_, String>("waker_id"),
                        member.get::<_, String>("waker_name"),
                        member.get::<_, String>("role"),
                    )
                })
                .collect(),
        ))
    }

    pub(crate) async fn delete_group(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("grup memerlukan PostgreSQL".to_string());
        };
        client
            .execute("DELETE FROM waker_group WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn save_group_message(
        &self,
        group_id: &str,
        waker_id: Option<&str>,
        role: &str,
        content: &str,
        run_id: Option<&str>,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "INSERT INTO waker_group_message (group_id, waker_id, role, content, run_id)
                 VALUES ($1,$2,$3,$4,$5)",
                &[&group_id, &waker_id, &role, &content, &run_id],
            )
            .await;
    }

    pub(crate) async fn group_messages(&self, group_id: &str) -> Vec<(String, String, String)> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        let rows = client
            .query(
                "SELECT COALESCE(w.name, m.waker_id, 'sistem') AS who, m.role, m.content
                 FROM waker_group_message m
                 LEFT JOIN waker w ON w.id = m.waker_id
                 WHERE m.group_id = $1 ORDER BY m.id",
                &[&group_id],
            )
            .await
            .unwrap_or_default();
        rows.iter()
            .map(|row| {
                (
                    row.get::<_, String>("who"),
                    row.get::<_, String>("role"),
                    row.get::<_, String>("content"),
                )
            })
            .collect()
    }

    // ------------------------------------------------------------ channel

    pub(crate) async fn list_channels(&self) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT c.id, c.provider, c.name, c.credentials_ref, c.status, c.created_at,
                        (SELECT count(*) FROM waker_channel_binding b WHERE b.channel_id = c.id) AS bindings
                 FROM waker_channel c ORDER BY c.created_at DESC",
                &[],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "provider": row.get::<_, String>("provider"),
                        "name": row.get::<_, String>("name"),
                        "credentials_ref": row.get::<_, String>("credentials_ref"),
                        "status": row.get::<_, String>("status"),
                        "bindings": row.get::<_, i64>("bindings"),
                        "created_at": row.get::<_, DateTime<Utc>>("created_at").to_rfc3339(),
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn create_channel(
        &self,
        id: &str,
        provider: &str,
        name: &str,
        credentials_ref: &str,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("channel memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_channel (id, provider, name, credentials_ref, status)
                 VALUES ($1,$2,$3,$4,$5)",
                &[
                    &id,
                    &provider,
                    &name,
                    &credentials_ref,
                    &if credentials_ref.is_empty() {
                        "disconnected"
                    } else {
                        "connected"
                    },
                ],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn get_channel(&self, id: &str) -> Option<(String, String, String)> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT provider, name, credentials_ref FROM waker_channel WHERE id = $1",
                &[&id],
            )
            .await
            .ok()??;
        Some((
            row.get("provider"),
            row.get("name"),
            row.get("credentials_ref"),
        ))
    }

    pub(crate) async fn delete_channel(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("channel memerlukan PostgreSQL".to_string());
        };
        client
            .execute("DELETE FROM waker_channel WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn list_bindings(&self, channel_id: &str) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT b.id, b.waker_id, b.external_chat_id, b.mode, b.created_at,
                        w.name AS waker_name
                 FROM waker_channel_binding b
                 LEFT JOIN waker w ON w.id = b.waker_id
                 WHERE b.channel_id = $1 ORDER BY b.created_at",
                &[&channel_id],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "waker_id": row.get::<_, String>("waker_id"),
                        "waker_name": row.get::<_, Option<String>>("waker_name"),
                        "external_chat_id": row.get::<_, String>("external_chat_id"),
                        "mode": row.get::<_, String>("mode"),
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn add_binding(
        &self,
        id: &str,
        channel_id: &str,
        waker_id: &str,
        external_chat_id: &str,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("channel memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_channel_binding (id, channel_id, waker_id, external_chat_id)
                 VALUES ($1,$2,$3,$4)
                 ON CONFLICT (channel_id, external_chat_id) DO UPDATE SET waker_id = EXCLUDED.waker_id",
                &[&id, &channel_id, &waker_id, &external_chat_id],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn delete_binding(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("channel memerlukan PostgreSQL".to_string());
        };
        client
            .execute("DELETE FROM waker_channel_binding WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    /// Cari binding berdasarkan ID chat saja — dipakai ingress IM yang tidak
    /// mengetahui channel mana yang dimaksud.
    pub(crate) async fn find_binding_any(
        &self,
        external_chat_id: &str,
    ) -> Option<(String, String)> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT channel_id, waker_id FROM waker_channel_binding
                 WHERE external_chat_id = $1 LIMIT 1",
                &[&external_chat_id],
            )
            .await
            .ok()??;
        Some((row.get("channel_id"), row.get("waker_id")))
    }

    pub(crate) async fn find_binding(
        &self,
        channel_id: &str,
        external_chat_id: &str,
    ) -> Option<String> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT waker_id FROM waker_channel_binding
                 WHERE channel_id = $1 AND external_chat_id = $2",
                &[&channel_id, &external_chat_id],
            )
            .await
            .ok()??;
        Some(row.get("waker_id"))
    }

    pub(crate) async fn log_channel_message(
        &self,
        channel_id: &str,
        direction: &str,
        external_chat_id: &str,
        waker_id: Option<&str>,
        run_id: Option<&str>,
        content: &str,
        status: &str,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "INSERT INTO waker_channel_message
                    (channel_id, direction, external_chat_id, waker_id, run_id, content, status)
                 VALUES ($1,$2,$3,$4,$5,$6,$7)",
                &[
                    &channel_id,
                    &direction,
                    &external_chat_id,
                    &waker_id,
                    &run_id,
                    &content,
                    &status,
                ],
            )
            .await;
    }

    pub(crate) async fn list_channel_messages(&self, channel_id: &str) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT direction, external_chat_id, waker_id, run_id, content, status, created_at
                 FROM waker_channel_message WHERE channel_id = $1
                 ORDER BY created_at DESC LIMIT 20",
                &[&channel_id],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "direction": row.get::<_, String>("direction"),
                        "external_chat_id": row.get::<_, String>("external_chat_id"),
                        "waker_id": row.get::<_, Option<String>>("waker_id"),
                        "run_id": row.get::<_, Option<String>>("run_id"),
                        "content": row.get::<_, String>("content"),
                        "status": row.get::<_, String>("status"),
                        "created_at": row.get::<_, DateTime<Utc>>("created_at").to_rfc3339(),
                    })
                })
                .collect(),
        )
    }

    // ----------------------------------------------------------- workflow

    pub(crate) async fn create_workflow(&self, workflow: &Workflow) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("workflow memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_workflow (id, waker_id, name, version) VALUES ($1,$2,$3,1)",
                &[&workflow.id, &workflow.waker_id, &workflow.name],
            )
            .await
            .map_err(|error| format!("{error:?}"))?;

        self.insert_steps(workflow).await?;
        self.insert_version(workflow).await?;
        Ok(())
    }

    async fn insert_steps(&self, workflow: &Workflow) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Ok(());
        };
        for step in &workflow.steps {
            let config = serde_json::json!({
                "prompt": step.prompt,
                "command": step.command,
                "branch_contains": step.branch_contains,
                "branch_goto": step.branch_goto,
            })
            .to_string();
            client
                .execute(
                    "INSERT INTO waker_workflow_step
                        (id, workflow_id, ord, name, kind, config, max_retries, on_failure)
                     VALUES ($1,$2,$3,$4,$5,$6::text::jsonb,$7,$8)",
                    &[
                        &format!("wfs_{}_{}", nanos(), step.ord),
                        &workflow.id,
                        &step.ord,
                        &step.name,
                        &step.kind,
                        &config,
                        &step.max_retries,
                        &step.on_failure,
                    ],
                )
                .await
                .map_err(|error| format!("{error:?}"))?;
        }
        Ok(())
    }

    async fn insert_version(&self, workflow: &Workflow) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Ok(());
        };
        let snapshot = serde_json::to_string(&serde_json::json!({
            "name": workflow.name,
            "steps": workflow.steps,
        }))
        .unwrap_or_else(|_| "{}".to_string());
        client
            .execute(
                "INSERT INTO waker_workflow_version (id, workflow_id, version, snapshot)
                 VALUES ($1,$2,$3,$4::text::jsonb)
                 ON CONFLICT (workflow_id, version)
                 DO UPDATE SET snapshot = EXCLUDED.snapshot",
                &[
                    &format!("wfv_{}_{}", nanos(), workflow.version),
                    &workflow.id,
                    &workflow.version,
                    &snapshot,
                ],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn update_workflow(&self, workflow: &Workflow) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("workflow memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "UPDATE waker_workflow SET name = $2, version = $3 WHERE id = $1",
                &[&workflow.id, &workflow.name, &workflow.version],
            )
            .await
            .map_err(|error| format!("{error:?}"))?;
        client
            .execute(
                "DELETE FROM waker_workflow_step WHERE workflow_id = $1",
                &[&workflow.id],
            )
            .await
            .map_err(|error| format!("{error:?}"))?;
        self.insert_steps(workflow).await?;
        self.insert_version(workflow).await?;
        Ok(())
    }

    pub(crate) async fn list_versions(&self, workflow_id: &str) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT version, created_at FROM waker_workflow_version
                 WHERE workflow_id = $1 ORDER BY version DESC",
                &[&workflow_id],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "version": row.get::<_, i32>("version"),
                        "created_at": row
                            .get::<_, DateTime<Utc>>("created_at")
                            .to_rfc3339(),
                    })
                })
                .collect(),
        )
    }

    /// Mengambil snapshot versi lama untuk dijadikan versi baru (rollback).
    pub(crate) async fn version_snapshot(
        &self,
        workflow_id: &str,
        version: i32,
    ) -> Option<(String, Vec<WorkflowStep>)> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT snapshot::text AS snapshot_text FROM waker_workflow_version
                 WHERE workflow_id = $1 AND version = $2",
                &[&workflow_id, &version],
            )
            .await
            .ok()??;
        let snapshot: serde_json::Value = row
            .get::<_, String>("snapshot_text")
            .parse()
            .unwrap_or(serde_json::json!({}));
        let name = snapshot
            .get("name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("Workflow")
            .to_string();
        let steps = snapshot
            .get("steps")
            .cloned()
            .and_then(|value| serde_json::from_value::<Vec<WorkflowStep>>(value).ok())
            .unwrap_or_default();
        Some((name, steps))
    }

    pub(crate) async fn list_workflows(&self) -> Option<Vec<Workflow>> {
        let client = self.client()?;
        let rows = client
            .query(
                "SELECT id, waker_id, name, version FROM waker_workflow ORDER BY created_at DESC",
                &[],
            )
            .await
            .ok()?;
        let mut items = Vec::with_capacity(rows.len());
        for row in rows {
            let id: String = row.get("id");
            items.push(Workflow {
                id: id.clone(),
                waker_id: row.get("waker_id"),
                name: row.get("name"),
                version: row.get("version"),
                steps: self.workflow_steps(&id).await.unwrap_or_default(),
            });
        }
        Some(items)
    }

    pub(crate) async fn get_workflow(&self, id: &str) -> Option<Workflow> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT id, waker_id, name, version FROM waker_workflow WHERE id = $1",
                &[&id],
            )
            .await
            .ok()??;
        Some(Workflow {
            id: row.get("id"),
            waker_id: row.get("waker_id"),
            name: row.get("name"),
            version: row.get("version"),
            steps: self.workflow_steps(id).await.unwrap_or_default(),
        })
    }

    async fn workflow_steps(&self, workflow_id: &str) -> Option<Vec<WorkflowStep>> {
        let client = self.client()?;
        let rows = client
            .query(
                "SELECT ord, name, kind, config::text AS config_text, max_retries, on_failure
                 FROM waker_workflow_step WHERE workflow_id = $1 ORDER BY ord",
                &[&workflow_id],
            )
            .await
            .ok()?;
        Some(
            rows.iter()
                .map(|row| {
                    let config: serde_json::Value = row
                        .get::<_, String>("config_text")
                        .parse()
                        .unwrap_or(serde_json::json!({}));
                    WorkflowStep {
                        ord: row.get("ord"),
                        name: row.get("name"),
                        kind: row.get("kind"),
                        prompt: config
                            .get("prompt")
                            .and_then(serde_json::Value::as_str)
                            .map(str::to_string),
                        command: config
                            .get("command")
                            .and_then(serde_json::Value::as_str)
                            .map(str::to_string),
                        branch_contains: config
                            .get("branch_contains")
                            .and_then(serde_json::Value::as_str)
                            .map(str::to_string),
                        branch_goto: config
                            .get("branch_goto")
                            .and_then(serde_json::Value::as_i64)
                            .map(|value| value as i32),
                        max_retries: row.get("max_retries"),
                        on_failure: row.get("on_failure"),
                    }
                })
                .collect(),
        )
    }

    pub(crate) async fn delete_workflow(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("workflow memerlukan PostgreSQL".to_string());
        };
        client
            .execute("DELETE FROM waker_workflow WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn create_workflow_run(&self, run: &WorkflowRun) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("workflow memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_workflow_run
                    (id, workflow_id, status, current_step, log, started_at)
                 VALUES ($1,$2,$3,$4,$5::text::jsonb,$6)",
                &[
                    &run.id,
                    &run.workflow_id,
                    &run.status,
                    &run.current_step,
                    &run.log.to_string(),
                    &run.started_at.as_deref().map(parse_ts),
                ],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn update_workflow_run(&self, run: &WorkflowRun) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE waker_workflow_run SET status = $2, current_step = $3,
                        log = $4::text::jsonb, error = $5, started_at = $6, finished_at = $7
                 WHERE id = $1",
                &[
                    &run.id,
                    &run.status,
                    &run.current_step,
                    &run.log.to_string(),
                    &run.error,
                    &run.started_at.as_deref().map(parse_ts),
                    &run.finished_at.as_deref().map(parse_ts),
                ],
            )
            .await;
    }

    pub(crate) async fn get_workflow_run(&self, id: &str) -> Option<WorkflowRun> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT id, workflow_id, status, current_step, log::text AS log_text,
                        error, started_at, finished_at
                 FROM waker_workflow_run WHERE id = $1",
                &[&id],
            )
            .await
            .ok()??;
        Some(row_to_workflow_run(&row))
    }

    pub(crate) async fn list_workflow_runs(&self, workflow_id: &str) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT id, workflow_id, status, current_step, log::text AS log_text,
                        error, started_at, finished_at
                 FROM waker_workflow_run WHERE workflow_id = $1
                 ORDER BY created_at DESC LIMIT 20",
                &[&workflow_id],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| serde_json::to_value(row_to_workflow_run(row)).unwrap_or_default())
                .collect(),
        )
    }

    // --------------------------------------------------------------- eval

    pub(crate) async fn create_eval_run(
        &self,
        id: &str,
        label: Option<&str>,
        waker_id: &str,
        engine_key: &str,
        model: Option<&str>,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("eval memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO eval_run (id, label, waker_id, engine_key, model, status)
                 VALUES ($1,$2,$3,$4,$5,'running')",
                &[&id, &label, &waker_id, &engine_key, &model],
            )
            .await
            .map(|_| ())
            .map_err(|error| format!("{error:?}"))
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) async fn finish_eval_run(
        &self,
        id: &str,
        status: &str,
        passed: i32,
        total: i32,
        skipped: i32,
        score: f64,
        input_tokens: Option<i64>,
        output_tokens: Option<i64>,
        error: Option<&str>,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE eval_run SET status=$2, passed=$3, total=$4, skipped=$5, score=$6,
                        input_tokens=$7, output_tokens=$8, error=$9, finished_at=now()
                 WHERE id=$1",
                &[
                    &id,
                    &status,
                    &passed,
                    &total,
                    &skipped,
                    &score,
                    &input_tokens,
                    &output_tokens,
                    &error,
                ],
            )
            .await;
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) async fn insert_eval_case(
        &self,
        id: &str,
        eval_run_id: &str,
        task_id: &str,
        status: &str,
        passed: bool,
        checks: &serde_json::Value,
        output: Option<&str>,
        reason: Option<&str>,
        run_id: Option<&str>,
        input_tokens: Option<i64>,
        output_tokens: Option<i64>,
        duration_ms: i64,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let checks_text = checks.to_string();
        let _ = client
            .execute(
                "INSERT INTO eval_case (id, eval_run_id, task_id, status, passed, checks, output,
                        reason, run_id, input_tokens, output_tokens, duration_ms)
                 VALUES ($1,$2,$3,$4,$5,$6::text::jsonb,$7,$8,$9,$10,$11,$12)",
                &[
                    &id,
                    &eval_run_id,
                    &task_id,
                    &status,
                    &passed,
                    &checks_text,
                    &output,
                    &reason,
                    &run_id,
                    &input_tokens,
                    &output_tokens,
                    &duration_ms,
                ],
            )
            .await;
    }

    pub(crate) async fn list_eval_runs(&self, limit: i64) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT id, label, waker_id, engine_key, model, status, passed, total, skipped,
                        score, input_tokens, output_tokens, error, created_at, finished_at
                 FROM eval_run ORDER BY created_at DESC LIMIT $1",
                &[&limit],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(rows.iter().map(row_to_eval_run).collect())
    }

    pub(crate) async fn get_eval_run(&self, id: &str) -> Option<serde_json::Value> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT id, label, waker_id, engine_key, model, status, passed, total, skipped,
                        score, input_tokens, output_tokens, error, created_at, finished_at
                 FROM eval_run WHERE id = $1",
                &[&id],
            )
            .await
            .ok()??;
        let mut run = row_to_eval_run(&row);
        let cases = client
            .query(
                "SELECT id, task_id, status, passed, checks::text AS checks_text, output, reason,
                        run_id, input_tokens, output_tokens, duration_ms, created_at
                 FROM eval_case WHERE eval_run_id = $1 ORDER BY created_at",
                &[&id],
            )
            .await
            .unwrap_or_default();
        if let Some(object) = run.as_object_mut() {
            object.insert(
                "cases".into(),
                serde_json::Value::Array(
                    cases
                        .iter()
                        .map(|case| {
                            serde_json::json!({
                                "id": case.get::<_, String>("id"),
                                "task_id": case.get::<_, String>("task_id"),
                                "status": case.get::<_, String>("status"),
                                "passed": case.get::<_, bool>("passed"),
                                "checks": case
                                    .get::<_, String>("checks_text")
                                    .parse::<serde_json::Value>()
                                    .unwrap_or_else(|_| serde_json::json!([])),
                                "output": case.get::<_, Option<String>>("output"),
                                "reason": case.get::<_, Option<String>>("reason"),
                                "run_id": case.get::<_, Option<String>>("run_id"),
                                "input_tokens": case.get::<_, Option<i64>>("input_tokens"),
                                "output_tokens": case.get::<_, Option<i64>>("output_tokens"),
                                "duration_ms": case.get::<_, i64>("duration_ms"),
                                "created_at": case
                                    .get::<_, DateTime<Utc>>("created_at")
                                    .to_rfc3339(),
                            })
                        })
                        .collect(),
                ),
            );
        }
        Some(run)
    }

    // ------------------------------------------------------------- kuota

    /// Pembacaan meter TERAKHIR per (engine, scope, jendela) dari `engine_quota`.
    /// Isi tabel itu milik meter F8.2; modul ini hanya membacanya.
    pub(crate) async fn latest_quota(&self) -> Vec<serde_json::Value> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        let rows = client
            .query(
                "SELECT DISTINCT q.engine, q.scope, q.limit_window, q.state,
                        q.remaining_fraction::float8 AS remaining_fraction,
                        q.reset_at, q.detail, q.ts
                 FROM engine_quota q
                 JOIN (SELECT engine, scope, limit_window, MAX(ts) AS mts
                       FROM engine_quota GROUP BY engine, scope, limit_window) t
                   ON t.engine = q.engine AND t.scope = q.scope
                  AND t.limit_window = q.limit_window AND t.mts = q.ts
                 ORDER BY q.engine, q.scope, q.limit_window",
                &[],
            )
            .await
            .unwrap_or_default();
        rows.iter()
            .map(|row| {
                serde_json::json!({
                    "engine": row.get::<_, String>("engine"),
                    "scope": row.get::<_, String>("scope"),
                    "window": row.get::<_, String>("limit_window"),
                    "state": row.get::<_, String>("state"),
                    "remaining_fraction": row.get::<_, Option<f64>>("remaining_fraction"),
                    "reset_at": row
                        .get::<_, Option<DateTime<Utc>>>("reset_at")
                        .map(|at| at.to_rfc3339()),
                    "detail": row.get::<_, Option<String>>("detail"),
                    "read_at": row.get::<_, DateTime<Utc>>("ts").to_rfc3339(),
                })
            })
            .collect()
    }

    // --------------------------------------------------------- persetujuan

    pub(crate) async fn create_approval(
        &self,
        origin: &str,
        payload: &serde_json::Value,
    ) -> Result<String, String> {
        let Some(client) = self.client() else {
            return Err("persetujuan memerlukan PostgreSQL".to_string());
        };
        let id = format!("appr_{:x}", nanos());
        let text = payload.to_string();
        client
            .execute(
                "INSERT INTO waker_approval (id, tool, payload, origin)
                 VALUES ($1,$2,$3::text::jsonb,$4)",
                &[&id, &"run", &text, &origin],
            )
            .await
            .map(|_| id)
            .map_err(|error| format!("{error:?}"))
    }

    pub(crate) async fn list_approvals(&self, status: Option<&str>) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = match status {
            Some(status) => {
                client
                    .query(
                        "SELECT id, origin, status, payload::text AS payload_text,
                                requested_at, decided_at, note, run_id
                         FROM waker_approval WHERE status = $1
                         ORDER BY requested_at DESC LIMIT 50",
                        &[&status],
                    )
                    .await
            }
            None => {
                client
                    .query(
                        "SELECT id, origin, status, payload::text AS payload_text,
                                requested_at, decided_at, note, run_id
                         FROM waker_approval
                         ORDER BY requested_at DESC LIMIT 50",
                        &[],
                    )
                    .await
            }
        }
        .unwrap_or_default();

        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    let payload: serde_json::Value = row
                        .get::<_, String>("payload_text")
                        .parse()
                        .unwrap_or(serde_json::json!({}));
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "origin": row.get::<_, Option<String>>("origin"),
                        "status": row.get::<_, String>("status"),
                        "requested_at": row.get::<_, DateTime<Utc>>("requested_at").to_rfc3339(),
                        "decided_at": row
                            .get::<_, Option<DateTime<Utc>>>("decided_at")
                            .map(|at| at.to_rfc3339()),
                        "note": row.get::<_, Option<String>>("note"),
                        "run_id": row.get::<_, Option<String>>("run_id"),
                        "payload": payload,
                    })
                })
                .collect(),
        )
    }

    pub(crate) async fn get_approval(
        &self,
        id: &str,
    ) -> Option<(String, serde_json::Value)> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT status, payload::text AS payload_text
                 FROM waker_approval WHERE id = $1",
                &[&id],
            )
            .await
            .ok()??;
        let payload: serde_json::Value = row
            .get::<_, String>("payload_text")
            .parse()
            .unwrap_or(serde_json::json!({}));
        Some((row.get::<_, String>("status"), payload))
    }

    pub(crate) async fn decide_approval(
        &self,
        id: &str,
        status: &str,
        note: Option<&str>,
        run_id: Option<&str>,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE waker_approval
                 SET status = $2, decided_at = now(), decided_by = 'operator',
                     note = $3, run_id = COALESCE($4, run_id)
                 WHERE id = $1 AND status = 'pending'",
                &[&id, &status, &note, &run_id],
            )
            .await;
    }

    pub(crate) async fn update_automation_run(
        &self,
        automation_run_id: &str,
        run_id: Option<&str>,
        status: &str,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE waker_automation_run
                 SET run_id = COALESCE($2, run_id), status = $3
                 WHERE id = $1",
                &[&automation_run_id, &run_id, &status],
            )
            .await;
    }

    // --------------------------------------------------------- automation

    pub(crate) async fn list_automations(&self) -> Option<Vec<Automation>> {
        let client = self.client()?;
        let rows = client
            .query(
                "SELECT id, waker_id, name, cron, timezone, enabled, overlap_policy, prompt,
                        kind, command, next_run_at, last_run_at
                 FROM waker_automation ORDER BY created_at DESC",
                &[],
            )
            .await
            .ok()?;
        Some(rows.iter().map(row_to_automation).collect())
    }

    pub(crate) async fn get_automation(&self, id: &str) -> Option<Automation> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT id, waker_id, name, cron, timezone, enabled, overlap_policy, prompt,
                        kind, command, next_run_at, last_run_at
                 FROM waker_automation WHERE id = $1",
                &[&id],
            )
            .await
            .ok()??;
        Some(row_to_automation(&row))
    }

    pub(crate) async fn upsert_automation(&self, automation: &Automation) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("automation memerlukan PostgreSQL".to_string());
        };
        client
            .execute(
                "INSERT INTO waker_automation (id, waker_id, name, cron, timezone, enabled,
                                               overlap_policy, prompt, kind, command, next_run_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                 ON CONFLICT (id) DO UPDATE SET
                    waker_id = EXCLUDED.waker_id,
                    name = EXCLUDED.name,
                    cron = EXCLUDED.cron,
                    timezone = EXCLUDED.timezone,
                    enabled = EXCLUDED.enabled,
                    overlap_policy = EXCLUDED.overlap_policy,
                    prompt = EXCLUDED.prompt,
                    kind = EXCLUDED.kind,
                    command = EXCLUDED.command,
                    next_run_at = EXCLUDED.next_run_at,
                    updated_at = now()",
                &[
                    &automation.id,
                    &automation.waker_id,
                    &automation.name,
                    &automation.cron,
                    &automation.timezone,
                    &automation.enabled,
                    &automation.overlap_policy,
                    &automation.prompt,
                    &automation.kind,
                    &automation.command,
                    &automation.next_run_at.as_deref().map(parse_ts),
                ],
            )
            .await
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    pub(crate) async fn delete_automation(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Err("automation memerlukan PostgreSQL".to_string());
        };
        client
            .execute("DELETE FROM waker_automation WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    pub(crate) async fn due_automations(&self, now: DateTime<Utc>) -> Vec<Automation> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        client
            .query(
                "SELECT id, waker_id, name, cron, timezone, enabled, overlap_policy, prompt,
                        kind, command, next_run_at, last_run_at
                 FROM waker_automation
                 WHERE enabled = TRUE AND (next_run_at IS NULL OR next_run_at <= $1)
                 ORDER BY next_run_at NULLS FIRST LIMIT 20",
                &[&now],
            )
            .await
            .map(|rows| rows.iter().map(row_to_automation).collect())
            .unwrap_or_default()
    }

    pub(crate) async fn mark_automation(&self, id: &str, next: DateTime<Utc>) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE waker_automation SET next_run_at = $2, last_run_at = now(),
                                             updated_at = now()
                 WHERE id = $1",
                &[&id, &next],
            )
            .await;
    }

    pub(crate) async fn disable_automation(&self, id: &str) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE waker_automation SET enabled = FALSE, next_run_at = NULL,
                                             updated_at = now()
                 WHERE id = $1",
                &[&id],
            )
            .await;
    }

    pub(crate) async fn running_run_for_automation(&self, id: &str) -> Option<String> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT ar.run_id FROM waker_automation_run ar
                 JOIN waker_run r ON r.id = ar.run_id
                 WHERE ar.automation_id = $1 AND r.status IN ('running', 'queued')
                 ORDER BY ar.scheduled_for DESC LIMIT 1",
                &[&id],
            )
            .await
            .ok()??;
        row.get::<_, Option<String>>("run_id")
    }

    pub(crate) async fn automation_is_running(&self, id: &str) -> bool {
        let Some(client) = self.client() else {
            return false;
        };
        client
            .query_opt(
                // Command jobs have no waker_run: their row stays 'running' until
                // the script finishes, so both shapes must count as running.
                "SELECT 1 FROM waker_automation_run ar
                 LEFT JOIN waker_run r ON r.id = ar.run_id
                 WHERE ar.automation_id = $1
                   AND (r.status = 'running'
                        OR (ar.run_id IS NULL AND ar.status = 'running'))
                 LIMIT 1",
                &[&id],
            )
            .await
            .ok()
            .flatten()
            .is_some()
    }

    pub(crate) async fn record_automation_run(
        &self,
        automation_id: &str,
        run_id: Option<&str>,
        scheduled_for: DateTime<Utc>,
        status: &str,
    ) -> Result<String, String> {
        let Some(client) = self.client() else {
            return Ok(String::new());
        };
        let id = format!("arun_{:x}", nanos());
        client
            .execute(
                "INSERT INTO waker_automation_run (id, automation_id, run_id, scheduled_for, status)
                 VALUES ($1,$2,$3,$4,$5)
                 ON CONFLICT (automation_id, scheduled_for) DO UPDATE SET
                    status = EXCLUDED.status,
                    run_id = COALESCE(EXCLUDED.run_id, waker_automation_run.run_id)",
                &[&id, &automation_id, &run_id, &scheduled_for, &status],
            )
            .await
            .map(|_| id)
            .map_err(|error| error.to_string())
    }

    pub(crate) async fn last_automation_run(&self, automation_id: &str) -> Option<String> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT status FROM waker_automation_run
                 WHERE automation_id = $1 ORDER BY scheduled_for DESC LIMIT 1",
                &[&automation_id],
            )
            .await
            .ok()??;
        Some(row.get::<_, String>("status"))
    }

    pub(crate) async fn automation_runs(
        &self,
        automation_id: &str,
        limit: i64,
    ) -> serde_json::Value {
        let Some(client) = self.client() else {
            return serde_json::json!([]);
        };
        let rows = client
            .query(
                "SELECT ar.id, ar.status, ar.scheduled_for, ar.run_id, r.status AS run_status
                 FROM waker_automation_run ar
                 LEFT JOIN waker_run r ON r.id = ar.run_id
                 WHERE ar.automation_id = $1
                 ORDER BY ar.scheduled_for DESC LIMIT $2",
                &[&automation_id, &limit],
            )
            .await
            .unwrap_or_default();
        serde_json::Value::Array(
            rows.iter()
                .map(|row| {
                    serde_json::json!({
                        "id": row.get::<_, String>("id"),
                        "status": row.get::<_, String>("status"),
                        "run_id": row.get::<_, Option<String>>("run_id"),
                        "run_status": row.get::<_, Option<String>>("run_status"),
                        "scheduled_for": row
                            .get::<_, DateTime<Utc>>("scheduled_for")
                            .to_rfc3339(),
                    })
                })
                .collect(),
        )
    }

    // ------------------------------------------------------------- wakers

    pub(crate) async fn load_wakers(&self) -> Option<Vec<Waker>> {
        let client = self.client()?;
        let rows = client
            .query(
                "SELECT id, slug, name, description, system_prompt, engine_key, model,
                        reasoning_effort, context_window, workspace, status, approval_mode,
                        answer_style, max_output_tokens::bigint AS max_output_tokens,
                        chat_available, created_at, updated_at
                 FROM waker ORDER BY created_at",
                &[],
            )
            .await
            .ok()?;
        Some(rows.iter().map(row_to_waker).collect())
    }

    pub(crate) async fn upsert_waker(&self, waker: &Waker) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Ok(());
        };
        let context = waker.context_window.map(|value| value as i64);
        // Kolom `max_output_tokens` bertipe INTEGER; kirim sebagai i32 agar
        // serialisasi cocok (i64 hanya dipetakan ke BIGINT).
        let max_output_tokens = waker.max_output_tokens.map(|value| value as i32);
        client
            .execute(
                "INSERT INTO waker (id, slug, name, description, system_prompt, engine_key, model,
                                    reasoning_effort, context_window, workspace, status,
                                    approval_mode, answer_style, max_output_tokens,
                                    chat_available, created_at, updated_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                 ON CONFLICT (id) DO UPDATE SET
                    slug = EXCLUDED.slug,
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    system_prompt = EXCLUDED.system_prompt,
                    engine_key = EXCLUDED.engine_key,
                    model = EXCLUDED.model,
                    reasoning_effort = EXCLUDED.reasoning_effort,
                    context_window = EXCLUDED.context_window,
                    workspace = EXCLUDED.workspace,
                    status = EXCLUDED.status,
                    approval_mode = EXCLUDED.approval_mode,
                    answer_style = EXCLUDED.answer_style,
                    max_output_tokens = EXCLUDED.max_output_tokens,
                    chat_available = EXCLUDED.chat_available,
                    updated_at = EXCLUDED.updated_at",
                &[
                    &waker.id,
                    &waker.slug,
                    &waker.name,
                    &waker.description,
                    &waker.system_prompt,
                    &waker.engine_key,
                    &waker.model,
                    &waker.reasoning_effort,
                    &context,
                    &waker.workspace,
                    &waker.status,
                    &waker.approval_mode,
                    &waker.answer_style,
                    &max_output_tokens,
                    &waker.chat_available,
                    &parse_ts(&waker.created_at),
                    &parse_ts(&waker.updated_at),
                ],
            )
            .await
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    pub(crate) async fn delete_waker(&self, id: &str) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Ok(());
        };
        client
            .execute("DELETE FROM waker WHERE id = $1", &[&id])
            .await
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    // -------------------------------------------------------- percakapan

    /// Satu percakapan per waker pada tahap ini; grup percakapan menyusul.
    pub(crate) async fn ensure_conversation(&self, waker_id: &str) -> Option<String> {
        let client = self.client()?;
        if let Ok(Some(row)) = client
            .query_opt(
                "SELECT id FROM waker_conversation WHERE waker_id = $1
                 ORDER BY created_at LIMIT 1",
                &[&waker_id],
            )
            .await
        {
            return Some(row.get::<_, String>("id"));
        }
        let id = format!("conv_{:x}", nanos());
        client
            .execute(
                "INSERT INTO waker_conversation (id, waker_id, title) VALUES ($1,$2,$3)",
                &[&id, &waker_id, &"Percakapan"],
            )
            .await
            .ok()?;
        Some(id)
    }

    // --------------------------------------------------------------- run

    pub(crate) async fn start_run(
        &self,
        run: &RunRecord,
        conversation_id: Option<&str>,
        status: &str,
    ) -> Result<(), String> {
        let Some(client) = self.client() else {
            return Ok(());
        };
        let context = run.context_window.map(|value| value as i64);
        client
            .execute(
                "INSERT INTO waker_run (id, waker_id, conversation_id, engine_key, model,
                                        reasoning_effort, context_window, status, prompt, content,
                                        workspace, started_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NULL)",
                &[
                    &run.id,
                    &run.waker_id,
                    &conversation_id,
                    &run.engine_key,
                    &run.model,
                    &run.reasoning_effort,
                    &context,
                    &status,
                    &run.prompt,
                    &run.content,
                    &run.workspace,
                ],
            )
            .await
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    pub(crate) async fn mark_run_running(&self, id: &str, started_at: &str) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE waker_run SET status = 'running', started_at = $2 WHERE id = $1",
                &[&id, &parse_ts(started_at)],
            )
            .await;
    }

    pub(crate) async fn append_event(&self, run_id: &str, event: &RunEvent) {
        let Some(client) = self.client() else {
            return;
        };
        let payload = serde_json::json!({ "text": event.text }).to_string();
        if let Err(error) = client
            .execute(
                // `::text::jsonb`: parameter dikirim sebagai teks (String), lalu
                // di-cast oleh Postgres. `$4::jsonb` membuat klien diminta
                // menyerialisasi langsung ke JSONB, yang tidak didukung tanpa
                // fitur serde_json pada tokio-postgres.
                "INSERT INTO waker_run_event (run_id, seq, kind, payload, at)
                 VALUES ($1,$2,$3,$4::text::jsonb,$5)
                 ON CONFLICT (run_id, seq) DO NOTHING",
                &[
                    &run_id,
                    &(event.seq as i32),
                    &event.kind,
                    &payload,
                    &parse_ts(&event.at),
                ],
            )
            .await
        {
            eprintln!("⚠ event run {run_id} ({}): {error}", event.kind);
        }
    }

    pub(crate) async fn finish_run(&self, run: &RunRecord) {
        let Some(client) = self.client() else {
            return;
        };
        let finished = run.finished_at.as_deref().map(parse_ts);
        let _ = client
            .execute(
                "UPDATE waker_run SET status = $2, exit_code = $3, error_message = $4,
                                      finished_at = $5, input_tokens = $6,
                                      output_tokens = $7, cache_read_tokens = $8
                 WHERE id = $1",
                &[
                    &run.id,
                    &run.status,
                    &run.exit_code,
                    &run.error,
                    &finished,
                    &run.input_tokens,
                    &run.output_tokens,
                    &run.cache_read_tokens,
                ],
            )
            .await;
        // Status automation mengikuti status run-nya, supaya console tidak
        // menampilkan "berjalan" untuk tugas yang sudah selesai.
        let _ = client
            .execute(
                "UPDATE waker_automation_run SET status = $2 WHERE run_id = $1",
                &[&run.id, &run.status],
            )
            .await;
    }

    pub(crate) async fn save_message(
        &self,
        conversation_id: &str,
        role: &str,
        content: &str,
        run_id: Option<&str>,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "INSERT INTO waker_message (conversation_id, role, content, run_id)
                 VALUES ($1,$2,$3,$4)",
                &[&conversation_id, &role, &content, &run_id],
            )
            .await;
        let _ = client
            .execute(
                "UPDATE waker_conversation SET updated_at = now() WHERE id = $1",
                &[&conversation_id],
            )
            .await;
    }

    /// N pesan terakhir (urut lama → baru) untuk konteks prompt.
    pub(crate) async fn conversation_messages(
        &self,
        conversation_id: &str,
        limit: i64,
    ) -> Vec<StoredMessage> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        let rows = client
            .query(
                "SELECT id, role, content FROM (
                     SELECT id, role, content FROM waker_message
                     WHERE conversation_id = $1 ORDER BY id DESC LIMIT $2
                 ) recent ORDER BY id ASC",
                &[&conversation_id, &limit],
            )
            .await
            .unwrap_or_default();
        rows.iter()
            .map(|row| StoredMessage {
                id: row.get("id"),
                role: row.get("role"),
                content: row.get("content"),
            })
            .collect()
    }

    /// Semua pesan setelah `after_id`, urut lama → baru. Dipakai pembuat ringkasan
    /// supaya pesan yang sudah tercakup tidak diringkas dua kali.
    pub(crate) async fn messages_after(
        &self,
        conversation_id: &str,
        after_id: i64,
    ) -> Vec<StoredMessage> {
        let Some(client) = self.client() else {
            return Vec::new();
        };
        let rows = client
            .query(
                "SELECT id, role, content FROM waker_message
                 WHERE conversation_id = $1 AND id > $2 ORDER BY id ASC",
                &[&conversation_id, &after_id],
            )
            .await
            .unwrap_or_default();
        rows.iter()
            .map(|row| StoredMessage {
                id: row.get("id"),
                role: row.get("role"),
                content: row.get("content"),
            })
            .collect()
    }

    pub(crate) async fn conversation_summary(
        &self,
        conversation_id: &str,
    ) -> Option<ConversationSummary> {
        let client = self.client()?;
        let row = client
            .query_opt(
                "SELECT summary, summary_covered_id
                 FROM waker_conversation WHERE id = $1",
                &[&conversation_id],
            )
            .await
            .ok()??;
        let summary: Option<String> = row.get("summary");
        let summary = summary?;
        if summary.trim().is_empty() {
            return None;
        }
        Some(ConversationSummary {
            summary,
            covered_id: row
                .get::<_, Option<i64>>("summary_covered_id")
                .unwrap_or(0),
        })
    }

    pub(crate) async fn set_conversation_summary(
        &self,
        conversation_id: &str,
        summary: &str,
        covered_id: i64,
        model: &str,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "UPDATE waker_conversation
                 SET summary = $2, summary_covered_id = $3, summary_model = $4,
                     summary_updated_at = now()
                 WHERE id = $1",
                &[&conversation_id, &summary, &covered_id, &model],
            )
            .await;
    }

    // ------------------------------------------------------- cache hasil

    /// Baca hasil yang masih dalam TTL dan catat satu hit. `None` berarti cache
    /// kosong/kedaluwarsa atau backend JSON tidak mendukung cache.
    pub(crate) async fn cache_lookup(&self, key: &str, ttl_secs: i64) -> Option<CachedResult> {
        let client = self.client()?;
        let cutoff = Utc::now() - chrono::Duration::seconds(ttl_secs.max(60));
        let row = client
            .query_opt(
                "UPDATE waker_result_cache
                 SET hits = hits + 1, last_hit_at = now()
                 WHERE cache_key = $1 AND created_at >= $2
                 RETURNING output, input_tokens, output_tokens",
                &[&key, &cutoff],
            )
            .await
            .ok()??;
        Some(CachedResult {
            output: row.get("output"),
            input_tokens: row.get("input_tokens"),
            output_tokens: row.get("output_tokens"),
        })
    }

    pub(crate) async fn cache_store(
        &self,
        key: &str,
        engine_key: &str,
        model: Option<&str>,
        output: &str,
        input_tokens: Option<i64>,
        output_tokens: Option<i64>,
    ) {
        let Some(client) = self.client() else {
            return;
        };
        let _ = client
            .execute(
                "INSERT INTO waker_result_cache
                     (cache_key, engine_key, model, output, input_tokens, output_tokens)
                 VALUES ($1,$2,$3,$4,$5,$6)
                 ON CONFLICT (cache_key) DO UPDATE
                 SET output = EXCLUDED.output,
                     input_tokens = EXCLUDED.input_tokens,
                     output_tokens = EXCLUDED.output_tokens,
                     created_at = now()",
                &[
                    &key,
                    &engine_key,
                    &model,
                    &output,
                    &input_tokens,
                    &output_tokens,
                ],
            )
            .await;
    }

    pub(crate) async fn cache_prune(&self, ttl_secs: i64) {
        let Some(client) = self.client() else {
            return;
        };
        let cutoff = Utc::now() - chrono::Duration::seconds(ttl_secs.max(60));
        let _ = client
            .execute(
                "DELETE FROM waker_result_cache WHERE created_at < $1",
                &[&cutoff],
            )
            .await;
    }

    /// Riwayat lintas-restart. Event diambil per run; 50 run terakhir cukup untuk
    /// memulihkan percakapan tanpa memuat seluruh tabel.
    pub(crate) async fn recent_runs(&self, limit: i64) -> Option<Vec<RunRecord>> {
        let client = self.client()?;
        let rows = client
            .query(
                "SELECT id, waker_id, conversation_id, engine_key, model, reasoning_effort,
                        context_window, status, prompt, content, workspace,
                        started_at, finished_at, exit_code, error_message,
                        input_tokens, output_tokens, cache_read_tokens
                 FROM waker_run
                 ORDER BY started_at DESC NULLS LAST LIMIT $1",
                &[&limit],
            )
            .await
            .ok()?;

        let mut runs = Vec::with_capacity(rows.len());
        for row in rows {
            let run_id: String = row.get("id");
            let event_rows = client
                .query(
                    "SELECT seq, kind, at, COALESCE(payload->>'text','') AS text
                     FROM waker_run_event WHERE run_id = $1 ORDER BY seq",
                    &[&run_id],
                )
                .await
                .unwrap_or_default();
            let events: Vec<RunEvent> = event_rows
                .iter()
                .map(|event| RunEvent {
                    seq: event.get::<_, i32>("seq") as u64,
                    at: event.get::<_, DateTime<Utc>>("at").to_rfc3339(),
                    kind: event.get("kind"),
                    text: event.get("text"),
                })
                .collect();
            let mut output = String::new();
            for event in &events {
                match event.kind.as_str() {
                    "output" => {
                        if !output.is_empty() {
                            output.push('\n');
                        }
                        output.push_str(&event.text);
                    }
                    "delta" => output.push_str(&event.text),
                    _ => {}
                }
            }

            let next_seq = events.last().map(|event| event.seq + 1).unwrap_or(0);
            runs.push(RunRecord {
                id: run_id,
                waker_id: row.get("waker_id"),
                engine_key: row.get("engine_key"),
                model: row.get("model"),
                reasoning_effort: row.get("reasoning_effort"),
                context_window: row
                    .get::<_, Option<i64>>("context_window")
                    .map(|value| value as u64),
                content: row.get("content"),
                prompt: row.get("prompt"),
                workspace: row.get("workspace"),
                status: row.get("status"),
                output,
                input_tokens: row.get("input_tokens"),
                output_tokens: row.get("output_tokens"),
                cache_read_tokens: row.get("cache_read_tokens"),
                error: row.get("error_message"),
                exit_code: row.get("exit_code"),
                started_at: row
                    .get::<_, Option<DateTime<Utc>>>("started_at")
                    .map(|at| at.to_rfc3339())
                    .unwrap_or_default(),
                finished_at: row
                    .get::<_, Option<DateTime<Utc>>>("finished_at")
                    .map(|at| at.to_rfc3339()),
                events,
                next_seq,
                last_activity: Instant::now(),
            });
        }
        Some(runs)
    }
}

fn snippet_around(content: &str, tokens: &[String]) -> String {
    let lower = content.to_lowercase();
    let found = tokens
        .iter()
        .filter_map(|token| lower.find(token.as_str()))
        .min()
        .map(|byte| lower[..byte].chars().count())
        .unwrap_or(0);
    let chars: Vec<char> = content.chars().collect();
    let start = found.saturating_sub(120).min(chars.len());
    let end = (found + 680).min(chars.len());
    let mut text: String = chars[start..end].iter().collect();
    if start > 0 {
        text.insert(0, '…');
    }
    if end < chars.len() {
        text.push('…');
    }
    text
}

fn row_to_workflow_run(row: &tokio_postgres::Row) -> WorkflowRun {
    WorkflowRun {
        id: row.get("id"),
        workflow_id: row.get("workflow_id"),
        status: row.get("status"),
        current_step: row.get("current_step"),
        log: row
            .get::<_, String>("log_text")
            .parse()
            .unwrap_or(serde_json::json!([])),
        error: row.get("error"),
        started_at: row
            .get::<_, Option<DateTime<Utc>>>("started_at")
            .map(|at| at.to_rfc3339()),
        finished_at: row
            .get::<_, Option<DateTime<Utc>>>("finished_at")
            .map(|at| at.to_rfc3339()),
    }
}

fn row_to_eval_run(row: &tokio_postgres::Row) -> serde_json::Value {
    serde_json::json!({
        "id": row.get::<_, String>("id"),
        "label": row.get::<_, Option<String>>("label"),
        "waker_id": row.get::<_, String>("waker_id"),
        "engine_key": row.get::<_, String>("engine_key"),
        "model": row.get::<_, Option<String>>("model"),
        "status": row.get::<_, String>("status"),
        "passed": row.get::<_, i32>("passed"),
        "total": row.get::<_, i32>("total"),
        "skipped": row.get::<_, i32>("skipped"),
        "score": row.get::<_, f64>("score"),
        "input_tokens": row.get::<_, Option<i64>>("input_tokens"),
        "output_tokens": row.get::<_, Option<i64>>("output_tokens"),
        "error": row.get::<_, Option<String>>("error"),
        "created_at": row.get::<_, DateTime<Utc>>("created_at").to_rfc3339(),
        "finished_at": row
            .get::<_, Option<DateTime<Utc>>>("finished_at")
            .map(|at| at.to_rfc3339()),
    })
}

fn row_to_automation(row: &tokio_postgres::Row) -> Automation {
    Automation {
        id: row.get("id"),
        waker_id: row.get("waker_id"),
        name: row.get("name"),
        cron: row.get("cron"),
        timezone: row.get("timezone"),
        enabled: row.get("enabled"),
        overlap_policy: row.get("overlap_policy"),
        prompt: row.get("prompt"),
        kind: row.get("kind"),
        command: row.get("command"),
        next_run_at: row
            .get::<_, Option<DateTime<Utc>>>("next_run_at")
            .map(|at| at.to_rfc3339()),
        last_run_at: row
            .get::<_, Option<DateTime<Utc>>>("last_run_at")
            .map(|at| at.to_rfc3339()),
    }
}

fn parse_ts(value: &str) -> DateTime<Utc> {
    DateTime::parse_from_rfc3339(value)
        .map(|at| at.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now())
}

fn nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

fn row_to_waker(row: &tokio_postgres::Row) -> Waker {
    Waker {
        id: row.get("id"),
        slug: row.get("slug"),
        name: row.get("name"),
        description: row.get::<_, Option<String>>("description").unwrap_or_default(),
        system_prompt: row
            .get::<_, Option<String>>("system_prompt")
            .unwrap_or_default(),
        engine_key: row.get("engine_key"),
        model: row.get("model"),
        reasoning_effort: row.get("reasoning_effort"),
        context_window: row
            .get::<_, Option<i64>>("context_window")
            .map(|value| value as u64),
        workspace: row.get("workspace"),
        status: row.get("status"),
        approval_mode: row.get("approval_mode"),
        answer_style: row.get("answer_style"),
        max_output_tokens: row.get("max_output_tokens"),
        chat_available: row.get::<_, Option<bool>>("chat_available").unwrap_or(true),
        created_at: row
            .get::<_, DateTime<Utc>>("created_at")
            .to_rfc3339(),
        updated_at: row
            .get::<_, DateTime<Utc>>("updated_at")
            .to_rfc3339(),
    }
}
