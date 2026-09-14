//! Read-only algorithm debug tools sharing the AD playback clock.
use egui::{Color32, RichText, Ui};
use re_viewer_context::{AppContext, TimeControlCommand};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Kind {
    Inspector,
    Plot,
    Watch,
    Control,
    Trajectory,
    States,
    Health,
    Profile,
}

impl Kind {
    pub const ALL: [Self; 8] = [
        Self::Inspector,
        Self::Plot,
        Self::Watch,
        Self::Control,
        Self::Trajectory,
        Self::States,
        Self::Health,
        Self::Profile,
    ];
    pub fn title(self) -> &'static str {
        match self {
            Self::Inspector => "Topic inspector",
            Self::Plot => "Signal plot",
            Self::Watch => "Value watch",
            Self::Control => "Control dashboard",
            Self::Trajectory => "Trajectory XY",
            Self::States => "State transitions",
            Self::Health => "Topic health",
            Self::Profile => "Planning profile",
        }
    }
    pub fn description(self) -> &'static str {
        match self {
            Self::Inspector => "Message fields, search, copy and message stepping",
            Self::Plot => "Numeric fields, cursor, zoom and window statistics",
            Self::Watch => "Pinned values at the current playback time",
            Self::Control => "Speed, acceleration, steering and tracking errors",
            Self::Trajectory => "Current plan vs. last 10 s of localization",
            Self::States => "Gear, driving mode, booleans and enum changes",
            Self::Health => "Topic counts, rates, age, duplicate times and gaps",
            Self::Profile => {
                "Current trajectory speed, acceleration and curvature vs. relative time"
            }
        }
    }
}

#[derive(Clone, Serialize, Deserialize)]
struct Signal {
    topic: String,
    field: String,
    label: String,
    unit: String,
}
fn signal(topic: &str, field: &str, label: &str, unit: &str) -> Signal {
    Signal {
        topic: topic.into(),
        field: field.into(),
        label: label.into(),
        unit: unit.into(),
    }
}
fn control_signals() -> Vec<Signal> {
    let c = "/apollo/control";
    vec![
        signal(
            c,
            "debug.simple_lon_debug.speed_reference",
            "Reference speed",
            "m/s",
        ),
        signal(
            "/apollo/canbus/chassis",
            "speed_mps",
            "Chassis speed",
            "m/s",
        ),
        signal(
            c,
            "debug.simple_lon_debug.acceleration_reference",
            "Reference acceleration",
            "m/s²",
        ),
        signal(
            c,
            "debug.simple_lon_debug.current_acceleration",
            "Measured acceleration",
            "m/s²",
        ),
        signal(c, "steering_target", "Steering command", "%"),
        signal(
            "/apollo/canbus/chassis",
            "steering_percentage",
            "Steering feedback",
            "%",
        ),
        signal(c, "throttle", "Throttle", "%"),
        signal(c, "brake", "Brake", "%"),
        signal(
            c,
            "debug.simple_lon_debug.station_error",
            "Station error",
            "m",
        ),
        signal(
            c,
            "debug.simple_lat_debug.lateral_error",
            "Lateral error",
            "m",
        ),
        signal(
            c,
            "debug.simple_lat_debug.heading_error",
            "Heading error",
            "rad",
        ),
        signal(c, "latency_stats.total_time_ms", "Controller runtime", "ms"),
    ]
}

type Reply = std::sync::Arc<parking_lot::Mutex<Option<Result<Value, String>>>>;
#[derive(Default)]
struct Runtime {
    response: Option<Value>,
    error: Option<String>,
    pending: Option<Reply>,
    key: String,
    source: String,
    requested_at: Option<web_time::Instant>,
    revision: u64,
    content_top_y: f32,
}

impl Runtime {
    fn initial_loading(&self) -> bool {
        self.pending.is_some() && self.response.is_none()
    }
}

#[derive(Serialize, Deserialize)]
pub(super) struct Panel {
    id: u64,
    kind: Kind,
    topic: String,
    field: String,
    filter: String,
    signals: Vec<Signal>,
    pins: Vec<String>,
    follow: bool,
    window_s: f64,
    #[serde(skip)]
    runtime: Runtime,
    #[serde(skip)]
    topic_search: String,
    #[serde(skip)]
    topic_ui: Value,
}

