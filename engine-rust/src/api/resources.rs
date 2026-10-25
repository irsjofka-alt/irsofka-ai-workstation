//! HTTP untuk status sumber daya GPU dan keputusan guard model lokal.

use axum::Json;
use serde_json::Value;

pub(crate) async fn get() -> Json<Value> {
    Json(crate::resources::report().await)
}
