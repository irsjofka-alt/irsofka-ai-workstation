//! HTTP P3.3: potret kuota per engine dan saran routing.
//!
//! Angkanya datang dari meter `engine_quota`; tidak ada probe yang dijalankan di
//! sini dan tidak ada nilai cadangan yang dikarang. Jendela yang belum dilaporkan
//! tampil sebagai `unsupported` beserta alasannya.

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::json;

use crate::api::AppState;

#[derive(Deserialize)]
pub(crate) struct RoleQuery {
    #[serde(default)]
    pub(crate) role: Option<String>,
}

pub(crate) async fn engines(State(state): State<AppState>) -> Response {
    match crate::quota::snapshot(&state.store).await {
        Ok(engines) => {
            let count = engines.len();
            Json(json!({ "engines": engines, "count": count })).into_response()
        }
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}

pub(crate) async fn suggest(
    State(state): State<AppState>,
    Query(query): Query<RoleQuery>,
) -> Response {
    match crate::quota::suggest(&state.store, query.role.as_deref()).await {
        Ok(result) => Json(result).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": error })),
        )
            .into_response(),
    }
}
