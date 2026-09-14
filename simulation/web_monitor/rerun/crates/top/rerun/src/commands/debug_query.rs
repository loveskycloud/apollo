//! Persistent, bounded protobuf debug worker. No repeated full-file subprocess per frame.
use std::io::{BufRead as _, Write as _};

pub(super) struct DebugWorker {
    child: std::process::Child,
    input: std::process::ChildStdin,
    output: std::sync::mpsc::Receiver<Result<String, String>>,
}

impl Drop for DebugWorker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl DebugWorker {
    pub fn start() -> Result<Self, String> {
        let script = std::env::var("WEB_MONITOR_DEBUG_QUERY").unwrap_or_else(|_| {
            "/apollo_workspace/tools/apollo_record_tools/mcap_debug_query.py".into()
        });
        Self::start_script(&script)
    }

    pub fn start_script(script: &str) -> Result<Self, String> {
        let mut child = std::process::Command::new("python3")
            .arg("-u")
            .arg(script)
            .env("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::inherit())
            .spawn()
            .map_err(|e| format!("Cannot start debug query worker: {e}"))?;
        let input = child.stdin.take().ok_or("Missing worker stdin")?;
        let output = child.stdout.take().ok_or("Missing worker stdout")?;
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        std::thread::Builder::new()
            .name("ad_debug_worker_stdout".into())
            .spawn(move || {
                for line in std::io::BufReader::new(output).lines() {
                    if tx.send(line.map_err(|e| e.to_string())).is_err() {
                        break;
                    }
                }
            })
            .map_err(|e| format!("Cannot start debug worker reader: {e}"))?;
        Ok(Self {
            child,
            input,
            output: rx,
        })
    }

    pub fn query(&mut self, request: &serde_json::Value) -> Result<String, String> {
        writeln!(self.input, "{request}").map_err(|e| format!("Debug worker write failed: {e}"))?;
        self.input.flush().map_err(|e| e.to_string())?;
        self.output
            .recv_timeout(std::time::Duration::from_secs(45))
            .map_err(|e| format!("Debug worker did not respond within 45s: {e}"))?
    }
}
