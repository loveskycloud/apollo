//! Persistent, bounded protobuf debug worker. No repeated full-file subprocess per frame.
use std::io::{BufRead as _, Write as _};

type EventHandler = std::sync::Arc<dyn Fn(Result<String, String>) + Send + Sync>;

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
    fn apollo_record_tools_dir() -> std::path::PathBuf {
        if let Ok(p) = std::env::var("WEB_MONITOR_RECORD_TOOLS") {
            return std::path::PathBuf::from(p);
        }
        const CANDIDATES: &[&str] = &[
            "/apollo_workspace/modules/simulation/tools/apollo_record_tools",
            "/apollo_workspace/simulation/tools/apollo_record_tools",
            "/apollo_workspace/tools/apollo_record_tools",
        ];
        for candidate in CANDIDATES {
            let path = std::path::PathBuf::from(candidate);
            if path.is_dir() {
                return path;
            }
        }
        std::path::PathBuf::from(CANDIDATES[0])
    }

    pub fn start() -> Result<Self, String> {
        let script = std::env::var("WEB_MONITOR_DEBUG_QUERY").unwrap_or_else(|_| {
            Self::apollo_record_tools_dir()
                .join("mcap_debug_query.py")
                .display()
                .to_string()
        });
        Self::start_script(&script)
    }

    pub fn start_script(script: &str) -> Result<Self, String> {
        Self::start_script_with_events(script, None)
    }

    pub fn start_script_with_events(
        script: &str,
        events: Option<EventHandler>,
    ) -> Result<Self, String> {
        let mut command = std::process::Command::new("python3");
        command.arg("-u").arg(script);
        if events.is_some() {
            command.arg("--events");
        }
        let mut child = command
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
                    if let (Some(events), Ok(line)) = (&events, &line)
                        && serde_json::from_str::<serde_json::Value>(line)
                            .is_ok_and(|value| value["event"] == "simulation_jobs")
                    {
                        events(Ok(line.clone()));
                        continue;
                    }
                    if tx.send(line.map_err(|e| e.to_string())).is_err() {
                        break;
                    }
                }
                if let Some(events) = events {
                    events(Err("Simulation event service disconnected".into()));
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
