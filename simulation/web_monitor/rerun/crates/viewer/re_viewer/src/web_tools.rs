//! Web-specific tools used by various parts of the application.
//!
//! Host-operation replies are tab-local (`sessionStorage`). Using localStorage
//! lets another viewer tab consume a reply, leaving the requester stuck waiting.

// TODO(grtlr): Move the remaining generic JS helpers to `re_web`.

use re_log::ResultExt as _;
use serde::Deserialize;
use wasm_bindgen::{JsCast as _, JsValue};

pub trait JsResultExt<T> {
    /// Logs an error if the result is an error and returns the result.
    fn ok_or_log_js_error(self) -> Option<T>;

    /// Logs an error if the result is an error and returns the result, but only once.
    #[expect(unused)]
    fn ok_or_log_js_error_once(self) -> Option<T>;

    /// Log a warning if there is an `Err`, but only log the exact same message once.
    #[expect(unused)]
    fn warn_on_js_err_once(self, msg: impl std::fmt::Display) -> Option<T>;

    /// Unwraps in debug builds otherwise logs an error if the result is an error and returns the result.
    #[expect(unused)]
    fn unwrap_debug_or_log_js_error(self) -> Option<T>;
}

impl<T> JsResultExt<T> for Result<T, JsValue> {
    fn ok_or_log_js_error(self) -> Option<T> {
        self.map_err(re_web::Error::from).ok_or_log_error()
    }

    fn ok_or_log_js_error_once(self) -> Option<T> {
        self.map_err(re_web::Error::from).ok_or_log_error_once()
    }

    fn warn_on_js_err_once(self, msg: impl std::fmt::Display) -> Option<T> {
        self.map_err(re_web::Error::from).warn_on_err_once(msg)
    }

    fn unwrap_debug_or_log_js_error(self) -> Option<T> {
        self.map_err(re_web::Error::from)
            .unwrap_debug_or_log_error()
    }
}

