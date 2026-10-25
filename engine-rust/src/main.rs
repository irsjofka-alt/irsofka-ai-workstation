//! Irsofka AI Workstation daemon — one process, both the native window and the API.
//!
//! Two launch modes share one state: `--headless` / `--daemon` / `--server` serves
//! only http://127.0.0.1:8999; otherwise tao + wry open `src/gui.html` and talk to
//! the same router. Terminal panes are tmux sessions on socket `-L irsofka`, hosted
//! by `irsofka-tabs.service` outside this process's cgroup, so restarting the daemon
//! never kills a running CLI.
//!
//! What is left here is deliberately small: startup, the window, and the router table.
//! Everything else has a home — `paths` (where things live), `profile` (how a tab spawns
//! its CLI), `terminal` (who owns the pane), `spool` (how events reach SQL), `probe` and
//! `engineinfo` (what the CLIs say about themselves), `save_state` (PostgreSQL/SQLite
//! counts), and `api/` (one file per HTTP domain). The full map is generated into
//! `brain/memory/projects/ARCHITECTURE.md` by `bin/arch_map.sh`.
use axum::{
    http::HeaderValue,
    routing::{get, patch, post}, Router,
};

mod adapters;
mod api;
mod approvals;
mod automations;
mod compress;
mod context;
mod cron;
mod embeddings;
mod engineinfo;
mod eval;
mod groups;
mod history;
mod knowledge;
mod mcp;
mod paths;
mod probe;
mod profile;
mod qoder;
mod quota;
mod resources;
mod runs;
mod procinfo;
mod save_state;
mod spool;
mod store;
mod terminal;
mod workflows;
use api::{daemon, desktop, static_files};
use api::AppState;
use profile::{default_profiles, ensure_config, TabProfile};
use terminal::create_session;
use paths::{bind_host, station_port};
use std::{
    collections::HashMap,
    sync::{
        atomic::AtomicU64,
        Arc, Mutex,
    },
    time::Duration,
};
use tower_http::cors::{AllowOrigin, CorsLayer};

pub(crate) const TABS: [&str; 3] = ["qoder", "antigravity", "shell"];

/// Penghitung permintaan muat-ulang UI; dibaca WebView lewat polling /api/stats.
pub(crate) static UI_RELOAD_TICK: AtomicU64 = AtomicU64::new(0);

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let is_headless = args
        .iter()
        .any(|a| a == "--headless" || a == "--daemon" || a == "--server");

    let profiles = ensure_config();

    if is_headless {
        println!("🦀 Irsofka AI Workstation — headless daemon");
        let rt = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("Failed to initialize tokio runtime");
        rt.block_on(run_server(profiles));
    } else {
        println!("🦀 Irsofka AI Workstation — native desktop GUI");
        let port = station_port();
        let server_up = std::net::TcpStream::connect(format!("127.0.0.1:{}", port)).is_ok();
        if !server_up {
            println!("🚀 Engine tidak aktif di port {}, start embedded...", port);
            std::thread::spawn(move || {
                let rt = tokio::runtime::Builder::new_multi_thread()
                    .enable_all()
                    .build()
                    .expect("Failed to initialize embedded tokio runtime");
                rt.block_on(run_server(profiles));
            });
            for _ in 0..50 {
                if std::net::TcpStream::connect(format!("127.0.0.1:{}", port)).is_ok() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        } else {
            println!("⚡ Terhubung ke daemon aktif di 127.0.0.1:{}", port);
        }
        launch_native_window();
    }
}

