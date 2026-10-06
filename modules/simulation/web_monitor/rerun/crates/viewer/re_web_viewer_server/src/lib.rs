//! Serves the web viewer wasm/html.
//!
//! ## Feature flags
#![doc = document_features::document_features!()]
//!

#![forbid(unsafe_code)]
#![warn(clippy::all, rust_2018_idioms)]

use std::borrow::Cow;
use std::fmt::Display;
use std::path::Path;
use std::str::FromStr;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

mod simulation_events;
pub use simulation_events::SimulationEvents;

pub const DEFAULT_WEB_VIEWER_SERVER_PORT: u16 = 9090;

// See `Cargo.toml` for docs about the `disable_web_viewer_server` and `trailing_web_viewer` cfgs:
#[cfg(all(not(disable_web_viewer_server), trailing_web_viewer))]
mod trailing_data;

/// Failure to host the web viewer.
#[derive(thiserror::Error, Debug)]
pub enum WebViewerServerError {
    #[error("Could not parse address: {0}")]
    AddrParseFailed(#[from] std::net::AddrParseError),

    #[error("Failed to create server: {source}: ({address})")]
    CreateServerFailed {
        source: Box<dyn std::error::Error + Send + Sync + 'static>,
        address: String,
    },

    #[error(transparent)]
    FailedToLoadData(#[from] WebViewerDataError),
}

/// Failure to load the [`WebViewerData`].
#[derive(thiserror::Error, Debug)]
pub enum WebViewerDataError {
    #[error("Failed to get current executable path: {0}")]
    CurrentExe(std::io::Error),

    #[error("Failed to open executable: {source}, path: {path}")]
    OpenFile {
        path: std::path::PathBuf,
        source: std::io::Error,
    },

    #[error("Failed to read executable metadata: {0}")]
    ExeMetadata(std::io::Error),

    #[error("Failed to open web viewer assets archive: {source}, path: {path}")]
    OpenArchive {
        path: std::path::PathBuf,
        source: std::io::Error,
    },

    #[error("Failed to read web viewer asset: {source}, path: {path}")]
    ReadAssetFile {
        path: std::path::PathBuf,
        source: std::io::Error,
    },

    #[error(
        "This build contains no built-in web viewer assets (RERUN_EXTERNAL_WEB_VIEWER=1). The assets must be loaded from a zip archive on disk, but no archive path was provided."
    )]
    NoBuiltinAssets,

    #[error("Failed to read trailer from executable: {0}")]
    ReadTrailer(std::io::Error),

    #[error(
        "Invalid magic marker in trailing data. Expected {expected:?}, got {actual:?}. This binary was built with RERUN_TRAILING_WEB_VIEWER=1 but the post-processing step (scripts/append_web_viewer.py) has not been completed."
    )]
    InvalidMagic {
        expected: &'static [u8],
        actual: Vec<u8>,
    },

    #[error("Failed to seek to zip offset {offset} in executable: {source}")]
    SeekToZip { offset: u64, source: std::io::Error },

    #[error("Failed to read {size} bytes of zip data: {source}")]
    ReadZip { size: u64, source: std::io::Error },

    #[error("Failed to parse zip archive: {0}. The data may be corrupted.")]
    ParseZip(zip::result::ZipError),

    #[error("Failed to extract file '{name}' from zip archive: {source}")]
    ExtractFile {
        name: String,
        source: zip::result::ZipError,
    },

    #[error("Failed to read file '{name}' contents: {source}")]
    ReadFileContents {
        name: String,
        source: std::io::Error,
    },
}

/// The contents of the files that make up the web viewer application.
///
/// Loaded once per [`WebViewerServer`], via `WebViewerData::load` for the built-in assets
/// (which does not exist in `disable_web_viewer_server` builds) or via [`WebViewerData::from_dir`]
/// / [`WebViewerData::from_archive`] for assets on disk.
pub struct WebViewerData {
    index_html: Cow<'static, [u8]>,
    favicon: Cow<'static, [u8]>,
    apple_touch_icon: Cow<'static, [u8]>,
    sw_js: Cow<'static, [u8]>,
    viewer_js: Cow<'static, [u8]>,
    viewer_wasm: Cow<'static, [u8]>,
    signed_in_html: Cow<'static, [u8]>,
    signed_out_html: Cow<'static, [u8]>,
}

/// Manual impl to show the size of each file instead of dumping its contents.
impl std::fmt::Debug for WebViewerData {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let Self {
            index_html,
            favicon,
            apple_touch_icon,
            sw_js,
            viewer_js,
            viewer_wasm,
            signed_in_html,
            signed_out_html,
        } = self;

        let mut f = f.debug_struct("WebViewerData");
        for (name, contents) in [
            ("index_html", index_html),
            ("favicon", favicon),
            ("apple_touch_icon", apple_touch_icon),
            ("sw_js", sw_js),
            ("viewer_js", viewer_js),
            ("viewer_wasm", viewer_wasm),
            ("signed_in_html", signed_in_html),
            ("signed_out_html", signed_out_html),
        ] {
            f.field(name, &format_args!("<{} bytes>", contents.len()));
        }
        f.finish()
    }
}

impl WebViewerData {
    /// Load the web viewer assets from a zip archive on disk.
    ///
    /// The archive must contain the files produced by the web viewer build
    /// (`pixi run rerun-build-web`) at the archive root.
    pub fn from_archive(path: &Path) -> Result<Self, WebViewerDataError> {
        let file = std::fs::File::open(path).map_err(|source| WebViewerDataError::OpenArchive {
            path: path.to_owned(),
            source,
        })?;
        let mut zip = zip::ZipArchive::new(std::io::BufReader::new(file))
            .map_err(WebViewerDataError::ParseZip)?;
        Self::from_zip(&mut zip)
    }

    /// Load the web viewer assets from a directory on disk.
    ///
    /// The directory must contain the files produced by the web viewer build
    /// (`pixi run rerun-build-web`), i.e. `<workspace>/crates/viewer/re_web_viewer_server/web_viewer`.
    pub fn from_dir(dir: &Path) -> Result<Self, WebViewerDataError> {
        fn read_file(dir: &Path, name: &str) -> Result<Cow<'static, [u8]>, WebViewerDataError> {
            let path = dir.join(name);
            std::fs::read(&path)
                .map(Cow::Owned)
                .map_err(|source| WebViewerDataError::ReadAssetFile { path, source })
        }