impl Panel {
    pub(super) fn new(kind: Kind, id: u64) -> Self {
        let topic = if kind == Kind::States {
            "/apollo/canbus/chassis"
        } else {
            "/apollo/control"
        };
        let signals = match kind {
            Kind::Control => control_signals(),
            Kind::States => vec![
                signal(topic, "driving_mode", "Driving mode", ""),
                signal(topic, "gear_location", "Gear", ""),
                signal(topic, "parking_brake", "Parking brake", ""),
            ],
            _ => vec![signal(topic, "steering_target", "steering_target", "%")],
        };
        Self {
            id,
            kind,
            topic: topic.into(),
            field: if kind == Kind::States {
                "driving_mode"
            } else {
                "steering_target"
            }
            .into(),
            filter: String::new(),
            signals,
            pins: vec![
                "speed".into(),
                "throttle".into(),
                "brake".into(),
                "steering_target".into(),
                "gear_location".into(),
            ],
            follow: true,
            window_s: 10.0,
            runtime: Runtime::default(),
            topic_search: String::new(),
            topic_ui: Value::Null,
        }
    }

    pub(super) fn show(
        &mut self,
        ctx: &AppContext<'_>,
        ui: &mut Ui,
        topics: &[String],
        mcap: &str,
        origin: Option<i64>,
        source_notice: &str,
    ) {
        let at = ctx
            .active_time_ctrl()
            .and_then(|t| t.time_int())
            .map(|t| t.as_i64());
        let clock = ctx
            .active_time_ctrl()
            .map(|t| t.timeline_name().as_str().to_owned());
        // Controls must use the viewport width. A horizontal outer scroll area
        // gives them unbounded width and pushes the topic picker off-screen.
        // Large message tables retain their own horizontal scrolling below.
        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                if mcap.is_empty()
                    || at.is_none()
                    || origin.is_none()
                    || !matches!(clock.as_deref(), Some("publish_time" | "message_time"))
                {
                    ui.colored_label(Color32::YELLOW, source_notice);
                    return;
                }
                let (at, origin) = (at.expect("checked"), origin.expect("checked"));
                let clock = clock.as_deref().expect("active time control");
                self.ui(ctx, ui, topics, mcap, at, origin, clock);
            });
        ctx.egui_ctx.data_mut(|d| {
            let key = egui::Id::new("ad_debug_panel_state");
            let mut summaries = d.get_temp::<Value>(key).unwrap_or(json!([]));
            if let Some(items) = summaries.as_array_mut() {
                items.push(self.diagnostic());
            }
            d.insert_temp(key, summaries);
        });
    }

    pub(super) fn preset(name: &str, id: u64) -> Self {
        let kind = match name {
            "trajectory" => Kind::Trajectory,
            "states" => Kind::States,
            "profile" => Kind::Profile,
            "health" => Kind::Health,
            "watch" => Kind::Watch,
            "control" => Kind::Control,
            "plot" | "speed" | "steering" | "errors" | "pedals" | "acceleration" | "heading"
            | "runtime" => Kind::Plot,
            _ => Kind::Inspector,
        };
        let mut panel = Self::new(kind, id);
        let all = control_signals();
        let indices: &[usize] = match name {
            "speed" => &[0, 1],
            "steering" => &[4, 5],
            "errors" => &[8, 9],
            "pedals" => &[6, 7],
            "acceleration" => &[2, 3],
            "heading" => &[10],
            "runtime" => &[11],
            _ => &[],
        };
        if !indices.is_empty() {
            panel.signals = indices.iter().map(|&i| all[i].clone()).collect();
        }
        if name == "planning" {
            panel.topic = "/apollo/planning".into();
        }
        panel
    }
}

