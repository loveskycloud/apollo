//! Bounded browser chunks; open the first Cyber chunk before accepting the rest.
use super::convert_record::ConvertManager;
use parking_lot::Mutex;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    io::{Read, Seek, SeekFrom},
    path::PathBuf,
    sync::Arc,
};
pub const CHUNK_BYTES: u64 = 2 * 1024 * 1024;

struct Session {
    path: PathBuf,
    map: Option<PathBuf>,
    total: u64,
    received: u64,
    scan: u64,
    is_record: bool,
    full_range: Option<Value>,
    preview_started: bool,
    preview: Option<Value>,
    complete: Option<Value>,
    error: Option<String>,
}
pub struct BrowserRecords {
    sessions: Mutex<HashMap<String, Arc<Mutex<Session>>>>,
    converter: Arc<ConvertManager>,
    tool: PathBuf,
    root: PathBuf,
}
impl BrowserRecords {
    pub fn new(converter: Arc<ConvertManager>, tool: PathBuf, root: PathBuf) -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            converter,
            tool,
            root,
        }
    }
    pub fn request(&self, query: &str, reader: &mut dyn Read) -> Result<String, String> {
        let args: HashMap<_, _> = query.split('&').filter_map(|v| v.split_once('=')).collect();
        let action = *args.get("action").ok_or("Missing browser record action")?;
        if action == "start" {
            let mut body = String::new();
            reader
                .take(16385)
                .read_to_string(&mut body)
                .map_err(|e| e.to_string())?;
            if body.len() > 16384 {
                return Err("File metadata too large".into());
            }
            let v: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
            let name = v["name"].as_str().ok_or("Missing filename")?;
            if name.is_empty() || name.contains(['/', '\\', '\0']) || name == "." || name == ".." {
                return Err("Invalid filename".into());
            }
            let lower = name.to_ascii_lowercase();
            let is_record =
                lower.contains(".record") && !lower.ends_with(".rrd") && !lower.ends_with(".rbl");
            if !(is_record
                || lower.ends_with(".mcap")
                || lower.ends_with(".rrd")
                || lower.ends_with(".rbl"))
            {
                return Err("Unsupported file type".into());
            }
            let total = v["size"]
                .as_u64()
                .filter(|s| *s > 0)
                .ok_or("Empty or invalid file size")?;
            let map = v["map"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(PathBuf::from);
            let id = re_chunk::RowId::new().to_string();
            let dir = self.root.join(&id);
            std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
            let path = dir.join(name);
            std::fs::File::create(&path).map_err(|e| e.to_string())?;
            let session = Arc::new(Mutex::new(Session {
                path,
                map,
                total,
                received: 0,
                scan: 2064,
                is_record,
                full_range: None,
                preview_started: false,
                preview: None,
                complete: None,
                error: None,
            }));
            self.sessions.lock().insert(id.clone(), session);
            return Ok(json!({"status":"ok","id":id,"chunk_bytes":CHUNK_BYTES}).to_string());
        }
        let id = *args.get("id").ok_or("Missing browser record ID")?;
        let shared = self
            .sessions
            .lock()
            .get(id)
            .cloned()
            .ok_or("Unknown browser record")?;
        let mut s = shared.lock();
        if action == "cancel" {
            s.error = Some("Browser file reading cancelled or interrupted".into());
            return Ok(json!({"status":"cancelled"}).to_string());
        }
        if let Some(error) = &s.error {
            return Err(error.clone());
        }
        if action == "chunk" {
            let offset: u64 = args
                .get("offset")
                .ok_or("Missing offset")?
                .parse()
                .map_err(|_| "Invalid offset")?;
            if offset != s.received {
                return Err(format!("Expected offset {}, got {offset}", s.received));
            }
            if s.received == s.total {
                return Err("File is already complete".into());
            }
            let max = CHUNK_BYTES.min(s.total - s.received);
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(&s.path)
                .map_err(|e| e.to_string())?;
            let n =
                std::io::copy(&mut reader.take(max + 1), &mut file).map_err(|e| e.to_string())?;
            if n == 0 || n > max {
                s.error = Some("Invalid browser chunk length".into());
                return Err(s.error.clone().expect("just set"));
            }
            s.received += n;
            if s.is_record && s.received >= 2064 && s.full_range.is_none() {
                let output = std::process::Command::new(&self.tool).arg("header").arg(&s.path)
                    .output().map_err(|e| e.to_string())?;
                if !output.status.success() {
                    let error = format!("Cannot read Cyber header: {}", String::from_utf8_lossy(&output.stderr));
                    s.error = Some(error.clone());
                    return Err(error);
                }
                s.full_range = Some(serde_json::from_slice(&output.stdout).map_err(|e| e.to_string())?);
            }

            if s.is_record && !s.preview_started {
                match first_chunk_ready(&mut s) {
                    Ok(true) => {
                        s.preview_started = true;
                        let path = s.path.clone();
                        let map = s.map.clone();
                        let tool = self.tool.clone();
                        let manager = Arc::clone(&self.converter);
                        let destination = path
                            .parent()
                            .ok_or("Invalid session path")?
                            .join("preview.record");
                        let state = Arc::clone(&shared);
                        std::thread::Builder::new()
                            .name("browser-record-preview".into())
                            .spawn(move || {
                                let result = std::process::Command::new(tool)
                                    .arg("prefix")
                                    .arg("-o")
                                    .arg(&destination)
                                    .arg(path)
                                    .output()
                                    .map_err(|e| e.to_string())
                                    .and_then(|output| {
                                        if !output.status.success() {
                                            return Err(format!(
                                                "Cannot parse first Cyber chunk: {}",
                                                String::from_utf8_lossy(&output.stderr)
                                            ));
                                        }
                                        manager.start_or_cached(destination, map)
                                    })
                                    .and_then(|body| {
                                        serde_json::from_str::<Value>(&body)
                                            .map_err(|e| e.to_string())
                                    });
                                let mut state = state.lock();
                                match result {
                                    Ok(job) => state.preview = Some(job),
                                    Err(error) => state.error = Some(error),
                                }
                            })
                            .map_err(|e| e.to_string())?;
                    }
                    Ok(false) => {}
                    Err(error) => {
                        s.error = Some(error.clone());
                        return Err(error);
                    }
                }
            }
        } else if action != "status" {
            return Err("Unknown browser record action".into());
        }
        refresh_job(&self.converter, &mut s.preview)?;
        if s.received == s.total && s.complete.is_none() {
            if s.is_record {
                s.complete = Some(
                    serde_json::from_str(
                        &self
                            .converter
                            .start_or_cached(s.path.clone(), s.map.clone())?,
                    )
                    .map_err(|e| e.to_string())?,
                );
            } else {
                s.complete = Some(json!({"status":"done","output_path":s.path,"direct":true}));
            }
        }
        refresh_job(&self.converter, &mut s.complete)?;
        Ok(json!({
            "status":"ok","id":id,"received":s.received,"total":s.total,
            "source":s.path,"full_range":s.full_range,"preview_started":s.preview_started,"preview":s.preview,"complete":s.complete
        }).to_string())
    }
}
fn refresh_job(manager: &Arc<ConvertManager>, job: &mut Option<Value>) -> Result<(), String> {
    if let Some(value) = job
        && let Some(id) = value["job_id"].as_str()
    {
        *value = serde_json::from_str(&manager.status_json(id)?).map_err(|e| e.to_string())?;
        if value["status"] == "error" {
            return Err(value["message"]
                .as_str()
                .unwrap_or("Record conversion failed")
                .to_owned());
        }
    }
    Ok(())
}
// Section framing only. Cyber RecordFileReader handles protobuf and message decoding.
fn first_chunk_ready(s: &mut Session) -> Result<bool, String> {
    let mut file = std::fs::File::open(&s.path).map_err(|e| e.to_string())?;
    if s.received >= 16 {
        let mut header = [0u8; 16];
        file.read_exact(&mut header).map_err(|e| e.to_string())?;
        if i32::from_le_bytes(header[..4].try_into().expect("4 bytes")) != 0 {
            return Err("Not an Apollo Cyber record header".into());
        }
    }
    while s.scan + 16 <= s.received {
        file.seek(SeekFrom::Start(s.scan))
            .map_err(|e| e.to_string())?;
        let mut header = [0u8; 16];
        file.read_exact(&mut header).map_err(|e| e.to_string())?;
        let kind = i32::from_le_bytes(header[..4].try_into().expect("4 bytes"));
        let length = i64::from_le_bytes(header[8..].try_into().expect("8 bytes"));
        if length <= 0 || !matches!(kind, 1..=4) {
            return Err("Invalid Cyber section header".into());
        }
        let end = s
            .scan
            .checked_add(16)
            .and_then(|v| v.checked_add(length as u64))
            .ok_or("Cyber section overflow")?;
        if end > s.total {
            return Err("Truncated Cyber section".into());
        }
        if end > s.received {
            return Ok(false);
        }
        s.scan = end;
        if kind == 2 {
            return Ok(true);
        }
        if kind == 3 {
            return Err("Record has no message chunk".into());
        }
    }
    if s.received == s.total {
        return Err("Record has no complete message chunk".into());
    }
    Ok(false)
}
