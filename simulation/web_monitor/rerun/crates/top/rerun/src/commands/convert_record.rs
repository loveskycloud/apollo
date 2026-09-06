//! Host-side Apollo `.record` → semantic MCAP conversion jobs for web_monitor.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use parking_lot::Mutex;

const CONVERTER_VERSION: &str = "semantic-mcap-v3";

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
        format!(
            r#"{{"job_id":"{}","status":"{}","progress":{:.4},"message":"{}","source":"{}","output_path":{},"error":{}}}"#,
            escape(&self.job_id),
            escape(&self.status),
            self.progress,
            escape(&self.message),
            escape(&self.source.display().to_string()),
            output,
            error,
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
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
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
        Ok(job.to_json())
    }

    pub fn start_or_cached(self: &Arc<Self>, source: PathBuf) -> Result<String, String> {
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

        let cache_key = cache_key_for(&source)?;
        let cache_dir = cache_dir();
        std::fs::create_dir_all(&cache_dir)
            .map_err(|e| format!("create cache dir {}: {e}", cache_dir.display()))?;
        let output = cache_dir.join(format!("{cache_key}.mcap"));
        let progress_file = cache_dir.join(format!("{cache_key}.progress.json"));

        if output.is_file() && output.metadata().map(|m| m.len() > 0).unwrap_or(false) {
            let job_id = format!("cached-{cache_key}");
            let job = ConvertJob {
                job_id: job_id.clone(),
                status: "ready".into(),
                progress: 1.0,
                message: "Using cached MCAP".into(),
                source: source.clone(),
                output: Some(output),
                progress_file,
                error: None,
            };
            let json = job.to_json();
            self.jobs.lock().insert(job_id.clone(), job);
            self.by_cache_key.lock().insert(cache_key, job_id);
            return Ok(json);
        }

        if let Some(existing_id) = self.by_cache_key.lock().get(&cache_key).cloned() {
            if let Some(job) = self.jobs.lock().get_mut(&existing_id) {
                refresh_from_progress_file(job);
                if matches!(
                    job.status.as_str(),
                    "running" | "queued" | "done" | "ready"
                ) {
                    return Ok(job.to_json());
                }
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
                run_converter(mgr, job_id_spawn, source, output, progress_file);
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

fn cache_dir() -> PathBuf {
    if let Ok(p) = std::env::var("WEB_MONITOR_CONVERT_CACHE") {
        return PathBuf::from(p);
    }
    PathBuf::from("/apollo_workspace/data/bag/.wm_mcap_cache")
}

fn converter_script() -> PathBuf {
    if let Ok(p) = std::env::var("WEB_MONITOR_RECORD_TO_MCAP") {
        return PathBuf::from(p);
    }
    PathBuf::from(
        "/apollo_workspace/tools/apollo_record_tools/apollo_record_to_semantic_mcap.py",
    )
}

fn record_tool() -> PathBuf {
    if let Ok(p) = std::env::var("WEB_MONITOR_RECORD_TOOL") {
        return PathBuf::from(p);
    }
    PathBuf::from("/apollo_workspace/tools/apollo_record_tools/bin/apollo_record_tool")
}

fn cache_key_for(source: &Path) -> Result<String, String> {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let meta = std::fs::metadata(source).map_err(|e| format!("stat {}: {e}", source.display()))?;
    let mut hasher = DefaultHasher::new();
    CONVERTER_VERSION.hash(&mut hasher);
    source.hash(&mut hasher);
    meta.len().hash(&mut hasher);
    meta.modified()
        .map_err(|e| format!("mtime: {e}"))?
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
        .hash(&mut hasher);
    std::env::var("WEB_MONITOR_CONVERT_CAMERA")
        .unwrap_or_else(|_| "Front120".into())
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
    if job.status == "error" {
        job.error = Some(job.message.clone());
    }
}

fn json_str_field(text: &str, key: &str) -> Option<String> {
    let pat = format!("\"{key}\":");
    let idx = text.find(&pat)?;
    let rest = text[idx + pat.len()..].trim_start();
    let bytes = rest.as_bytes();
    if !rest.starts_with('"') {
        return None;
    }
    let mut out = String::new();
    let mut i = 1usize;
    while i < bytes.len() {
        match bytes[i] {
            b'"' => return Some(out),
            b'\\' if i + 1 < bytes.len() => {
                out.push(bytes[i + 1] as char);
                i += 2;
            }
            b => {
                out.push(b as char);
                i += 1;
            }
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

fn run_converter(
    mgr: Arc<ConvertManager>,
    job_id: String,
    source: PathBuf,
    output: PathBuf,
    progress_file: PathBuf,
) {
    {
        let mut jobs = mgr.jobs.lock();
        if let Some(job) = jobs.get_mut(&job_id) {
            job.status = "running".into();
            job.message = "Converting Apollo record → MCAP…".into();
            job.progress = 0.01;
        }
    }

    let script = converter_script();
    let tool = record_tool();
    let mut cmd = Command::new("python3");
    cmd.env("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
        .env(
            "PYTHONPATH",
            std::env::var("PYTHONPATH").unwrap_or_else(|_| "/opt/apollo/neo/python".into()),
        )
        .arg(&script)
        .arg("-o")
        .arg(&output)
        .arg("--tool")
        .arg(&tool)
        .arg("--progress-file")
        .arg(&progress_file)
        .arg("--camera")
        .arg(std::env::var("WEB_MONITOR_CONVERT_CAMERA").unwrap_or_else(|_| "Front120".into()));

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
            job.message = format!("Converted → {}", output.display());
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