impl Panel {
    fn ui(
        &mut self,
        ctx: &AppContext<'_>,
        ui: &mut Ui,
        topics: &[String],
        mcap: &str,
        at: i64,
        origin: i64,
        clock: &str,
    ) {
        self.topic_ui = json!({"available_topics":topics});
        ui.horizontal_wrapped(|ui| {
            ui.checkbox(&mut self.follow, "Follow playhead");
            if ui.button("Refresh").clicked() {
                self.runtime.key.clear();
            }
            ui.label(format!("{:.3} s  |  {clock}", (at - origin) as f64 / 1e9));
        });
        egui::CollapsingHeader::new("Topic / signal settings")
            .default_open(matches!(
                self.kind,
                Kind::Inspector | Kind::Watch | Kind::Health
            ))
            .show(ui, |ui| {
                if !matches!(
                    self.kind,
                    Kind::Control | Kind::Trajectory | Kind::Profile | Kind::Health
                ) {
                    self.topic_selector(ui, topics);
                }
                if matches!(self.kind, Kind::Plot | Kind::States) {
                    ui.horizontal_wrapped(|ui| {
                        ui.label("Field");
                        let width = (ui.available_width() - 100.0).clamp(90.0, 355.0);
                        ui.add(egui::TextEdit::singleline(&mut self.field).desired_width(width));
                        if ui.button("Add signal").clicked() && !self.field.trim().is_empty() {
                            if self.signals.len() < 24 {
                                self.signals.push(signal(
                                    &self.topic,
                                    self.field.trim(),
                                    self.field.trim(),
                                    "",
                                ));
                                self.runtime.key.clear();
                            } else {
                                self.runtime.error = Some("Maximum 24 signals per panel".into());
                            }
                        }
                    });
                    let mut remove = None;
                    for (i, s) in self.signals.iter().enumerate() {
                        ui.horizontal(|ui| {
                            if ui.small_button("×").clicked() {
                                remove = Some(i);
                            }
                            ui.label(format!("{} : {} {}", s.topic, s.field, s.unit));
                        });
                    }
                    if let Some(i) = remove {
                        self.signals.remove(i);
                        self.runtime.key.clear();
                    }
                }
                if matches!(self.kind, Kind::Plot | Kind::Control | Kind::States) {
                    ui.horizontal_wrapped(|ui| {
                        ui.label("Query window (s)");
                        if ui
                            .add(
                                egui::DragValue::new(&mut self.window_s)
                                    .range(2.0..=120.0)
                                    .speed(1.0),
                            )
                            .changed()
                        {
                            self.runtime.key.clear();
                        }
                        ui.weak("Click chart to seek; drag/scroll to inspect.");
                    });
                }
            });
        if matches!(self.kind, Kind::Inspector | Kind::Watch | Kind::States)
            && let Some(notice) = topic_selection_notice(topics, &self.topic)
        {
            // An empty edit is a selection state, not a backend query. Also
            // discard any pending reply for the previously selected topic.
            self.runtime = Runtime::default();
            self.topic_ui["notice"] = json!(notice);
            if self.topic.trim().is_empty() {
                ui.weak(&notice);
            } else {
                self.runtime.error = Some(notice.clone());
                ui.colored_label(Color32::LIGHT_RED, &notice);
            }
            return;
        }
        let mode = match self.kind {
            Kind::Inspector | Kind::Watch => "snapshot",
            Kind::Plot | Kind::Control => "series",
            Kind::States => "states",
            Kind::Trajectory => "trajectory",
            Kind::Health => "health",
            Kind::Profile => "profile",
        };
        let center = (at - origin) / 2_000_000_000 * 2_000_000_000 + origin;
        let mut request = json!({"mcap":mcap,"mode":mode,"topic":self.topic.trim(),
            "clock":clock,"at_ns":at.to_string(),"origin_ns":origin.to_string(),
            "begin_ns":(center-(self.window_s*0.5e9) as i64).max(origin).to_string(),
            "end_ns":(center+(self.window_s*0.5e9) as i64).to_string(),"signals":self.signals});
        // Keep the previous result only when refreshing the same query definition.
        // A topic/tool/signal change must not display an unrelated in-flight reply.
        let source = json!({"mcap":mcap,"clock":clock,"origin":origin,"mode":mode,
            "topic":self.topic.trim(),"signals":self.signals,"window_s":self.window_s})
        .to_string();
        if self.runtime.source != source {
            self.runtime = Runtime {
                source,
                ..Default::default()
            };
        }
        self.take_reply();
        if self.runtime.pending.is_some()
            && self
                .runtime
                .requested_at
                .is_some_and(|t| t.elapsed().as_secs() >= 50)
        {
            self.runtime.pending = None;
            self.runtime.response = None;
            self.runtime.error =
                Some("Debug query timed out after 50 s; use Refresh to retry".into());
        }
        if matches!(mode, "series" | "states") {
            request["at_ns"] = center.to_string().into();
        }
        let key = request.to_string();
        let throttle_ready = self
            .runtime
            .requested_at
            .is_none_or(|t| t.elapsed().as_millis() >= 250);
        if self.runtime.pending.is_none()
            && throttle_ready
            && self.runtime.key != key
            && (self.follow || self.runtime.key.is_empty())
        {
            self.fetch(ctx, request, key);
        }
        if self.runtime.pending.is_some() {
            ctx.egui_ctx
                .request_repaint_after(std::time::Duration::from_millis(50));
        }
        // Reserve the same status row before/during/after a query. Background
        // refreshes never insert a spinner row or displace the existing chart.
        self.refresh_status(ui, at, origin);
        if let Some(error) = &self.runtime.error {
            ui.colored_label(Color32::LIGHT_RED, error);
        }
        self.runtime.content_top_y = ui.cursor().top();
        if let Some(data) = self.runtime.response.clone() {
            if let Some(notice) = data["notice"].as_str() {
                ui.colored_label(Color32::YELLOW, notice);
            }
            match self.kind {
                Kind::Inspector | Kind::Watch => self.inspector(ctx, ui, &data),
                Kind::Plot | Kind::Control => self.plots(ctx, ui, &data, at, origin),
                Kind::States => self.states(ctx, ui, &data, origin),
                Kind::Trajectory => self.trajectory(ui, &data),
                Kind::Health => self.health(ui, &data),
                Kind::Profile => {
                    let height = (ui.available_height() / 3.0 - 14.0).clamp(90.0, 150.0);
                    for (field, unit) in [("v", "m/s"), ("a", "m/s²"), ("kappa", "1/m")] {
                        egui_plot::Plot::new((self.id, field))
                            .height(height)
                            .x_axis_label("Trajectory relative time (s)")
                            .y_axis_label(unit)
                            .show(ui, |plot| {
                                plot.line(egui_plot::Line::new(field, points(&data[field])));
                            });
                    }
                }
            }
        }
        if self.follow {
            ctx.egui_ctx
                .request_repaint_after(std::time::Duration::from_millis(250));
        }
    }

