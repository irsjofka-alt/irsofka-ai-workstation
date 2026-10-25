//! Klien MCP (Model Context Protocol) lewat stdio.
//!
//! Satu server = satu proses anak yang dijalankan hanya saat dibutuhkan, lalu
//! dimatikan. Tidak ada proses MCP yang standby di latar belakang: katalog
//! di-cache, tetapi eksekusi selalu memulai proses baru.
//!
//! Alur JSON-RPC: `initialize` → `notifications/initialized` → `tools/list`
//! atau `tools/call`. Pesan dipisahkan baris baru (transport stdio resmi MCP).

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::OnceLock;
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;

use crate::store::Store;

const CLIENT_NAME: &str = "irsofka-ai-station";
const CLIENT_VERSION: &str = "2.0.0";
const PROTOCOL_VERSION: &str = "2024-11-05";
const CATALOG_TTL: Duration = Duration::from_secs(300);
const MAX_TOOL_OUTPUT: usize = 200_000;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct McpConfig {
    #[serde(default)]
    pub(crate) servers: HashMap<String, McpServer>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct McpServer {
    #[serde(default)]
    pub(crate) command: Option<String>,
    #[serde(default)]
    pub(crate) args: Vec<String>,
    #[serde(default)]
    pub(crate) env: HashMap<String, String>,
    #[serde(default = "default_true")]
    pub(crate) enabled: bool,
    #[serde(default)]
    pub(crate) cwd: Option<String>,
    /// Transport HTTP/SSE belum dijalankan; entri dengan `url` dilaporkan
    /// sebagai tidak didukung, bukan diam-diam diabaikan.
    #[serde(default)]
    pub(crate) url: Option<String>,
    /// Izin bawaan per tool: allow | ask | deny. Izin worker tetap menang.
    #[serde(default)]
    pub(crate) tools: HashMap<String, String>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct McpTool {
    pub(crate) server: String,
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) input_schema: Value,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ToolCall {
    pub(crate) server: String,
    pub(crate) tool: String,
    pub(crate) arguments: Value,
}

#[derive(Debug, Clone)]
pub(crate) struct ToolResult {
    pub(crate) text: String,
    pub(crate) is_error: bool,
}

fn config_path() -> std::path::PathBuf {
    crate::paths::config_dir().join("mcp_servers.json")
}

fn default_config() -> McpConfig {
    let script = crate::paths::station_dir()
        .join("tools")
        .join("mcp_workstation_local.py");
    let mut servers = HashMap::new();
    servers.insert(
        "local-workstation".to_string(),
        McpServer {
            command: Some("python3".to_string()),
            args: vec![script.to_string_lossy().to_string()],
            env: HashMap::new(),
            enabled: true,
            cwd: None,
            url: None,
            tools: HashMap::new(),
        },
    );
    McpConfig { servers }
}

pub(crate) fn load_config() -> (McpConfig, String) {
    let path = config_path();
    match std::fs::read_to_string(&path) {
        Ok(text) => match serde_json::from_str::<McpConfig>(&text) {
            Ok(config) => (config, path.display().to_string()),
            Err(error) => (
                default_config(),
                format!("default (mcp_servers.json tidak valid: {error})"),
            ),
        },
        Err(_) => {
            let config = default_config();
            if let Ok(text) = serde_json::to_string_pretty(&config) {
                let _ = std::fs::write(&path, text);
            }
            (config, format!("default (dibuat di {})", path.display()))
        }
    }
}

fn initialize_request() -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": { "name": CLIENT_NAME, "version": CLIENT_VERSION }
        }
    })
}

fn initialized_notification() -> Value {
    json!({ "jsonrpc": "2.0", "method": "notifications/initialized" })
}