        Ok(Self {
            index_html: read_file(dir, "index.html")?,
            favicon: read_file(dir, "sim_scope.png")?,
            apple_touch_icon: read_file(dir, "sim_scope.png")?,
            sw_js: read_file(dir, "sw.js")?,
            viewer_js: read_file(dir, "re_viewer.js")?,
            viewer_wasm: read_file(dir, "re_viewer_bg.wasm")?,
            signed_in_html: read_file(dir, "signed-in.html")?,
            signed_out_html: read_file(dir, "signed-out.html")?,
        })
    }

    /// Extract the assets from a zip archive.
    fn from_zip<R: std::io::Read + std::io::Seek>(
        zip: &mut zip::ZipArchive<R>,
    ) -> Result<Self, WebViewerDataError> {
        fn extract_file<R: std::io::Read + std::io::Seek>(
            zip: &mut zip::ZipArchive<R>,
            name: &str,
        ) -> Result<Cow<'static, [u8]>, WebViewerDataError> {
            use std::io::Read as _;

            let mut file = zip
                .by_name(name)
                .map_err(|source| WebViewerDataError::ExtractFile {
                    name: name.to_owned(),
                    source,
                })?;

            let mut contents = Vec::with_capacity(file.size() as usize);
            file.read_to_end(&mut contents).map_err(|source| {
                WebViewerDataError::ReadFileContents {
                    name: name.to_owned(),
                    source,
                }
            })?;

            Ok(Cow::Owned(contents))
        }

        Ok(Self {
            index_html: extract_file(zip, "index.html")?,
            favicon: extract_file(zip, "sim_scope.png")?,
            apple_touch_icon: extract_file(zip, "sim_scope.png")?,
            sw_js: extract_file(zip, "sw.js")?,
            viewer_js: extract_file(zip, "re_viewer.js")?,
            viewer_wasm: extract_file(zip, "re_viewer_bg.wasm")?,
            signed_in_html: extract_file(zip, "signed-in.html")?,
            signed_out_html: extract_file(zip, "signed-out.html")?,
        })
    }

    /// No assets at all.
    ///
    /// `disable_web_viewer_server` builds serve nothing, but still accept a [`WebViewerData`]
    /// so the public API has the same shape in every build.
    #[cfg(disable_web_viewer_server)]
    fn empty() -> Self {
        Self {
            index_html: Cow::Borrowed(b""),
            favicon: Cow::Borrowed(b""),
            apple_touch_icon: Cow::Borrowed(b""),
            sw_js: Cow::Borrowed(b""),
            viewer_js: Cow::Borrowed(b""),
            viewer_wasm: Cow::Borrowed(b""),
            signed_in_html: Cow::Borrowed(b""),
            signed_out_html: Cow::Borrowed(b""),
        }
    }
}

#[cfg(not(disable_web_viewer_server))]
impl WebViewerData {
    /// Load the web viewer assets.
    ///
    /// If `assets_archive_path` is set, the assets are read from the given zip archive.
    /// Otherwise, the built-in assets are used:
    /// by default these are embedded into the binary at compile time,
    /// while `trailing_web_viewer` builds read them from a zip archive
    /// appended to the executable by `scripts/append_web_viewer.py`.
    /// `external_web_viewer` builds have no built-in assets at all,
    /// and fail if no archive path is given.
    pub fn load(assets_archive_path: Option<&Path>) -> Result<Self, WebViewerDataError> {
        match assets_archive_path {
            Some(path) => Self::from_archive(path),
            None => Self::builtin(),
        }
    }

    /// The assets embedded into the binary at compile time.
    #[cfg(all(not(trailing_web_viewer), not(external_web_viewer)))]
    #[expect(clippy::large_include_file)]
    #[expect(clippy::unnecessary_wraps)] // Signature must match the `trailing_web_viewer` version.
    fn builtin() -> Result<Self, WebViewerDataError> {
        // If you add/remove/change the paths here, also update the include-list in `Cargo.toml`!
        Ok(Self {
            index_html: Cow::Borrowed(include_bytes!("../web_viewer/index.html")),
            favicon: Cow::Borrowed(include_bytes!("../web_viewer/sim_scope.png")),
            apple_touch_icon: Cow::Borrowed(include_bytes!("../web_viewer/sim_scope.png")),
            sw_js: Cow::Borrowed(include_bytes!("../web_viewer/sw.js")),
            viewer_js: Cow::Borrowed(include_bytes!("../web_viewer/re_viewer.js")),
            viewer_wasm: Cow::Borrowed(include_bytes!("../web_viewer/re_viewer_bg.wasm")),
            signed_in_html: Cow::Borrowed(include_bytes!("../web_viewer/signed-in.html")),
            signed_out_html: Cow::Borrowed(include_bytes!("../web_viewer/signed-out.html")),
        })
    }

    /// The assets from the zip archive appended to the executable
    /// by `scripts/append_web_viewer.py`.
    #[cfg(trailing_web_viewer)]
    fn builtin() -> Result<Self, WebViewerDataError> {
        let zip_bytes = trailing_data::read_zip_from_exe()?;
        let mut zip = zip::ZipArchive::new(std::io::Cursor::new(zip_bytes))
            .map_err(WebViewerDataError::ParseZip)?;
        Self::from_zip(&mut zip)
    }

    /// `external_web_viewer` builds have no built-in assets:
    /// they must be loaded from an archive on disk instead.
    #[cfg(external_web_viewer)]
    fn builtin() -> Result<Self, WebViewerDataError> {
        Err(WebViewerDataError::NoBuiltinAssets)
    }
}

impl WebViewerData {
    #[inline]
    pub fn index_html(&self) -> &[u8] {
        &self.index_html
    }

    #[inline]
    pub fn favicon(&self) -> &[u8] {
        &self.favicon
    }

    #[inline]
    pub fn apple_touch_icon(&self) -> &[u8] {
        &self.apple_touch_icon
    }

    #[inline]
    pub fn sw_js(&self) -> &[u8] {
        &self.sw_js
    }

    #[inline]
    pub fn viewer_js(&self) -> &[u8] {
        &self.viewer_js
    }

    #[inline]
    pub fn viewer_wasm(&self) -> &[u8] {
        &self.viewer_wasm
    }

    #[inline]
    pub fn signed_in_html(&self) -> &[u8] {
        &self.signed_in_html
    }

    #[inline]
    pub fn signed_out_html(&self) -> &[u8] {
        &self.signed_out_html
    }
}

// ----------------------------------------------------------------------------

/// Callback used by `POST /api/open_local` to open a host filesystem path and stream it
/// into the gRPC message proxy (native read + push). Returns an error message on failure.
pub type OpenLocalHandler = std::sync::Arc<dyn Fn(&str) -> Result<(), String> + Send + Sync>;

/// `POST /api/convert_record` — body = host path to Apollo `.record`.
/// Returns JSON (`job_id`, `status`, `progress`, `output_path`, …).
pub type ConvertRecordHandler =
    std::sync::Arc<dyn Fn(&str) -> Result<String, String> + Send + Sync>;