    fn topic_selector(&mut self, ui: &mut Ui, topics: &[String]) {
        ui.label("Topic");
        let input = ui.add(
            egui::TextEdit::singleline(&mut self.topic)
                .hint_text("Select below or paste an exact topic path")
                .desired_width(ui.available_width()),
        );
        self.topic_ui["input"] = json!([input.rect.center().x, input.rect.center().y]);
        if input.changed() {
            self.runtime = Runtime::default();
        }
        // Give selection its own bounded row, including in narrow docked panels.
        let picker = egui::ComboBox::from_id_salt((self.id, "topics"))
            .selected_text(format!("Browse topics ({})", topics.len()))
            .width(ui.available_width())
            .height(280.0)
            .close_behavior(egui::PopupCloseBehavior::CloseOnClickOutside)
            .show_ui(ui, |ui| {
                let search = ui.add(
                    egui::TextEdit::singleline(&mut self.topic_search)
                        .hint_text("Filter topics…")
                        .desired_width(ui.available_width()),
                );
                self.topic_ui["search"] = json!([search.rect.center().x, search.rect.center().y]);
                let filter = self.topic_search.trim().to_lowercase();
                let mut matches = 0;
                for topic in topics.iter().filter(|t| t.to_lowercase().contains(&filter)) {
                    matches += 1;
                    let row = ui.selectable_value(&mut self.topic, topic.clone(), topic);
                    self.topic_ui["rows"][topic] =
                        json!([row.rect.center().x, row.rect.center().y]);
                    if row.changed() {
                        self.runtime = Runtime::default();
                    }
                    if row.clicked() {
                        ui.close();
                    }
                }
                if topics.is_empty() {
                    ui.colored_label(Color32::YELLOW, "This recording's topic catalog is empty.");
                } else if matches == 0 {
                    ui.weak("No matching topics. Clear the filter to show all topics.");
                }
            });
        self.topic_ui["picker"] = json!([
            picker.response.rect.center().x,
            picker.response.rect.center().y
        ]);
        self.topic_ui["picker_rect"] = json!([
            picker.response.rect.left(),
            picker.response.rect.top(),
            picker.response.rect.right(),
            picker.response.rect.bottom()
        ]);
    }