// Can't deserialize `Option<js_sys::Function>` directly, so newtype it is.
#[derive(Clone, Deserialize)]
#[repr(transparent)]
pub struct Callback(#[serde(with = "serde_wasm_bindgen::preserve")] js_sys::Function);

impl Callback {
    #[inline]
    pub fn call0(&self) -> Result<JsValue, re_web::Error> {
        let window: JsValue = re_web::browser::window()?.into();
        self.0.call0(&window).map_err(Into::into)
    }

    #[inline]
    pub fn call1(&self, arg0: &JsValue) -> Result<JsValue, re_web::Error> {
        let window: JsValue = re_web::browser::window()?.into();
        self.0.call1(&window, arg0).map_err(Into::into)
    }

    #[inline]
    pub fn call2(&self, arg0: &JsValue, arg1: &JsValue) -> Result<JsValue, re_web::Error> {
        let window: JsValue = re_web::browser::window()?.into();
        self.0.call2(&window, arg0, arg1).map_err(Into::into)
    }
}

// Deserializes from JS string or array of strings.
#[derive(Clone, Debug)]
pub struct StringOrStringArray(Vec<String>);

impl StringOrStringArray {
    pub fn into_inner(self) -> Vec<String> {
        self.0
    }
}

impl std::ops::Deref for StringOrStringArray {
    type Target = Vec<String>;

    #[inline]
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl<'de> Deserialize<'de> for StringOrStringArray {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        fn from_value(value: JsValue) -> Option<Vec<String>> {
            if let Some(value) = value.as_string() {
                return Some(vec![value]);
            }

            let array = value.dyn_into::<js_sys::Array>().ok()?;
            let mut out = Vec::with_capacity(array.length() as usize);
            for item in array {
                out.push(item.as_string()?);
            }
            Some(out)
        }

        let value = serde_wasm_bindgen::preserve::deserialize(deserializer)?;
        from_value(value)
            .map(Self)
            .ok_or_else(|| serde::de::Error::custom("value is not a string or array of strings"))
    }
}

/// Ask the host `POST /api/open_local` to stream a path/filename into the viewer.
///
/// Used for large bags: the browser never reads the file bytes.
pub fn request_host_open_local(path: &str, egui_ctx: egui::Context) {
    let path_owned = path.to_owned();
    let origin = web_sys::window()
        .and_then(|w| w.location().origin().ok())
        .unwrap_or_else(|| "http://127.0.0.1:9090".into());
    let url = format!("{origin}/api/open_local");
    re_log::info!("Requesting host open_local for {path_owned}");
    let request = ehttp::Request::post(url, path_owned.clone().into_bytes());
    ehttp::fetch(request, move |result| {
        // Only surface failures to the UI. Success is already shown as
        // "Host streaming…" and becomes "Loaded …" when the recording activates.
        let fail_msg = match result {
            Ok(resp) if resp.ok => {
                let body = resp.text().unwrap_or("ok");
                re_log::info!("Host accepted stream for {path_owned}: {body}");
                // open_local fills host summary-topic cache before streaming; pull it now.
                if path_owned.to_ascii_lowercase().ends_with(".mcap") {
                    request_host_mcap_topics(&path_owned, egui_ctx.clone());
                }
                None
            }
            Ok(resp) => {
                let body = resp.text().unwrap_or_default();
                re_log::error!(
                    "Host open failed for {path_owned} (HTTP {}): {body}",
                    resp.status
                );
                Some(format!("Open failed (HTTP {}): {body}", resp.status))
            }
            Err(err) => {
                re_log::error!("Host open request failed for {path_owned}: {err}");
                Some(format!("Open request failed: {err}"))
            }
        };
        if let Some(msg) = fail_msg {
            if let Some(win) = web_sys::window() {
                let _ = win
                    .session_storage()
                    .ok()
                    .flatten()
                    .and_then(|s| s.set_item("wm_open_local_status", &msg).ok());
            }
        }
        egui_ctx.request_repaint();
    });
}

pub fn take_open_local_status() -> Option<String> {
    let win = web_sys::window()?;
    let store = win.session_storage().ok()??;
    let v = store.get_item("wm_open_local_status").ok()??;
    let _ = store.remove_item("wm_open_local_status");
    Some(v)
}

/// Ask host to stream a filtered MCAP time window (`POST /api/playback_window`).
///
/// Body: `mcap\nbegin_ns\nend_ns\nreset\ntopic…`. Only listed topics are imported.
pub fn request_host_playback_window(
    mcap: &str,
    begin_ns: i64,
    end_ns: i64,
    reset: bool,
    topics: &[String],
    egui_ctx: egui::Context,
) {
    // Empty topics allowed only with reset (clear streams / drop prior import).
    if topics.is_empty() && !reset {
        re_log::error!("playback_window refused: no topics selected");
        if let Some(win) = web_sys::window() {
            let _ = win.session_storage().ok().flatten().and_then(|s| {
                s.set_item(
                    "wm_playback_window",
                    r#"{"status":"error","message":"no topics selected — enable topics in Topics picker"}"#,
                )
                .ok()
            });
        }
        egui_ctx.request_repaint();
        return;
    }
    let mcap_owned = mcap.to_owned();
    let origin = web_sys::window()
        .and_then(|w| w.location().origin().ok())
        .unwrap_or_else(|| "http://127.0.0.1:9090".into());
    let url = format!("{origin}/api/playback_window");
    let mut body = format!(
        "{mcap_owned}\n{begin_ns}\n{end_ns}\n{}\n",
        if reset { "1" } else { "0" }
    );
    for t in topics {
        body.push_str(t);
        body.push('\n');
    }
    re_log::debug!(
        "Requesting playback_window [{begin_ns}, {end_ns}) reset={reset} topics={} path={mcap_owned}",
        topics.len()
    );
    let request = ehttp::Request::post(url, body.into_bytes());
    ehttp::fetch(request, move |result| {
        let store_body = |text: &str| {
            if let Some(win) = web_sys::window() {
                let _ = win
                    .session_storage()
                    .ok()
                    .flatten()
                    .and_then(|s| s.set_item("wm_playback_window", text).ok());
            }
        };
        match result {
            Ok(resp) => {
                let text = resp.text().unwrap_or_default();
                if resp.ok {
                    store_body(&text);
                } else {
                    if text.contains("\"message\"") {
                        store_body(&text);
                    } else {
                        store_body(&format!(
                            "{{\"status\":\"error\",\"message\":\"playback_window HTTP {}: {}\"}}",
                            resp.status,
                            text.replace('\\', "\\\\").replace('"', "\\\"")
                        ));
                    }
                    re_log::error!(
                        "playback_window failed for {mcap_owned} (HTTP {}): {text}",
                        resp.status
                    );
                }
            }
            Err(err) => {
                store_body(&format!(
                    "{{\"status\":\"error\",\"message\":\"playback_window request failed: {}\"}}",
                    err.replace('\\', "\\\\").replace('"', "\\\"")
                ));
                re_log::error!("playback_window request failed for {mcap_owned}: {err}");
            }
        }
        egui_ctx.request_repaint();
    });
}

pub fn take_playback_window_json() -> Option<String> {
    let win = web_sys::window()?;
    let store = win.session_storage().ok()??;
    let v = store.get_item("wm_playback_window").ok()??;
    let _ = store.remove_item("wm_playback_window");
    Some(v)
}

/// Host replay stream, independent of the currently selected recording.
pub fn playback_proxy_url() -> Result<String, String> {
    let window = web_sys::window().ok_or("Browser window unavailable")?;
    js_sys::Reflect::get(&window, &"__web_monitor_proxy_url".into())
        .ok()
        .and_then(|value| value.as_string())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            "Playback connection configuration is missing. Reload the viewer page.".into()
        })
}