/// `GET /api/convert_record?job_id=` — JSON progress/status for a convert job.
pub type ConvertStatusHandler =
    std::sync::Arc<dyn Fn(&str) -> Result<String, String> + Send + Sync>;

/// `POST /api/mcap_topics` (body = path) or `GET /api/mcap_topics` (cached last open).
/// Returns JSON: `{"path":"...","topics":[...],"count":N}`.
pub type McapTopicsHandler =
    std::sync::Arc<dyn Fn(Option<&str>) -> Result<String, String> + Send + Sync>;

/// `POST /api/topic_debug` — body = `mcap_path\ntopic[\nat_ns]` (2–3 lines).
/// Returns JSON DebugString or `{"status":"error","message":...}` (fail loud).
pub type TopicDebugHandler =
    std::sync::Arc<dyn Fn(&str, &str, Option<i64>) -> Result<String, String> + Send + Sync>;

/// `POST /api/upload_recording` — browser uploads a bag from the client machine.
/// Args: `(filename, raw_bytes)` → JSON `{"status":"ok","path":"/host/path"}`.
pub type UploadRecordingHandler =
    std::sync::Arc<dyn Fn(&str, &[u8]) -> Result<String, String> + Send + Sync>;

pub type BrowserRecordHandler =
    std::sync::Arc<dyn Fn(&str, &mut dyn std::io::Read) -> Result<String, String> + Send + Sync>;

pub type DebugQueryHandler = std::sync::Arc<dyn Fn(&str) -> Result<String, String> + Send + Sync>;

/// `POST /api/playback_window` — body lines:
/// `mcap_path`, `begin_ns`, `end_ns`, `reset` (`0`|`1`), then one topic per line.
/// Streams a filtered MCAP time window into the gRPC proxy (fail loud if empty topics / bad range).
pub type PlaybackWindowHandler =
    std::sync::Arc<dyn Fn(&str, u64, u64, bool, &[String]) -> Result<String, String> + Send + Sync>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
/// Typed port for use with [`WebViewerServer`]
pub struct WebViewerServerPort(pub u16);

impl From<u16> for WebViewerServerPort {
    #[inline]
    fn from(port: u16) -> Self {
        Self(port)
    }
}

impl WebViewerServerPort {
    /// Port to use with [`WebViewerServer::new`] when you want the OS to pick a port for you.
    ///
    /// This is defined as `0`.
    pub const AUTO: Self = Self(0);
}

impl Default for WebViewerServerPort {
    fn default() -> Self {
        Self(DEFAULT_WEB_VIEWER_SERVER_PORT)
    }
}

impl Display for WebViewerServerPort {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

// Needed for clap
impl FromStr for WebViewerServerPort {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.parse::<u16>() {
            Ok(port) => Ok(Self(port)),
            Err(err) => Err(format!("Failed to parse port: {err}")),
        }
    }
}

/// HTTP host for the Rerun Web Viewer application
/// This serves the HTTP+Wasm+JS files that make up the web-viewer.
#[must_use = "Dropping this means stopping the server"]
pub struct WebViewerServer {
    inner: Arc<WebViewerServerInner>,
    thread_handle: Option<std::thread::JoinHandle<()>>,
}

struct WebViewerServerInner {
    server: tiny_http::Server,
    shutdown: AtomicBool,
    num_wasm_served: AtomicU64,

    #[cfg(not(disable_web_viewer_server))]
    data: WebViewerData,

    /// Optional host-path opener for Apollo web_monitor (native read + gRPC stream).
    open_local: parking_lot::Mutex<Option<OpenLocalHandler>>,
    /// Optional Apollo `.record` → MCAP converter.
    convert_record: parking_lot::Mutex<Option<ConvertRecordHandler>>,
    /// Optional convert job status lookup.
    convert_status: parking_lot::Mutex<Option<ConvertStatusHandler>>,
    /// Optional client→host bag upload (remote browser users).
    upload_recording: parking_lot::Mutex<Option<UploadRecordingHandler>>,
    browser_record: parking_lot::Mutex<Option<BrowserRecordHandler>>,
    /// Optional MCAP summary topic list (header/channels, no message decode).
    mcap_topics: parking_lot::Mutex<Option<McapTopicsHandler>>,
    topic_debug: parking_lot::Mutex<Option<TopicDebugHandler>>,
    debug_query: parking_lot::Mutex<Option<DebugQueryHandler>>,
    simulation: parking_lot::Mutex<Option<DebugQueryHandler>>,
    simulation_events: Arc<SimulationEvents>,
    /// Optional windowed MCAP playback (time range + topic subset).
    playback_window: parking_lot::Mutex<Option<PlaybackWindowHandler>>,
}

impl WebViewerServer {
    /// Create new [`WebViewerServer`] to host the Rerun Web Viewer on a specified port.
    ///
    /// [`WebViewerServerPort::AUTO`] will tell the OS choose any free port.
    ///
    /// The server will immediately start listening for incoming connections
    /// and stop doing so when the returned [`WebViewerServer`] is dropped.
    ///
    /// ## Example
    /// ``` no_run
    /// # use re_web_viewer_server::{WebViewerServer, WebViewerServerPort, WebViewerServerError};
    /// # async fn example() -> Result<(), WebViewerServerError> {
    /// let server = WebViewerServer::new("0.0.0.0", WebViewerServerPort::AUTO)?;
    /// let server_url = server.server_url();
    /// # Ok(()) }
    /// ```
    pub fn new(bind_ip: &str, port: WebViewerServerPort) -> Result<Self, WebViewerServerError> {
        Self::with_archive(bind_ip, port, None)
    }

    /// Like [`WebViewerServer::new`], but if `assets_archive_path` is set,
    /// the web viewer assets are served from the given zip archive
    /// instead of the assets built into the binary.
    pub fn with_archive(
        bind_ip: &str,
        port: WebViewerServerPort,
        assets_archive_path: Option<&Path>,
    ) -> Result<Self, WebViewerServerError> {
        // Load the assets eagerly so that e.g. a missing archive fails server
        // creation instead of killing the serve thread on the first request.
        cfg_select! {
            disable_web_viewer_server => {
                let _ = assets_archive_path;
                Self::with_data(bind_ip, port, WebViewerData::empty())
            }
            _ => {
                Self::with_data(bind_ip, port, WebViewerData::load(assets_archive_path)?)
            }
        }
    }