    fn refresh_status(&self, ui: &mut Ui, at: i64, origin: i64) {
        ui.allocate_ui_with_layout(
            egui::vec2(ui.available_width(), 20.0),
            egui::Layout::left_to_right(egui::Align::Center),
            |ui| {
                if self.runtime.initial_loading() {
                    ui.add(egui::Spinner::new().size(14.0));
                    ui.weak("Loading current data…");
                    return;
                }
                let Some(data) = &self.runtime.response else {
                    ui.weak("No query result");
                    return;
                };
                let sample = data["sample_ns"].as_str().and_then(|t| t.parse::<i64>().ok());
                let queried = data["at_ns"].as_str().and_then(|t| t.parse::<i64>().ok());
                let mut status = if let Some(sample) = sample {
                    if sample > at {
                        format!("Previous sample: {sample} — awaiting seek result")
                    } else {
                        // Age is relative to the live cursor, not the old request's cursor.
                        format!("Sample: {sample}  |  Age: {:.1} ms", (at - sample) as f64 / 1e6)
                    }
                } else if let Some(queried) = queried {
                    let title = if matches!(self.kind, Kind::Plot | Kind::Control | Kind::States) {
                        "Window center"
                    } else {
                        "Queried at"
                    };
                    format!("{title}: {:.3} s", (queried - origin) as f64 / 1e9)
                } else {
                    "Query complete".to_owned()
                };
                let delayed = self.runtime.pending.is_some() && self.runtime.requested_at
                    .is_some_and(|t| t.elapsed().as_millis() >= 750);
                if delayed {
                    status.push_str("  |  Refresh delayed; showing previous result");
                }
                let color = if delayed || sample.is_some_and(|t| t > at) {
                    Color32::YELLOW
                } else {
                    ui.visuals().weak_text_color()
                };
                ui.add(egui::Label::new(RichText::new(status).color(color)).truncate())
                    .on_hover_text("Background queries replace the displayed result when complete. Sample age follows the live cursor; a retained result is not a new sample. Errors are shown explicitly.");
            },
        );
    }

    fn take_reply(&mut self) {
        let result = self.runtime.pending.as_ref().and_then(|p| p.lock().take());
        if let Some(result) = result {
            self.runtime.pending = None;
            match result {
                Ok(data) if data["status"] == "ok" => {
                    self.runtime.response = Some(data);
                    self.runtime.error = None;
                    self.runtime.revision += 1;
                }
                Ok(data) => {
                    self.runtime.error = Some(
                        data["message"]
                            .as_str()
                            .unwrap_or("Invalid debug response")
                            .into(),
                    );
                    self.runtime.response = None;
                }
                Err(error) => {
                    self.runtime.error = Some(error);
                    self.runtime.response = None;
                }
            }
        }
    }

    fn fetch(&mut self, ctx: &AppContext<'_>, request: Value, key: String) {
        self.runtime.key = key;
        self.runtime.requested_at = Some(web_time::Instant::now());
        #[cfg(target_arch = "wasm32")]
        {
            let Some(origin) = web_sys::window().and_then(|w| w.location().origin().ok()) else {
                self.runtime.error = Some("Browser origin unavailable".into());
                return;
            };
            let reply = Reply::default();
            self.runtime.pending = Some(reply.clone());
            let egui = ctx.egui_ctx.clone();
            ehttp::fetch(
                ehttp::Request::post(
                    format!("{origin}/api/debug_query"),
                    request.to_string().into_bytes(),
                ),
                move |result| {
                    *reply.lock() = Some(result.map_err(|e| e.to_string()).and_then(|r| {
                        serde_json::from_slice(&r.bytes)
                            .map_err(|e| format!("Invalid debug JSON (HTTP {}): {e}", r.status))
                    }));
                    egui.request_repaint();
                },
            );
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            let _ = (ctx, request);
            self.runtime.error =
                Some("Host debug tools require the web viewer on port 9090".into());
        }
    }

