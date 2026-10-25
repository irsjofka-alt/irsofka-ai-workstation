//! HTTP untuk runtime MCP: daftar server/tool, segarkan katalog, dan panggil
//! tool secara langsung (uji dari console).

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;

pub(crate) async fn servers() -> Json<Value> {
    let (config, source) = crate::mcp::load_config();
    let (tools, errors) = crate::mcp::catalog(false).await;
    let mut items: Vec<Value> = config
        .servers
        .iter()
        .map(|(key, server)| {
            let count = tools.iter().filter(|tool| &tool.server == key).count();
            json!({
                "key": key,
                "command": server.command,
                "enabled": server.enabled,
                "transport": if server.command.is_some() { "stdio" } else if server.url.is_some() { "http (belum didukung)" } else { "tidak diketahui" },
                "tool_count": count,
                "tools": server.tools,
            })
        })
        .collect();
    items.sort_by(|a, b| a["key"].as_str().cmp(&b["key"].as_str()));
    Json(json!({
        "servers": items,
        "errors": errors,
        "source": source,
    }))
}

#[derive(Deserialize)]
pub(crate) struct ToolQuery {
    #[serde(default)]
    pub(crate) refresh: Option<String>,
}

pub(crate) async fn tools(Query(query): Query<ToolQuery>) -> Json<Value> {
    let force = query
        .refresh
        .as_deref()
        .map(|value| matches!(value, "1" | "true" | "yes"))
        .unwrap_or(false);
    let (tools, errors) = if force {
        crate::mcp::refresh().await
    } else {
        crate::mcp::catalog(false).await
    };
    Json(json!({
        "tools": tools,
        "count": tools.len(),
        "errors": errors,
    }))
}

pub(crate) async fn refresh() -> Json<Value> {
    let (tools, errors) = crate::mcp::refresh().await;
    Json(json!({
        "tools": tools,
        "count": tools.len(),
        "errors": errors,
    }))
}

#[derive(Deserialize)]
pub(crate) struct CallBody {
    pub(crate) server: String,
    pub(crate) tool: String,
    #[serde(default)]
    pub(crate) arguments: Value,
}

pub(crate) async fn call(
    State(_state): State<AppState>,
    Json(body): Json<CallBody>,
) -> Response {
    let server = body.server.trim();
    let tool = body.tool.trim();
    if server.is_empty() || tool.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "server dan tool wajib diisi" })),
        )
            .into_response();
    }
    let arguments = if body.arguments.is_null() {
        json!({})
    } else {
        body.arguments
    };
    match crate::mcp::call_tool(server, tool, &arguments).await {
        Ok(result) => Json(json!({
            "server": server,
            "tool": tool,
            "text": result.text,
            "is_error": result.is_error,
        }))
        .into_response(),
        Err(error) => (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}