/// Ask host for MCAP summary channel topics (`POST /api/mcap_topics`).
pub fn request_host_mcap_topics(path: &str, egui_ctx: egui::Context) {
    let path_owned = path.to_owned();
    let origin = web_sys::window()
        .and_then(|w| w.location().origin().ok())
        .unwrap_or_else(|| "http://127.0.0.1:9090".into());
    let url = format!("{origin}/api/mcap_topics");
    re_log::info!("Requesting host mcap_topics for {path_owned}");
    let request = ehttp::Request::post(url, path_owned.clone().into_bytes());
    ehttp::fetch(request, move |result| {
        match result {
            Ok(resp) if resp.ok => {
                let body = resp.text().unwrap_or("{}");
                let mut value = serde_json::from_str::<serde_json::Value>(body).unwrap_or_else(
                    |e| serde_json::json!({"error":format!("Invalid source response: {e}")}),
                );
                value["requested_path"] = serde_json::json!(path_owned);
                let body = value.to_string();
                re_log::debug!("mcap_topics response: {body}");
                if let Some(win) = web_sys::window() {
                    let _ = win
                        .session_storage()
                        .ok()
                        .flatten()
                        .and_then(|s| s.set_item("wm_mcap_topics", &body).ok());
                }
            }
            Ok(resp) => {
                let body = resp.text().unwrap_or_default();
                store_source_error(
                    &path_owned,
                    &format!("Source lookup failed (HTTP {}): {body}", resp.status),
                );
                re_log::warn!(
                    "mcap_topics failed for {path_owned} (HTTP {}): {body}",
                    resp.status
                );
            }
            Err(err) => {
                store_source_error(&path_owned, &format!("Source lookup failed: {err}"));
                re_log::warn!("mcap_topics request failed for {path_owned}: {err}");
            }
        }
        egui_ctx.request_repaint();
    });
}

fn store_source_error(path: &str, error: &str) {
    if let Some(storage) = web_sys::window().and_then(|w| w.session_storage().ok().flatten()) {
        let _ = storage.set_item(
            "wm_mcap_topics",
            &serde_json::json!({"requested_path":path,"error":error}).to_string(),
        );
    }
}

/// Per-tab persistence, independent of server-wide caches and other browsers.
pub fn playback_bookmark() -> Result<Option<String>, String> {
    let storage = web_sys::window()
        .ok_or("No browser window")?
        .session_storage()
        .map_err(|e| format!("Session storage unavailable: {e:?}"))?
        .ok_or("Session storage unavailable")?;
    storage
        .get_item("wm_playback_bookmark_v1")
        .map_err(|e| format!("Cannot read playback session: {e:?}"))
}