    fn inspector(&mut self, ctx: &AppContext<'_>, ui: &mut Ui, data: &Value) {
        ui.horizontal_wrapped(|ui| {
            for (label, key) in [
                ("Previous message", "previous_ns"),
                ("Next message", "next_ns"),
            ] {
                let time = data[key].as_str().and_then(|s| s.parse::<i64>().ok());
                if ui
                    .add_enabled(time.is_some(), egui::Button::new(label))
                    .clicked()
                {
                    seek(ctx, time.expect("enabled"));
                }
            }
            if ui.button("Copy JSON").clicked() {
                ui.ctx()
                    .copy_text(serde_json::to_string_pretty(&data["message"]).expect("JSON value"));
            }
        });
        ui.horizontal(|ui| {
            ui.label("Filter fields");
            ui.add(
                egui::TextEdit::singleline(&mut self.filter).desired_width(ui.available_width()),
            );
        });
        ui.weak(format!(
            "{}  |  {} messages  |  Pin fields for Value watch",
            data["type"], data["count"]
        ));
        let fields = data["fields"].as_array().map(Vec::as_slice).unwrap_or(&[]);
        ui.horizontal_wrapped(|ui| {
            if self.kind == Kind::Inspector && ui.button("Watch pinned fields").clicked() {
                self.kind = Kind::Watch;
                self.filter.clear();
            }
            if ui.button("Plot pinned numeric fields").clicked() {
                let signals: Vec<Signal> = fields
                    .iter()
                    .filter(|f| {
                        f["value"].is_number()
                            && f["path"]
                                .as_str()
                                .is_some_and(|p| self.pins.iter().any(|pin| pin == p))
                    })
                    .map(|f| {
                        let path = f["path"].as_str().expect("field path");
                        signal(&self.topic, path, path, "")
                    })
                    .collect();
                if signals.is_empty() || signals.len() > 24 {
                    self.runtime.error = Some("Pin between 1 and 24 numeric fields to plot".into());
                } else {
                    self.signals = signals;
                    self.kind = Kind::Plot;
                    self.runtime.key.clear();
                    self.runtime.response = None;
                }
            }
        });
        let shown: Vec<&Value> = fields
            .iter()
            .filter(|f| {
                let path = f["path"].as_str().unwrap_or("");
                path.to_lowercase().contains(&self.filter.to_lowercase())
                    && (self.kind != Kind::Watch
                        || self.pins.contains(&path.to_owned())
                        || !self.filter.is_empty())
            })
            .collect();
        ui.label(format!("{} matching fields", shown.len()));
        egui::ScrollArea::both()
            .max_height(450.0)
            .show_rows(ui, 24.0, shown.len(), |ui, range| {
                for f in &shown[range] {
                    let path = f["path"].as_str().unwrap_or("");
                    ui.horizontal(|ui| {
                        let mut pinned = self.pins.contains(&path.to_owned());
                        if ui.checkbox(&mut pinned, "").changed() {
                            if pinned {
                                self.pins.push(path.into());
                            } else {
                                self.pins.retain(|p| p != path);
                            }
                        }
                        ui.monospace(path);
                        ui.label(
                            RichText::new(f["value"].to_string())
                                .color(Color32::from_rgb(110, 220, 200)),
                        );
                    });
                }
            });
        if self.kind == Kind::Watch {
            for pin in &self.pins {
                if !fields.iter().any(|f| f["path"] == *pin) {
                    ui.colored_label(Color32::YELLOW, format!("{pin}: absent in this message"));
                }
            }
        }
    }

    fn plots(&self, ctx: &AppContext<'_>, ui: &mut Ui, data: &Value, at: i64, origin: i64) {
        let series = data["series"].as_array().map(Vec::as_slice).unwrap_or(&[]);
        for s in series {
            if let Some(error) = s["error"].as_str() {
                ui.colored_label(Color32::LIGHT_RED, format!("{}: {error}", s["field"]));
            }
        }
        let groups: Vec<(&str, Vec<usize>)> = if self.kind == Kind::Control {
            vec![
                ("Speed (m/s)", vec![0, 1]),
                ("Acceleration (m/s²)", vec![2, 3]),
                ("Steering (%)", vec![4, 5]),
                ("Pedals (%)", vec![6, 7]),
                ("Tracking error (m)", vec![8, 9]),
                ("Heading error (rad)", vec![10]),
                ("Runtime (ms)", vec![11]),
            ]
        } else {
            vec![(
                "Selected numeric fields (native units)",
                (0..series.len()).collect(),
            )]
        };
        egui::ScrollArea::vertical()
            .max_height(540.0)
            .show(ui, |ui| {
                for (group_id, (title, indices)) in groups.iter().enumerate() {
                    ui.strong(*title);
                    let plot = egui_plot::Plot::new((self.id, group_id))
                        .height(if self.kind == Kind::Control {
                            165.0
                        } else {
                            ui.available_height().clamp(120.0, 290.0)
                        })
                        .legend(egui_plot::Legend::default())
                        .x_axis_label("Seconds from bag start");
                    let result = plot.show(ui, |plot_ui| {
                        plot_ui.vline(
                            egui_plot::VLine::new("Playhead", (at - origin) as f64 / 1e9)
                                .color(Color32::WHITE),
                        );
                        for &i in indices {
                            if let Some(s) = series.get(i) {
                                plot_ui.line(
                                    egui_plot::Line::new(
                                        format!(
                                            "{} {}",
                                            s["label"].as_str().unwrap_or("Signal"),
                                            self.signals.get(i).map_or("", |s| s.unit.as_str())
                                        ),
                                        points(&s["points"]),
                                    )
                                    .width(1.6),
                                );
                            }
                        }
                        plot_ui.pointer_coordinate()
                    });
                    if result.response.clicked()
                        && let Some(pos) = result.inner
                    {
                        seek(ctx, origin + (pos.x * 1e9) as i64);
                    }
                    for &i in indices {
                        if let Some(s) = series.get(i) {
                            ui.small(format!(
                                "{}  n={}  missing={}  min={}  max={}  mean={}  RMS={}",
                                s["label"],
                                s["points"].as_array().map_or(0, Vec::len),
                                s["missing"],
                                number(&s["stats"]["min"]),
                                number(&s["stats"]["max"]),
                                number(&s["stats"]["mean"]),
                                number(&s["stats"]["rms"])
                            ));
                        }
                    }
                }
            });
    }