fn kill_group(pid: Option<u32>) {
    let Some(pid) = pid else {
        return;
    };
    unsafe {
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
}

/// Mengirim beberapa pesan sekaligus dan menunggu balasan dengan `wanted_id`.
/// Server dijalankan per permintaan; setelah balasan diterima, proses dimatikan.
async fn exchange(
    server: &McpServer,
    requests: &[Value],
    wanted_id: u64,
    timeout: Duration,
) -> Result<Value, String> {
    let Some(program) = server.command.as_deref().filter(|value| !value.trim().is_empty()) else {
        if server.url.is_some() {
            return Err("transport HTTP/SSE belum didukung; gunakan 'command' (stdio)".to_string());
        }
        return Err("server MCP tidak punya 'command'".to_string());
    };

    let mut command = Command::new(program);
    command
        .args(&server.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .process_group(0)
        .kill_on_drop(true);
    if let Some(cwd) = server.cwd.as_deref().filter(|value| !value.is_empty()) {
        command.current_dir(cwd);
    }
    for (key, value) in &server.env {
        command.env(key, value);
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("gagal menjalankan {program}: {error}"))?;
    let pid = child.id();
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| "stdin server MCP tidak tersedia".to_string())?;
    let payload = requests
        .iter()
        .map(|request| request.to_string())
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";
    stdin
        .write_all(payload.as_bytes())
        .await
        .map_err(|error| format!("gagal menulis ke server MCP: {error}"))?;
    let _ = stdin.flush().await;
    // `stdin` sengaja tetap hidup sampai balasan diterima: banyak server keluar
    // saat stdin ditutup, sebelum sempat menjawab.

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "stdout server MCP tidak tersedia".to_string())?;
    let mut lines = BufReader::new(stdout).lines();
    let deadline = tokio::time::Instant::now() + timeout;

    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            kill_group(pid);
            let _ = child.kill().await;
            return Err(format!(
                "server MCP melewati batas waktu {} detik",
                timeout.as_secs()
            ));
        }
        match tokio::time::timeout(remaining, lines.next_line()).await {
            Ok(Ok(Some(line))) => {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let Ok(value) = serde_json::from_str::<Value>(trimmed) else {
                    continue;
                };
                if value.get("id").and_then(Value::as_u64) != Some(wanted_id) {
                    // Notifikasi atau permintaan lain: bukan balasan yang dicari.
                    continue;
                }
                if let Some(error) = value.get("error") {
                    let message = error
                        .get("message")
                        .and_then(Value::as_str)
                        .unwrap_or("kesalahan JSON-RPC");
                    return Err(format!("MCP: {message}"));
                }
                return Ok(value.get("result").cloned().unwrap_or(Value::Null));
            }
            Ok(Ok(None)) => {
                return Err("server MCP menutup stdout sebelum menjawab".to_string());
            }
            Ok(Err(error)) => return Err(format!("gagal membaca stdout MCP: {error}")),
            Err(_) => {
                kill_group(pid);
                let _ = child.kill().await;
                return Err(format!(
                    "server MCP melewati batas waktu {} detik",
                    timeout.as_secs()
                ));
            }
        }
    }
}

