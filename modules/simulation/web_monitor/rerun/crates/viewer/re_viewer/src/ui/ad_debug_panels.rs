//! Read-only algorithm debug tools sharing the AD playback clock.
use egui::{Color32, RichText, Stroke, StrokeKind, Ui};
use egui_plot::HoverPosition;
use re_viewer_context::{AppContext, TimeControlCommand};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::ad_shell::theme;

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
    pub(super) fn kind_title(&self) -> &'static str {
        self.kind.title()
    }

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
            pins: match kind {
                // Inspector/Watch start empty so chassis defaults don't stick on pose.
                Kind::Inspector | Kind::Watch => Vec::new(),
                _ => vec![
                    "speed".into(),
                    "throttle".into(),
                    "brake".into(),
                    "steering_target".into(),
                    "gear_location".into(),
                ],
            },
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
                apply_debug_visuals(ui);
                if mcap.is_empty()
                    || at.is_none()
                    || origin.is_none()
                    || !matches!(clock.as_deref(), Some("publish_time" | "message_time"))
                {
                    ui.label(
                        RichText::new(source_notice)
                            .size(12.0)
                            .color(Color32::from_rgb(0xFE, 0xF0, 0x8C)),
                    );
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
        // Always track the playhead (no Follow / Refresh toolbar — timeline is enough).
        self.follow = true;
        egui::CollapsingHeader::new(
            RichText::new("Topic / signal settings")
                .size(12.0)
                .color(theme::TEXT),
        )
            .default_open(matches!(
                self.kind,
                Kind::Inspector | Kind::Watch | Kind::Health
            ))
            .show(ui, |ui| {
                apply_debug_visuals(ui);
                if !matches!(
                    self.kind,
                    Kind::Control | Kind::Trajectory | Kind::Profile | Kind::Health
                ) {
                    self.topic_selector(ui, topics);
                }
                if matches!(self.kind, Kind::Plot | Kind::States) {
                    ui.label(
                        RichText::new("Field")
                            .size(11.0)
                            .color(theme::TEXT_DIM),
                    );
                    ui.horizontal(|ui| {
                        let width = (ui.available_width() - 100.0).clamp(90.0, 355.0);
                        dark_text_edit(ui, &mut self.field, "field path", width);
                        if chip_button(ui, "Add").clicked() && !self.field.trim().is_empty() {
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
                            if chip_button(ui, "Remove").clicked() {
                                remove = Some(i);
                            }
                            ui.label(
                                RichText::new(format!("{} : {} {}", s.topic, s.field, s.unit))
                                    .size(12.0)
                                    .color(theme::TEXT),
                            );
                        });
                    }
                    if let Some(i) = remove {
                        self.signals.remove(i);
                        self.runtime.key.clear();
                    }
                }
                if matches!(self.kind, Kind::Plot | Kind::Control | Kind::States) {
                    ui.horizontal(|ui| {
                        ui.label(
                            RichText::new("Query window (s)")
                                .size(11.0)
                                .color(theme::TEXT_DIM),
                        );
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
                    });
                    ui.label(
                        RichText::new("Click chart to seek · drag/scroll to inspect")
                            .size(11.0)
                            .color(theme::TEXT_DIM),
                    );
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
                ui.label(
                    RichText::new(&notice).size(12.0).color(theme::TEXT_DIM),
                );
            } else {
                self.runtime.error = Some(notice.clone());
                ui.label(
                    RichText::new(&notice)
                        .size(12.0)
                        .color(Color32::from_rgb(0xFE, 0xCA, 0xCA)),
                );
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
                Some("Debug query timed out after 50 s".into());
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
        self.refresh_status(ui, at);
        if let Some(error) = &self.runtime.error {
            ui.label(
                RichText::new(error)
                    .size(12.0)
                    .color(Color32::from_rgb(0xFE, 0xCA, 0xCA)),
            );
        }
        self.runtime.content_top_y = ui.cursor().top();
        if let Some(data) = self.runtime.response.clone() {
            if let Some(notice) = data["notice"].as_str() {
                ui.label(
                    RichText::new(notice)
                        .size(12.0)
                        .color(Color32::from_rgb(0xFE, 0xF0, 0x8C)),
                );
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
                        style_plot(
                            egui_plot::Plot::new((self.id, field))
                                .height(height)
                                .x_axis_label("Trajectory relative time (s)")
                                .y_axis_label(unit),
                        )
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
        ui.label(
            RichText::new("Topic")
                .size(11.0)
                .strong()
                .color(theme::TEXT),
        );
        ui.add_space(4.0);
        let browse_label = if self.topic.trim().is_empty() {
            format!("Browse topics ({})", topics.len())
        } else if let Some((_, leaf)) = self.topic.rsplit_once('/') {
            leaf.to_owned()
        } else {
            self.topic.clone()
        };
        let picker = dropdown_trigger(ui, &browse_label);
        self.topic_ui["picker"] = json!([picker.rect.center().x, picker.rect.center().y]);
        self.topic_ui["picker_rect"] = json!([
            picker.rect.left(),
            picker.rect.top(),
            picker.rect.right(),
            picker.rect.bottom()
        ]);
        egui::Popup::menu(&picker)
            .id(egui::Id::new((self.id, "topics")))
            .align(egui::RectAlign::BOTTOM_START)
            .gap(4.0)
            .show(|ui| {
                egui::Frame::new()
                    .fill(theme::PANEL_BG)
                    .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.4)))
                    .corner_radius(8.0)
                    .inner_margin(egui::Margin::symmetric(8, 8))
                    .show(ui, |ui| {
                        ui.set_min_width(picker.rect.width().max(260.0));
                        ui.set_max_height(280.0);
                        ui.visuals_mut().override_text_color = Some(theme::TEXT);
                        ui.visuals_mut().widgets.hovered.weak_bg_fill = theme::CARD_BG_HOVER;
                        ui.visuals_mut().extreme_bg_color = theme::CARD_BG;
                        let search = dark_text_edit(
                            ui,
                            &mut self.topic_search,
                            "Filter topics…",
                            ui.available_width(),
                        );
                        self.topic_ui["search"] =
                            json!([search.rect.center().x, search.rect.center().y]);
                        ui.add_space(4.0);
                        let filter = self.topic_search.trim().to_lowercase();
                        let mut matches = 0;
                        egui::ScrollArea::vertical().show(ui, |ui| {
                            for topic in topics.iter().filter(|t| t.to_lowercase().contains(&filter))
                            {
                                matches += 1;
                                let on = self.topic == *topic;
                                let row = menu_row(ui, topic, on);
                                self.topic_ui["rows"][topic] =
                                    json!([row.rect.center().x, row.rect.center().y]);
                                if row.clicked() {
                                    if self.topic != *topic {
                                        self.pins.clear();
                                        self.filter.clear();
                                    }
                                    self.topic = topic.clone();
                                    self.runtime = Runtime::default();
                                    ui.close();
                                }
                            }
                            if topics.is_empty() {
                                ui.label(
                                    RichText::new("This recording's topic catalog is empty.")
                                        .size(11.0)
                                        .color(Color32::from_rgb(0xFE, 0xF0, 0x8C)),
                                );
                            } else if matches == 0 {
                                ui.label(
                                    RichText::new(
                                        "No matching topics. Clear the filter to show all.",
                                    )
                                    .size(11.0)
                                    .color(theme::TEXT_DIM),
                                );
                            }
                        });
                    });
            });
        ui.add_space(6.0);
        egui::CollapsingHeader::new(
            RichText::new("Paste topic path")
                .size(11.0)
                .color(theme::TEXT_DIM),
        )
        .id_salt((self.id, "paste_topic"))
        .show(ui, |ui| {
            let previous = self.topic.clone();
            let input = dark_text_edit(
                ui,
                &mut self.topic,
                "Exact topic path",
                ui.available_width(),
            );
            self.topic_ui["input"] = json!([input.rect.center().x, input.rect.center().y]);
            if input.changed() {
                if self.topic != previous {
                    self.pins.clear();
                    self.filter.clear();
                }
                self.runtime = Runtime::default();
            }
        });
    }

    /// Status only — no playhead / publish_time / window-center clock (timeline shows that).
    fn refresh_status(&self, ui: &mut Ui, at: i64) {
        ui.allocate_ui_with_layout(
            egui::vec2(ui.available_width(), 20.0),
            egui::Layout::left_to_right(egui::Align::Center),
            |ui| {
                if self.runtime.initial_loading() {
                    ui.add(egui::Spinner::new().size(14.0));
                    ui.label(
                        RichText::new("Loading…")
                            .size(11.0)
                            .color(theme::TEXT_DIM),
                    );
                    return;
                }
                let Some(data) = &self.runtime.response else {
                    ui.label(
                        RichText::new("No query result")
                            .size(11.0)
                            .color(theme::TEXT_DIM),
                    );
                    return;
                };
                let sample = data["sample_ns"].as_str().and_then(|t| t.parse::<i64>().ok());
                let delayed = self.runtime.pending.is_some()
                    && self
                        .runtime
                        .requested_at
                        .is_some_and(|t| t.elapsed().as_millis() >= 750);
                let awaiting = sample.is_some_and(|t| t > at);
                let (text, color) = if delayed {
                    (
                        "Update delayed — showing previous result",
                        Color32::from_rgb(0xFE, 0xF0, 0x8C),
                    )
                } else if awaiting {
                    (
                        "Awaiting seek result",
                        Color32::from_rgb(0xFE, 0xF0, 0x8C),
                    )
                } else {
                    ("", theme::TEXT_DIM)
                };
                if !text.is_empty() {
                    ui.add(egui::Label::new(RichText::new(text).size(11.0).color(color)).truncate())
                        .on_hover_text(
                            "Background queries replace the displayed result when complete.",
                        );
                }
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
                    .add_enabled(time.is_some(), chip_button_widget(label))
                    .clicked()
                {
                    seek(ctx, time.expect("enabled"));
                }
            }
            if chip_button(ui, "Copy JSON").clicked() {
                ui.ctx()
                    .copy_text(serde_json::to_string_pretty(&data["message"]).expect("JSON value"));
            }
        });
        ui.add_space(4.0);
        ui.label(
            RichText::new("Filter fields")
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
        dark_text_edit(ui, &mut self.filter, "path contains…", ui.available_width());
        ui.label(
            RichText::new(format!(
                "{}  ·  {} messages  ·  Pin fields for Value watch",
                data["type"].as_str().unwrap_or("—"),
                data["count"]
            ))
            .size(11.0)
            .color(theme::TEXT_DIM),
        );
        let fields = data["fields"].as_array().map(Vec::as_slice).unwrap_or(&[]);
        ui.horizontal_wrapped(|ui| {
            if self.kind == Kind::Inspector && chip_button(ui, "Watch pinned fields").clicked() {
                self.kind = Kind::Watch;
                self.filter.clear();
            }
            if chip_button(ui, "Plot pinned numeric fields").clicked() {
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
            if !self.pins.is_empty() && chip_button(ui, "Clear pins").clicked() {
                self.pins.clear();
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
        ui.label(
            RichText::new(format!("{} matching fields", shown.len()))
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
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
                        ui.label(
                            RichText::new(path)
                                .size(12.0)
                                .monospace()
                                .color(theme::TEXT_DIM),
                        );
                        ui.label(
                            RichText::new(f["value"].to_string())
                                .size(12.0)
                                .color(theme::TEXT),
                        );
                    });
                }
            });
        if self.kind == Kind::Watch {
            let absent: Vec<String> = self
                .pins
                .iter()
                .filter(|pin| !fields.iter().any(|f| f["path"] == **pin))
                .cloned()
                .collect();
            for pin in absent {
                ui.horizontal(|ui| {
                    let mut pinned = true;
                    if ui.checkbox(&mut pinned, "").changed() && !pinned {
                        self.pins.retain(|p| p != &pin);
                    }
                    ui.label(
                        RichText::new(format!("{pin}: absent in this message"))
                            .size(12.0)
                            .color(Color32::from_rgb(0xFE, 0xF0, 0x8C)),
                    );
                });
            }
        }
    }

    fn plots(&self, ctx: &AppContext<'_>, ui: &mut Ui, data: &Value, at: i64, origin: i64) {
        let series = data["series"].as_array().map(Vec::as_slice).unwrap_or(&[]);
        for s in series {
            if let Some(error) = s["error"].as_str() {
                ui.label(
                    RichText::new(format!("{}: {error}", s["field"]))
                        .size(12.0)
                        .color(Color32::from_rgb(0xFE, 0xCA, 0xCA)),
                );
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
            vec![("", (0..series.len()).collect())]
        };
        egui::ScrollArea::vertical()
            .max_height(540.0)
            .show(ui, |ui| {
                apply_debug_visuals(ui);
                for (group_id, (title, indices)) in groups.iter().enumerate() {
                    if !title.is_empty() {
                        ui.label(
                            RichText::new(*title)
                                .size(12.0)
                                .strong()
                                .color(theme::TEXT),
                        );
                    }
                    let plot = style_plot(
                        egui_plot::Plot::new((self.id, group_id))
                            .height(if self.kind == Kind::Control {
                                165.0
                            } else {
                                ui.available_height().clamp(120.0, 290.0)
                            })
                            .legend(egui_plot::Legend::default())
                            .label_formatter(|hover| match hover {
                                HoverPosition::NearDataPoint {
                                    plot_name,
                                    position,
                                    ..
                                } => Some(format!(
                                    "{plot_name}\nt={:.3} s\ny={:.4}",
                                    position.x, position.y
                                )),
                                HoverPosition::Elsewhere { position } => Some(format!(
                                    "t={:.3} s\ny={:.4}",
                                    position.x, position.y
                                )),
                            }),
                    );
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
                            ui.label(
                                RichText::new(format!(
                                    "{}  n={}  missing={}  min={}  max={}  mean={}  RMS={}",
                                    s["label"],
                                    s["points"].as_array().map_or(0, Vec::len),
                                    s["missing"],
                                    number(&s["stats"]["min"]),
                                    number(&s["stats"]["max"]),
                                    number(&s["stats"]["mean"]),
                                    number(&s["stats"]["rms"])
                                ))
                                .size(11.0)
                                .color(theme::TEXT_DIM),
                            );
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
                        ui.label(
                            RichText::new(format!("{} : {}", s["topic"], s["field"]))
                                .size(12.0)
                                .strong()
                                .color(theme::TEXT),
                        );
                        if let Some(error) = s["error"].as_str() {
                            ui.label(
                                RichText::new(error)
                                    .size(12.0)
                                    .color(Color32::from_rgb(0xFE, 0xCA, 0xCA)),
                            );
                        }
                        if let Some(rows) = s["points"].as_array() {
                            for (i, p) in rows.iter().enumerate() {
                                let t = p[0].as_f64().unwrap_or(0.0);
                                if chip_button(ui, &format!("{t:8.3} s   →   {}", p[1])).clicked()
                                {
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
            RichText::new(
                data["coordinate_frame"]
                    .as_str()
                    .unwrap_or("Missing frame metadata"),
            )
            .size(12.0)
            .color(theme::TEXT),
        );
        ui.label(
            RichText::new(format!(
                "Plan sample {} | Localization {} | planned {} points, actual {} points",
                data["sample_ns"],
                data["localization_ns"],
                data["planned"].as_array().map_or(0, Vec::len),
                data["actual"].as_array().map_or(0, Vec::len)
            ))
            .size(11.0)
            .color(theme::TEXT_DIM),
        );
        style_plot(
            egui_plot::Plot::new((self.id, "xy"))
                .height(ui.available_height().clamp(160.0, 470.0))
                .data_aspect(1.0)
                .legend(egui_plot::Legend::default())
                .x_axis_label("Map X (m)")
                .y_axis_label("Map Y (m)"),
        )
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
            ui.label(
                RichText::new(error)
                    .size(12.0)
                    .color(Color32::from_rgb(0xFE, 0xCA, 0xCA)),
            );
        }
        if let Some(selected) = data.get("selected") {
            ui.label(
                RichText::new(format!(
                    "{} | n={} | age={} ms",
                    selected["topic"],
                    selected["count"],
                    number(&selected["age_ms"])
                ))
                .size(12.0)
                .color(theme::TEXT),
            );
            ui.label(
                RichText::new(format!(
                    "Interval min / median / max: {} / {} / {} ms | duplicate times={} | gaps > 2× median={}",
                    number(&selected["period_min_ms"]),
                    number(&selected["period_median_ms"]),
                    number(&selected["period_max_ms"]),
                    selected["duplicates"],
                    selected["gaps_over_2x_median"]
                ))
                .size(11.0)
                .color(theme::TEXT_DIM),
            );
        }
        ui.label(
            RichText::new(
                "Bag-average Hz = message count / bag duration; gap counts are observations, not fault verdicts.",
            )
            .size(11.0)
            .color(theme::TEXT_DIM),
        );
        ui.label(
            RichText::new("Filter topics")
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
        dark_text_edit(ui, &mut self.filter, "topic contains…", ui.available_width());
        egui::ScrollArea::both().max_height(440.0).show(ui, |ui| {
            if let Some(topics) = data["topics"].as_array() {
                egui::Grid::new((self.id, "health_table"))
                    .striped(true)
                    .show(ui, |ui| {
                        for header in ["Topic", "Messages", "Bag avg Hz", "Type"] {
                            ui.label(
                                RichText::new(header)
                                    .size(11.0)
                                    .strong()
                                    .color(theme::TEXT),
                            );
                        }
                        ui.end_row();
                        for t in topics {
                            if !t["topic"].as_str().unwrap_or("").contains(&self.filter) {
                                continue;
                            }
                            if ui
                                .add(
                                    egui::Button::new(
                                        RichText::new(t["topic"].as_str().unwrap_or(""))
                                            .size(12.0)
                                            .color(theme::TEXT),
                                    )
                                    .fill(Color32::TRANSPARENT)
                                    .frame(false),
                                )
                                .clicked()
                            {
                                self.topic = t["topic"].as_str().expect("topic row").into();
                                self.runtime.key.clear();
                            }
                            ui.label(
                                RichText::new(t["count"].to_string())
                                    .size(12.0)
                                    .color(theme::TEXT),
                            );
                            ui.label(
                                RichText::new(number(&t["bag_average_hz"]))
                                    .size(12.0)
                                    .color(theme::TEXT),
                            );
                            ui.label(
                                RichText::new(t["type"].as_str().unwrap_or(""))
                                    .size(12.0)
                                    .color(theme::TEXT_DIM),
                            );
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

fn apply_debug_visuals(ui: &mut Ui) {
    let v = ui.visuals_mut();
    v.override_text_color = Some(theme::TEXT);
    v.extreme_bg_color = theme::CARD_BG;
    v.text_edit_bg_color = Some(theme::CARD_BG);
    v.faint_bg_color = theme::PANEL_BG;
    v.panel_fill = theme::PANEL_BG;
    v.widgets.noninteractive.fg_stroke = Stroke::new(1.0, theme::TEXT);
    v.widgets.inactive.fg_stroke = Stroke::new(1.0, theme::TEXT);
    v.widgets.hovered.fg_stroke = Stroke::new(1.0, theme::TEXT);
    v.widgets.active.fg_stroke = Stroke::new(1.0, Color32::WHITE);
    v.widgets.inactive.bg_fill = theme::CARD_BG;
    v.widgets.inactive.weak_bg_fill = theme::CARD_BG;
    v.widgets.hovered.bg_fill = theme::CARD_BG_HOVER;
    v.widgets.hovered.weak_bg_fill = theme::CARD_BG_HOVER;
    v.selection.bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.45);
    v.selection.stroke = Stroke::new(1.0, theme::ACCENT);
}

fn chip_button(ui: &mut Ui, label: &str) -> egui::Response {
    ui.add(chip_button_widget(label))
}

fn chip_button_widget(label: &str) -> egui::Button<'_> {
    egui::Button::new(RichText::new(label).size(12.0).color(theme::TEXT))
        .fill(theme::CARD_BG)
        .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.28)))
        .corner_radius(6.0)
        .min_size(egui::vec2(0.0, 28.0))
}

fn dark_text_edit(ui: &mut Ui, text: &mut String, hint: &str, width: f32) -> egui::Response {
    // Do NOT use Frame::NONE: egui skips background_color when a custom frame is set,
    // which left light glyphs on a light/white slab in the web viewer.
    ui.scope(|ui| {
        ui.visuals_mut().override_text_color = Some(theme::TEXT);
        ui.visuals_mut().text_edit_bg_color = Some(theme::CARD_BG);
        ui.visuals_mut().extreme_bg_color = theme::CARD_BG;
        ui.add(
            egui::TextEdit::singleline(text)
                .desired_width(width.max(40.0))
                .background_color(theme::CARD_BG)
                .text_color(theme::TEXT)
                .hint_text(RichText::new(hint).color(theme::TEXT_DIM))
                .margin(egui::Margin::symmetric(8, 5)),
        )
    })
    .inner
}

fn dropdown_trigger(ui: &mut Ui, label: &str) -> egui::Response {
    let height = 32.0;
    let width = ui.available_width();
    let (rect, response) =
        ui.allocate_exact_size(egui::vec2(width, height), egui::Sense::click());
    let fill = if response.hovered() || response.has_focus() {
        theme::CARD_BG_HOVER
    } else {
        theme::CARD_BG
    };
    ui.painter().rect(
        rect,
        6.0,
        fill,
        Stroke::new(
            1.0,
            if response.hovered() {
                theme::ACCENT.gamma_multiply(0.55)
            } else {
                theme::ACCENT.gamma_multiply(0.32)
            },
        ),
        StrokeKind::Inside,
    );
    let chevron_w = 28.0;
    let text_rect = egui::Rect::from_min_max(
        egui::pos2(rect.left() + 10.0, rect.top()),
        egui::pos2(rect.right() - chevron_w, rect.bottom()),
    );
    ui.painter().text(
        text_rect.left_center(),
        egui::Align2::LEFT_CENTER,
        label,
        egui::FontId::proportional(13.0),
        theme::TEXT,
    );
    let c = egui::pos2(rect.right() - chevron_w * 0.5, rect.center().y + 0.5);
    let s = 4.5;
    ui.painter().add(egui::Shape::convex_polygon(
        vec![
            egui::pos2(c.x - s, c.y - s * 0.55),
            egui::pos2(c.x + s, c.y - s * 0.55),
            egui::pos2(c.x, c.y + s * 0.7),
        ],
        theme::TEXT_DIM,
        Stroke::NONE,
    ));
    response
}

fn menu_row(ui: &mut Ui, label: &str, selected: bool) -> egui::Response {
    ui.add_sized(
        [ui.available_width(), 28.0],
        egui::Button::new(
            RichText::new(label)
                .size(12.0)
                .color(if selected { Color32::WHITE } else { theme::TEXT }),
        )
        .fill(if selected {
            theme::ACCENT_STRONG.gamma_multiply(0.7)
        } else {
            Color32::TRANSPARENT
        })
        .corner_radius(4.0),
    )
}

fn style_plot(plot: egui_plot::Plot<'_>) -> egui_plot::Plot<'_> {
    plot.show_background(true)
}