    fn states(&self, ctx: &AppContext<'_>, ui: &mut Ui, data: &Value, origin: i64) {
        egui::ScrollArea::vertical()
            .max_height(500.0)
            .show(ui, |ui| {
                if let Some(series) = data["series"].as_array() {
                    for s in series {
                        ui.strong(format!("{} : {}", s["topic"], s["field"]));
                        if let Some(error) = s["error"].as_str() {
                            ui.colored_label(Color32::LIGHT_RED, error);
                        }
                        if let Some(rows) = s["points"].as_array() {
                            for (i, p) in rows.iter().enumerate() {
                                let t = p[0].as_f64().unwrap_or(0.0);
                                if ui.button(format!("{t:8.3} s   →   {}", p[1])).clicked() {
                                    let ns = s["times_ns"][i]
                                        .as_str()
                                        .and_then(|t| t.parse().ok())
                                        .unwrap_or(origin + (t * 1e9) as i64);
                                    seek(ctx, ns);
                                }
                            }
                        }
                    }
                }
            });
    }

    fn trajectory(&self, ui: &mut Ui, data: &Value) {
        ui.label(
            data["coordinate_frame"]
                .as_str()
                .unwrap_or("Missing frame metadata"),
        );
        ui.label(format!(
            "Plan sample {} | Localization {} | planned {} points, actual {} points",
            data["sample_ns"],
            data["localization_ns"],
            data["planned"].as_array().map_or(0, Vec::len),
            data["actual"].as_array().map_or(0, Vec::len)
        ));
        egui_plot::Plot::new((self.id, "xy"))
            .height(ui.available_height().clamp(160.0, 470.0))
            .data_aspect(1.0)
            .legend(egui_plot::Legend::default())
            .x_axis_label("Map X (m)")
            .y_axis_label("Map Y (m)")
            .show(ui, |p| {
                p.line(
                    egui_plot::Line::new("Current planned trajectory", points(&data["planned"]))
                        .color(Color32::LIGHT_BLUE)
                        .width(2.0),
                );
                let actual = points(&data["actual"]);
                if let Some(&last) = actual.last() {
                    p.points(egui_plot::Points::new("Current pose", vec![last]).radius(5.0));
                }
                p.line(
                    egui_plot::Line::new("Localization history", actual)
                        .color(Color32::LIGHT_GREEN)
                        .width(2.0),
                );
            });
    }

    fn health(&mut self, ui: &mut Ui, data: &Value) {
        if let Some(error) = data["selected_error"].as_str() {
            ui.colored_label(Color32::LIGHT_RED, error);
        }
        if let Some(selected) = data.get("selected") {
            ui.label(format!(
                "{} | n={} | age={} ms",
                selected["topic"],
                selected["count"],
                number(&selected["age_ms"])
            ));
            ui.label(format!("Interval min / median / max: {} / {} / {} ms | duplicate times={} | gaps > 2× median={}",
                number(&selected["period_min_ms"]),number(&selected["period_median_ms"]),number(&selected["period_max_ms"]),
                selected["duplicates"],selected["gaps_over_2x_median"]));
        }
        ui.weak("Bag-average Hz = message count / bag duration; gap counts are observations, not fault verdicts.");
        ui.horizontal(|ui| {
            ui.label("Filter topics");
            ui.text_edit_singleline(&mut self.filter);
        });
        egui::ScrollArea::both().max_height(440.0).show(ui, |ui| {
            if let Some(topics) = data["topics"].as_array() {
                egui::Grid::new((self.id, "health_table"))
                    .striped(true)
                    .show(ui, |ui| {
                        ui.strong("Topic");
                        ui.strong("Messages");
                        ui.strong("Bag avg Hz");
                        ui.strong("Type");
                        ui.end_row();
                        for t in topics {
                            if !t["topic"].as_str().unwrap_or("").contains(&self.filter) {
                                continue;
                            }
                            if ui
                                .selectable_label(false, t["topic"].as_str().unwrap_or(""))
                                .clicked()
                            {
                                self.topic = t["topic"].as_str().expect("topic row").into();
                                self.runtime.key.clear();
                            }
                            ui.label(t["count"].to_string());
                            ui.label(number(&t["bag_average_hz"]));
                            ui.label(t["type"].as_str().unwrap_or(""));
                            ui.end_row();
                        }
                    });
            }
        });
    }

