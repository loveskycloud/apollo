//! Browser subscription lifetime is separate from command replies and editable state.
use super::State;
use serde_json::Value;
use wasm_bindgen::{JsCast as _, closure::Closure};

struct Subscription {
    source: web_sys::EventSource,
    _message: Closure<dyn FnMut(web_sys::MessageEvent)>,
    _error: Closure<dyn FnMut(web_sys::Event)>,
}

impl Drop for Subscription {
    fn drop(&mut self) {
        self.source.set_onmessage(None);
        self.source.set_onerror(None);
        self.source.close();
    }
}

thread_local! {
    static SUBSCRIPTION: std::cell::RefCell<Option<Subscription>> = const { std::cell::RefCell::new(None) };
}

fn update(ctx: &egui::Context, change: impl FnOnce(&mut State)) {
    ctx.data_mut(|data| {
        let id = egui::Id::new("ad_sim_runtime");
        let mut state = data.get_temp::<State>(id).unwrap_or_default();
        change(&mut state);
        data.insert_temp(id, state);
    });
    ctx.request_repaint();
}

pub(super) fn set_open(ctx: &egui::Context, open: bool) {
    SUBSCRIPTION.with_borrow_mut(|subscription| {
        if !open {
            *subscription = None;
        } else if subscription.is_none() {
            match subscribe(ctx) {
                Ok(value) => *subscription = Some(value),
                Err(error) => update(ctx, |state| state.stream_error = Some(error)),
            }
        }
    });
}

fn subscribe(ctx: &egui::Context) -> Result<Subscription, String> {
    let source = web_sys::EventSource::new("/api/sim/events")
        .map_err(|error| format!("Cannot subscribe to simulation tasks: {error:?}"))?;
    let message_ctx = ctx.clone();
    let message = Closure::new(move |event: web_sys::MessageEvent| {
        let result = event
            .data()
            .as_string()
            .ok_or_else(|| "Invalid simulation event payload".to_owned())
            .and_then(|text| {
                serde_json::from_str::<Value>(&text).map_err(|error| error.to_string())
            });
        update(&message_ctx, |state| match result {
            Ok(value) if value["status"] == "ok" => {
                match (value["jobs"].as_array(), value["snapshot"].as_bool()) {
                    (Some(jobs), Some(snapshot))
                        if jobs.iter().all(|job| job["id"].is_string()) =>
                    {
                        state.apply_jobs(jobs, snapshot);
                        state.stream_error = None;
                    }
                    _ => state.stream_error = Some("Invalid simulation task update".into()),
                }
            }
            Ok(value) => {
                state.stream_error =
                    Some(format!("Simulation updates failed: {}", value["message"]));
            }
            Err(error) => state.stream_error = Some(format!("Invalid simulation event: {error}")),
        });
    });
    let error_ctx = ctx.clone();
    let error = Closure::new(move |_: web_sys::Event| {
        update(&error_ctx, |state| {
            if state.stream_error.is_none() {
                state.stream_error = Some("Task updates disconnected; reconnecting…".into());
            }
        });
    });
    source.set_onmessage(Some(message.as_ref().unchecked_ref()));
    source.set_onerror(Some(error.as_ref().unchecked_ref()));
    Ok(Subscription {
        source,
        _message: message,
        _error: error,
    })
}