fn launch_native_window() {
    use tao::{
        event::{Event, WindowEvent},
        event_loop::{ControlFlow, EventLoop},
        platform::unix::WindowExtUnix,
        window::WindowBuilder,
    };
    use wry::{WebViewBuilder, WebViewBuilderExtUnix};

    let port = station_port();
    println!("🖼️ Membuka jendela native (WebKitGTK + Tao)...");
    let event_loop = EventLoop::new();
    // Ukuran mengikuti monitor: 1440x900 logical lebih besar dari layar ber-skala 2x,
    // sehingga bagian bawah jendela (composer) terpotong. Ambil 92% area monitor.
    let monitor = event_loop.primary_monitor();
    let (width, height) = monitor
        .as_ref()
        .map(|handle| {
            let size = handle.size();
            let scale = handle.scale_factor();
            (
                (size.width as f64 / scale * 0.92).clamp(900.0, 1600.0),
                (size.height as f64 / scale * 0.86).clamp(620.0, 920.0),
            )
        })
        .unwrap_or((1200.0, 760.0));
    println!("🖼️ Ukuran jendela: {width:.0}x{height:.0} logical");
    let window = WindowBuilder::new()
        .with_title("Irsofka AI Workstation - Native Studio")
        .with_inner_size(tao::dpi::LogicalSize::new(width, height))
        .with_min_inner_size(tao::dpi::LogicalSize::new(900.0, 600.0))
        .build(&event_loop)
        .expect("Failed to create native desktop window");

    let vbox = window.default_vbox().expect("Failed to get window default vbox");
    let _webview = WebViewBuilder::new()
        .with_url(format!(
            "http://127.0.0.1:{}{}",
            port,
            std::env::var("STATION_START_PATH").unwrap_or_default()
        ))
        .with_devtools(true)
        .build_gtk(vbox)
        .expect("Failed to initialize WebKitGTK webview inside native window");

    println!("✨ Irsofka AI Workstation Native Rust Window is Running!");

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                println!("🛑 Jendela ditutup.");
                *control_flow = ControlFlow::Exit;
            }
            _ => (),
        }
    });
}

/// Gerbang token untuk akses non-lokal (LAN).
///
/// Bila `STATION_ACCESS_TOKEN` kosong, perilaku lama tidak berubah. Bila diisi,
/// semua rute mensyaratkan token lewat header `x-station-token`, query `?token=`,
/// atau cookie yang dipasang setelah query pertama. Cookie membuat console dan
/// fetch same-origin tetap bekerja tanpa menempelkan token di setiap URL.
async fn token_gate(
    axum::extract::ConnectInfo(peer): axum::extract::ConnectInfo<std::net::SocketAddr>,
    request: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    use axum::http::{header, StatusCode};
    use axum::response::IntoResponse;

    // Loopback = jendela/console lokal; tidak perlu login.
    if peer.ip().is_loopback() {
        return next.run(request).await;
    }
    // Halaman login selalu terbuka; tanpa mode auth, semuanya lewat.
    if request.uri().path() == "/login" {
        return next.run(request).await;
    }
    if paths::auth_mode() == paths::AuthMode::Sudo {
        if session_valid(request.headers()) {
            return next.run(request).await;
        }
        if request.uri().path().starts_with("/api/") {
            return (
                StatusCode::UNAUTHORIZED,
                axum::Json(serde_json::json!({ "error": "login diperlukan" })),
            )
                .into_response();
        }
        return login_page(None);
    }

    let Some(token) = paths::access_token() else {
        return next.run(request).await;
    };

    let header_ok = request
        .headers()
        .get("x-station-token")
        .and_then(|value| value.to_str().ok())
        == Some(token.as_str());
    let query_ok = request
        .uri()
        .query()
        .map(|query| query.split('&').any(|pair| pair == format!("token={token}")))
        .unwrap_or(false);
    let cookie_ok = request
        .headers()
        .get(header::COOKIE)
        .and_then(|value| value.to_str().ok())
        .map(|cookies| {
            cookies
                .split(';')
                .any(|part| part.trim() == format!("station_token={token}"))
        })
        .unwrap_or(false);

    if !(header_ok || query_ok || cookie_ok) {
        return (
            StatusCode::UNAUTHORIZED,
            axum::response::Html(
                "<h1>Akses butuh token</h1><p>Buka alamat dengan <code>?token=…</code> sekali; cookie akan dipasang.</p>",
            ),
        )
            .into_response();
    }

    let mut response = next.run(request).await;
    if query_ok || header_ok {
        let cookie = format!(
            "station_token={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
        );
        if let Ok(value) = cookie.parse() {
            response.headers_mut().append(header::SET_COOKIE, value);
        }
    }
    response
}

static SESSIONS: std::sync::OnceLock<Mutex<HashMap<String, std::time::Instant>>> =
    std::sync::OnceLock::new();