    fn diagnostic(&self) -> Value {
        let d = self.runtime.response.as_ref();
        let mut summary = json!({"id":self.id,"kind":self.kind.title(),"topic":self.topic,
            "pending":self.runtime.pending.is_some(),"error":self.runtime.error,"filter":self.filter,"follow":self.follow,
            "has_data":self.runtime.response.is_some(),"initial_loading":self.runtime.initial_loading(),
            "revision":self.runtime.revision,"content_top_y":self.runtime.content_top_y,"topic_ui":self.topic_ui});
        if let Some(d) = d {
            for key in [
                "at_ns",
                "sample_ns",
                "previous_ns",
                "next_ns",
                "age_ms",
                "selected",
                "notice",
            ] {
                summary[key] = d[key].clone();
            }
            summary["fields"] = d["fields"].clone();
            summary["visible_fields"] = json!(
                d["fields"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|f| {
                        let path = f["path"].as_str()?;
                        (path.to_lowercase().contains(&self.filter.to_lowercase())
                            && (self.kind != Kind::Watch
                                || self.pins.iter().any(|p| p == path)
                                || !self.filter.is_empty()))
                        .then_some(path)
                    })
                    .collect::<Vec<_>>()
            );
            summary["series"] = json!(d["series"].as_array().map(|ss|ss.iter().map(|s|json!({
                "field":s["field"],"topic":s["topic"],"count":s["points"].as_array().map_or(0,Vec::len),
                "first":s["points"].as_array().and_then(|p|p.first()),"last":s["points"].as_array().and_then(|p|p.last()),
                "stats":s["stats"],"error":s["error"]})).collect::<Vec<_>>()));
            for key in ["planned", "actual", "topics"] {
                summary[format!("{key}_count")] = json!(d[key].as_array().map_or(0, Vec::len));
            }
        }
        summary
    }
}

fn topic_selection_notice(topics: &[String], selected: &str) -> Option<String> {
    if topics.is_empty() {
        Some("This recording's topic catalog is empty; no topic can be queried.".into())
    } else if selected.trim().is_empty() {
        Some("Select a topic from Browse topics to inspect its messages.".into())
    } else if !topics.iter().any(|t| t == selected.trim()) {
        Some(format!(
            "Topic not present in this bag: {}. Choose from the {} available topics below the input.",
            selected.trim(),
            topics.len()
        ))
    } else {
        None
    }
}

#[cfg(test)]
mod topic_selection_tests {
    use super::topic_selection_notice;

    #[test]
    fn empty_selection_is_not_a_query() {
        let topics = vec!["/apollo/localization/pose".into()];
        for empty in ["", " ", "\t"] {
            assert!(
                topic_selection_notice(&topics, empty)
                    .unwrap()
                    .contains("Select a topic")
            );
        }
    }

    #[test]
    fn catalog_validation_is_exact_and_does_not_substitute_control() {
        let topics = vec!["/apollo/localization/pose".into()];
        assert!(topic_selection_notice(&topics, " /apollo/localization/pose ").is_none());
        assert!(
            topic_selection_notice(&topics, "/apollo/control")
                .unwrap()
                .contains("not present")
        );
        assert!(
            topic_selection_notice(&[], "/apollo/control")
                .unwrap()
                .contains("catalog is empty")
        );
    }
}

fn number(v: &Value) -> String {
    v.as_f64().map_or_else(|| "—".into(), |v| format!("{v:.4}"))
}
fn points(v: &Value) -> Vec<[f64; 2]> {
    v.as_array()
        .into_iter()
        .flatten()
        .filter_map(|p| Some([p[0].as_f64()?, p[1].as_f64()?]))
        .collect()
}
fn seek(ctx: &AppContext<'_>, ns: i64) {
    ctx.egui_ctx
        .data_mut(|d| d.insert_temp(egui::Id::new("web_monitor_cancel_buffer_resume"), true));
    ctx.send_time_commands_to_active_recording(vec![
        TimeControlCommand::Pause,
        TimeControlCommand::SetTime(re_log_types::TimeInt::new_temporal(ns).into()),
    ]);
}
