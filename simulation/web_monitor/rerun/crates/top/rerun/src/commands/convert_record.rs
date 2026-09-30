//! Host-side Apollo `.record` → semantic MCAP conversion jobs for web_monitor.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use parking_lot::Mutex;

const CONVERTER_VERSION: &str = "semantic-mcap-v15";

#[derive(Clone, Debug)]
pub struct ConvertJob {
    pub job_id: String,
    pub status: String, // queued | running | done | error | ready
    pub progress: f64,
    pub message: String,
    pub source: PathBuf,
    pub output: Option<PathBuf>,
    pub progress_file: PathBuf,
    pub error: Option<String>,
    /// Cyber record header `begin_time` (ns), from dump-jsonl `meta` — not recomputed.
    pub begin_ns: Option<u64>,
    /// Cyber record header `end_time` (ns), from dump-jsonl `meta` — not recomputed.
    pub end_ns: Option<u64>,
}

impl ConvertJob {
    fn to_json(&self) -> String {
        let output = self
            .output
            .as_ref()
            .map(|p| format!(r#""{}""#, escape(&p.display().to_string())))
            .unwrap_or_else(|| "null".into());
        let error = self
            .error
            .as_ref()
            .map(|e| format!(r#""{}""#, escape(e)))
            .unwrap_or_else(|| "null".into());
        let begin_ns = self
            .begin_ns
            .map(|v| v.to_string())
            .unwrap_or_else(|| "null".into());
        let end_ns = self
            .end_ns
            .map(|v| v.to_string())
            .unwrap_or_else(|| "null".into());
        format!(
            r#"{{"job_id":"{}","status":"{}","progress":{:.4},"message":"{}","source":"{}","output_path":{},"error":{},"begin_ns":{},"end_ns":{}}}"#,
            escape(&self.job_id),
            escape(&self.status),
            self.progress,
            escape(&self.message),
            escape(&self.source.display().to_string()),
            output,
            error,
            begin_ns,
            end_ns,
        )
    }
}

fn escape(s: &str) -> String {
    let mut out = String::new();
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            // Always \u-escape non-ASCII so clients never see raw multi-byte UTF-8
            // in hand-rolled JSON (avoids "…" → "Ã¢Â€Â¦" mojibake).
            c if (c as u32) < 0x20 || (c as u32) > 0x7E => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

pub struct ConvertManager {
    jobs: Mutex<HashMap<String, ConvertJob>>,
    by_cache_key: Mutex<HashMap<String, String>>,
}

impl ConvertManager {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            jobs: Mutex::new(HashMap::new()),
            by_cache_key: Mutex::new(HashMap::new()),
        })
    }

    pub fn status_json(&self, job_id: &str) -> Result<String, String> {
        if job_id.is_empty() {
            return Err("missing job_id".into());
        }
        let mut jobs = self.jobs.lock();
        let Some(job) = jobs.get_mut(job_id) else {
            return Err(format!("unknown job_id: {job_id}"));
        };
        refresh_from_progress_file(job);
        if matches!(job.status.as_str(), "done" | "ready") {
            let missing = job
                .output
                .as_ref()
                .map(|p| !cache_output_usable(p))
                .unwrap_or(true);
            if missing {
                job.status = "error".into();
                job.message = "Cached MCAP missing; reopen the .record to re-convert".into();
                job.error = Some(job.message.clone());
            }
        }
        Ok(job.to_json())
    }

    pub fn start_or_cached(
        self: &Arc<Self>,
        source: PathBuf,
        map: Option<PathBuf>,
    ) -> Result<String, String> {
        if !source.is_file() {
            return Err(format!("file not found: {}", source.display()));
        }
        let name = source
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !(name.contains(".record") && !name.ends_with(".rrd") && !name.ends_with(".rbl")) {
            return Err("expected an Apollo Cyber .record path".into());
        }

        let map = resolve_map(&source, map)?;
        let cache_key = cache_key_for(&source, map.as_deref())?;
        let cache_dir = cache_dir();
        std::fs::create_dir_all(&cache_dir)
            .map_err(|e| format!("create cache dir {}: {e}", cache_dir.display()))?;
        let output = cache_dir.join(format!("{cache_key}.mcap"));
        let progress_file = cache_dir.join(format!("{cache_key}.progress.json"));

        if cache_output_usable(&output) {
            let (mut begin_ns, mut end_ns) = read_header_times_from_progress(&progress_file);
            if begin_ns.is_none() || end_ns.is_none() {
                let (b, e) = peek_record_header_times(&source);
                if begin_ns.is_none() {
                    begin_ns = b;
                }
                if end_ns.is_none() {
                    end_ns = e;
                }
                // Persist so later ready responses stay header-sourced.
                if let (Some(b), Some(e)) = (begin_ns, end_ns) {
                    if let Ok(mut text) = std::fs::read_to_string(&progress_file) {
                        if !text.contains("\"begin_ns\"") {
                            text = text.trim_end().trim_end_matches('}').to_owned();
                            text.push_str(&format!(",\"begin_ns\":{b},\"end_ns\":{e}}}\n"));
                            let _ = std::fs::write(&progress_file, text);
                        }
                    }
                }
            }
            let job_id = format!("cached-{cache_key}");
            let job = ConvertJob {
                job_id: job_id.clone(),
                status: "ready".into(),
                progress: 1.0,
                message: "Using cached MCAP".into(),
                source: source.clone(),
                output: Some(output.clone()),
                progress_file: progress_file.clone(),
                error: None,
                begin_ns,
                end_ns,
            };
            let json = job.to_json();
            self.jobs.lock().insert(job_id.clone(), job);
            self.by_cache_key.lock().insert(cache_key, job_id);
            return Ok(json);
        }

        // MCAP gone (user deleted cache) but progress.json may still say "done".
        if progress_file.exists() {
            invalidate_missing_cache(&output, &progress_file);
        }

        // Take the id then drop the cache-key lock before locking jobs (avoid deadlock).
        let existing_id = self.by_cache_key.lock().get(&cache_key).cloned();
        if let Some(existing_id) = existing_id {
            let mut drop_stale = false;
            let early = {
                let mut jobs = self.jobs.lock();
                if let Some(job) = jobs.get_mut(&existing_id) {
                    refresh_from_progress_file(job);
                    let output_ok = job
                        .output
                        .as_ref()
                        .map(|p| cache_output_usable(p))
                        .unwrap_or(false);
                    match job.status.as_str() {
                        "running" | "queued" => Some(job.to_json()),
                        "done" | "ready" if output_ok => Some(job.to_json()),
                        "done" | "ready" => {
                            drop_stale = true;
                            None
                        }
                        _ => None,
                    }
                } else {
                    None
                }
            };
            if let Some(json) = early {
                return Ok(json);
            }
            if drop_stale {
                re_log::warn!(
                    "web_monitor convert_record: job {existing_id} done but MCAP missing; re-queue"
                );
                self.jobs.lock().remove(&existing_id);
                self.by_cache_key.lock().remove(&cache_key);
            }
        }

        let job_id = format!(
            "job-{}-{}",
            cache_key,
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or(0)
        );
        let job = ConvertJob {
            job_id: job_id.clone(),
            status: "queued".into(),
            progress: 0.0,
            message: "Queued for conversion".into(),
            source: source.clone(),
            output: Some(output.clone()),
            progress_file: progress_file.clone(),
            error: None,
            begin_ns: None,
            end_ns: None,
        };
        self.jobs.lock().insert(job_id.clone(), job.clone());
        self.by_cache_key
            .lock()
            .insert(cache_key.clone(), job_id.clone());

        let mgr = Arc::clone(self);
        let job_id_spawn = job_id.clone();
        std::thread::Builder::new()
            .name(format!("convert_record({cache_key})"))
            .spawn(move || {
                run_converter(mgr, job_id_spawn, source, output, progress_file, map);
            })
            .map_err(|e| format!("failed to spawn convert thread: {e}"))?;

        Ok(self
            .jobs
            .lock()
            .get(&job_id)
            .map(|j| j.to_json())
            .unwrap_or_else(|| job.to_json()))
    }
}

fn cache_output_usable(path: &Path) -> bool {
    path.is_file() && path.metadata().map(|m| m.len() > 0).unwrap_or(false)
}

/// Drop stale progress when the MCAP was deleted so we do not keep reporting "done".
fn invalidate_missing_cache(output: &Path, progress_file: &Path) {
    if progress_file.exists() {
        let _ = std::fs::remove_file(progress_file);
    }
    re_log::warn!(
        "web_monitor convert_record: cached MCAP missing ({}), will re-convert",
        output.display()
    );
}

fn cache_dir() -> PathBuf {
    if let Ok(p) = std::env::var("WEB_MONITOR_CONVERT_CACHE") {
        return PathBuf::from(p);
    }
    PathBuf::from("/apollo_workspace/data/bag/.wm_mcap_cache")
}

/// Apollo record ↔ MCAP tools live under simulation/ (symlink in application-core).
pub(super) fn apollo_record_tools_dir() -> PathBuf {
    if let Ok(p) = std::env::var("WEB_MONITOR_RECORD_TOOLS") {
        return PathBuf::from(p);
    }
    const CANDIDATES: &[&str] = &[
        "/apollo_workspace/modules/simulation/tools/apollo_record_tools",
        "/apollo_workspace/simulation/tools/apollo_record_tools",
        "/apollo_workspace/tools/apollo_record_tools",
    ];
    for candidate in CANDIDATES {
        let path = PathBuf::from(candidate);
        if path.is_dir() {
            return path;
        }
    }
    PathBuf::from(CANDIDATES[0])
}

fn converter_script() -> PathBuf {
    if let Ok(p) = std::env::var("WEB_MONITOR_RECORD_TO_MCAP") {
        return PathBuf::from(p);
    }
    apollo_record_tools_dir().join("apollo_record_to_semantic_mcap.py")
}

pub(super) fn record_tool() -> PathBuf {
    if let Ok(p) = std::env::var("WEB_MONITOR_RECORD_TOOL") {
        return PathBuf::from(p);
    }
    apollo_record_tools_dir().join("bin/apollo_record_tool")
}

fn resolve_map(source: &Path, explicit: Option<PathBuf>) -> Result<Option<PathBuf>, String> {
    let snapshot = source
        .parent()
        .filter(|p| {
            p.file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with("run-"))
        })
        .and_then(Path::parent)
        .filter(|p| p.join("manifest.json").is_file());
    let path = if let Some(job) = snapshot {
        Some(job.join("map"))
    } else {
        explicit.clone()
    };
    let Some(mut path) = path else {
        return Ok(None);
    };
    if path.is_dir() {
        path = path.join(if path.join("base_map.bin").is_file() {
            "base_map.bin"
        } else {
            "base_map.txt"
        });
    }
    let path = path
        .canonicalize()
        .map_err(|e| format!("HD map not found: {}: {e}", path.display()))?;
    if snapshot.is_some()
        && let Some(mut explicit) = explicit
    {
        if explicit.is_dir() {
            explicit = explicit.join(if explicit.join("base_map.bin").is_file() {
                "base_map.bin"
            } else {
                "base_map.txt"
            });
        }
        if explicit.canonicalize().map_err(|e| e.to_string())? != path {
            return Err("Simulation replay must use its own map snapshot".into());
        }
    }
    if !path.is_file() {
        return Err(format!("Not an HD map file: {}", path.display()));
    }
    Ok(Some(path))
}

fn cache_key_for(source: &Path, map: Option<&Path>) -> Result<String, String> {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let meta = std::fs::metadata(source).map_err(|e| format!("stat {}: {e}", source.display()))?;
    let mut hasher = DefaultHasher::new();
    CONVERTER_VERSION.hash(&mut hasher);
    if let Some(map) = map {
        map.hash(&mut hasher);
        // Geometry changes invalidate the cache even if file size/mtime are preserved.
        std::fs::read(map)
            .map_err(|e| format!("Read HD map {}: {e}", map.display()))?
            .hash(&mut hasher);
        if let Some(job) = source.parent().and_then(Path::parent) {
            let manifest = job.join("manifest.json");
            if manifest.is_file() {
                std::fs::read(manifest)
                    .map_err(|e| e.to_string())?
                    .hash(&mut hasher);
            }
        }
    }
    source.hash(&mut hasher);
    meta.len().hash(&mut hasher);
    meta.modified()
        .map_err(|e| format!("mtime: {e}"))?
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
        .hash(&mut hasher);
    std::env::var("WEB_MONITOR_CONVERT_CAMERA")
        .unwrap_or_else(|_| "all".into())
        .hash(&mut hasher);
    std::env::var("WEB_MONITOR_CONVERT_WORKERS")
        .unwrap_or_default()
        .hash(&mut hasher);
    std::env::var("WEB_MONITOR_CONVERT_GPU")
        .unwrap_or_else(|_| "auto".into())
        .hash(&mut hasher);
    std::env::var("WEB_MONITOR_CONVERT_BEGIN_NS")
        .unwrap_or_default()
        .hash(&mut hasher);
    std::env::var("WEB_MONITOR_CONVERT_DURATION_MS")
        .unwrap_or_default()
        .hash(&mut hasher);
    Ok(format!("{:016x}", hasher.finish()))
}

fn refresh_from_progress_file(job: &mut ConvertJob) {
    let Ok(text) = std::fs::read_to_string(&job.progress_file) else {
        return;
    };
    let text = text.trim();
    if let Some(v) = json_str_field(text, "status") {
        job.status = v;
    }
    if let Some(v) = json_f64_field(text, "progress") {
        job.progress = v;
    }
    if let Some(v) = json_str_field(text, "message") {
        job.message = v;
    }
    if let Some(v) = json_str_field(text, "output") {
        job.output = Some(PathBuf::from(v));
    }
    if let Some(v) = json_u64_field(text, "begin_ns") {
        job.begin_ns = Some(v);
    }
    if let Some(v) = json_u64_field(text, "end_ns") {
        job.end_ns = Some(v);
    }
    if job.status == "error" {
        job.error = Some(job.message.clone());
    }
}

fn read_header_times_from_progress(progress_file: &Path) -> (Option<u64>, Option<u64>) {
    let Ok(text) = std::fs::read_to_string(progress_file) else {
        return (None, None);
    };
    let text = text.trim();
    (
        json_u64_field(text, "begin_ns"),
        json_u64_field(text, "end_ns"),
    )
}

/// Read Cyber `GetHeader().begin_time/end_time` via dump-jsonl first `meta` line (fast).
fn peek_record_header_times(source: &Path) -> (Option<u64>, Option<u64>) {
    let tool = record_tool();
    if !tool.is_file() {
        return (None, None);
    }
    let mut child = match Command::new(&tool)
        .arg("dump-jsonl")
        .arg(source)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return (None, None),
    };
    let mut begin = None;
    let mut end = None;
    if let Some(stdout) = child.stdout.take() {
        use std::io::{BufRead as _, BufReader};
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        // First non-empty line should be {"op":"meta",...}
        for _ in 0..5 {
            line.clear();
            if reader.read_line(&mut line).ok().unwrap_or(0) == 0 {
                break;
            }
            let t = line.trim();
            if t.is_empty() {
                continue;
            }
            if t.contains("\"op\":\"meta\"") {
                begin = json_u64_field(t, "begin_ns");
                end = json_u64_field(t, "end_ns");
            }
            break;
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    (begin, end)
}

fn json_str_field(text: &str, key: &str) -> Option<String> {
    let pat = format!("\"{key}\":");
    let idx = text.find(&pat)?;
    let rest = text[idx + pat.len()..].trim_start();
    if !rest.starts_with('"') {
        return None;
    }
    // Iterate by Unicode scalar values — never push raw UTF-8 bytes as chars
    // (that turns "…" into mojibake like "Ã¢Â€Â¦").
    let mut out = String::new();
    let mut chars = rest[1..].chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => return Some(out),
            '\\' => {
                let esc = chars.next()?;
                match esc {
                    'n' => out.push('\n'),
                    'r' => out.push('\r'),
                    't' => out.push('\t'),
                    '"' => out.push('"'),
                    '\\' => out.push('\\'),
                    '/' => out.push('/'),
                    'u' => {
                        let hex: String = chars.by_ref().take(4).collect();
                        if hex.len() != 4 {
                            return None;
                        }
                        let cp = u32::from_str_radix(&hex, 16).ok()?;
                        out.push(char::from_u32(cp)?);
                    }
                    other => out.push(other),
                }
            }
            c => out.push(c),
        }
    }
    None
}