    /// Like [`WebViewerServer::new`], but serving already-loaded assets,
    /// e.g. from [`WebViewerData::from_dir`].
    pub fn with_data(
        bind_ip: &str,
        port: WebViewerServerPort,
        data: WebViewerData,
    ) -> Result<Self, WebViewerServerError> {
        // `disable_web_viewer_server` builds serve nothing, so drop the assets right away.
        #[cfg(disable_web_viewer_server)]
        drop(data);

        let bind_addr = std::net::SocketAddr::new(bind_ip.parse()?, port.0);

        let server = tiny_http::Server::http(bind_addr).map_err(|err| {
            WebViewerServerError::CreateServerFailed {
                address: bind_addr.to_string(),
                source: err,
            }
        })?;
        let shutdown = AtomicBool::new(false);

        let inner = Arc::new(WebViewerServerInner {
            server,
            shutdown,
            num_wasm_served: Default::default(),
            #[cfg(not(disable_web_viewer_server))]
            data,
            open_local: parking_lot::Mutex::new(None),
            convert_record: parking_lot::Mutex::new(None),
            convert_status: parking_lot::Mutex::new(None),
            upload_recording: parking_lot::Mutex::new(None),
            browser_record: parking_lot::Mutex::new(None),
            mcap_topics: parking_lot::Mutex::new(None),
            topic_debug: parking_lot::Mutex::new(None),
            debug_query: parking_lot::Mutex::new(None),
            simulation: parking_lot::Mutex::new(None),
            simulation_events: Arc::default(),
            playback_window: parking_lot::Mutex::new(None),
        });

        let inner_copy = inner.clone();

        // TODO(andreas): Should we create a bunch of worker threads as proposed by https://docs.rs/tiny_http/latest/tiny_http/#creating-the-server ?
        // Not doing this right now since what we're serving out is so trivial (just a few files).
        let thread_handle = std::thread::Builder::new()
            .name("re_web_viewer_server".to_owned())
            .spawn(move || inner_copy.serve())
            .ok();

        Ok(Self {
            inner,
            thread_handle,
        })
    }

    /// Includes `http://` prefix
    pub fn server_url(&self) -> String {
        let local_addr = self.inner.server.server_addr();
        if let Some(local_addr) = local_addr.clone().to_ip()
            && local_addr.ip().is_unspecified()
        {
            return format!("http://127.0.0.1:{}", local_addr.port());
        }
        format!("http://{local_addr}")
    }

    pub fn bound_url(&self) -> String {
        format!("http://{}", self.inner.server.server_addr())
    }

    /// Install a handler for `POST /api/open_local` (body = absolute host path).
    ///
    /// Used by Apollo web_monitor so the browser can ask the host process to open large
    /// `.rrd` / `.mcap` files via native import + gRPC streaming (no full download into wasm).
    pub fn set_open_local_handler(&self, handler: OpenLocalHandler) {
        *self.inner.open_local.lock() = Some(handler);
    }

    /// Install handler for `POST /api/convert_record` (Apollo `.record` → semantic MCAP).
    pub fn set_convert_record_handler(&self, handler: ConvertRecordHandler) {
        *self.inner.convert_record.lock() = Some(handler);
    }

    /// Install handler for `GET /api/convert_record?job_id=…`.
    pub fn set_convert_status_handler(&self, handler: ConvertStatusHandler) {
        *self.inner.convert_status.lock() = Some(handler);
    }

    pub fn set_browser_record_handler(&self, handler: BrowserRecordHandler) {
        *self.inner.browser_record.lock() = Some(handler);
    }

    /// Install handler for `POST /api/upload_recording` (client machine bag → host path).
    pub fn set_upload_recording_handler(&self, handler: UploadRecordingHandler) {
        *self.inner.upload_recording.lock() = Some(handler);
    }

    /// Install handler for `GET|POST /api/mcap_topics` (MCAP summary channel list).
    pub fn set_mcap_topics_handler(&self, handler: McapTopicsHandler) {
        *self.inner.mcap_topics.lock() = Some(handler);
    }

    /// Install handler for `POST /api/topic_debug` (Dreamview-style on-demand DebugString).
    pub fn set_topic_debug_handler(&self, handler: TopicDebugHandler) {
        *self.inner.topic_debug.lock() = Some(handler);
    }

    pub fn set_debug_query_handler(&self, handler: DebugQueryHandler) {
        *self.inner.debug_query.lock() = Some(handler);
    }

    pub fn set_simulation_handler(&self, handler: DebugQueryHandler) {
        *self.inner.simulation.lock() = Some(handler);
    }

    /// Task events are independent from command request/response state.
    pub fn simulation_events(&self) -> Arc<SimulationEvents> {
        self.inner.simulation_events.clone()
    }

    /// Install handler for `POST /api/playback_window` (time-range + topic-subset MCAP stream).
    pub fn set_playback_window_handler(&self, handler: PlaybackWindowHandler) {
        *self.inner.playback_window.lock() = Some(handler);
    }

    /// Blocks execution as long as the server is running.
    ///
    /// There's no way of shutting the server down from the outside right now.
    pub fn block(mut self) {
        if let Some(thread_handle) = self.thread_handle.take() {
            thread_handle.join().ok();
        }
    }

    /// Keeps the web viewer running until the parent process shuts down.
    pub fn detach(mut self) {
        if let Some(thread_handle) = self.thread_handle.take() {
            // dropping the thread handle detaches the thread.
            drop(thread_handle);
        }
    }
}

impl Drop for WebViewerServer {
    fn drop(&mut self) {
        if let Some(thread_handle) = self.thread_handle.take() {
            let num_wasm_served = self.inner.num_wasm_served.load(Ordering::Relaxed);
            re_log::debug!(
                "Shutting down web server after serving the Wasm {num_wasm_served} time(s)"
            );

            self.inner.shutdown.store(true, Ordering::Release);
            self.inner.server.unblock();
            thread_handle.join().ok();
        }
    }
}

