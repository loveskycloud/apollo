//! Read-only algorithm debug tools sharing the AD playback clock.
use egui::{Color32, RichText, Stroke, StrokeKind, Ui};
use egui_plot::HoverPosition;
use re_viewer_context::{AppContext, TimeControlCommand};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::ad_shell::theme;

pub(super) const PANEL_BG: Color32 = Color32::from_rgb(25, 25, 40);
const PLOT_BG: Color32 = Color32::from_rgb(34, 34, 56);
const BORDER: Color32 = Color32::from_rgb(74, 66, 103);
const CYAN: Color32 = Color32::from_rgb(64, 229, 238);
const PURPLE: Color32 = Color32::from_rgb(185, 136, 239);
const AMBER: Color32 = Color32::from_rgb(255, 187, 66);
const GREEN: Color32 = Color32::from_rgb(106, 235, 77);
const RED: Color32 = Color32::from_rgb(255, 82, 95);
const SERIES_COLORS: [Color32; 5] = [CYAN, PURPLE, AMBER, GREEN, RED];

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
    #[serde(skip)]
    expanded_fields: std::collections::BTreeMap<String, bool>,
    #[serde(skip)]
    body_height: f32,
    #[serde(skip)]
    settings_open: bool,
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
            expanded_fields: Default::default(),
            body_height: 0.0,
            settings_open: false,
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
        // Message tables clip long cells and expose the complete value on hover.
        self.body_height = (ui.available_height() - 24.0).max(60.0);
        egui::ScrollArea::vertical()
            .max_height(self.body_height)
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
        self.refresh_status(ui, at.unwrap_or(0));
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
        self.follow = true;
        if matches!(self.kind, Kind::Inspector | Kind::Watch | Kind::States)
            && let Some(notice) = topic_selection_notice(topics, &self.topic)
        {
            // An empty edit is a selection state, not a backend query. Also
            // discard any pending reply for the previously selected topic.
            self.runtime = Runtime::default();
            self.topic_ui["notice"] = json!(notice);
            if self.topic.trim().is_empty() {
                ui.label(RichText::new(&notice).size(12.0).color(theme::TEXT_DIM));
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
            self.runtime.error = Some("Debug query timed out after 50 s".into());
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
                    let height = ((self.body_height - 60.0) / 3.0).max(55.0);
                    for (field, title, unit, color) in [
                        ("v", "速度", "m/s", CYAN),
                        ("a", "加速度", "m/s²", PURPLE),
                        ("kappa", "曲率", "1/m", AMBER),
                    ] {
                        ui.label(
                            RichText::new(format!("{title}  {unit}"))
                                .size(13.0)
                                .color(theme::TEXT),
                        );
                        style_plot(
                            egui_plot::Plot::new((self.id, field))
                                .height(height)
                                .y_axis_min_width(40.0),
                        )
                        .x_axis_label(if field == "kappa" {
                            "轨迹相对时间 (s)"
                        } else {
                            ""
                        })
                        .show(ui, |plot| {
                            plot.line(
                                egui_plot::Line::new(title, points(&data[field]))
                                    .color(color)
                                    .width(2.0),
                            );
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

    pub(super) fn header_ui(&mut self, ui: &mut Ui, topics: &[String], header: Option<Value>) {
        self.topic_ui = json!({"available_topics":topics,"controls":{},"header":header});
        let settings = re_ui::ad_panel_icon_button(ui, &re_ui::icons::SETTINGS, "面板设置");
        self.control("settings", &settings);
        let mut open = self.settings_open;
        if settings.clicked() {
            open = !open;
        }
        // Memory-backed popups are exclusive. Keep the settings container open
        // independently so its topic picker and ComboBox can use that popup slot.
        let close_behavior = if egui::Popup::is_any_open(ui.ctx()) {
            egui::PopupCloseBehavior::IgnoreClicks
        } else {
            egui::PopupCloseBehavior::CloseOnClickOutside
        };
        egui::Popup::from_response(&settings)
            .id(egui::Id::new((self.id, "settings")))
            .open_bool(&mut open)
            .close_behavior(close_behavior)
            .layout(egui::Layout::top_down_justified(egui::Align::Min))
            .show(|ui| {
                ui.set_width(320.0);
                // Let the scroll area grow when an editor is expanded instead
                // of retaining the popup's previous, collapsed height.
                ui.set_max_height(440.0);
                apply_debug_visuals(ui);
                egui::ScrollArea::vertical()
                    .max_height(440.0)
                    .show(ui, |ui| {
                        ui.label(RichText::new("面板设置").strong().size(14.0));
                        egui::ComboBox::from_id_salt((self.id, "kind"))
                            .selected_text(self.kind.title())
                            .show_ui(ui, |ui| {
                                for kind in Kind::ALL {
                                    if ui
                                        .selectable_label(self.kind == kind, kind.title())
                                        .clicked()
                                    {
                                        *self = Self::new(kind, self.id);
                                    }
                                }
                            });
                        ui.separator();
                        self.settings_ui(ui, topics);
                    });
            });
        self.settings_open = open;
    }

    pub(super) fn topic_badge(&self) -> String {
        match self.kind {
            Kind::Profile => "/apollo/planning".into(),
            Kind::Trajectory | Kind::Health => String::new(),
            Kind::Plot | Kind::Control | Kind::States => {
                let topics: std::collections::BTreeSet<_> =
                    self.signals.iter().map(|s| s.topic.as_str()).collect();
                if topics.len() == 1 {
                    topics.first().expect("one topic").to_string()
                } else {
                    format!("{} 个话题", topics.len())
                }
            }
            _ => self.topic.clone(),
        }
    }

    fn control(&mut self, key: &str, response: &egui::Response) {
        if response.interact_rect.is_positive() {
            self.topic_ui["controls"][key] = json!([
                response.interact_rect.center().x,
                response.interact_rect.center().y
            ]);
        }
    }

    fn settings_ui(&mut self, ui: &mut Ui, topics: &[String]) {
        apply_debug_visuals(ui);
        if !matches!(
            self.kind,
            Kind::Control | Kind::Trajectory | Kind::Profile | Kind::Health
        ) {
            self.topic_selector(ui, topics);
        }
        if matches!(self.kind, Kind::Plot | Kind::States) {
            ui.label(RichText::new("Field").size(11.0).color(theme::TEXT_DIM));
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
            .close_behavior(egui::PopupCloseBehavior::CloseOnClickOutside)
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
                            for topic in
                                topics.iter().filter(|t| t.to_lowercase().contains(&filter))
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
        let paste = egui::CollapsingHeader::new(
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
        self.control("paste_topic", &paste.header_response);
    }

    /// Status only — no playhead / publish_time / window-center clock (timeline shows that).
    fn refresh_status(&self, ui: &mut Ui, at: i64) {
        ui.allocate_ui_with_layout(
            egui::vec2(ui.available_width(), 20.0),
            egui::Layout::left_to_right(egui::Align::Center),
            |ui| {
                if self.runtime.initial_loading() {
                    ui.add(egui::Spinner::new().size(14.0));
                    ui.label(RichText::new("Loading…").size(11.0).color(theme::TEXT_DIM));
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
                let sample = data["sample_ns"]
                    .as_str()
                    .and_then(|t| t.parse::<i64>().ok());
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
                    ("Awaiting seek result", Color32::from_rgb(0xFE, 0xF0, 0x8C))
                } else {
                    ("", theme::TEXT_DIM)
                };
                if !text.is_empty() {
                    ui.add(
                        egui::Label::new(RichText::new(text).size(11.0).color(color)).truncate(),
                    )
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
        ui.horizontal(|ui| {
            for (direction, key, control, label) in [
                (-1.0, "previous_ns", "previous", "上一条消息"),
                (1.0, "next_ns", "next", "下一条消息"),
            ] {
                let time = data[key].as_str().and_then(|s| s.parse::<i64>().ok());
                let response = ui.add_enabled(
                    time.is_some(),
                    chip_button_widget("").min_size(egui::vec2(32.0, 32.0)),
                );
                let center = response.rect.center();
                ui.painter().add(egui::Shape::line(
                    vec![
                        center + egui::vec2(-direction * 2.5, -5.0),
                        center + egui::vec2(direction * 2.5, 0.0),
                        center + egui::vec2(-direction * 2.5, 5.0),
                    ],
                    Stroke::new(1.3, if time.is_some() { theme::TEXT } else { BORDER }),
                ));
                self.control(control, &response);
                if response.clicked() {
                    seek(ctx, time.expect("enabled"));
                }
                response.on_hover_text(label);
            }
            ui.label(
                RichText::new(format!("{} 条消息", data["count"]))
                    .size(12.0)
                    .color(theme::TEXT_DIM),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let copy = panel_tool_button(ui, "复制 JSON", &re_ui::icons::AD_PANEL_COPY, false);
                self.control("copy", &copy);
                if copy.clicked() {
                    ui.ctx().copy_text(
                        serde_json::to_string_pretty(&data["message"]).expect("JSON value"),
                    );
                }
            });
        });
        ui.add_space(4.0);
        let fields = data["fields"].as_array().map(Vec::as_slice).unwrap_or(&[]);
        ui.horizontal(|ui| {
            let compact = ui.available_width() < 380.0;
            let tool_width = if compact { 32.0 } else { 100.0 };
            let filter_width = (ui.available_width() - 2.0 * tool_width - 16.0).max(60.0);
            let filter = field_search(ui, &mut self.filter, filter_width);
            self.control("filter", &filter);
            let watch = panel_tool_button(
                ui,
                if self.kind == Kind::Watch {
                    "全部字段"
                } else {
                    "监视字段"
                },
                &re_ui::icons::FILTER,
                compact,
            );
            self.control("watch", &watch);
            if watch.clicked() {
                self.kind = if self.kind == Kind::Watch {
                    Kind::Inspector
                } else {
                    Kind::Watch
                };
                self.filter.clear();
            }
            let plot = panel_tool_button(ui, "绘制曲线", &re_ui::icons::AD_PANEL_PLOT, compact);
            self.control("plot", &plot);
            if plot.clicked() {
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
                    self.runtime.error = Some("请选择 1 至 24 个数值字段绘制曲线".into());
                } else {
                    self.signals = signals;
                    self.kind = Kind::Plot;
                    self.runtime.key.clear();
                    self.runtime.response = None;
                }
            }
        });
        ui.add_space(6.0);
        let rows = message_rows(
            &data["message"],
            fields,
            &self.expanded_fields,
            &self.filter,
            (self.kind == Kind::Watch).then_some(self.pins.as_slice()),
        );
        self.topic_ui["displayed_rows"] =
            json!(rows.iter().map(|row| &row.path).collect::<Vec<_>>());
        let row_height = 25.0;
        egui::Frame::new()
            .stroke(Stroke::new(1.0, BORDER))
            .corner_radius(5.0)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                let width = ui.available_width();
                let field_width = width * 0.53;
                let (header, _) =
                    ui.allocate_exact_size(egui::vec2(width, row_height), egui::Sense::hover());
                ui.painter()
                    .rect_filled(header, 0.0, Color32::from_rgb(41, 38, 63));
                for (x, title) in [
                    (header.left() + 10.0, "字段"),
                    (header.left() + field_width + 10.0, "值"),
                ] {
                    ui.painter().text(
                        egui::pos2(x, header.center().y),
                        egui::Align2::LEFT_CENTER,
                        title,
                        egui::FontId::proportional(12.0),
                        theme::TEXT_DIM,
                    );
                }
                ui.painter().vline(
                    header.left() + field_width,
                    header.y_range(),
                    Stroke::new(1.0, BORDER),
                );
                ui.spacing_mut().item_spacing.y = 0.0;
                let (root_rect, root) =
                    ui.allocate_exact_size(egui::vec2(width, row_height), egui::Sense::hover());
                ui.painter()
                    .with_clip_rect(root_rect.intersect(ui.clip_rect()))
                    .text(
                        root_rect.left_center() + egui::vec2(10.0, 0.0),
                        egui::Align2::LEFT_CENTER,
                        data["type"].as_str().unwrap_or("Message"),
                        egui::FontId::proportional(12.0),
                        theme::TEXT,
                    );
                root.on_hover_text(data["type"].as_str().unwrap_or("Message"));
                egui::ScrollArea::vertical()
                    .id_salt((self.id, "message_table"))
                    .max_height((self.body_height - 138.0).max(75.0))
                    .auto_shrink([false, false])
                    .show_rows(ui, row_height, rows.len(), |ui, range| {
                        for index in range {
                            let row = &rows[index];
                            let (rect, response) = ui.allocate_exact_size(
                                egui::vec2(ui.available_width(), row_height),
                                egui::Sense::click(),
                            );
                            let pinned = self.pins.contains(&row.path);
                            if response.hovered() || pinned || index % 2 == 0 {
                                ui.painter().rect_filled(
                                    rect,
                                    0.0,
                                    if pinned {
                                        Color32::from_rgb(56, 43, 78)
                                    } else {
                                        Color32::from_rgb(29, 28, 46)
                                    },
                                );
                            }
                            ui.painter().hline(
                                rect.x_range(),
                                rect.bottom(),
                                Stroke::new(1.0, BORDER.gamma_multiply(0.55)),
                            );
                            ui.painter().vline(
                                rect.left() + field_width,
                                rect.y_range(),
                                Stroke::new(1.0, BORDER.gamma_multiply(0.45)),
                            );
                            let x = rect.left() + 12.0 + 10.0 * row.depth.min(8) as f32;
                            if let Some(open) = row.open {
                                let center = egui::pos2(x, rect.center().y);
                                let points = if open {
                                    vec![
                                        center + egui::vec2(-4.0, -2.0),
                                        center + egui::vec2(4.0, -2.0),
                                        center + egui::vec2(0.0, 3.0),
                                    ]
                                } else {
                                    vec![
                                        center + egui::vec2(-2.0, -4.0),
                                        center + egui::vec2(-2.0, 4.0),
                                        center + egui::vec2(3.0, 0.0),
                                    ]
                                };
                                ui.painter().add(egui::Shape::convex_polygon(
                                    points,
                                    theme::TEXT_DIM,
                                    Stroke::NONE,
                                ));
                                if response.clicked() {
                                    self.expanded_fields.insert(row.path.clone(), !open);
                                }
                                self.control(&format!("expand_{}", row.path), &response);
                            } else {
                                let check_rect = egui::Rect::from_center_size(
                                    egui::pos2(x, rect.center().y),
                                    egui::vec2(12.0, 12.0),
                                );
                                ui.painter().rect(
                                    check_rect,
                                    2.0,
                                    if pinned { PURPLE } else { Color32::TRANSPARENT },
                                    Stroke::new(1.0, BORDER),
                                    StrokeKind::Inside,
                                );
                                if response.clicked() {
                                    if pinned {
                                        self.pins.retain(|p| p != &row.path);
                                    } else {
                                        self.pins.push(row.path.clone());
                                    }
                                }
                                self.control(&format!("pin_{}", row.path), &response);
                            }
                            let field_rect = egui::Rect::from_min_max(
                                egui::pos2(x + 12.0, rect.top()),
                                egui::pos2(rect.left() + field_width - 8.0, rect.bottom()),
                            );
                            ui.painter()
                                .with_clip_rect(field_rect.intersect(ui.clip_rect()))
                                .text(
                                    field_rect.left_center(),
                                    egui::Align2::LEFT_CENTER,
                                    &row.path,
                                    egui::FontId::monospace(12.0),
                                    theme::TEXT,
                                );
                            let value_rect = egui::Rect::from_min_max(
                                egui::pos2(rect.left() + field_width + 10.0, rect.top()),
                                rect.right_bottom() - egui::vec2(6.0, 0.0),
                            );
                            ui.painter()
                                .with_clip_rect(value_rect.intersect(ui.clip_rect()))
                                .text(
                                    value_rect.left_center(),
                                    egui::Align2::LEFT_CENTER,
                                    &row.value,
                                    egui::FontId::monospace(12.0),
                                    theme::TEXT_DIM,
                                );
                            response.on_hover_text(format!("{}\n{}", row.path, row.value));
                        }
                    });
            });
        if rows.is_empty() {
            ui.label(
                RichText::new(if self.kind == Kind::Watch {
                    "请在全部字段中勾选需要监视的字段"
                } else {
                    "没有匹配字段"
                })
                .size(12.0)
                .color(theme::TEXT_DIM),
            );
        }
        if !self.pins.is_empty() {
            ui.horizontal(|ui| {
                ui.label(
                    RichText::new(format!("已选择 {} 个字段", self.pins.len()))
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                );
                if ui.small_button("清除选择").clicked() {
                    self.pins.clear();
                }
            });
        }
        if self.kind == Kind::Watch {
            let absent: Vec<_> = self
                .pins
                .iter()
                .filter(|pin| !fields.iter().any(|f| f["path"] == **pin))
                .cloned()
                .collect();
            for pin in absent {
                ui.horizontal(|ui| {
                    ui.colored_label(AMBER, format!("{pin}：当前消息中不存在"));
                    if ui.small_button("取消监视").clicked() {
                        self.pins.retain(|p| p != &pin);
                    }
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
                        ui.label(RichText::new(*title).size(12.0).strong().color(theme::TEXT));
                    }
                    let plot = style_plot(
                        egui_plot::Plot::new((self.id, group_id))
                            .height(if self.kind == Kind::Control {
                                165.0
                            } else {
                                (self.body_height - 44.0).max(100.0)
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
                                HoverPosition::Elsewhere { position } => {
                                    Some(format!("t={:.3} s\ny={:.4}", position.x, position.y))
                                }
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
                                    .width(2.0)
                                    .color(SERIES_COLORS[i % SERIES_COLORS.len()]),
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
        ui.horizontal(|ui| {
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                legend_item(ui, "当前位置", RED, true);
                legend_item(ui, "规划轨迹", CYAN, false);
                legend_item(ui, "历史轨迹", GREEN, false);
            });
        });
        style_plot(
            egui_plot::Plot::new((self.id, "xy"))
                .height((self.body_height - 54.0).max(100.0))
                .data_aspect(1.0)
                .y_axis_label("Y (m)"),
        )
        .x_axis_label("X (m)")
        .show(ui, |plot| {
            let actual = points(&data["actual"]);
            plot.line(
                egui_plot::Line::new("历史轨迹", actual.clone())
                    .color(GREEN)
                    .width(2.0),
            );
            plot.line(
                egui_plot::Line::new("规划轨迹", points(&data["planned"]))
                    .color(CYAN)
                    .width(2.0),
            );
            if let Some(&last) = actual.last() {
                plot.points(
                    egui_plot::Points::new("当前位置", vec![last])
                        .color(RED)
                        .radius(5.0),
                );
            }
        });
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(
                RichText::new(format!(
                    "规划 {} 点 · 历史 {} 点",
                    data["planned"].as_array().map_or(0, Vec::len),
                    data["actual"].as_array().map_or(0, Vec::len)
                ))
                .size(11.0)
                .color(theme::TEXT_DIM),
            )
            .on_hover_text(format!(
                "{}\nPlan: {}\nLocalization: {}",
                data["coordinate_frame"], data["sample_ns"], data["localization_ns"]
            ));
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
        dark_text_edit(
            ui,
            &mut self.filter,
            "topic contains…",
            ui.available_width(),
        );
        egui::ScrollArea::both().max_height(440.0).show(ui, |ui| {
            if let Some(topics) = data["topics"].as_array() {
                egui::Grid::new((self.id, "health_table"))
                    .striped(true)
                    .show(ui, |ui| {
                        for header in ["Topic", "Messages", "Bag avg Hz", "Type"] {
                            ui.label(RichText::new(header).size(11.0).strong().color(theme::TEXT));
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

#[cfg(test)]
mod message_table_tests {
    use super::*;

    #[test]
    fn arrays_expand_without_losing_full_leaf_paths_and_search_reaches_collapsed_data() {
        let message =
            json!({"header":{"timestamp_sec":16.6},"trajectory_point":[{"v":4.5},{"v":6.0}]});
        let fields = vec![
            json!({"path":"header.timestamp_sec","value":16.6}),
            json!({"path":"trajectory_point[1].v","value":6.0}),
        ];
        let mut expanded = std::collections::BTreeMap::new();
        let rows = message_rows(&message, &fields, &expanded, "", None);
        assert!(rows.iter().any(|r| r.path == "header.timestamp_sec"));
        assert!(
            rows.iter()
                .any(|r| r.path == "trajectory_point" && r.value == "[2]" && r.open == Some(false))
        );
        assert!(!rows.iter().any(|r| r.path == "trajectory_point[1].v"));
        let filtered = message_rows(&message, &fields, &expanded, "POINT[1]", None);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].value, "6.0");
        expanded.insert("trajectory_point".into(), true);
        expanded.insert("trajectory_point[1]".into(), true);
        let rows = message_rows(&message, &fields, &expanded, "", None);
        assert!(
            rows.iter()
                .any(|r| r.path == "trajectory_point[1].v" && r.open.is_none())
        );
        let pins = vec!["trajectory_point[1].v".into()];
        assert_eq!(
            message_rows(&message, &fields, &expanded, "", Some(&pins)),
            filtered
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

#[derive(Debug, PartialEq)]
struct MessageRow {
    path: String,
    value: String,
    depth: usize,
    open: Option<bool>,
}

fn message_rows(
    message: &Value,
    fields: &[Value],
    expanded: &std::collections::BTreeMap<String, bool>,
    filter: &str,
    pins: Option<&[String]>,
) -> Vec<MessageRow> {
    let filter = filter.trim().to_lowercase();
    if !filter.is_empty() || pins.is_some() {
        return fields
            .iter()
            .filter_map(|field| {
                let path = field["path"].as_str()?;
                (path.to_lowercase().contains(&filter)
                    && pins.is_none_or(|pins| {
                        pins.iter().any(|pin| pin == path) || !filter.is_empty()
                    }))
                .then(|| MessageRow {
                    path: path.into(),
                    value: field["value"].to_string(),
                    depth: 0,
                    open: None,
                })
            })
            .collect();
    }
    fn visit(
        value: &Value,
        path: String,
        depth: usize,
        expanded: &std::collections::BTreeMap<String, bool>,
        rows: &mut Vec<MessageRow>,
    ) {
        let container = value.is_object() || value.is_array();
        let open = container.then(|| expanded.get(&path).copied().unwrap_or(path == "header"));
        let text = match value {
            Value::Array(items) => format!("[{}]", items.len()),
            Value::Object(_) => String::new(),
            _ => value.to_string(),
        };
        if !path.is_empty() {
            rows.push(MessageRow {
                path: path.clone(),
                value: text,
                depth,
                open,
            });
        }
        if path.is_empty() || open == Some(true) {
            match value {
                Value::Object(items) => {
                    let ordered = items
                        .iter()
                        .filter(|(k, _)| k.as_str() == "header")
                        .chain(items.iter().filter(|(k, _)| k.as_str() != "header"));
                    for (key, value) in ordered {
                        visit(
                            value,
                            if path.is_empty() {
                                key.clone()
                            } else {
                                format!("{path}.{key}")
                            },
                            if path.is_empty() { 0 } else { depth + 1 },
                            expanded,
                            rows,
                        );
                    }
                }
                Value::Array(items) => {
                    for (i, value) in items.iter().enumerate() {
                        visit(value, format!("{path}[{i}]"), depth + 1, expanded, rows);
                    }
                }
                _ => {}
            }
        }
    }
    let mut rows = Vec::new();
    visit(message, String::new(), 0, expanded, &mut rows);
    rows
}

fn legend_item(ui: &mut Ui, label: &str, color: Color32, point: bool) {
    ui.label(RichText::new(label).size(11.0).color(theme::TEXT_DIM));
    let (rect, _) = ui.allocate_exact_size(egui::vec2(22.0, 16.0), egui::Sense::hover());
    if point {
        ui.painter().circle_filled(rect.center(), 4.0, color);
    } else {
        ui.painter().line_segment(
            [rect.left_center(), rect.right_center()],
            Stroke::new(2.5, color),
        );
    }
    ui.add_space(10.0);
}

fn apply_debug_visuals(ui: &mut Ui) {
    let v = ui.visuals_mut();
    v.override_text_color = Some(theme::TEXT);
    v.extreme_bg_color = PLOT_BG;
    v.text_edit_bg_color = Some(theme::CARD_BG);
    v.faint_bg_color = theme::PANEL_BG;
    v.panel_fill = PANEL_BG;
    v.widgets.noninteractive.bg_stroke = Stroke::new(1.0, BORDER);
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
        .fill(Color32::from_rgb(34, 33, 52))
        .stroke(Stroke::new(1.0, BORDER))
        .corner_radius(4.0)
        .min_size(egui::vec2(0.0, 28.0))
}

fn panel_tool_button(
    ui: &mut Ui,
    label: &str,
    icon: &re_ui::Icon,
    compact: bool,
) -> egui::Response {
    let (rect, response) = ui.allocate_exact_size(
        egui::vec2(if compact { 32.0 } else { 100.0 }, 32.0),
        egui::Sense::click(),
    );
    ui.painter().rect(
        rect,
        4.0,
        if response.hovered() {
            Color32::from_rgb(49, 43, 70)
        } else {
            Color32::from_rgb(34, 33, 52)
        },
        Stroke::new(1.0, BORDER),
        StrokeKind::Inside,
    );
    let center = if compact {
        rect.center()
    } else {
        egui::pos2(rect.left() + 15.0, rect.center().y)
    };
    icon.as_image().tint(theme::TEXT).paint_at(
        ui,
        egui::Rect::from_center_size(center, egui::Vec2::splat(16.0)),
    );
    if !compact {
        ui.painter().text(
            egui::pos2(rect.left() + 28.0, rect.center().y),
            egui::Align2::LEFT_CENTER,
            label,
            egui::FontId::proportional(13.0),
            theme::TEXT,
        );
    }
    response.widget_info(|| {
        egui::WidgetInfo::labeled(egui::WidgetType::Button, ui.is_enabled(), label)
    });
    response
        .on_hover_cursor(egui::CursorIcon::PointingHand)
        .on_hover_text(label)
}

fn field_search(ui: &mut Ui, text: &mut String, width: f32) -> egui::Response {
    let response = ui
        .scope(|ui| {
            ui.visuals_mut().widgets.inactive.bg_stroke = Stroke::new(1.0, BORDER);
            ui.visuals_mut().widgets.inactive.corner_radius = egui::CornerRadius::same(4);
            ui.add(
                egui::TextEdit::singleline(text)
                    .desired_width(width)
                    .font(egui::FontId::proportional(13.0))
                    .background_color(Color32::from_rgb(29, 29, 46))
                    .text_color(theme::TEXT)
                    .hint_text(RichText::new("过滤字段…").color(theme::TEXT_DIM))
                    .margin(egui::Margin {
                        left: 30,
                        right: 8,
                        top: 8,
                        bottom: 8,
                    }),
            )
        })
        .inner;
    re_ui::icons::SEARCH
        .as_image()
        .tint(theme::TEXT_DIM)
        .paint_at(
            ui,
            egui::Rect::from_center_size(
                egui::pos2(response.rect.left() + 14.0, response.rect.center().y),
                egui::Vec2::splat(16.0),
            ),
        );
    response
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
    let (rect, response) = ui.allocate_exact_size(egui::vec2(width, height), egui::Sense::click());
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
        egui::Button::new(RichText::new(label).size(12.0).color(if selected {
            Color32::WHITE
        } else {
            theme::TEXT
        }))
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
        .show_grid(true)
        .custom_x_axes(vec![
            egui_plot::AxisHints::new_x().label_spacing(32.0..=48.0),
        ])
        .grid_spacing(egui::Rangef::new(8.0, 60.0))
        .grid_color(Color32::from_rgb(87, 81, 118))
}