pub fn save_playback_bookmark(value: &str) -> Result<(), String> {
    let storage = web_sys::window()
        .ok_or("No browser window")?
        .session_storage()
        .map_err(|e| format!("Session storage unavailable: {e:?}"))?
        .ok_or("Session storage unavailable")?;
    storage
        .set_item("wm_playback_bookmark_v1", value)
        .map_err(|e| format!("Cannot save playback session: {e:?}"))
}

pub fn clear_playback_bookmark() -> Result<(), String> {
    let storage = web_sys::window()
        .ok_or("Browser window unavailable")?
        .session_storage()
        .map_err(|_| "Cannot access playback session storage")?
        .ok_or("Playback session storage unavailable")?;
    storage
        .remove_item("wm_playback_bookmark_v1")
        .map_err(|_| "Cannot clear previous playback session".into())
}

pub fn take_mcap_topics_json() -> Option<String> {
    let win = web_sys::window()?;
    let store = win.session_storage().ok()??;
    let v = store.get_item("wm_mcap_topics").ok()??;
    let _ = store.remove_item("wm_mcap_topics");
    Some(v)
}

/// Ask host to convert Apollo `.record` → semantic MCAP (cached). Returns via callback JSON.
pub fn request_host_convert_record(path: &str, egui_ctx: egui::Context) {
    request_host_convert_record_with_map(path, "", egui_ctx);
}

pub fn request_host_convert_record_with_map(path: &str, map: &str, egui_ctx: egui::Context) {
    let path_owned = path.to_owned();
    let origin = web_sys::window()
        .and_then(|w| w.location().origin().ok())
        .unwrap_or_else(|| "http://127.0.0.1:9090".into());
    let url = format!("{origin}/api/convert_record");
    re_log::info!("Requesting host convert_record for {path_owned}");
    let body = serde_json::json!({"path":path,"map":map}).to_string();
    let request = ehttp::Request::post(url, body.into_bytes());
    ehttp::fetch(request, move |result| {
        match result {
            Ok(resp) if resp.ok => {
                let body = resp.text().unwrap_or("{}");
                re_log::debug!("convert_record response: {body}");
                if let Some(win) = web_sys::window() {
                    let _ = win
                        .session_storage()
                        .ok()
                        .flatten()
                        .and_then(|s| s.set_item("wm_convert_status", body).ok());
                }
            }
            Ok(resp) => {
                let body = resp.text().unwrap_or_default();
                if let Some(win) = web_sys::window() {
                    let _ = win
                        .session_storage()
                        .ok()
                        .flatten()
                        .and_then(|s| s.set_item("wm_convert_status", body).ok());
                }
                re_log::error!(
                    "Host convert failed for {path_owned} (HTTP {}): {body}",
                    resp.status
                );
            }
            Err(err) => {
                re_log::error!("Host convert request failed for {path_owned}: {err}");
                if let Some(win) = web_sys::window() {
                    let body = serde_json::json!({"status":"error","message":format!("Conversion request failed: {err}")}).to_string();
                    let _ = win
                        .session_storage()
                        .ok()
                        .flatten()
                        .and_then(|s| s.set_item("wm_convert_status", &body).ok());
                }
            }
        }
        egui_ctx.request_repaint();
    });
}

pub fn poll_host_convert_status(job_id: &str, egui_ctx: egui::Context) {
    let job = job_id.to_owned();
    let origin = web_sys::window()
        .and_then(|w| w.location().origin().ok())
        .unwrap_or_else(|| "http://127.0.0.1:9090".into());
    let url = format!("{origin}/api/convert_record?job_id={job}");
    let request = ehttp::Request::get(url);
    ehttp::fetch(request, move |result| {
        if let Ok(resp) = result {
            if resp.ok {
                let body = resp.text().unwrap_or("{}");
                if let Some(win) = web_sys::window() {
                    let _ = win
                        .session_storage()
                        .ok()
                        .flatten()
                        .and_then(|s| s.set_item("wm_convert_status", body).ok());
                }
            }
        }
        egui_ctx.request_repaint();
    });
}

pub fn take_convert_status_json() -> Option<String> {
    let win = web_sys::window()?;
    let store = win.session_storage().ok()??;
    let v = store.get_item("wm_convert_status").ok()??;
    let _ = store.remove_item("wm_convert_status");
    Some(v)
}