fn parse_tools(server_key: &str, result: &Value) -> Vec<McpTool> {
    result
        .get("tools")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let name = item.get("name").and_then(Value::as_str)?.to_string();
                    Some(McpTool {
                        server: server_key.to_string(),
                        name,
                        description: item
                            .get("description")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        input_schema: item
                            .get("inputSchema")
                            .cloned()
                            .unwrap_or_else(|| json!({ "type": "object", "properties": {} })),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

async fn list_server_tools(server_key: &str, server: &McpServer) -> Result<Vec<McpTool>, String> {
    let result = exchange(
        server,
        &[
            initialize_request(),
            initialized_notification(),
            json!({ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {} }),
        ],
        2,
        Duration::from_secs(20),
    )
    .await?;
    Ok(parse_tools(server_key, &result))
}

static CATALOG: OnceLock<tokio::sync::Mutex<Option<(Instant, Vec<McpTool>, Vec<String>)>>> =
    OnceLock::new();

fn catalog_cache() -> &'static tokio::sync::Mutex<Option<(Instant, Vec<McpTool>, Vec<String>)>> {
    CATALOG.get_or_init(|| tokio::sync::Mutex::new(None))
}

/// Katalog tool dari semua server yang aktif. Hasil di-cache 5 menit agar prompt
/// tidak memicu proses MCP di setiap run.
pub(crate) async fn catalog(force: bool) -> (Vec<McpTool>, Vec<String>) {
    let cache = catalog_cache();
    let mut guard = cache.lock().await;
    if !force {
        if let Some((at, tools, errors)) = guard.as_ref() {
            if at.elapsed() < CATALOG_TTL {
                return (tools.clone(), errors.clone());
            }
        }
    }

    let (config, _) = load_config();
    let mut tools = Vec::new();
    let mut errors = Vec::new();
    for (key, server) in &config.servers {
        if !server.enabled {
            continue;
        }
        match list_server_tools(key, server).await {
            Ok(mut items) => tools.append(&mut items),
            Err(error) => errors.push(format!("{key}: {error}")),
        }
    }
    tools.sort_by(|a, b| (&a.server, &a.name).cmp(&(&b.server, &b.name)));
    *guard = Some((Instant::now(), tools.clone(), errors.clone()));
    (tools, errors)
}

pub(crate) async fn refresh() -> (Vec<McpTool>, Vec<String>) {
    catalog(true).await
}

pub(crate) async fn call_tool(
    server_key: &str,
    tool: &str,
    arguments: &Value,
) -> Result<ToolResult, String> {
    let (config, _) = load_config();
    let Some(server) = config.servers.get(server_key) else {
        return Err(format!("server MCP \"{server_key}\" tidak dikonfigurasi"));
    };
    if !server.enabled {
        return Err(format!("server MCP \"{server_key}\" dimatikan"));
    }
    if !arguments.is_object() {
        return Err("arguments harus berupa objek JSON".to_string());
    }

    let result = exchange(
        server,
        &[
            initialize_request(),
            initialized_notification(),
            json!({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": { "name": tool, "arguments": arguments }
            }),
        ],
        2,
        Duration::from_secs(90),
    )
    .await?;

    let is_error = result
        .get("isError")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut text = String::new();
    if let Some(items) = result.get("content").and_then(Value::as_array) {
        for item in items {
            if let Some(value) = item.get("text").and_then(Value::as_str) {
                if !text.is_empty() {
                    text.push('\n');
                }
                text.push_str(value);
            }
        }
    }
    if text.is_empty() {
        if let Some(structured) = result.get("structuredContent") {
            text = structured.to_string();
        }
    }
    if text.is_empty() {
        text = result.to_string();
    }
    if text.chars().count() > MAX_TOOL_OUTPUT {
        text = text.chars().take(MAX_TOOL_OUTPUT).collect();
    }
    Ok(ToolResult { text, is_error })
}

/// Membaca blok `{"mcp_call": {...}}` dari keluaran model, termasuk bila model
/// menambahkan prosa atau pagar kode di sekitarnya.
pub(crate) fn parse_tool_call(text: &str) -> Option<ToolCall> {
    let marker = text.find("\"mcp_call\"")?;
    let start = text[..marker].rfind('{')?;
    let mut depth = 0i32;
    let mut in_string = false;
    let mut escaped = false;
    for (offset, ch) in text[start..].char_indices() {
        if in_string {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            '"' => in_string = true,
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    let slice = &text[start..start + offset + 1];
                    let value: Value = serde_json::from_str(slice).ok()?;
                    let call = value.get("mcp_call")?;
                    let server = call.get("server").and_then(Value::as_str)?.trim();
                    let tool = call.get("tool").and_then(Value::as_str)?.trim();
                    if server.is_empty() || tool.is_empty() {
                        return None;
                    }
                    let arguments = call
                        .get("arguments")
                        .cloned()
                        .filter(Value::is_object)
                        .unwrap_or_else(|| json!({}));
                    return Some(ToolCall {
                        server: server.to_string(),
                        tool: tool.to_string(),
                        arguments,
                    });
                }
            }
            _ => {}
        }
    }
    None
}