fn json_f64_field(text: &str, key: &str) -> Option<f64> {
    let pat = format!("\"{key}\":");
    let idx = text.find(&pat)?;
    let rest = text[idx + pat.len()..].trim_start();
    let end = rest
        .find(|c: char| c == ',' || c == '}' || c.is_whitespace())
        .unwrap_or(rest.len());
    rest[..end].parse().ok()
}

fn json_u64_field(text: &str, key: &str) -> Option<u64> {
    let pat = format!("\"{key}\":");
    let idx = text.find(&pat)?;
    let rest = text[idx + pat.len()..].trim_start();
    if rest.starts_with("null") {
        return None;
    }
    let end = rest
        .find(|c: char| c == ',' || c == '}' || c.is_whitespace())
        .unwrap_or(rest.len());
    rest[..end].parse().ok()
}

fn run_converter(
    mgr: Arc<ConvertManager>,
    job_id: String,
    source: PathBuf,
    output: PathBuf,
    progress_file: PathBuf,
    map: Option<PathBuf>,
) {
    {
        let mut jobs = mgr.jobs.lock();
        if let Some(job) = jobs.get_mut(&job_id) {
            job.status = "running".into();
            job.message = "Converting Apollo record -> MCAP...".into();
            job.progress = 0.01;
        }
    }

    let script = converter_script();
    let tool = record_tool();
    let mut cmd = Command::new("python3");
    if let Some(map) = map {
        cmd.arg(&script).arg("--map").arg(map);
    } else {
        cmd.arg(&script);
    }
    cmd.env("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
        .env(
            "PYTHONPATH",
            std::env::var("PYTHONPATH").unwrap_or_else(|_| "/opt/apollo/neo/python".into()),
        )
        .arg("-o")
        .arg(&output)
        .arg("--tool")
        .arg(&tool)
        .arg("--progress-file")
        .arg(&progress_file)
        .arg("--camera")
        .arg(std::env::var("WEB_MONITOR_CONVERT_CAMERA").unwrap_or_else(|_| "all".into()));

    if let Ok(workers) = std::env::var("WEB_MONITOR_CONVERT_WORKERS") {
        if !workers.is_empty() {
            cmd.arg("--workers").arg(workers);
        }
    }
    cmd.arg("--gpu")
        .arg(std::env::var("WEB_MONITOR_CONVERT_GPU").unwrap_or_else(|_| "auto".into()));

    if let Ok(begin) = std::env::var("WEB_MONITOR_CONVERT_BEGIN_NS") {
        cmd.arg("--begin-ns").arg(begin);
    }
    if let Ok(duration_ms) = std::env::var("WEB_MONITOR_CONVERT_DURATION_MS") {
        cmd.arg("--duration-ms").arg(duration_ms);
    }

    cmd.arg(&source)
        .stdout(Stdio::null())
        .stderr(Stdio::piped());

    re_log::info!(
        "web_monitor convert_record: starting {} → {}",
        source.display(),
        output.display()
    );

    let result = cmd.output();
    let mut jobs = mgr.jobs.lock();
    let Some(job) = jobs.get_mut(&job_id) else {
        return;
    };
    refresh_from_progress_file(job);

    match result {
        Ok(out) if out.status.success() && output.is_file() => {
            job.status = "done".into();
            job.progress = 1.0;
            job.output = Some(output.clone());
            job.message = format!("Converted -> {}", output.display());
            job.error = None;
            re_log::info!("web_monitor convert_record: done {}", output.display());
        }
        Ok(out) => {
            let err = String::from_utf8_lossy(&out.stderr).trim().to_owned();
            let err = if err.is_empty() {
                format!("converter failed with status {}", out.status)
            } else {
                err.chars().take(800).collect()
            };
            job.status = "error".into();
            job.error = Some(err.clone());
            job.message = err;
            re_log::error!("web_monitor convert_record failed: {}", job.message);
        }
        Err(err) => {
            job.status = "error".into();
            job.error = Some(err.to_string());
            job.message = err.to_string();
            re_log::error!("web_monitor convert_record spawn failed: {err}");
        }
    }
}