pub fn is_apollo_record_path(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    lower.contains(".record") && !lower.ends_with(".rrd") && !lower.ends_with(".rbl")
}

/// Open the **native** browser file picker immediately (no intermediate Ok dialog).
///
/// Must be called from a user-gesture handler (e.g. button `clicked()`), otherwise
/// browsers will block `input.click()`.
///
/// Selected recordings are opened on the **host** via `/api/open_local` (by file name /
/// absolute path). The browser never loads multi‑hundred‑MB bags into WASM memory.
pub fn pick_local_recording_files(
    _command_sender: re_viewer_context::CommandSender,
    egui_ctx: egui::Context,
    map: String,
) {
    use wasm_bindgen::closure::Closure;
    use web_sys::HtmlInputElement;

    let window = match web_sys::window() {
        Some(w) => w,
        None => {
            re_log::error!("No window for file picker");
            return;
        }
    };
    let document = match window.document() {
        Some(d) => d,
        None => {
            re_log::error!("No document for file picker");
            return;
        }
    };

    let Ok(input) = document.create_element("input") else {
        return;
    };
    let Ok(input) = input.dyn_into::<HtmlInputElement>() else {
        return;
    };

    input.set_type("file");
    // One open action must produce exactly one convert/import stream.
    // Concurrent segment selections race through the shared convert status slot
    // and can attach several large MCAP streams to the same viewer.
    input.set_multiple(false);
    // Do NOT set `accept` to `.record` — Apollo Cyber bags are named like
    // `….record.00000.…` (extension is a timestamp), so browsers hide them under
    // "Custom files". Show all files; validate names in the change handler.
    let _ = input.remove_attribute("accept");
    let _ = input.set_attribute("style", "display:none");
    let _ = input.set_attribute("hidden", "true");

    let input_for_handler = input.clone();
    let on_change = Closure::wrap(Box::new(move |_event: web_sys::Event| {
        let Some(files) = input_for_handler.files() else {
            return;
        };
        let ctx = egui_ctx.clone();
        let len = files.length();
        for i in 0..len {
            let Some(file) = files.item(i) else {
                continue;
            };
            let name = file.name();
            let lower = name.to_ascii_lowercase();
            let is_record =
                lower.contains(".record") && !lower.ends_with(".rrd") && !lower.ends_with(".rbl");
            if !(is_record
                || lower.ends_with(".rrd")
                || lower.ends_with(".rbl")
                || lower.ends_with(".mcap"))
            {
                re_log::error!(
                    "Rejected {name}: unsupported type (use .record / .rrd / .rbl / .mcap)."
                );
                continue;
            }
            let size_mib = file.size() / (1024.0 * 1024.0);
            if is_record {
                re_log::info!(
                    "Browse selected Apollo record {name} ({size_mib:.1} MiB) — host convert → MCAP"
                );
                request_host_convert_record_with_map(&name, &map, ctx.clone());
            } else if lower.ends_with(".mcap") {
                // Windowed playback: only fetch topic list; Panel checkboxes drive data load.
                re_log::info!(
                    "Browse selected {name} ({size_mib:.1} MiB) — windowed MCAP (no full stream)"
                );
                if let Some(win) = web_sys::window() {
                    let _ = win.session_storage().ok().flatten().and_then(|s| {
                        s.set_item("wm_pending_mcap", &name).ok();
                        s.set_item(
                            "wm_open_local_status",
                            &format!("Preparing windowed playback for {name}…"),
                        )
                        .ok()
                    });
                }
                request_host_mcap_topics(&name, ctx.clone());
            } else {
                re_log::info!(
                    "Browse selected {name} ({size_mib:.1} MiB) — host stream (no browser load)"
                );
                request_host_open_local(&name, ctx.clone());
            }
        }
        ctx.request_repaint();
    }) as Box<dyn FnMut(_)>);

    if input
        .add_event_listener_with_callback("change", on_change.as_ref().unchecked_ref())
        .is_err()
    {
        re_log::error!("Failed to attach file input change listener");
        return;
    }
    on_change.forget();

    if let Some(body) = document.body() {
        let _ = body.append_child(&input);
    }

    // Same user-gesture stack as the button click → OS file browser, no Ok modal.
    input.click();
}