fn permission_map(value: &Value) -> HashMap<String, String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let tool = item.get("tool").and_then(Value::as_str)?;
                    let decision = item.get("decision").and_then(Value::as_str)?;
                    Some((tool.to_string(), decision.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn effective_permission(
    config: &McpConfig,
    permissions: &HashMap<String, String>,
    server: &str,
    tool: &str,
) -> String {
    for key in [
        format!("mcp:{server}:{tool}"),
        format!("mcp:{server}"),
        "mcp".to_string(),
    ] {
        if let Some(decision) = permissions.get(&key) {
            return decision.clone();
        }
    }
    config
        .servers
        .get(server)
        .and_then(|entry| entry.tools.get(tool).cloned())
        .unwrap_or_else(|| "ask".to_string())
}

pub(crate) async fn permission_for(
    store: &Store,
    waker_id: &str,
    server: &str,
    tool: &str,
) -> String {
    let permissions = permission_map(&store.list_permissions(waker_id).await);
    let (config, _) = load_config();
    effective_permission(&config, &permissions, server, tool)
}

fn arg_summary(schema: &Value) -> String {
    let Some(properties) = schema.get("properties").and_then(Value::as_object) else {
        return "tanpa argumen".to_string();
    };
    if properties.is_empty() {
        return "tanpa argumen".to_string();
    }
    let required: Vec<&str> = schema
        .get("required")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    let mut parts: Vec<String> = properties
        .keys()
        .map(|key| {
            if required.contains(&key.as_str()) {
                format!("{key}*")
            } else {
                key.clone()
            }
        })
        .collect();
    parts.sort();
    format!("arg: {}", parts.join(", "))
}

fn truncate_chars(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let mut out: String = text.chars().take(max).collect();
    out.push('…');
    out
}

/// Indeks tool untuk prompt. Hanya nama, deskripsi singkat, dan nama argumen —
/// skema penuh tidak dikirim sampai tool benar-benar dipanggil.
pub(crate) async fn prompt_index(store: &Store, waker_id: &str) -> String {
    let permissions = permission_map(&store.list_permissions(waker_id).await);
    // Tanpa satu pun izin MCP, jangan tampilkan tool dan jangan menjalankan
    // server MCP hanya untuk membangun indeks.
    if !permissions
        .keys()
        .any(|key| key == "mcp" || key.starts_with("mcp:"))
    {
        return String::new();
    }
    let (tools, _) = catalog(false).await;
    if tools.is_empty() {
        return String::new();
    }
    let (config, _) = load_config();
    let limit = std::env::var("STATION_MCP_INDEX_LIMIT")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(12)
        .clamp(1, 40);

    let mut lines = Vec::new();
    for tool in &tools {
        let decision = effective_permission(&config, &permissions, &tool.server, &tool.name);
        if decision == "deny" {
            continue;
        }
        let note = if decision == "ask" { " [butuh izin]" } else { "" };
        lines.push(format!(
            "- {}.{}: {}{} ({})",
            tool.server,
            tool.name,
            truncate_chars(tool.description.trim(), 160),
            note,
            arg_summary(&tool.input_schema)
        ));
        if lines.len() >= limit {
            break;
        }
    }
    if lines.is_empty() {
        return String::new();
    }
    format!(
        "Tool MCP yang tersedia (pakai hanya bila benar-benar perlu):\n{}\n\n\
         Untuk memanggil tool, balas HANYA dengan satu blok JSON:\n\
         {{\"mcp_call\":{{\"server\":\"<server>\",\"tool\":\"<tool>\",\"arguments\":{{}}}}}}\n\
         Setelah hasil tool diberikan, lanjutkan jawaban akhir tanpa memanggil tool yang sama lagi.",
        lines.join("\n")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_call_without_surrounding_prose() {
        let call = parse_tool_call(
            r#"{"mcp_call":{"server":"local-workstation","tool":"get_hardware_telemetry","arguments":{}}}"#,
        )
        .expect("tool call");
        assert_eq!(call.server, "local-workstation");
        assert_eq!(call.tool, "get_hardware_telemetry");
    }

    #[test]
    fn parses_call_inside_fenced_block() {
        let text = "Saya perlu data.\n```json\n{\"mcp_call\":{\"server\":\"s\",\"tool\":\"t\",\"arguments\":{\"x\":1}}}\n```";
        let call = parse_tool_call(text).expect("tool call");
        assert_eq!(call.arguments["x"], 1);
    }

    #[test]
    fn rejects_plain_text() {
        assert!(parse_tool_call("tidak ada panggilan").is_none());
    }

    #[test]
    fn permission_precedence() {
        let config = McpConfig {
            servers: HashMap::new(),
        };
        let mut permissions = HashMap::new();
        permissions.insert("mcp".to_string(), "allow".to_string());
        permissions.insert("mcp:s".to_string(), "ask".to_string());
        permissions.insert("mcp:s:t".to_string(), "deny".to_string());
        assert_eq!(effective_permission(&config, &permissions, "s", "t"), "deny");
        assert_eq!(effective_permission(&config, &permissions, "s", "x"), "ask");
        assert_eq!(effective_permission(&config, &permissions, "o", "x"), "allow");
        assert_eq!(
            effective_permission(&config, &HashMap::new(), "o", "x"),
            "ask"
        );
    }
}