fn sessions() -> &'static Mutex<HashMap<String, std::time::Instant>> {
    SESSIONS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn new_session_id() -> String {
    use std::io::Read;
    let mut buffer = [0u8; 32];
    if let Ok(mut file) = std::fs::File::open("/dev/urandom") {
        let _ = file.read_exact(&mut buffer);
    }
    buffer.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn session_id(headers: &axum::http::HeaderMap) -> Option<String> {
    let cookies = headers.get(axum::http::header::COOKIE)?.to_str().ok()?;
    cookies.split(';').find_map(|part| {
        part.trim()
            .strip_prefix("station_session=")
            .map(str::to_string)
    })
}

fn session_valid(headers: &axum::http::HeaderMap) -> bool {
    let Some(id) = session_id(headers) else {
        return false;
    };
    let mut map = match sessions().lock() {
        Ok(map) => map,
        Err(_) => return false,
    };
    match map.get(&id) {
        Some(created) if created.elapsed().as_secs() < 43_200 => true,
        Some(_) => {
            map.remove(&id);
            false
        }
        None => false,
    }
}

fn login_page(error: Option<&str>) -> axum::response::Response {
    use axum::response::IntoResponse;
    let message = error
        .map(|text| format!("<p class=\"err\">{text}</p>"))
        .unwrap_or_default();
    axum::response::Html(format!(
        r#"<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Station · Masuk</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0b0e;color:#e9ecf2;font:14px system-ui,sans-serif}}
.card{{width:min(360px,92vw);padding:24px;border:1px solid #232936;border-radius:16px;background:#14161b}}
h1{{margin:0 0 6px;font-size:18px}} p{{color:#8b93a5;font-size:12.5px;margin:0 0 16px}}
input{{width:100%;padding:11px 12px;border:1px solid #232936;border-radius:10px;background:#0e1014;color:#e9ecf2;outline:none;box-sizing:border-box}}
button{{margin-top:12px;width:100%;padding:11px;border:0;border-radius:10px;background:#7c5cff;color:#fff;font-weight:600;cursor:pointer}}
.err{{color:#ff5c7a;font-size:12px;margin-top:10px}}
</style></head><body><form class="card" method="post" action="/login">
<h1>AI Station</h1><p>Masuk dengan password akun mesin ini.</p>
<input type="password" name="password" autofocus autocomplete="current-password" placeholder="Password sudo">
<button type="submit">Masuk</button>{message}
</form></body></html>"#
    ))
    .into_response()
}

async fn verify_sudo(password: &str) -> bool {
    use std::process::Stdio;
    use tokio::io::AsyncWriteExt;

    let mut child = match tokio::process::Command::new("sudo")
        .args(["-S", "-k", "-v"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return false,
    };
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(format!("{password}\n").as_bytes()).await;
    }
    let ok = child.wait().await.map(|status| status.success()).unwrap_or(false);
    // Bersihkan timestamp sudo agar verifikasi tidak meninggalkan sesi sudo aktif.
    let _ = tokio::process::Command::new("sudo")
        .arg("-k")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await;
    ok
}

#[derive(serde::Deserialize)]
struct LoginForm {
    password: String,
}

async fn login_submit(axum::Form(form): axum::Form<LoginForm>) -> axum::response::Response {
    use axum::http::header;
    use axum::response::IntoResponse;

    if !verify_sudo(&form.password).await {
        tokio::time::sleep(Duration::from_millis(600)).await;
        return login_page(Some("Password salah."));
    }
    let id = new_session_id();
    if let Ok(mut map) = sessions().lock() {
        map.insert(id.clone(), std::time::Instant::now());
    }
    let cookie = format!(
        "station_session={id}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"
    );
    let mut response = axum::response::Redirect::to("/").into_response();
    if let Ok(value) = cookie.parse() {
        response.headers_mut().append(header::SET_COOKIE, value);
    }
    response
}

async fn run_server(profiles: HashMap<String, TabProfile>) {
    println!("🦀 Inisialisasi PTY sessions & Axum API...");

    // Console tidak butuh tiga pane terminal yang menunggu selamanya. Dengan
    // STATION_SKIP_TABS=1 daemon tidak melahirkan qoder/agy/shell sama sekali,
    // sehingga tidak ada agen standby — dan tidak ada CLI yang membuka browser
    // untuk minta login saat start.
    let skip_tabs = std::env::var("STATION_SKIP_TABS")
        .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
        .unwrap_or(false);

    let mut map = HashMap::new();
    if skip_tabs {
        println!("🪟 Tab terminal dilewati (STATION_SKIP_TABS=1)");
    } else {
        let fallbacks = default_profiles();
        for tab in TABS {
            let profile = profiles
                .get(tab)
                .cloned()
                .or_else(|| fallbacks.get(tab).cloned())
                .unwrap_or_default();
            map.insert(tab.to_string(), create_session(tab, &profile));
        }
        println!(
            "🔌 Session aktif: {}",
            map.iter()
                .map(|(k, v)| format!("{}=pid{}", k, v.root_pid))
                .collect::<Vec<_>>()
                .join(" ")
        );
    }

    let store = store::Store::connect().await;
    println!("🗄️  Penyimpanan domain: {}", store.backend);

    let wakers = api::wakers::load_store(&store).await;
    let run_manager = runs::RunManager::new(store.clone());
    if let Some(recent) = store.recent_runs(50).await {
        run_manager.seed(recent).await;
    }

    let state = AppState {
        sessions: Arc::new(map),
        typed: Arc::new(Mutex::new(HashMap::new())),
        wakers,
        runs: run_manager,
        store,
    };

    // Penjadwal automation: satu detak tiap 20 detik. Ringan, dan keterlambatan
    // maksimal 20 detik masih jauh di bawah granularitas cron satu menit.
    let scheduler_state = state.clone();
    let tick_secs = std::env::var("STATION_SCHEDULER_TICK_SECS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(20)
        .max(1);
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(tick_secs)).await;
            automations::tick(&scheduler_state).await;
        }
    });

    // Katalog model dihangatkan sekali, lalu disegarkan tiap 10 menit. Model baru di
    // QoderWake muncul tanpa operator menekan tombol, dan paint pertama halaman tidak
    // perlu menunggu panggilan CLI.
    tokio::spawn(async {
        if let Err(error) = qoder::catalog(false).await {
            eprintln!("⚠ katalog model QoderWake: {error}");
        }
        loop {
            tokio::time::sleep(Duration::from_secs(600)).await;
            if let Err(error) = qoder::catalog(true).await {
                eprintln!("⚠ katalog model QoderWake: {error}");
            }
        }
    });

    let port = station_port();
    let allowed: Vec<HeaderValue> = vec![
        format!("http://127.0.0.1:{}", port),
        format!("http://localhost:{}", port),
    ]
    .into_iter()
    .filter_map(|s| s.parse().ok())
    .collect();

    let app = Router::new()
        .route(
            "/login",
            get(|| async { login_page(None) }).post(login_submit),
        )
        // `/` kini console aplikasi. UI terminal lama tetap tersedia di `/legacy`
        // selama pane tmux belum dipensiunkan.
        .route("/", get(static_files::serve_console))
        .route("/legacy", get(static_files::serve_gui))
        .route("/console", get(static_files::serve_console))
        .route("/console/*path", get(static_files::serve_console_asset))
        // Situs Playground: hasil generate tools/site/build.py. Loopback saja karena
        // router ini mengikuti bind_host() (default 127.0.0.1).
        .route("/site", get(static_files::serve_site_index))
        .route("/site/", get(static_files::serve_site_index))
        .route("/site/*path", get(static_files::serve_site_asset))
        // Tulis konten situs: satu pintu (tools/site/publish.py) untuk HTTP, MCP, dan CLI.
        .route("/api/site/posts", post(api::site::post_post))
        .route("/api/site/media", post(api::site::post_media))
        .route("/api/site/rebuild", post(api::site::post_rebuild))
        .route("/station", get(static_files::serve_station))
        .route("/api/wakers", get(api::wakers::list).post(api::wakers::create))
        .route(
            "/api/wakers/:id",
            get(api::wakers::get)
                .patch(api::wakers::patch)
                .delete(api::wakers::remove),
        )
        .route("/api/engines", get(api::wakers::engines))
        .route("/api/engines/quota", get(api::quota::engines))
        .route("/api/routing/suggest", get(api::quota::suggest))
        .route("/api/eval/tasks", get(api::eval::tasks))
        .route("/api/eval/run", post(api::eval::start))
        .route("/api/eval/runs", get(api::eval::list))
        .route("/api/eval/runs/:id", get(api::eval::get))
        .route("/api/qoder/account", get(api::qoder::account))
        .route(
            "/api/qoder/login",
            get(api::qoder::login_status).post(api::qoder::login),
        )
        .route("/api/qoder/logout", post(api::qoder::logout))
        .route(
            "/api/antigravity/account",
            get(api::antigravity::account),
        )
        .route(
            "/api/antigravity/login",
            post(api::antigravity::login),
        )
        .route("/api/models/catalog", get(api::catalog::get_catalog))
        .route("/api/models/agy", get(api::engine::get_agy_models))
        .route("/api/models/ollama", get(api::engine::get_ollama_models))
        .route(
            "/api/engines/:key/model",
            axum::routing::put(api::wakers::set_engine_model),
        )
        .route("/api/capabilities", get(api::capabilities::get_capabilities))
        .route("/api/capabilities/tree", get(api::capabilities::tree))
        .route("/api/capabilities/file", get(api::capabilities::file))
        .route("/api/context", get(api::capabilities::context))
        .route(
            "/api/providers/nvidia",
            get(api::providers::get_nvidia).put(api::providers::put_nvidia),
        )
        .route("/api/files", get(api::files::view))
        .route("/api/files/raw", get(api::files::raw))
        .route("/api/system/resources", get(api::resources::get))
        .route("/api/mcp/servers", get(api::mcp::servers))
        .route("/api/mcp/tools", get(api::mcp::tools))
        .route("/api/mcp/refresh", post(api::mcp::refresh))
        .route("/api/mcp/call", post(api::mcp::call))
        .route(
            "/api/automations",
            get(api::automations::list).post(api::automations::create),
        )
        .route(
            "/api/automations/:id",
            patch(api::automations::patch).delete(api::automations::remove),
        )
        .route("/api/automations/:id/run", post(api::automations::run_now))
        .route("/api/automations/:id/runs", get(api::automations::runs))
        .route(
            "/api/auto-pilot-mode",
            get(api::auto_pilot::get_mode).post(api::auto_pilot::post_mode),
        )
        .route("/api/approvals", get(api::approvals::list))
        .route("/api/approvals/:id/decide", post(api::approvals::decide))
        .route(
            "/api/workflows",
            get(api::workflows::list).post(api::workflows::create),
        )
        .route(
            "/api/workflows/:id",
            get(api::workflows::get)
                .patch(api::workflows::patch)
                .delete(api::workflows::remove),
        )
        .route("/api/workflows/:id/run", post(api::workflows::run))
        .route("/api/workflows/:id/runs", get(api::workflows::runs))
        .route("/api/workflows/:id/versions", get(api::workflows::versions))
        .route("/api/workflows/:id/rollback", post(api::workflows::rollback))
        .route("/api/workflow-runs/:id", get(api::workflows::get_run))
        .route(
            "/api/workflow-runs/:id/terminate",
            post(api::workflows::terminate),
        )
        .route(
            "/api/workflow-runs/:id/retry",
            post(api::workflows::retry_run),
        )
        .route(
            "/api/channels",
            get(api::channels::list).post(api::channels::create),
        )
        .route("/api/channels/readiness", get(api::channels::readiness))
        .route("/api/channels/:id", axum::routing::delete(api::channels::remove))
        .route(
            "/api/channels/:id/bindings",
            get(api::channels::bindings).post(api::channels::add_binding),
        )
        .route(
            "/api/channels/:id/bindings/:binding",
            axum::routing::delete(api::channels::remove_binding),
        )
        .route("/api/channels/:id/inbound", post(api::channels::inbound))
        .route("/api/channels/:id/test", post(api::channels::test_send))
        .route("/api/ingress/lark", post(api::channels::lark_ingress))
        .route("/api/channels/:id/messages", get(api::channels::messages))
        .route(
            "/api/groups",
            get(api::groups::list).post(api::groups::create),
        )
        .route(
            "/api/groups/:id",
            get(api::groups::get).delete(api::groups::remove),
        )
        .route(
            "/api/groups/:id/messages",
            get(api::groups::messages).post(api::groups::send),
        )
        .route(
            "/api/groups/:id/todos",
            get(api::groups::todos).post(api::groups::add_todo),
        )
        .route(
            "/api/todos/:id",
            axum::routing::patch(api::groups::patch_todo)
                .delete(api::groups::remove_todo),
        )
        .route(
            "/api/groups/:id/goal",
            get(api::groups::goal).put(api::groups::put_goal),
        )
        .route(
            "/api/knowledge",
            get(api::knowledge::list).post(api::knowledge::create),
        )
        .route(
            "/api/knowledge/search",
            post(api::knowledge::search),
        )
        .route("/api/knowledge/reindex", post(api::knowledge::reindex))
        .route(
            "/api/knowledge/:id",
            axum::routing::delete(api::knowledge::remove),
        )
        .route("/api/traces/summary", get(api::traces::summary))
        .route("/api/traces", get(api::traces::list))
        .route("/api/traces/:id", get(api::traces::get))
        .route(
            "/api/wakers/:id/permissions",
            get(api::permissions::list).put(api::permissions::set),
        )
        .route(
            "/api/wakers/:id/permissions/:tool",
            axum::routing::delete(api::permissions::remove),
        )
        .route("/api/memory/scopes", get(api::memory::scopes))
        .route("/api/memory/files", get(api::memory::files))
        .route("/api/memory/file", get(api::memory::file))
        .route("/api/wakers/:id/messages", post(api::runs::post_message))
        .route("/api/worker/context", post(api::worker_context::post_context))
        .route("/api/stream", get(api::events::stream))
        .route("/api/runs", get(api::runs::list_runs))
        .route("/api/runs/:id", get(api::runs::get_run))
        .route("/api/runs/:id/cancel", post(api::runs::cancel))
        .route("/api/station", get(api::workspace::get_station))
        .route("/api/treasury", get(api::workspace::get_treasury))
        .route("/assets/*path", get(static_files::serve_asset))
        .route("/api/stats", get(api::stats::get_stats))
        .route("/api/workspace", get(api::workspace::get_workspace))
        .route("/api/log", get(api::workspace::get_log))
        .route("/api/recovery", get(api::workspace::get_recovery))
        .route("/api/models", get(api::engine::get_models))
        .route("/api/master", get(api::master::get_master).post(api::master::post_master))
        .route("/api/autopilot", get(api::autopilot::get_state).post(api::autopilot::post_state))
        .route(
            "/api/decision-mode",
            get(api::autopilot::get_decision_mode).post(api::autopilot::post_decision_mode),
        )
        // Menjawab, bukan membaca: `/api/autopilot` tetap jalur keadaan sakelar, dan jawaban
        // manusia punya jalurnya sendiri supaya satu URL tidak punya dua arti.
        .route("/api/decide", post(api::autopilot::post_decide))
        .route("/api/byok", get(api::byok::get_state).post(api::byok::post_state))
        // Dashboard F10.10: jalur tulis untuk perintah operator, proyek, dan pedoman. Bentuk
        // masukan diperiksa di sini; keputusannya ada di tools/work_order.py, satu kali.
        .route("/api/work", get(api::work::get_work).post(api::work::post_work))
        .route("/api/usage", get(api::engine::get_usage))
        .route("/api/cli/config", get(api::engine::get_cli_config).post(api::engine::patch_cli_config))
        .route("/api/cli/restart", post(api::engine::restart_cli_tab))
        .route("/api/term/read", get(api::terminal::term_read))
        .route("/api/term/screen", get(api::terminal::term_screen))
        .route("/api/term/history", get(api::terminal::term_history))
        .route("/api/term/write", post(api::terminal::term_write))
        .route("/api/term/reset", post(api::terminal::term_reset))
        .route("/api/term/resize", post(api::terminal::term_resize))
        .route("/api/cli/run", post(api::terminal::cli_run))
        .route("/api/chat", post(api::terminal::cli_run))
        .route("/api/see", post(desktop::handle_see))
        .route("/api/action", post(desktop::handle_action))
        .route("/api/screenshot/latest", get(desktop::serve_screenshot))
        .route("/api/restart-server", post(daemon::restart_server))
        .route("/api/ui/reload", post(daemon::ui_reload))
        .layer(
            CorsLayer::new()
                .allow_origin(AllowOrigin::list(allowed))
                .allow_methods(tower_http::cors::Any)
                .allow_headers(tower_http::cors::Any),
        )
        .layer(axum::middleware::from_fn(token_gate))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(format!("{}:{}", bind_host(), port))
        .await
        .expect("Failed to bind TCP listener");

    println!("⚡ Irsofka AI Workstation berjalan di http://127.0.0.1:{}", port);
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .await
    .expect("Failed to run Axum server");
}
