//! Katalog model dari QoderWake, diteruskan apa adanya ke console.
//!
//! Endpoint ini tidak menyentuh `config/engines.json`: daftar model datang dari
//! `qoderwake models list --json`, lengkap dengan context window dan thinking level
//! per model. Bila QoderWake tidak bisa dihubungi, yang dikembalikan adalah galat
//! yang bisa dibaca manusia — bukan daftar kosong tanpa alasan.

use axum::extract::Query;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Deserialize)]
pub(crate) struct CatalogQuery {
    #[serde(default)]
    pub(crate) refresh: Option<String>,
}

pub(crate) async fn get_catalog(Query(query): Query<CatalogQuery>) -> Json<Value> {
    let force = matches!(query.refresh.as_deref(), Some("1") | Some("true"));
    match crate::qoder::catalog(force).await {
        Ok(value) => Json(value),
        Err(error) => Json(json!({
            "source": "qoderwake",
            "models": [],
            "count": 0,
            "error": error,
        })),
    }
}
