//! One bounded, prefetched scalar window shared by all 3D HUDs.
use re_viewer_context::AppContext;
use serde_json::{Value, json};

type Reply = std::sync::Arc<parking_lot::Mutex<Option<Result<Value, String>>>>;
#[derive(Clone, Default)]
struct Runtime {
    source: String,
    window: Option<std::sync::Arc<Value>>,
    pending: Option<Reply>,
    requested: Option<web_time::Instant>,
    error: Option<String>,
}

pub(super) fn publish(ctx: &AppContext<'_>, mcap: &str, source_notice: &str) {
    let id = egui::Id::new("ad_dashboard_runtime");
    let mut runtime = ctx
        .egui_ctx
        .data_mut(|d| d.get_temp::<Runtime>(id))
        .unwrap_or_default();
    let clock = ctx.active_time_ctrl();
    let at = clock
        .as_ref()
        .and_then(|t| t.time_int())
        .map(|t| t.as_i64());
    let timeline = clock
        .as_ref()
        .map(|t| t.timeline_name().as_str().to_owned());
    let source = format!("{mcap}:{timeline:?}");
    if runtime.source != source {
        runtime = Runtime {
            source,
            ..Default::default()
        };
    }
    if let Some(reply) = runtime.pending.as_ref().and_then(|r| r.lock().take()) {
        runtime.pending = None;
        match reply {
            Ok(value) if value["status"] == "ok" => {
                runtime.window = Some(std::sync::Arc::new(value));
                runtime.error = None;
            }
            Ok(value) => {
                runtime.error = Some(
                    value["message"]
                        .as_str()
                        .unwrap_or("Invalid dashboard response")
                        .into(),
                )
            }
            Err(error) => runtime.error = Some(error),
        }
    }
    if runtime.pending.is_some()
        && runtime
            .requested
            .is_some_and(|t| t.elapsed().as_secs() >= 50)
    {
        runtime.pending = None;
        runtime.error = Some("Dashboard query timed out after 50 s".into());
    }
    let mut output = json!({"at_ns":at.map(|t|t.to_string()),"notice":source_notice});
    if let (Some(at), Some(timeline)) = (at, timeline)
        && !mcap.is_empty()
        && matches!(timeline.as_str(), "publish_time" | "message_time")
    {
        let range = runtime
            .window
            .as_ref()
            .and_then(|w| Some((ns(&w["begin_ns"])?, ns(&w["end_ns"])?)));
        let covered = range.is_some_and(|(b, e)| b <= at && at <= e);
        let need_window = !range.is_some_and(|(b, e)| b <= at && at < e - 2_000_000_000);
        if need_window
            && runtime.pending.is_none()
            && runtime
                .requested
                .is_none_or(|t| t.elapsed().as_millis() >= 500)
        {
            runtime.requested = Some(web_time::Instant::now());
            fetch(
                ctx,
                &mut runtime,
                json!({"mode":"dashboard_window", "mcap":mcap,"at_ns":at.to_string(),"clock":timeline}),
            );
        }
        output =
            json!({"at_ns":at.to_string(),"covered":covered,"error":runtime.error,"streams":{}});
        if covered && let Some(window) = &runtime.window {
            for name in ["chassis", "traffic"] {
                let stream = &window["streams"][name];
                let sample = stream["rows"].as_array().and_then(|rows| {
                    let end =
                        rows.partition_point(|r| ns(&r["sample_ns"]).is_some_and(|t| t <= at));
                    end.checked_sub(1).map(|i| &rows[i])
                });
                output["streams"][name] =
                    json!({"topic":stream["topic"],"notice":stream["notice"],"sample":sample});
            }
        } else {
            output["notice"] =
                "Current time is outside the dashboard buffer — awaiting data".into();
        }
    }
    ctx.egui_ctx.data_mut(|d| {
        d.insert_temp(id, runtime);
        d.insert_temp(egui::Id::new("ad_dashboard_data"), output);
        d.insert_temp(egui::Id::new("ad_dashboard_views"), json!([]));
    });
    ctx.egui_ctx
        .request_repaint_after(std::time::Duration::from_millis(100));
}

fn ns(value: &Value) -> Option<i64> {
    value.as_str()?.parse().ok()
}

#[cfg(target_arch = "wasm32")]
fn fetch(ctx: &AppContext<'_>, runtime: &mut Runtime, request: Value) {
    let Some(origin) = web_sys::window().and_then(|w| w.location().origin().ok()) else {
        runtime.error = Some("Browser origin unavailable".into());
        return;
    };
    let reply = Reply::default();
    runtime.pending = Some(reply.clone());
    let egui = ctx.egui_ctx.clone();
    ehttp::fetch(
        ehttp::Request::post(
            format!("{origin}/api/debug_query"),
            request.to_string().into_bytes(),
        ),
        move |result| {
            *reply.lock() = Some(result.and_then(|r| {
                if !r.ok {
                    return Err(format!(
                        "Dashboard HTTP {}: {}",
                        r.status,
                        r.text().unwrap_or("No response body")
                    ));
                }
                serde_json::from_slice(&r.bytes).map_err(|e| format!("Invalid dashboard JSON: {e}"))
            }));
            egui.request_repaint();
        },
    );
}
#[cfg(not(target_arch = "wasm32"))]
fn fetch(_: &AppContext<'_>, runtime: &mut Runtime, _: Value) {
    runtime.error = Some("Vehicle dashboard queries require the web viewer".into());
}
