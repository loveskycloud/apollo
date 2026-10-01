//! Bounded latest-state delivery: slow subscribers coalesce task updates, never lose final states.
use std::io::Write as _;
use std::time::Duration;

use parking_lot::{Condvar, Mutex};
use serde_json::{Value, json};

#[derive(Default)]
struct State {
    revision: u64,
    generation: u64,
    jobs: Vec<(u64, Value)>,
    error: Option<String>,
}

/// Shared task snapshots fed by the simulation worker's event pipe.
#[derive(Default)]
pub struct SimulationEvents {
    state: Mutex<State>,
    changed: Condvar,
}

impl SimulationEvents {
    /// Publish a worker event, or expose a broken worker connection to subscribers.
    pub fn publish(&self, event: Result<String, String>) {
        let event = event.and_then(|text| {
            let value: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
            if let Some(error) = value["error"].as_str() {
                return Err(error.to_owned());
            }
            let jobs = value["jobs"].as_array().ok_or("Missing simulation jobs")?;
            if !value["snapshot"].is_boolean()
                || jobs.iter().any(|job| job["id"].as_str().is_none())
            {
                return Err("Invalid simulation task event".into());
            }
            Ok(value)
        });
        let mut state = self.state.lock();
        state.revision += 1;
        let revision = state.revision;
        match event {
            Ok(event) => {
                if event["snapshot"] == true {
                    state.generation += 1;
                    state.jobs.clear();
                }
                for job in event["jobs"].as_array().expect("validated jobs") {
                    if let Some(existing) =
                        state.jobs.iter_mut().find(|(_, j)| j["id"] == job["id"])
                    {
                        *existing = (revision, job.clone());
                    } else {
                        state.jobs.push((revision, job.clone()));
                    }
                }
                state.error = None;
            }
            Err(error) => state.error = Some(error),
        }
        self.changed.notify_all();
    }

    fn next(&self, cursor: &mut (u64, u64), timeout: Duration) -> Option<Value> {
        let mut state = self.state.lock();
        self.changed
            .wait_while_for(&mut state, |state| state.revision == cursor.0, timeout);
        if state.revision == cursor.0 {
            return None;
        }
        if let Some(error) = &state.error {
            return Some(json!({"status":"error", "message":error}));
        }
        let snapshot = cursor.1 != state.generation;
        let jobs: Vec<_> = state
            .jobs
            .iter()
            .filter(|(revision, _)| snapshot || *revision > cursor.0)
            .map(|(_, job)| job)
            .collect();
        *cursor = (state.revision, state.generation);
        Some(json!({"status":"ok", "snapshot":snapshot, "jobs":jobs}))
    }

    pub(crate) fn serve(&self, request: tiny_http::Request) -> std::io::Result<()> {
        // A dedicated connection thread writes each event immediately. Response<Read>
        // buffering would delay small events until its output buffer fills.
        let mut writer = request.into_writer();
        writer.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nX-Accel-Buffering: no\r\nConnection: close\r\n\r\nretry: 3000\n\n")?;
        writer.flush()?;
        let mut cursor = (0, 0);
        loop {
            let event = self.next(&mut cursor, Duration::from_secs(15));
            if let Some(event) = &event {
                writeln!(writer, "data: {event}\n")?;
            } else {
                writer.write_all(b": heartbeat\n\n")?;
            }
            writer.flush()?;
            if event.is_some_and(|event| event["status"] == "error") {
                return Ok(());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subscribers_receive_snapshot_then_only_changed_jobs_and_reconnect_snapshot() {
        let events = SimulationEvents::default();
        events.publish(Ok(json!({"snapshot":true,"jobs":[{"id":"a","stage":"queued"},{"id":"b","stage":"queued"}]}).to_string()));
        let mut cursor = (0, 0);
        let first = events.next(&mut cursor, Duration::ZERO).unwrap();
        assert_eq!(first["snapshot"], true);
        assert_eq!(first["jobs"].as_array().unwrap().len(), 2);
        assert!(events.next(&mut cursor, Duration::ZERO).is_none());
        for stage in ["simulation_running", "completed"] {
            events.publish(Ok(
                json!({"snapshot":false,"jobs":[{"id":"a","stage":stage}]}).to_string(),
            ));
        }
        let delta = events.next(&mut cursor, Duration::ZERO).unwrap();
        assert_eq!(delta["snapshot"], false);
        assert_eq!(delta["jobs"], json!([{"id":"a","stage":"completed"}]));
        let reconnect = events.next(&mut (0, 0), Duration::ZERO).unwrap();
        assert_eq!(reconnect["jobs"].as_array().unwrap().len(), 2);
        events.publish(Err("worker exited".into()));
        assert_eq!(
            events.next(&mut cursor, Duration::ZERO).unwrap()["status"],
            "error"
        );
        events.publish(Ok(json!({"snapshot":true,"jobs":[]}).to_string()));
        let restarted = events.next(&mut cursor, Duration::ZERO).unwrap();
        assert_eq!(restarted["snapshot"], true);
        assert_eq!(restarted["jobs"], json!([]));
    }
}