#[cfg(not(disable_web_viewer_server))]
fn urlencoding_decode(s: &str) -> String {
    // Minimal %XX / + decoder for query paths (no dependency).
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < bytes.len() => {
                let h = |c: u8| -> Option<u8> {
                    match c {
                        b'0'..=b'9' => Some(c - b'0'),
                        b'a'..=b'f' => Some(c - b'a' + 10),
                        b'A'..=b'F' => Some(c - b'A' + 10),
                        _ => None,
                    }
                };
                if let (Some(a), Some(b)) = (h(bytes[i + 1]), h(bytes[i + 2])) {
                    out.push((a << 4) | b);
                    i += 3;
                } else {
                    out.push(bytes[i]);
                    i += 1;
                }
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn json_escape(s: &str) -> String {
    let mut out = String::from('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

impl WebViewerServerInner {
    fn serve(&self) {
        loop {
            let request = self.server.recv();
            if self.shutdown.load(Ordering::Acquire) {
                return;
            }

            let request = match request {
                Ok(request) => request,
                Err(err) => {
                    re_log::error!("Failed to receive http request: {err}");
                    continue;
                }
            };

            if let Err(err) = self.send_response(request) {
                re_log::error!("Failed to send http response: {err}");
            }
        }
    }

    fn on_serve_wasm(&self) {
        self.num_wasm_served.fetch_add(1, Ordering::Relaxed);

        #[cfg(feature = "analytics")]
        re_analytics::record(|| re_analytics::event::ServeWasm);
    }

    #[cfg(disable_web_viewer_server)]
    fn send_response(&self, _request: tiny_http::Request) -> Result<(), std::io::Error> {
        if false {
            self.on_serve_wasm(); // to silence warning about the function being unused
        }
        panic!(
            "re_web_viewer_server compiled without .wasm, because of '__disable_server' feature, `--all-features`, or 'RERUN_DISABLE_WEB_VIEWER_SERVER=1'. DON'T DO THAT! It's only meant for tests and docs!"
        );
    }

    #[cfg(not(disable_web_viewer_server))]
    fn handle_open_local(&self, mut request: tiny_http::Request) -> Result<(), std::io::Error> {
        use std::io::Read as _;

        let cors =
            tiny_http::Header::from_str("Access-Control-Allow-Origin: *").expect("valid header");

        if request.method() == &tiny_http::Method::Options {
            let mut response = tiny_http::Response::empty(204);
            response.add_header(cors);
            if let Ok(h) =
                tiny_http::Header::from_str("Access-Control-Allow-Methods: POST, OPTIONS")
            {
                response.add_header(h);
            }
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Headers: Content-Type")
            {
                response.add_header(h);
            }
            return request.respond(response);
        }

        if request.method() != &tiny_http::Method::Post {
            let mut response = tiny_http::Response::from_string(
                "use POST with body = absolute host path (.rrd/.rbl/.mcap)",
            )
            .with_status_code(405);
            response.add_header(cors);
            return request.respond(response);
        }

        let mut body = String::new();
        request.as_reader().read_to_string(&mut body)?;
        let path = body.trim().trim_matches('"');

        let Some(handler) = self.open_local.lock().clone() else {
            let mut response = tiny_http::Response::from_string(
                "open_local handler not configured on this server",
            )
            .with_status_code(501);
            response.add_header(cors);
            return request.respond(response);
        };

        match handler(path) {
            Ok(()) => {
                let mut response =
                    tiny_http::Response::from_string(format!("ok: streaming {path}"))
                        .with_status_code(200);
                response.add_header(cors);
                if let Ok(h) =
                    tiny_http::Header::from_str("Content-Type: text/plain; charset=utf-8")
                {
                    response.add_header(h);
                }
                request.respond(response)
            }
            Err(err) => {
                re_log::error!("open_local failed: {err}");
                let mut response = tiny_http::Response::from_string(err).with_status_code(400);
                response.add_header(cors);
                if let Ok(h) =
                    tiny_http::Header::from_str("Content-Type: text/plain; charset=utf-8")
                {
                    response.add_header(h);
                }
                request.respond(response)
            }
        }
    }

    #[cfg(not(disable_web_viewer_server))]
    fn handle_convert_record(&self, mut request: tiny_http::Request) -> Result<(), std::io::Error> {
        use std::io::Read as _;

        if request.method() == &tiny_http::Method::Options {
            let mut response = tiny_http::Response::empty(204);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Access-Control-Allow-Methods: POST, GET, OPTIONS")
            {
                response.add_header(h);
            }
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Headers: Content-Type")
            {
                response.add_header(h);
            }
            return request.respond(response);
        }

        let url = request.url().to_owned();
        let make_response = |status: u16, body: String| {
            let mut response = tiny_http::Response::from_string(body).with_status_code(status);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Content-Type: application/json; charset=utf-8")
            {
                response.add_header(h);
            }
            response
        };

        if request.method() == &tiny_http::Method::Get {
            let job_id = url
                .split('?')
                .nth(1)
                .unwrap_or("")
                .split('&')
                .find_map(|kv| kv.strip_prefix("job_id="))
                .unwrap_or("")
                .to_owned();
            let Some(handler) = self.convert_status.lock().clone() else {
                return request.respond(make_response(
                    501,
                    r#"{"status":"error","message":"convert_status handler not configured"}"#
                        .into(),
                ));
            };
            return match handler(&job_id) {
                Ok(body) => request.respond(make_response(200, body)),
                Err(err) => request.respond(make_response(
                    400,
                    format!(r#"{{"status":"error","message":{}}}"#, json_escape(&err)),
                )),
            };
        }

        if request.method() != &tiny_http::Method::Post {
            return request.respond(make_response(
                405,
                r#"{"status":"error","message":"use POST with body = host .record path, or GET ?job_id="}"#
                    .into(),
            ));
        }

        let mut body = String::new();
        request.as_reader().read_to_string(&mut body)?;
        let path = body.trim().trim_matches('"');
        let Some(handler) = self.convert_record.lock().clone() else {
            return request.respond(make_response(
                501,
                r#"{"status":"error","message":"convert_record handler not configured"}"#.into(),
            ));
        };
        match handler(path) {
            Ok(body) => request.respond(make_response(200, body)),
            Err(err) => request.respond(make_response(
                400,
                format!(r#"{{"status":"error","message":{}}}"#, json_escape(&err)),
            )),
        }
    }

    #[cfg(not(disable_web_viewer_server))]
    fn handle_upload_recording(
        &self,
        mut request: tiny_http::Request,
    ) -> Result<(), std::io::Error> {
        use std::io::Read as _;

        let cors =
            tiny_http::Header::from_str("Access-Control-Allow-Origin: *").expect("valid header");
        if request.method() == &tiny_http::Method::Options {
            let mut response = tiny_http::Response::empty(204);
            response.add_header(cors);
            if let Ok(h) =
                tiny_http::Header::from_str("Access-Control-Allow-Methods: POST, OPTIONS")
            {
                response.add_header(h);
            }
            if let Ok(h) = tiny_http::Header::from_str(
                "Access-Control-Allow-Headers: Content-Type, X-Filename, X-Map",
            ) {
                response.add_header(h);
            }
            return request.respond(response);
        }

        let make_response = |status: u16, body: String| {
            let mut response = tiny_http::Response::from_string(body).with_status_code(status);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Content-Type: application/json; charset=utf-8")
            {
                response.add_header(h);
            }
            response
        };

        if request.method() != &tiny_http::Method::Post {
            return request.respond(make_response(
                405,
                r#"{"status":"error","message":"use POST with X-Filename and raw body"}"#.into(),
            ));
        }

        let filename = request
            .headers()
            .iter()
            .find(|h| h.field.equiv("X-Filename"))
            .map(|h| h.value.as_str().to_owned())
            .unwrap_or_default();
        if filename.trim().is_empty() {
            return request.respond(make_response(
                400,
                r#"{"status":"error","message":"missing X-Filename header"}"#.into(),
            ));
        }

        let mut body = Vec::new();
        request.as_reader().read_to_end(&mut body)?;
        if body.is_empty() {
            return request.respond(make_response(
                400,
                r#"{"status":"error","message":"empty upload body"}"#.into(),
            ));
        }

        let Some(handler) = self.upload_recording.lock().clone() else {
            return request.respond(make_response(
                501,
                r#"{"status":"error","message":"upload_recording handler not configured"}"#.into(),
            ));
        };

        match handler(&filename, &body) {
            Ok(json) => request.respond(make_response(200, json)),
            Err(err) => request.respond(make_response(
                400,
                format!(r#"{{"status":"error","message":{}}}"#, json_escape(&err)),
            )),
        }
    }

    #[cfg(not(disable_web_viewer_server))]
    fn handle_mcap_topics(&self, mut request: tiny_http::Request) -> Result<(), std::io::Error> {
        use std::io::Read as _;

        if request.method() == &tiny_http::Method::Options {
            let mut response = tiny_http::Response::empty(204);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Access-Control-Allow-Methods: GET, POST, OPTIONS")
            {
                response.add_header(h);
            }
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Headers: Content-Type")
            {
                response.add_header(h);
            }
            return request.respond(response);
        }

        let make_response = |status: u16, body: String| {
            let mut response = tiny_http::Response::from_string(body).with_status_code(status);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Content-Type: application/json; charset=utf-8")
            {
                response.add_header(h);
            }
            response
        };

        let Some(handler) = self.mcap_topics.lock().clone() else {
            return request.respond(make_response(
                501,
                r#"{"status":"error","message":"mcap_topics handler not configured"}"#.into(),
            ));
        };

        let path_arg = if request.method() == &tiny_http::Method::Post {
            let mut body = String::new();
            request.as_reader().read_to_string(&mut body)?;
            let t = body.trim().trim_matches('"');
            if t.is_empty() {
                None
            } else {
                Some(t.to_owned())
            }
        } else if request.method() == &tiny_http::Method::Get {
            let url = request.url().to_owned();
            let q = url.split('?').nth(1).unwrap_or("");
            let mut path = None;
            for part in q.split('&') {
                if let Some(v) = part.strip_prefix("path=") {
                    let decoded = urlencoding_decode(v);
                    if !decoded.is_empty() {
                        path = Some(decoded);
                    }
                }
            }
            path
        } else {
            return request.respond(make_response(
                405,
                r#"{"status":"error","message":"use GET or POST"}"#.into(),
            ));
        };

        match handler(path_arg.as_deref()) {
            Ok(body) => request.respond(make_response(200, body)),
            Err(err) => request.respond(make_response(
                400,
                format!(r#"{{"status":"error","message":{}}}"#, json_escape(&err)),
            )),
        }
    }

    #[cfg(not(disable_web_viewer_server))]
    fn handle_debug_query(&self, mut request: tiny_http::Request) -> Result<(), std::io::Error> {
        use std::io::Read as _;
        let simulation = request.url().split('?').next() == Some("/api/sim");
        let handler = if simulation {
            &self.simulation
        } else {
            &self.debug_query
        };
        // Mutation API: browser cross-site requests must not enqueue/cancel jobs.
        let cross_site = simulation
            && request
                .headers()
                .iter()
                .any(|h| h.field.equiv("Sec-Fetch-Site") && h.value.as_str() == "cross-site");
        let host = request
            .headers()
            .iter()
            .find(|h| h.field.equiv("Host"))
            .map(|h| h.value.as_str());
        let invalid_origin = simulation
            && request.headers().iter().any(|h| {
                h.field.equiv("Origin")
                    && !host.is_some_and(|host| {
                        h.value.as_str() == format!("http://{host}")
                            || h.value.as_str() == format!("https://{host}")
                    })
            });
        let json_content = request.headers().iter().any(|h| {
            h.field.equiv("Content-Type")
                && h.value
                    .as_str()
                    .split(';')
                    .next()
                    .is_some_and(|v| v.trim() == "application/json")
        });
        let mut body = String::new();
        request.as_reader().take(65537).read_to_string(&mut body)?;
        let result = if cross_site || invalid_origin {
            Err("Cross-site simulation requests are forbidden".to_owned())
        } else if request.method() != &tiny_http::Method::Post || (simulation && !json_content) {
            Err("Use POST with JSON body".to_owned())
        } else if body.len() > 65536 {
            Err("Debug query exceeds 64 KiB request budget".to_owned())
        } else if let Some(handler) = handler.lock().clone() {
            handler(&body)
        } else {
            Err("Debug query handler not configured".to_owned())
        };
        let (code, body) = match result {
            Ok(body) => (200, body),
            Err(error) => (
                400,
                format!(r#"{{"status":"error","message":{}}}"#, json_escape(&error)),
            ),
        };
        let mut response = tiny_http::Response::from_string(body).with_status_code(code);
        if let Ok(h) = tiny_http::Header::from_str("Content-Type: application/json; charset=utf-8")
        {
            response.add_header(h);
        }
        request.respond(response)
    }

    #[cfg(not(disable_web_viewer_server))]
    fn handle_topic_debug(&self, mut request: tiny_http::Request) -> Result<(), std::io::Error> {
        use std::io::Read as _;

        if request.method() == &tiny_http::Method::Options {
            let mut response = tiny_http::Response::empty(204);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Access-Control-Allow-Methods: POST, OPTIONS")
            {
                response.add_header(h);
            }
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Headers: Content-Type")
            {
                response.add_header(h);
            }
            return request.respond(response);
        }

        let make_response = |status: u16, body: String| {
            let mut response = tiny_http::Response::from_string(body).with_status_code(status);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Content-Type: application/json; charset=utf-8")
            {
                response.add_header(h);
            }
            response
        };

        if request.method() != &tiny_http::Method::Post {
            return request.respond(make_response(
                405,
                r#"{"status":"error","message":"use POST body = mcap_path\\ntopic[\\nat_ns]"}"#
                    .into(),
            ));
        }

        let mut body = String::new();
        request.as_reader().read_to_string(&mut body)?;
        let mut lines = body.lines();
        let mcap = lines.next().unwrap_or("").trim();
        let topic = lines.next().unwrap_or("").trim();
        let at_ns = lines
            .next()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .and_then(|s| s.parse::<i64>().ok());
        if mcap.is_empty() || topic.is_empty() {
            return request.respond(make_response(
                400,
                r#"{"status":"error","message":"body must be mcap_path\\ntopic[\\nat_ns]"}"#.into(),
            ));
        }

        let Some(handler) = self.topic_debug.lock().clone() else {
            return request.respond(make_response(
                501,
                r#"{"status":"error","message":"topic_debug handler not configured"}"#.into(),
            ));
        };
        match handler(mcap, topic, at_ns) {
            Ok(body) => request.respond(make_response(200, body)),
            Err(err) => request.respond(make_response(
                400,
                format!(r#"{{"status":"error","message":{}}}"#, json_escape(&err)),
            )),
        }
    }

    #[cfg(not(disable_web_viewer_server))]
    fn handle_playback_window(
        &self,
        mut request: tiny_http::Request,
    ) -> Result<(), std::io::Error> {
        use std::io::Read as _;

        if request.method() == &tiny_http::Method::Options {
            let mut response = tiny_http::Response::empty(204);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Access-Control-Allow-Methods: POST, OPTIONS")
            {
                response.add_header(h);
            }
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Headers: Content-Type")
            {
                response.add_header(h);
            }
            return request.respond(response);
        }

        let make_response = |status: u16, body: String| {
            let mut response = tiny_http::Response::from_string(body).with_status_code(status);
            if let Ok(h) = tiny_http::Header::from_str("Access-Control-Allow-Origin: *") {
                response.add_header(h);
            }
            if let Ok(h) =
                tiny_http::Header::from_str("Content-Type: application/json; charset=utf-8")
            {
                response.add_header(h);
            }
            response
        };

        if request.method() != &tiny_http::Method::Post {
            return request.respond(make_response(
                405,
                r#"{"status":"error","message":"use POST body = mcap\\nbegin_ns\\nend_ns\\nreset\\ntopic…"}"#
                    .into(),
            ));
        }

        let mut body = String::new();
        request.as_reader().read_to_string(&mut body)?;
        let mut lines = body.lines();
        let mcap = lines.next().unwrap_or("").trim();
        let begin_ns = lines
            .next()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .and_then(|s| s.parse::<u64>().ok());
        let end_ns = lines
            .next()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .and_then(|s| s.parse::<u64>().ok());
        let reset = match lines.next().map(str::trim).unwrap_or("0") {
            "1" | "true" | "True" | "yes" => true,
            "0" | "false" | "False" | "no" | "" => false,
            other => {
                return request.respond(make_response(
                    400,
                    format!(
                        r#"{{"status":"error","message":"reset must be 0 or 1, got {other}"}}"#
                    ),
                ));
            }
        };
        let topics: Vec<String> = lines
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect();

        if mcap.is_empty() || begin_ns.is_none() || end_ns.is_none() {
            return request.respond(make_response(
                400,
                r#"{"status":"error","message":"body must be mcap\\nbegin_ns\\nend_ns\\nreset\\ntopic…"}"#
                    .into(),
            ));
        }

        let Some(handler) = self.playback_window.lock().clone() else {
            return request.respond(make_response(
                501,
                r#"{"status":"error","message":"playback_window handler not configured"}"#.into(),
            ));
        };

        match handler(mcap, begin_ns.unwrap(), end_ns.unwrap(), reset, &topics) {
            Ok(body) => request.respond(make_response(200, body)),
            Err(err) => {
                re_log::error!("playback_window failed: {err}");
                request.respond(make_response(
                    400,
                    format!(r#"{{"status":"error","message":{}}}"#, json_escape(&err)),
                ))
            }
        }
    }

    fn handle_browser_record(&self, mut request: tiny_http::Request) -> Result<(), std::io::Error> {
        let host = request
            .headers()
            .iter()
            .find(|h| h.field.equiv("Host"))
            .map(|h| h.value.as_str());
        let invalid_origin = request.headers().iter().any(|h| {
            (h.field.equiv("Sec-Fetch-Site") && h.value.as_str() == "cross-site")
                || (h.field.equiv("Origin")
                    && !host.is_some_and(|host| {
                        h.value.as_str() == format!("http://{host}")
                            || h.value.as_str() == format!("https://{host}")
                    }))
        });
        let query = request
            .url()
            .split_once('?')
            .map(|(_, q)| q.to_owned())
            .unwrap_or_default();
        let result = if invalid_origin {
            Err("Cross-site browser file request forbidden".to_owned())
        } else if request.method() != &tiny_http::Method::Post {
            Err("Use POST".to_owned())
        } else if request.body_length().is_some_and(|n| n > 2 * 1024 * 1024) {
            Err("Browser chunk exceeds 2 MiB".to_owned())
        } else if let Some(handler) = self.browser_record.lock().clone() {
            handler(&query, request.as_reader())
        } else {
            Err("Browser recording handler unavailable".to_owned())
        };
        let (code, body) = match result {
            Ok(body) => (200, body),
            Err(error) => (
                400,
                format!(r#"{{"status":"error","message":{}}}"#, json_escape(&error)),
            ),
        };
        let mut response = tiny_http::Response::from_string(body).with_status_code(code);
        response.add_header(
            tiny_http::Header::from_str("Content-Type: application/json").expect("valid header"),
        );
        request.respond(response)
    }

    fn send_response(&self, request: tiny_http::Request) -> Result<(), std::io::Error> {
        // Strip arguments from url so we get the actual path.
        let url = request.url();
        let path = url.split('?').next().unwrap_or(url);

        if path == "/api/sim/events" {
            let host = request
                .headers()
                .iter()
                .find(|h| h.field.equiv("Host"))
                .map(|h| h.value.as_str());
            if request.headers().iter().any(|h| {
                (h.field.equiv("Sec-Fetch-Site") && h.value.as_str() == "cross-site")
                    || (h.field.equiv("Origin")
                        && !host.is_some_and(|host| {
                            h.value.as_str() == format!("http://{host}")
                                || h.value.as_str() == format!("https://{host}")
                        }))
            }) {
                return request.respond(tiny_http::Response::empty(403));
            }
            if request.method() != &tiny_http::Method::Get {
                return request.respond(tiny_http::Response::empty(405));
            }
            let handler = self.simulation.lock().clone();
            let events = self.simulation_events.clone();
            std::thread::Builder::new()
                .name("simulation-sse".into())
                .spawn(move || {
                    let result = handler
                        .ok_or_else(|| "Simulation handler unavailable".to_owned())
                        .and_then(|handler| handler(r#"{"action":"subscribe"}"#))
                        .and_then(|reply| {
                            let reply: serde_json::Value =
                                serde_json::from_str(&reply).map_err(|e| e.to_string())?;
                            if reply["status"] == "ok" {
                                Ok(())
                            } else {
                                Err(reply["message"].to_string())
                            }
                        });
                    if let Err(error) = result {
                        events.publish(Err(error));
                    }
                    if let Err(error) = events.serve(request) {
                        re_log::debug!("Simulation subscriber disconnected: {error}");
                    }
                })?;
            return Ok(());
        }

        // Apollo web_monitor: host-side open (native read + stream via gRPC proxy).
        if path == "/api/open_local" {
            return self.handle_open_local(request);
        }
        if path == "/api/convert_record" {
            return self.handle_convert_record(request);
        }
        if path == "/api/browser_record" {
            return self.handle_browser_record(request);
        }
        if path == "/api/upload_recording" {
            return self.handle_upload_recording(request);
        }
        if path == "/api/mcap_topics" {
            return self.handle_mcap_topics(request);
        }
        if path == "/api/topic_debug" {
            return self.handle_topic_debug(request);
        }
        if path == "/api/debug_query" {
            return self.handle_debug_query(request);
        }
        if path == "/api/sim" {
            return self.handle_debug_query(request);
        }
        if path == "/api/playback_window" {
            return self.handle_playback_window(request);
        }

        let data = &self.data;
        let (mime, bytes): (&str, &[u8]) = match path {
            "/" | "/index.html" => ("text/html", data.index_html()),
            "/sim_scope.png" | "/favicon.ico" => ("image/png", data.favicon()),
            "/apple-touch-icon.png" => ("image/png", data.apple_touch_icon()),
            "/sw.js" => ("text/javascript", data.sw_js()),
            "/re_viewer.js" => ("text/javascript", data.viewer_js()),
            "/re_viewer_bg.wasm" => {
                self.on_serve_wasm();
                ("application/wasm", data.viewer_wasm())
            }
            "/signed-in" => ("text/html", data.signed_in_html()),
            "/signed-out" => ("text/html", data.signed_out_html()),
            _ => {
                re_log::warn!("404 path: {}", path);
                return request.respond(tiny_http::Response::empty(404));
            }
        };

        // TODO(#6061): Wasm should be compressed.

        let mut response = tiny_http::Response::from_data(bytes).with_header(
            tiny_http::Header::from_str(&format!("Content-Type: {mime}"))
                // Both `mime` and the header are hardcoded, so shouldn't be able to fail depending on user input.
                .expect("Invalid http header"),
        );

        // The wasm files are pretty large, so they'll be sent chunked (ideally we'd gzip them…).
        // (tiny_http will do so automatically if the data is above a certain threshold.
        // It is configurable, but we don't know all the implications of that.)
        // Unfortunately `Transfer-Encoding: chunked` means that no size is transmitted.
        // We work around this by adding a custom header with the size that web_viewer/index.html understands.
        if let Ok(header) =
            tiny_http::Header::from_str(&format!("rerun-final-length: {}", bytes.len()))
        {
            response.add_header(header);
        }

        request.respond(response)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(not(disable_web_viewer_server))]
    fn simulation_stream_delivers_small_events_without_blocking_commands() {
        use std::io::{BufRead as _, Write as _};
        let server = WebViewerServer::new("127.0.0.1", WebViewerServerPort::AUTO).unwrap();
        server.set_simulation_handler(Arc::new(|_| Ok(r#"{"status":"ok"}"#.into())));
        let events = server.simulation_events();
        events.publish(Ok(r#"{"snapshot":true,"jobs":[]}"#.into()));
        let address = server.inner.server.server_addr().to_ip().unwrap();
        let mut socket = std::net::TcpStream::connect(address).unwrap();
        socket
            .set_read_timeout(Some(std::time::Duration::from_secs(3)))
            .unwrap();
        write!(
            socket,
            "GET /api/sim/events HTTP/1.1\r\nHost: {address}\r\n\r\n"
        )
        .unwrap();
        let mut stream = std::io::BufReader::new(socket);
        let mut read_event = || {
            loop {
                let mut line = String::new();
                assert!(stream.read_line(&mut line).unwrap() > 0);
                if let Some(data) = line.strip_prefix("data: ") {
                    return serde_json::from_str::<serde_json::Value>(data).unwrap();
                }
            }
        };
        assert_eq!(read_event()["snapshot"], true);
        let mut command = std::net::TcpStream::connect(address).unwrap();
        command
            .set_read_timeout(Some(std::time::Duration::from_secs(3)))
            .unwrap();
        write!(command, "POST /api/sim HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}").unwrap();
        let mut status = String::new();
        std::io::BufReader::new(command)
            .read_line(&mut status)
            .unwrap();
        assert!(status.contains("200"), "{status}");
        events.publish(Ok(
            r#"{"snapshot":false,"jobs":[{"id":"a","stage":"completed"}]}"#.into(),
        ));
        assert_eq!(read_event()["jobs"][0]["stage"], "completed");
        events.publish(Err("test shutdown".into()));
        assert_eq!(read_event()["status"], "error");
    }

    #[test]
    fn unspecified_bind_address_has_distinct_bound_and_connect_urls() {
        let server = WebViewerServer::new("0.0.0.0", WebViewerServerPort::AUTO).unwrap();
        let port = server.inner.server.server_addr().to_ip().unwrap().port();

        assert_eq!(server.bound_url(), format!("http://0.0.0.0:{port}"));
        assert_eq!(server.server_url(), format!("http://127.0.0.1:{port}"));
    }

    #[cfg(not(disable_web_viewer_server))]
    const ASSET_FILE_NAMES: [&str; 7] = [
        "index.html",
        "sim_scope.png",
        "sw.js",
        "re_viewer.js",
        "re_viewer_bg.wasm",
        "signed-in.html",
        "signed-out.html",
    ];

    /// Write a zip archive containing the given file names, each with its own name as contents.
    #[cfg(not(disable_web_viewer_server))]
    fn write_asset_archive(path: &Path, file_names: &[&str]) {
        use std::io::Write as _;

        let mut zip = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
        for name in file_names {
            zip.start_file(*name, zip::write::SimpleFileOptions::default())
                .unwrap();
            zip.write_all(name.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }

    #[test]
    #[cfg(not(disable_web_viewer_server))]
    fn load_data_from_archive() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("web_viewer.zip");
        write_asset_archive(&path, &ASSET_FILE_NAMES);

        let data = WebViewerData::from_archive(&path).unwrap();
        assert_eq!(data.index_html(), b"index.html");
        assert_eq!(data.viewer_wasm(), b"re_viewer_bg.wasm");
        assert_eq!(data.signed_out_html(), b"signed-out.html");
    }

    #[test]
    #[cfg(not(disable_web_viewer_server))]
    fn archive_with_missing_file_fails() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("web_viewer.zip");
        write_asset_archive(&path, &ASSET_FILE_NAMES[..6]); // No `signed-out.html`

        let err = WebViewerData::from_archive(&path).unwrap_err();
        assert!(
            matches!(&err, WebViewerDataError::ExtractFile { name, .. } if name == "signed-out.html"),
            "unexpected error: {err}"
        );
    }

    #[test]
    #[cfg(not(disable_web_viewer_server))]
    fn missing_archive_fails() {
        let err =
            WebViewerData::from_archive(Path::new("/nonexistent/web_viewer.zip")).unwrap_err();
        assert!(
            matches!(err, WebViewerDataError::OpenArchive { .. }),
            "unexpected error: {err}"
        );
    }
}
