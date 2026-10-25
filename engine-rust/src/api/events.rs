//! Event bus + SSE untuk console.
//!
//! Ini bukan polling chat: daemon menerbitkan event saat run benar-benar
//! menulis sesuatu. Console berlangganan satu koneksi dan menerima `run_event`,
//! `run_state`, serta sinyal perubahan state lain. Dengan begitu angka context
//! dan langkah worker bergerak saat kejadiannya, bukan saat halaman dimuat ulang.
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use futures_core::Stream;
use serde_json::Value;
use std::convert::Infallible;
use std::pin::Pin;
use std::sync::OnceLock;
use std::task::{Context, Poll};
use tokio::sync::{broadcast, mpsc};

static BUS: OnceLock<broadcast::Sender<Value>> = OnceLock::new();

pub(crate) fn bus() -> &'static broadcast::Sender<Value> {
    BUS.get_or_init(|| {
        let (tx, _rx) = broadcast::channel(2048);
        tx
    })
}

/// Terbitkan satu objek JSON. Tidak ada yang berlangganan bukan error.
pub(crate) fn publish(value: Value) {
    let _ = bus().send(value);
}

struct BusStream {
    rx: mpsc::Receiver<Value>,
}

impl Stream for BusStream {
    type Item = Result<Event, Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.as_mut().get_mut();
        match this.rx.poll_recv(cx) {
            Poll::Ready(Some(value)) => {
                let data = serde_json::to_string(&value).unwrap_or_else(|_| "{}".to_string());
                Poll::Ready(Some(Ok(Event::default().data(data))))
            }
            Poll::Ready(None) => Poll::Ready(None),
            Poll::Pending => Poll::Pending,
        }
    }
}

/// GET /api/stream — satu koneksi SSE untuk seluruh console.
pub(crate) async fn stream() -> Response {
    let (tx, rx) = mpsc::channel(256);
    let mut bus_rx = bus().subscribe();
    tokio::spawn(async move {
        loop {
            match bus_rx.recv().await {
                Ok(value) => {
                    if tx.send(value).await.is_err() {
                        break;
                    }
                }
                Err(broadcast::error::RecvError::Lagged(skipped)) => {
                    let value = serde_json::json!({
                        "type": "resync",
                        "skipped": skipped,
                    });
                    if tx.send(value).await.is_err() {
                        break;
                    }
                }
                Err(broadcast::error::RecvError::Closed) => break,
            }
        }
    });
    Sse::new(BusStream { rx })
        .keep_alive(KeepAlive::default())
        .into_response()
}
