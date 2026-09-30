//! Docked configuration and bounded parallel tasks. Painting never runs a job.
use super::ad_shell::theme;
use re_viewer_context::AppContext;
use serde_json::{Value, json};

type Reply = std::sync::Arc<parking_lot::Mutex<Option<Result<Value, String>>>>;
#[cfg(target_arch = "wasm32")]
#[path = "ad_sim_events.rs"]
mod events;
#[path = "ad_sim_tasks.rs"]
mod tasks;
const MODULES: [&str; 6] = [
    "PREDICTION",
    "fake_prediction",
    "PLANNING",
    "CONTROL",
    "ROUTING",
    "ML_PLANNING",
];

#[derive(Clone, Copy, Default, PartialEq, Eq)]
enum Tab {
    #[default]
    Config,
    Tasks,
}
impl Tab {
    fn label(self) -> &'static str {
        match self {
            Self::Config => "Simulation Config",
            Self::Tasks => "Simulation Tasks",
        }
    }
    fn display_label(self) -> &'static str {
        match self {
            Self::Config => "仿真配置",
            Self::Tasks => "仿真任务",
        }
    }
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum Group {
    Running,
    Queued,
    Finished,
}
impl Group {
    fn key(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Queued => "queued",
            Self::Finished => "finished",
        }
    }
    fn for_stage(stage: &str) -> Self {
        match stage {
            "queued" => Self::Queued,
            "completed" | "failed" | "cancelled" | "interrupted" => Self::Finished,
            _ => Self::Running,
        }
    }
}
#[derive(Clone)]
struct State {
    kind: String,
    source: String,
    map: String,
    /// Selected vehicle identity (profile/pack directory). Backend derives
    /// vehicle_param + applies the profile overlay, like Dreamview CHANGE_VEHICLE.
    vehicle: String,
    model: String,
    modules: [bool; 6],
    use_suite: bool,
    suite: String,
    concurrency: u32,
    repeat: u32,
    seed: u32,
    step: u32,
    config_extra: serde_json::Map<String, Value>,
    config_from: Option<String>,
    catalog: Value,
    jobs: Vec<Value>,
    pending: Option<Reply>,
    pending_action: Option<String>,
    requested: Option<web_time::Instant>,
    error: Option<String>,
    stream_error: Option<String>,
    last_enqueued: Option<String>,
    tab: Tab,
    /// Live task detail is independent from the editable configuration.
    inspected: Option<Value>,
    differences: Value,
    differences_json: String,
    comparison: usize,
    include_messages: bool,
    difference_offset: u64,
    queued_difference: Option<Value>,
    filter: String,
    task_status: tasks::Status,
    task_source: tasks::Source,
    task_page: usize,
    collapsed_suites: std::collections::HashSet<String>,
}
impl Default for State {
    fn default() -> Self {
        Self {
            kind: "bag".into(),
            source: String::new(),
            map: String::new(),
            vehicle: String::new(),
            model: "perfect_planning".into(),
            modules: [true, false, true, true, false, false],
            use_suite: false,
            suite: String::new(),
            concurrency: 30,
            repeat: 2,
            seed: 1,
            step: 10,
            config_extra: Default::default(),
            config_from: None,
            catalog: Value::Null,
            jobs: Vec::new(),
            pending: None,
            pending_action: None,
            requested: None,
            error: None,
            stream_error: None,
            last_enqueued: None,
            tab: Tab::Config,
            inspected: None,
            differences: Value::Null,
            differences_json: String::new(),
            comparison: 0,
            include_messages: false,
            difference_offset: 0,
            queued_difference: None,
            filter: String::new(),
            task_status: tasks::Status::All,
            task_source: tasks::Source::All,
            task_page: 0,
            collapsed_suites: Default::default(),
        }
    }
}
impl State {
    fn new_task(&mut self) {
        self.edit_config(&json!({"id":"", "config":Self::default().config()}))
            .expect("default configuration is valid");
        self.config_from = None;
        self.error = None;
    }

    fn starting(&self) -> bool {
        matches!(
            self.pending_action.as_deref(),
            Some("enqueue" | "enqueue_suite")
        )
    }

    fn start_label(&self) -> &'static str {
        if self.starting() {
            "Starting…"
        } else if self.kind == "world" && self.use_suite {
            "Run scenario suite"
        } else {
            "Start simulation"
        }
    }

    fn apply_jobs(&mut self, jobs: &[Value], snapshot: bool) {
        if snapshot {
            self.jobs = jobs.to_vec();
        } else {
            for job in jobs {
                if let Some(existing) = self.jobs.iter_mut().find(|j| j["id"] == job["id"]) {
                    *existing = job.clone();
                } else {
                    self.jobs.push(job.clone());
                }
            }
        }
        if let Some(selected) = &self.inspected {
            self.inspected = self
                .jobs
                .iter()
                .find(|j| j["id"] == selected["id"])
                .cloned();
        }
    }

    fn config(&self) -> Value {
        let mut config = self.config_extra.clone();
        let edited = json!({
            "kind":self.kind,"source":self.source,"map":self.map,"vehicle":self.vehicle,
            "model":self.model,"seed":self.seed,"repeat":self.repeat,
            "step_ms":self.step,"modules":MODULES.iter().enumerate().filter(|(i,_)|self.modules[*i]).map(|(_,m)|*m).collect::<Vec<_>>()
        });
        config.extend(
            edited
                .as_object()
                .expect("config is an object literal")
                .clone(),
        );
        if self.kind == "world" && self.use_suite {
            config.insert("suite".into(), json!(self.suite));
            config.insert("concurrency".into(), json!(self.concurrency));
        }
        Value::Object(config)
    }
    fn receive(&mut self, reply: Result<Value, String>) {
        self.pending = None;
        self.pending_action = None;
        match reply {
            Ok(reply) if reply["status"] == "ok" => {
                self.error = None;
                if !reply["catalog"].is_null() {
                    self.catalog = reply["catalog"].clone();
                }
                if let Some(jobs) = reply["jobs"].as_array() {
                    self.apply_jobs(jobs, true);
                }
                let page = &reply["differences"];
                if let Some(selected) = &self.inspected
                    && page["task_id"] == selected["id"]
                    && page["comparison"].as_u64() == Some(self.comparison as u64)
                    && page["include_messages"].as_bool() == Some(self.include_messages)
                    && page["offset"].as_u64() == Some(self.difference_offset)
                {
                    self.differences = page.clone();
                    self.differences_json = serde_json::to_string_pretty(&page["diffs"])
                        .expect("JSON values are serializable");
                    self.difference_offset = page["offset"].as_u64().unwrap_or(0);
                }
                if let Some(id) = reply["id"].as_str() {
                    self.last_enqueued = Some(id.into());
                    self.tab = Tab::Tasks;
                    self.inspected = None;
                    self.filter.clear();
                    self.task_status = tasks::Status::All;
                    self.task_source = tasks::Source::All;
                    self.task_page = 0;
                    self.collapsed_suites.clear();
                }
            }
            Ok(reply) => {
                self.error = Some(
                    reply["message"]
                        .as_str()
                        .unwrap_or("Invalid simulation response")
                        .into(),
                );
            }
            Err(error) => self.error = Some(error),
        }
    }
    fn inspect(&mut self, job: &Value) {
        self.inspected = Some(job.clone());
        self.differences = Value::Null;
        self.differences_json.clear();
        self.comparison = 0;
        self.difference_offset = 0;
        self.queued_difference = None;
        self.tab = Tab::Tasks;
    }
    fn edit_config(&mut self, job: &Value) -> Result<(), String> {
        // Decode before assigning anything: malformed snapshots must not partly
        // replace the draft or silently acquire defaults.
        #[derive(serde::Deserialize)]
        struct Config {
            kind: String,
            source: String,
            map: String,
            vehicle: String,
            #[serde(default)]
            profile: String,
            model: String,
            modules: Vec<String>,
            repeat: u32,
            seed: u32,
            step_ms: u32,
            #[serde(flatten)]
            extra: serde_json::Map<String, Value>,
        }
        let c: Config = serde_json::from_value(job["config"].clone())
            .map_err(|e| format!("Cannot open task configuration: {e}"))?;
        if !["bag", "world"].contains(&c.kind.as_str())
            || !["perfect_planning", "kinematic_control"].contains(&c.model.as_str())
            || c.modules.iter().any(|m| !MODULES.contains(&m.as_str()))
            || !(1..=3).contains(&c.repeat)
            || ![1, 2, 5, 10].contains(&c.step_ms)
        {
            return Err(
                "Cannot open task configuration: unsupported input, model, module, runs or step"
                    .into(),
            );
        }
        self.use_suite = false;
        self.kind = c.kind;
        self.source = c.source;
        self.map = c.map;
        // Prefer profile dir (dreamview vehicle identity); fall back from vehicle_param path.
        self.vehicle = if !c.profile.is_empty() {
            c.profile
        } else {
            c.vehicle
                .strip_suffix("/modules/common/data/vehicle_param.pb.txt")
                .unwrap_or(&c.vehicle)
                .to_owned()
        };
        self.model = c.model;
        self.modules = MODULES.map(|m| c.modules.iter().any(|selected| selected == m));
        self.repeat = c.repeat;
        self.seed = c.seed;
        self.step = c.step_ms;
        self.config_extra = c.extra;
        self.config_from = job["id"].as_str().map(str::to_owned);
        self.tab = Tab::Config;
        self.inspected = None;
        self.error = None;
        Ok(())
    }
}
fn point(diagnostic: &mut Value, name: &str, response: &egui::Response) {
    // Only expose visible targets; tests must scroll or filter offscreen cards.
    if response.interact_rect.is_positive() {
        diagnostic[name] = json!([
            response.interact_rect.center().x,
            response.interact_rect.center().y
        ]);
    }
}
pub(super) fn show(
    ctx: &AppContext<'_>,
    ui: &mut egui::Ui,
    open: &mut bool,
) -> Option<(String, bool)> {
    #[cfg(target_arch = "wasm32")]
    events::set_open(&ctx.egui_ctx, *open);
    if !*open {
        ctx.egui_ctx.data_mut(|d| {
            let key = egui::Id::new("ad_sim_diagnostic");
            if let Some(mut diagnostic) = d.get_temp::<Value>(key) {
                diagnostic["open"] = json!(false);
                d.insert_temp(key, diagnostic);
            }
        });
        return None;
    }
    let id = egui::Id::new("ad_sim_runtime");
    let mut state = ctx
        .egui_ctx
        .data_mut(|d| d.get_temp::<State>(id))
        .unwrap_or_default();
    let reply = state.pending.as_ref().and_then(|r| r.lock().take());
    if let Some(reply) = reply {
        state.receive(reply);
    }
    if state.pending.is_some() && state.requested.is_some_and(|t| t.elapsed().as_secs() > 50) {
        state.receive(Err(
            "Simulation service timed out; check the server log".into()
        ));
    }
    let mut action = None;
    let mut replay = None;
    let mut diagnostic = json!({"open":true});
    let task_tab = state.tab == Tab::Tasks;
    let max_width = (ui.available_width() - 220.0).max(350.0);
    egui::Panel::left(if task_tab {
        "ad_sim_tasks_secondary"
    } else {
        "ad_sim_secondary"
    })
    .resizable(true)
    .drag_to_open(false)
    .default_size(if task_tab {
        900.0_f32.min(max_width)
    } else {
        410.0
    })
    .min_size(350.0)
    .max_size(max_width)
    .frame(egui::Frame {
        fill: if task_tab {
            theme::APP_BG
        } else {
            theme::PANEL_BG
        },
        inner_margin: egui::Margin::same(if task_tab { 18 } else { 12 }),
        stroke: egui::Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.2)),
        ..Default::default()
    })
    .show_collapsible(ui, open, |ui| {
        let panel = ui.max_rect();
        diagnostic["panel_rect"] =
            json!([panel.left(), panel.top(), panel.right(), panel.bottom()]);
        // scene_editor structure: fixed tabs above one shared, scrolling content area.
        ui.horizontal(|ui| {
            let width = if task_tab {
                ((ui.available_width() - 134.0) / 2.0).min(190.0)
            } else {
                (ui.available_width() - ui.spacing().item_spacing.x) / 2.0
            };
            for tab in [Tab::Config, Tab::Tasks] {
                let active = state.tab == tab;
                let r = ui.add_sized(
                    [width, 40.0],
                    egui::Button::new(
                        egui::RichText::new(tab.display_label())
                            .size(15.0)
                            .strong()
                            .color(if active { theme::TEXT } else { theme::TEXT_DIM }),
                    )
                    .fill(if active {
                        theme::CARD_BG
                    } else {
                        egui::Color32::TRANSPARENT
                    })
                    .corner_radius(8.0)
                    .frame(true),
                );
                if active {
                    ui.painter().hline(
                        r.rect.x_range(),
                        r.rect.bottom() - 1.0,
                        egui::Stroke::new(2.0, theme::ACCENT),
                    );
                }
                point(
                    &mut diagnostic,
                    if tab == Tab::Config {
                        "config_tab"
                    } else {
                        "tasks_tab"
                    },
                    &r,
                );
                if r.clicked() {
                    state.tab = tab;
                    if tab == Tab::Tasks {
                        state.inspected = None;
                    }
                }
            }
            if task_tab {
                ui.allocate_ui_with_layout(
                    egui::vec2(ui.available_width(), 40.0),
                    egui::Layout::right_to_left(egui::Align::Center),
                    |ui| {
                        let r = ui.add_enabled(
                            state.pending.is_none(),
                            egui::Button::new(
                                egui::RichText::new("＋ 新建任务")
                                    .size(14.0)
                                    .strong()
                                    .color(egui::Color32::WHITE),
                            )
                            .fill(theme::ACCENT_STRONG)
                            .corner_radius(8.0)
                            .min_size(egui::vec2(116.0, 38.0)),
                        );
                        point(&mut diagnostic, "new_task", &r);
                        if r.clicked() {
                            state.new_task();
                        }
                    },
                );
            }
        });
        ui.separator();
        if let Some(error) = &state.error {
            ui.colored_label(egui::Color32::LIGHT_RED, error);
            if state.catalog.is_null()
                && state.pending.is_none()
                && ui.button("Retry configuration loading").clicked()
            {
                action = Some(json!({"action":"catalog"}));
            }
        }
        if let Some(error) = &state.stream_error {
            ui.colored_label(egui::Color32::LIGHT_RED, error);
        }
        match state.tab {
            Tab::Config => {
                egui::ScrollArea::vertical()
                    .id_salt("sim_config_scroll")
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        config_editor(ui, &mut state, &mut diagnostic, &mut action);
                    });
            }
            Tab::Tasks if state.inspected.is_some() => {
                let job = state
                    .inspected
                    .clone()
                    .expect("detail branch has a selected task");
                ui.horizontal(|ui| {
                    let back = ui.add(
                        egui::Button::new(
                            egui::RichText::new("← 返回列表")
                                .size(13.0)
                                .color(theme::TEXT),
                        )
                        .fill(theme::CARD_BG)
                        .corner_radius(6.0),
                    );
                    point(&mut diagnostic, "back_to_tasks", &back);
                    if back.clicked() {
                        state.inspected = None;
                    }
                    ui.label(
                        egui::RichText::new("任务详情")
                            .size(16.0)
                            .strong()
                            .color(theme::TEXT),
                    );
                });
                ui.add_space(8.0);
                egui::ScrollArea::vertical()
                    .id_salt(("sim_detail_scroll", job["id"].as_str()))
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        task_card(
                            ui,
                            &job,
                            &mut state,
                            &mut diagnostic,
                            &mut action,
                            &mut replay,
                            true,
                        );
                    });
            }
            Tab::Tasks => {
                tasks::show(ui, &mut state, &mut diagnostic, &mut action, &mut replay);
            }
        }
    });
    if state.pending.is_none() {
        if let Some(action) = action.or_else(|| state.queued_difference.take()) {
            fetch(ctx, &mut state, action);
        } else if state.catalog.is_null() && state.requested.is_none() {
            fetch(ctx, &mut state, json!({"action":"catalog"}));
        }
    }
    diagnostic["jobs"] = json!(state.jobs);
    diagnostic["error"] = json!(state.error);
    diagnostic["catalog_ready"] = json!(!state.catalog.is_null());
    diagnostic["pending"] = json!(state.pending.is_some());
    diagnostic["starting"] = json!(state.starting());
    diagnostic["start_label"] = json!(state.start_label());
    diagnostic["start_enabled"] = json!(state.pending.is_none());
    diagnostic["stream_error"] = json!(state.stream_error);
    diagnostic["last_enqueued"] = json!(state.last_enqueued);
    diagnostic["kind"] = json!(state.kind);
    diagnostic["tab"] = json!(state.tab.label());
    diagnostic["filter_text"] = json!(state.filter);
    diagnostic["draft_config"] = state.config();
    diagnostic["inspected_task"] = json!(state.inspected);
    diagnostic["differences"] = state.differences.clone();
    diagnostic["differences_json"] = json!(state.differences_json);
    diagnostic["page"] = json!(if state.tab == Tab::Config {
        "config"
    } else if state.inspected.is_some() {
        "detail"
    } else {
        "tasks"
    });
    diagnostic["config_from"] = json!(state.config_from);
    if state.pending.is_some() {
        ctx.egui_ctx
            .request_repaint_after(std::time::Duration::from_millis(250));
    }
    ctx.egui_ctx.data_mut(|d| {
        d.insert_temp(egui::Id::new("ad_sim_diagnostic"), diagnostic);
        d.insert_temp(id, state);
    });
    replay
}
fn grouped_jobs<'a>(jobs: &'a [Value], group: Group, filter: &str) -> Vec<&'a Value> {
    let filter = filter.trim().to_lowercase();
    let mut result: Vec<_> = jobs
        .iter()
        .filter(|job| {
            Group::for_stage(job["stage"].as_str().unwrap_or("")) == group
                && (filter.is_empty()
                    || format!(
                        "{} {} {} {}",
                        job["id"], job["stage"], job["config"]["kind"], job["config"]["source"]
                    )
                    .to_lowercase()
                    .contains(&filter))
        })
        .collect();
    // Service list order is insertion/FIFO order. Only finished history is reversed.
    if group == Group::Finished {
        result.reverse();
    }
    result
}
fn config_editor(
    ui: &mut egui::Ui,
    state: &mut State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
) {
    let catalog = state.catalog.clone();
    ui.add_space(4.0);
    ui.label(
        egui::RichText::new(if let Some(id) = &state.config_from {
            format!("Based on task {id}")
        } else {
            "New closed-loop task".into()
        })
        .size(13.0)
        .strong()
        .color(theme::TEXT),
    );
    ui.label(
        egui::RichText::new("One virtual clock · frozen map / vehicle for this run")
            .size(11.0)
            .color(theme::TEXT_DIM),
    );
    ui.add_space(12.0);

    section_card(ui, "Input", |ui| {
        ui.label(
            egui::RichText::new("Mode")
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 8.0;
            let half = ((ui.available_width() - 8.0) / 2.0).max(110.0);
            for (kind, title, hint) in [
                ("bag", "LogSim", "Replay a Cyber record"),
                ("world", "WorldSim", "Closed-loop scenario JSON"),
            ] {
                let selected = state.kind == kind;
                let fill = if selected {
                    theme::ACCENT_STRONG
                } else {
                    theme::CARD_BG
                };
                let text = if selected {
                    egui::Color32::WHITE
                } else {
                    theme::TEXT
                };
                let r = ui
                    .add_sized(
                        [half, 40.0],
                        egui::Button::new(
                            egui::RichText::new(format!("{title}\n{hint}"))
                                .size(11.0)
                                .color(text),
                        )
                        .fill(fill)
                        .corner_radius(8.0)
                        .stroke(egui::Stroke::new(
                            1.0,
                            if selected {
                                theme::ACCENT
                            } else {
                                theme::ACCENT.gamma_multiply(0.25)
                            },
                        )),
                    )
                    .on_hover_text(hint);
                point(diagnostic, kind, &r);
                if r.clicked() {
                    state.kind = kind.into();
                    state.source.clear();
                    if kind == "world" {
                        state.modules[4] = true;
                        state.modules[3] = false;
                        state.modules[0] = false;
                        state.modules[1] = !state.modules[5];
                    }
                }
            }
        });
        ui.add_space(10.0);
        if state.kind == "world" {
            ui.horizontal(|ui| {
                for (enabled, label) in [(false, "Single scenario"), (true, "Scenario suite")] {
                    let response = module_chip(ui, label, state.use_suite == enabled);
                    point(
                        diagnostic,
                        if enabled { "suite_mode" } else { "single_mode" },
                        &response,
                    );
                    if response.clicked() {
                        state.use_suite = enabled;
                    }
                }
            });
        }
        let suite_mode = state.kind == "world" && state.use_suite;
        let source = if suite_mode {
            &mut state.suite
        } else {
            &mut state.source
        };
        let before = source.clone();
        diagnostic["Scenario"] = picker(
            ui,
            if suite_mode {
                "Scenario suite"
            } else if state.kind == "bag" {
                "Record"
            } else {
                "Scenario"
            },
            source,
            &catalog[if suite_mode {
                "suites"
            } else if state.kind == "bag" {
                "bags"
            } else {
                "worlds"
            }],
            false,
        );
        if *source != before {
            if let Some(map_id) = catalog["source_maps"][source.as_str()].as_str() {
                if let Some(map) = catalog["maps"].as_array().and_then(|maps| {
                    maps.iter()
                        .find_map(|m| m.as_str().filter(|m| m.rsplit('/').next() == Some(map_id)))
                }) {
                    state.map = map.to_owned();
                }
            }
        }
        if suite_mode {
            ui.add_space(8.0);
            field_label(ui, "Concurrent scenarios");
            themed_drag(
                ui,
                &mut state.concurrency,
                Some(1..=30),
                "concurrency",
                diagnostic,
            );
            ui.label(
                egui::RichText::new("Run every member; each has its own progress and replay.")
                    .size(11.0)
                    .color(theme::TEXT_DIM),
            );
        }
    });

    ui.add_space(10.0);
    section_card(ui, "Environment", |ui| {
        diagnostic["Map"] = picker(ui, "Map", &mut state.map, &catalog["maps"], false);
        ui.add_space(8.0);
        diagnostic["Vehicle"] = picker(
            ui,
            "Vehicle",
            &mut state.vehicle,
            &catalog["vehicles"],
            false,
        );
    });

    ui.add_space(10.0);
    section_card(ui, "Stack", |ui| {
        field_label(ui, "Planner");
        let mut planner = if state.modules[5] {
            "ML_PLANNING"
        } else {
            "PLANNING"
        }
        .to_owned();
        let title = planner.clone();
        let planner_response = themed_combo(ui, "planner", &title, &mut planner, |ui, selected| {
            menu_option(ui, selected, "PLANNING".into(), "Planning");
            menu_option(ui, selected, "ML_PLANNING".into(), "ML Planning");
        });
        point(diagnostic, "planner", &planner_response);
        if planner != title {
            state.modules[2] = planner == "PLANNING";
            state.modules[5] = planner == "ML_PLANNING";
            state.modules[4] = true;
            if state.modules[5] {
                state.modules[0] = false;
                state.modules[1] = false;
                state.modules[3] = false;
                state.model = "perfect_planning".into();
            } else if !state.modules[0] && !state.modules[1] {
                state.modules[1] = true;
            }
        }
        ui.add_space(8.0);
        ui.label(
            egui::RichText::new("Modules")
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
        ui.add_space(6.0);
        ui.horizontal_wrapped(|ui| {
            ui.spacing_mut().item_spacing = egui::vec2(6.0, 6.0);
            for (index, module) in MODULES.iter().enumerate() {
                if matches!(*module, "PLANNING" | "ML_PLANNING") {
                    continue;
                }
                let on = state.modules[index];
                let r = module_chip(ui, module_label(module), on);
                point(diagnostic, module, &r);
                if r.clicked() {
                    state.modules[index] = !on;
                    if !on && index == 0 {
                        state.modules[1] = false;
                    }
                    if !on && index == 1 {
                        state.modules[0] = false;
                    }
                }
            }
        });
        if state.kind == "world" {
            ui.add_space(8.0);
            ui.label(
                egui::RichText::new("Routing + one planner. Planning also requires prediction.")
                    .size(11.0)
                    .color(theme::TEXT_DIM),
            );
            ui.add_space(8.0);
            field_label(ui, "Ego model");
            let mut ego = state.model.clone();
            let ego_button = match ego.as_str() {
                "perfect_planning" => "Perfect planning".to_owned(),
                "kinematic_control" => "Kinematic control".to_owned(),
                other => other.to_owned(),
            };
            themed_combo(ui, "ego_model", &ego_button, &mut ego, |ui, selected| {
                menu_option(
                    ui,
                    selected,
                    "perfect_planning".into(),
                    "Perfect planning trajectory",
                );
                menu_option(
                    ui,
                    selected,
                    "kinematic_control".into(),
                    "Kinematic control",
                );
            });
            state.model = ego;
            ui.add_space(8.0);
            field_label(ui, "Step");
            let mut step_key = state.step.to_string();
            themed_combo(
                ui,
                "step_ms",
                &format!("{} ms", state.step),
                &mut step_key,
                |ui, selected| {
                    for ms in [1_u32, 2, 5, 10] {
                        menu_option(ui, selected, ms.to_string(), &format!("{ms} ms"));
                    }
                },
            );
            if let Ok(ms) = step_key.parse::<u32>() {
                state.step = ms;
            }
        }
        ui.add_space(10.0);
        ui.horizontal(|ui| {
            ui.vertical(|ui| {
                field_label(ui, "Seed");
                themed_drag(ui, &mut state.seed, None, "seed", diagnostic);
            });
            ui.add_space(12.0);
            ui.vertical(|ui| {
                field_label(ui, "Runs");
                themed_drag(ui, &mut state.repeat, Some(1..=3), "runs", diagnostic);
            });
        });
        ui.add_space(6.0);
        ui.label(
            egui::RichText::new(
                "2+ runs compare message order, timestamps and exact algorithm values.",
            )
            .size(11.0)
            .color(theme::TEXT_DIM),
        );
    });

    if !state.config_extra.is_empty() {
        ui.add_space(10.0);
        section_card(ui, "Advanced", |ui| {
            for (key, value) in &mut state.config_extra {
                if ["begin_s", "end_s", "timeout_s"].contains(&key.as_str()) {
                    ui.horizontal(|ui| {
                        ui.label(
                            egui::RichText::new(key.as_str())
                                .size(11.0)
                                .color(theme::TEXT),
                        );
                        if key == "timeout_s" {
                            if let Some(mut seconds) = value.as_u64() {
                                if ui
                                    .add(egui::DragValue::new(&mut seconds).range(10..=7200))
                                    .changed()
                                {
                                    *value = json!(seconds);
                                }
                            } else {
                                ui.colored_label(egui::Color32::LIGHT_RED, "Invalid timeout");
                            }
                        } else if let Some(mut seconds) = value.as_f64() {
                            if ui
                                .add(egui::DragValue::new(&mut seconds).range(0.0..=f64::MAX))
                                .changed()
                            {
                                *value = json!(seconds);
                            }
                        } else {
                            ui.colored_label(egui::Color32::LIGHT_RED, "Invalid time range");
                        }
                    });
                } else {
                    ui.add(
                        egui::Label::new(
                            egui::RichText::new(format!("{key}: {value} (preserved)"))
                                .color(theme::TEXT_DIM)
                                .size(11.0),
                        )
                        .wrap(),
                    );
                }
            }
        });
    }

    ui.add_space(14.0);
    let r = ui.add_enabled(
        state.pending.is_none(),
        egui::Button::new(
            egui::RichText::new(state.start_label())
                .size(14.0)
                .strong()
                .color(egui::Color32::WHITE),
        )
        .fill(theme::ACCENT_STRONG)
        .corner_radius(10.0)
        .min_size(egui::vec2(ui.available_width(), 40.0)),
    );
    point(diagnostic, "enqueue", &r);
    if r.clicked() {
        *action = Some(
            json!({"action":if state.kind == "world" && state.use_suite { "enqueue_suite" } else { "enqueue" },"config":state.config()}),
        );
    }
    ui.add_space(8.0);
}

fn module_label(module: &str) -> &'static str {
    match module {
        "PREDICTION" => "Prediction",
        "fake_prediction" => "Fake prediction",
        "PLANNING" => "Planning",
        "ML_PLANNING" => "ML Planning",
        "CONTROL" => "Control",
        "ROUTING" => "Routing",
        _ => "Module",
    }
}

fn section_card(ui: &mut egui::Ui, title: &str, add_contents: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::new()
        .fill(theme::RAIL_BG)
        .stroke(egui::Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.16)))
        .corner_radius(10.0)
        .inner_margin(egui::Margin::symmetric(12, 12))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.label(
                egui::RichText::new(title.to_ascii_uppercase())
                    .size(10.0)
                    .strong()
                    .color(theme::ACCENT),
            );
            ui.add_space(8.0);
            add_contents(ui);
        });
}

fn field_label(ui: &mut egui::Ui, text: &str) {
    ui.label(
        egui::RichText::new(text)
            .size(11.0)
            .color(theme::TEXT)
            .strong(),
    );
    ui.add_space(4.0);
}

fn module_chip(ui: &mut egui::Ui, label: &str, on: bool) -> egui::Response {
    let fill = if on {
        theme::ACCENT_STRONG.gamma_multiply(0.85)
    } else {
        theme::CARD_BG
    };
    let text = if on {
        egui::Color32::WHITE
    } else {
        theme::TEXT
    };
    ui.add(
        egui::Button::new(egui::RichText::new(label).size(12.0).color(text))
            .fill(fill)
            .corner_radius(0.0)
            .stroke(egui::Stroke::new(
                1.0,
                if on {
                    theme::ACCENT
                } else {
                    theme::ACCENT.gamma_multiply(0.28)
                },
            ))
            .min_size(egui::vec2(0.0, 28.0)),
    )
}

fn dropdown_trigger(ui: &mut egui::Ui, text: &str) -> egui::Response {
    let height = 34.0;
    let width = ui.available_width();
    let (rect, response) = ui.allocate_exact_size(egui::vec2(width, height), egui::Sense::click());

    let fill = if response.hovered() || response.has_focus() {
        theme::CARD_BG_HOVER
    } else {
        theme::CARD_BG
    };
    let stroke = egui::Stroke::new(
        1.0,
        if response.hovered() {
            theme::ACCENT.gamma_multiply(0.55)
        } else {
            theme::ACCENT.gamma_multiply(0.35)
        },
    );
    ui.painter()
        .rect(rect, 8.0, fill, stroke, egui::StrokeKind::Inside);

    // Right chevron well — makes this read as a select, not a label.
    let chevron_w = 28.0;
    let split_x = rect.right() - chevron_w;
    ui.painter().vline(
        split_x,
        rect.y_range().shrink(7.0),
        egui::Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.28)),
    );

    let text_rect = egui::Rect::from_min_max(
        egui::pos2(rect.left() + 12.0, rect.top()),
        egui::pos2(split_x - 6.0, rect.bottom()),
    );
    ui.painter().text(
        text_rect.left_center(),
        egui::Align2::LEFT_CENTER,
        text,
        egui::FontId::proportional(13.0),
        theme::TEXT,
    );

    // Drawn triangle (no Unicode glyph — web fonts often miss ▾ and show □).
    let c = egui::pos2(rect.right() - chevron_w * 0.5, rect.center().y + 0.5);
    let s = 4.5;
    ui.painter().add(egui::Shape::convex_polygon(
        vec![
            egui::pos2(c.x - s, c.y - s * 0.55),
            egui::pos2(c.x + s, c.y - s * 0.55),
            egui::pos2(c.x, c.y + s * 0.7),
        ],
        theme::TEXT_DIM,
        egui::Stroke::NONE,
    ));

    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

fn themed_combo(
    ui: &mut egui::Ui,
    id: &str,
    button_text: &str,
    selected: &mut String,
    add_contents: impl FnOnce(&mut egui::Ui, &mut String),
) -> egui::Response {
    let response = dropdown_trigger(ui, button_text);
    egui::Popup::menu(&response)
        .id(egui::Id::new(("themed_combo", id)))
        .align(egui::RectAlign::BOTTOM_START)
        .gap(4.0)
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_min_width(response.rect.width().max(220.0));
                add_contents(ui, selected);
            });
        });
    response
}

fn themed_drag(
    ui: &mut egui::Ui,
    value: &mut u32,
    range: Option<std::ops::RangeInclusive<u32>>,
    id: &str,
    diagnostic: &mut Value,
) {
    ui.scope(|ui| {
        ui.visuals_mut().extreme_bg_color = theme::CARD_BG;
        ui.visuals_mut().override_text_color = Some(theme::TEXT);
        ui.visuals_mut().widgets.inactive.bg_fill = theme::CARD_BG;
        ui.visuals_mut().widgets.inactive.weak_bg_fill = theme::CARD_BG;
        let mut drag = egui::DragValue::new(value);
        if let Some(range) = range {
            drag = drag.range(range);
        }
        let r = ui.add(drag);
        point(diagnostic, id, &r);
    });
}

fn dark_menu_frame(ui: &mut egui::Ui, add_contents: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::new()
        .fill(theme::PANEL_BG)
        .stroke(egui::Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.4)))
        .corner_radius(8.0)
        .inner_margin(egui::Margin::symmetric(8, 8))
        .show(ui, |ui| {
            ui.visuals_mut().override_text_color = Some(theme::TEXT);
            ui.visuals_mut().widgets.inactive.fg_stroke = egui::Stroke::new(1.0, theme::TEXT);
            ui.visuals_mut().widgets.hovered.weak_bg_fill = theme::CARD_BG_HOVER;
            ui.visuals_mut().selection.bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.45);
            add_contents(ui);
        });
}

fn menu_option(ui: &mut egui::Ui, selected: &mut String, value: String, label: &str) -> bool {
    let on = *selected == value;
    let text = egui::RichText::new(label).size(12.0).color(if on {
        egui::Color32::WHITE
    } else {
        theme::TEXT
    });
    let response = ui
        .add_sized(
            [ui.available_width(), 28.0],
            egui::Button::new(text)
                .fill(if on {
                    theme::ACCENT_STRONG.gamma_multiply(0.7)
                } else {
                    egui::Color32::TRANSPARENT
                })
                .corner_radius(6.0),
        )
        .on_hover_text(&value);
    if response.clicked() {
        *selected = value;
        true
    } else {
        false
    }
}

fn picker(
    ui: &mut egui::Ui,
    label: &str,
    selected: &mut String,
    values: &Value,
    optional: bool,
) -> Value {
    field_label(ui, label);
    let short = if selected.is_empty() {
        if optional {
            "Workspace defaults".to_owned()
        } else {
            "Choose…".to_owned()
        }
    } else if let Some((_, name)) = selected.rsplit_once('/') {
        name.to_owned()
    } else {
        selected.clone()
    };

    let response = dropdown_trigger(ui, &short);

    egui::Popup::menu(&response)
        .id(egui::Id::new(("path_picker", label)))
        .align(egui::RectAlign::BOTTOM_START)
        .gap(4.0)
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_min_width(response.rect.width().max(260.0));
                ui.set_max_height(280.0);
                egui::ScrollArea::vertical().show(ui, |ui| {
                    if optional {
                        menu_option(ui, selected, String::new(), "Workspace defaults");
                    }
                    if let Some(values) = values.as_array() {
                        for item in values {
                            if let Some(path) = item.as_str() {
                                let leaf = path.rsplit('/').next().unwrap_or(path);
                                menu_option(ui, selected, path.to_owned(), leaf);
                            }
                        }
                    } else if !optional {
                        ui.label(
                            egui::RichText::new("No catalog entries yet")
                                .size(11.0)
                                .color(theme::TEXT_DIM),
                        );
                    }
                });
            });
        });

    ui.add_space(2.0);
    let mut path_open = !selected.is_empty()
        && values
            .as_array()
            .is_none_or(|items| !items.iter().any(|v| v.as_str() == Some(selected.as_str())));
    egui::CollapsingHeader::new(
        egui::RichText::new("Paste server path")
            .size(11.0)
            .color(theme::TEXT_DIM),
    )
    .id_salt(("paste_path", label))
    .default_open(path_open)
    .show(ui, |ui| {
        ui.scope(|ui| {
            ui.visuals_mut().extreme_bg_color = theme::CARD_BG;
            ui.visuals_mut().text_edit_bg_color = Some(theme::CARD_BG);
            ui.visuals_mut().override_text_color = Some(theme::TEXT);
            ui.visuals_mut().widgets.inactive.bg_fill = theme::CARD_BG;
            ui.visuals_mut().widgets.inactive.weak_bg_fill = theme::CARD_BG;
            let r = ui.add(
                egui::TextEdit::singleline(selected)
                    .desired_width(ui.available_width())
                    .background_color(theme::CARD_BG)
                    .hint_text(egui::RichText::new("/apollo_workspace/...").color(theme::TEXT_DIM))
                    .text_color(theme::TEXT)
                    .margin(egui::Margin::symmetric(10, 8)),
            );
            path_open = r.has_focus();
            let _ = path_open;
        });
    });

    json!([response.rect.center().x, response.rect.center().y])
}
fn config_snapshot(ui: &mut egui::Ui, config: &Value) {
    let Some(fields) = config.as_object() else {
        ui.colored_label(
            egui::Color32::LIGHT_RED,
            "Task has no configuration snapshot",
        );
        return;
    };
    // Include every submitted field, including range/timeout and future options.
    for (key, value) in fields {
        let label = match key.as_str() {
            "kind" => "Input type",
            "source" => "Scenario",
            "map" => "Map",
            "vehicle" => "Vehicle",
            "profile" => "Profile (applied)",
            "modules" => "Algorithm modules",
            "model" => "Ego model",
            "repeat" => "Runs",
            "seed" => "Seed",
            "step_ms" => "Step / ms",
            "timeout_s" => "Max duration / s",
            "begin_s" => "Bag start / s",
            "end_s" => "Bag end / s (0 = full range)",
            other => other,
        };
        ui.label(egui::RichText::new(label).color(theme::TEXT_DIM).size(11.0));
        let text = if key == "profile" && value == "" {
            "Workspace defaults".into()
        } else if let Some(text) = value.as_str() {
            text.to_owned()
        } else {
            value.to_string()
        };
        ui.add(egui::Label::new(text).wrap());
        ui.add_space(6.0);
    }
}

fn stage_style(stage: &str) -> (egui::Color32, egui::Color32, &'static str) {
    // (accent / badge text, badge fill, label)
    match stage {
        "completed" => (
            egui::Color32::from_rgb(0x34, 0xD3, 0x99),
            egui::Color32::from_rgb(0x06, 0x4E, 0x3B),
            "completed",
        ),
        "failed" => (
            egui::Color32::from_rgb(0xF8, 0x71, 0x71),
            egui::Color32::from_rgb(0x7F, 0x1D, 0x1D),
            "failed",
        ),
        "cancelled" => (
            egui::Color32::from_rgb(0xFB, 0xBF, 0x24),
            egui::Color32::from_rgb(0x78, 0x35, 0x0F),
            "cancelled",
        ),
        "interrupted" => (
            egui::Color32::from_rgb(0xFB, 0xBF, 0x24),
            egui::Color32::from_rgb(0x78, 0x35, 0x0F),
            "interrupted",
        ),
        "queued" => (theme::ACCENT, theme::PANEL_BG, "queued"),
        _ => (
            egui::Color32::from_rgb(0x60, 0xA5, 0xFA),
            theme::PANEL_BG,
            "running",
        ),
    }
}

fn task_card(
    ui: &mut egui::Ui,
    job: &Value,
    state: &mut State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
    replay: &mut Option<(String, bool)>,
    detail: bool,
) {
    if let Some(name) = job["suite_name"].as_str() {
        ui.label(
            egui::RichText::new(format!(
                "{} · {}/{}",
                name, job["suite_index"], job["suite_size"]
            ))
            .size(11.0)
            .color(theme::TEXT_DIM),
        );
    }
    let job_id = job["id"].as_str().unwrap_or("Unknown task");
    let stage = job["stage"].as_str().unwrap_or("unknown");
    let (accent, badge_bg, stage_label) = stage_style(stage);
    let kind = job["config"]["kind"].as_str().unwrap_or("?");
    let source = job["config"]["source"].as_str().unwrap_or("");
    let source_leaf = source.rsplit('/').next().unwrap_or(source);
    let title = if source_leaf.is_empty() {
        format!("{kind} · {job_id}")
    } else {
        source_leaf.to_owned()
    };
    let id_short = &job_id[..job_id.len().min(8)];

    ui.push_id(job_id, |ui| {
        let frame = egui::Frame::new()
            .fill(theme::RAIL_BG)
            .stroke(egui::Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.18)))
            .corner_radius(10.0)
            .inner_margin(egui::Margin {
                left: 14,
                right: 10,
                top: 10,
                bottom: 10,
            })
            .show(ui, |ui| {
                ui.set_width(ui.available_width());

                // Fixed-height row: bare with_layout would expand to the full
                // ScrollArea height and leave a giant empty card.
                ui.allocate_ui_with_layout(
                    egui::vec2(ui.available_width(), 22.0),
                    egui::Layout::right_to_left(egui::Align::Center),
                    |ui| {
                        egui::Frame::new()
                            .fill(badge_bg)
                            .corner_radius(4.0)
                            .inner_margin(egui::Margin::symmetric(6, 2))
                            .show(ui, |ui| {
                                ui.label(
                                    egui::RichText::new(stage_label)
                                        .size(10.0)
                                        .strong()
                                        .color(accent),
                                );
                            });
                        let title_r = ui.add(
                            egui::Label::new(
                                egui::RichText::new(&title)
                                    .size(13.0)
                                    .strong()
                                    .color(theme::TEXT),
                            )
                            .truncate()
                            .sense(egui::Sense::click()),
                        );
                        point(diagnostic, &format!("inspect_{job_id}"), &title_r);
                        if title_r
                            .on_hover_text(format!("{source}\n{job_id}"))
                            .clicked()
                        {
                            state.inspect(job);
                        }
                    },
                );

                ui.add_space(3.0);
                ui.horizontal(|ui| {
                    ui.label(
                        egui::RichText::new(format!("{kind}  ·  {id_short}"))
                            .size(11.0)
                            .color(theme::TEXT_DIM),
                    );
                    if let Some(run) = job["run"].as_u64() {
                        ui.label(
                            egui::RichText::new(format!(
                                "·  run {run}/{}",
                                job["config"]["repeat"]
                            ))
                            .size(11.0)
                            .color(theme::TEXT_DIM),
                        );
                    }
                });

                if let Some(progress) = job["progress"].as_f64() {
                    ui.add_space(6.0);
                    ui.add(
                        egui::ProgressBar::new((progress as f32 / 100.0).clamp(0.0, 1.0))
                            .desired_width(ui.available_width())
                            .fill(accent.gamma_multiply(0.85))
                            .show_percentage(),
                    );
                }

                if let Some(error) = job["error"].as_str() {
                    ui.add_space(6.0);
                    ui.add(
                        egui::Label::new(
                            egui::RichText::new(error.lines().next().unwrap_or(error))
                                .size(11.0)
                                .color(egui::Color32::from_rgb(0xFE, 0xCA, 0xCA)),
                        )
                        .wrap(),
                    );
                }

                ui.add_space(8.0);
                ui.horizontal_wrapped(|ui| {
                    ui.spacing_mut().item_spacing.x = 6.0;
                    ui.spacing_mut().item_spacing.y = 4.0;
                    let open = ui.add(
                        egui::Button::new(
                            egui::RichText::new("Open").size(11.0).color(theme::TEXT),
                        )
                        .fill(theme::CARD_BG)
                        .corner_radius(6.0)
                        .min_size(egui::vec2(0.0, 26.0)),
                    );
                    if open.clicked() {
                        state.inspect(job);
                    }
                    let reuse = ui
                        .add(
                            egui::Button::new(
                                egui::RichText::new("Reuse config")
                                    .size(11.0)
                                    .color(theme::TEXT),
                            )
                            .fill(theme::CARD_BG)
                            .corner_radius(6.0)
                            .min_size(egui::vec2(0.0, 26.0)),
                        )
                        .on_hover_text("Load into Config editor; starting creates a new task");
                    point(diagnostic, &format!("view_config_{job_id}"), &reuse);
                    if reuse.clicked()
                        && let Err(error) = state.edit_config(job)
                    {
                        state.error = Some(error);
                    }
                    if Group::for_stage(stage) != Group::Finished {
                        let cancel = ui.add_enabled(
                            state.pending.is_none(),
                            egui::Button::new(
                                egui::RichText::new("Cancel").size(11.0).color(theme::TEXT),
                            )
                            .fill(theme::CARD_BG)
                            .corner_radius(6.0)
                            .min_size(egui::vec2(0.0, 26.0)),
                        );
                        point(diagnostic, &format!("cancel_{job_id}"), &cancel);
                        if cancel.clicked() {
                            *action = Some(json!({"action":"cancel","id":job_id}));
                        }
                    }
                    if Group::for_stage(stage) == Group::Finished
                        && let Some(outputs) = job["outputs"].as_array()
                    {
                        for (index, path) in outputs.iter().enumerate() {
                            if let Some(path) = path.as_str() {
                                let r = ui.add(
                                    egui::Button::new(
                                        egui::RichText::new(format!("Replay {}", index + 1))
                                            .size(11.0)
                                            .color(egui::Color32::WHITE),
                                    )
                                    .fill(theme::ACCENT_STRONG.gamma_multiply(0.85))
                                    .corner_radius(6.0)
                                    .min_size(egui::vec2(0.0, 26.0)),
                                );
                                point(diagnostic, &format!("replay_{job_id}_{index}"), &r);
                                if r.clicked() {
                                    *replay = Some((
                                        path.into(),
                                        job["config"]["modules"]
                                            .as_array()
                                            .is_some_and(|m| m.iter().any(|m| m == "CONTROL")),
                                    ));
                                }
                            }
                        }
                    }
                });

                if detail {
                    ui.add_space(8.0);
                    ui.separator();
                    ui.add_space(6.0);
                    if Group::for_stage(stage) == Group::Finished
                        && job["outputs"].as_array().is_some_and(|v| v.len() >= 2)
                    {
                        difference_inspector(ui, job, state, diagnostic);
                    }
                    egui::CollapsingHeader::new(
                        egui::RichText::new("Stages / analysis")
                            .color(theme::TEXT)
                            .size(12.0),
                    )
                    .id_salt(("stages", detail))
                    .default_open(true)
                    .show(ui, |ui| {
                        if let Some(error) = job["error"].as_str() {
                            ui.add(
                                egui::Label::new(egui::RichText::new(error).color(theme::TEXT))
                                    .wrap(),
                            );
                        }
                        if let Some(history) = job["history"].as_array() {
                            for entry in history {
                                ui.label(
                                    egui::RichText::new(
                                        entry["stage"].as_str().unwrap_or("Unknown stage"),
                                    )
                                    .color(theme::TEXT_DIM)
                                    .size(11.0),
                                );
                            }
                        }
                        if !job["analysis"].is_null() {
                            let expectation = &job["analysis"]["scenario_expectation"];
                            if let Some(mode) = expectation["expectation"].as_str() {
                                let label = match mode {
                                    "safe_stop" => "Blocked road · safe stop",
                                    "yield_then_proceed" => "Yield then reach destination",
                                    "reach_goal" => "Reach destination",
                                    other => other,
                                };
                                let status =
                                    expectation["status"].as_str().unwrap_or("NOT_EVALUATED");
                                ui.label(
                                    egui::RichText::new(format!("Expected: {label} · {status}"))
                                        .color(if status == "FAIL" {
                                            egui::Color32::from_rgb(255, 110, 110)
                                        } else {
                                            theme::TEXT
                                        }),
                                );
                                if let Some(reason) = expectation["reason"].as_str() {
                                    ui.label(
                                        egui::RichText::new(reason)
                                            .color(theme::TEXT_DIM)
                                            .size(11.0),
                                    );
                                }
                            }
                            let collision = &job["analysis"]["collision"];
                            let continuity = &job["analysis"]["planning_continuity"];
                            let continuity_status =
                                continuity["status"].as_str().unwrap_or("NOT_EVALUATED");
                            ui.label(
                                egui::RichText::new(format!(
                                    "Planning continuity: {continuity_status}"
                                ))
                                .color(
                                    if continuity_status == "FAIL" {
                                        egui::Color32::from_rgb(255, 110, 110)
                                    } else {
                                        theme::TEXT
                                    },
                                ),
                            );
                            if continuity_status == "FAIL" {
                                ui.label(
                                    egui::RichText::new(continuity["runs"].to_string())
                                        .color(theme::TEXT_DIM)
                                        .size(11.0),
                                );
                            }
                            let status = collision["status"].as_str().unwrap_or("NOT_EVALUATED");
                            ui.label(
                                egui::RichText::new(format!(
                                    "Collision: {status} · {} contacts",
                                    collision["collision_count"].as_u64().unwrap_or(0)
                                ))
                                .color(if status == "FAIL" {
                                    egui::Color32::from_rgb(255, 110, 110)
                                } else {
                                    theme::TEXT
                                }),
                            );
                            if let Some(runs) = collision["runs"].as_array() {
                                for run in runs {
                                    if let Some(contacts) = run["contacts"].as_array() {
                                        for contact in contacts {
                                            ui.label(format!(
                                                "Run {} · {} · first contact {:.2} s",
                                                run["run"],
                                                contact["actor_id"].as_str().unwrap_or("?"),
                                                contact["first_time_s"].as_f64().unwrap_or(0.0)
                                            ));
                                        }
                                    }
                                }
                            }
                            ui.label(
                                egui::RichText::new(format!(
                                    "Determinism: {}",
                                    job["analysis"]["determinism"]
                                ))
                                .color(theme::TEXT),
                            );
                            ui.add(
                                egui::Label::new(
                                    egui::RichText::new(
                                        job["analysis"]["topic_message_counts"].to_string(),
                                    )
                                    .color(theme::TEXT_DIM)
                                    .size(11.0),
                                )
                                .wrap(),
                            );
                        }
                        if let Some(path) = job["log_path"].as_str() {
                            ui.add(
                                egui::Label::new(
                                    egui::RichText::new(format!("Log: {path}"))
                                        .color(theme::TEXT_DIM)
                                        .size(11.0),
                                )
                                .wrap(),
                            );
                        }
                        if !job["simulation"].is_null() {
                            ui.add(
                                egui::Label::new(
                                    egui::RichText::new(format!(
                                        "Simulation: {}",
                                        job["simulation"]
                                    ))
                                    .color(theme::TEXT_DIM)
                                    .size(11.0),
                                )
                                .wrap(),
                            );
                        }
                        if !job["analysis"].is_null() {
                            egui::CollapsingHeader::new("Full result analysis").show(ui, |ui| {
                                ui.add(
                                    egui::Label::new(
                                        serde_json::to_string_pretty(&job["analysis"])
                                            .expect("JSON values are serializable"),
                                    )
                                    .wrap(),
                                );
                            });
                        }
                    });
                    egui::CollapsingHeader::new(
                        egui::RichText::new("Submitted configuration")
                            .color(theme::TEXT)
                            .size(12.0),
                    )
                    .show(ui, |ui| {
                        config_snapshot(ui, &job["config"]);
                    });
                }
            });

        let full = frame.response.rect;
        let strip = egui::Rect::from_min_max(
            egui::pos2(full.left(), full.top()),
            egui::pos2(full.left() + 4.0, full.bottom()),
        );
        ui.painter().rect_filled(
            strip,
            egui::CornerRadius {
                nw: 10,
                sw: 10,
                ne: 0,
                se: 0,
            },
            accent,
        );
        ui.add_space(8.0);
    });
}
fn difference_inspector(ui: &mut egui::Ui, job: &Value, state: &mut State, diagnostic: &mut Value) {
    ui.separator();
    ui.label("Determinism — message differences");
    let previous_selection = (state.comparison, state.include_messages);
    ui.scope(|ui| {
        egui::ComboBox::from_id_salt("difference_runs")
            .selected_text(format!("Run 1 vs run {}", state.comparison + 2))
            .show_ui(ui, |ui| {
                for comparison in 0..job["outputs"].as_array().map_or(0, |v| v.len() - 1) {
                    ui.selectable_value(
                        &mut state.comparison,
                        comparison,
                        format!("Run 1 vs run {}", comparison + 2),
                    );
                }
            });
        let r = ui.checkbox(&mut state.include_messages, "Include complete messages");
        point(diagnostic, "diff_include_messages", &r);
    });
    if previous_selection != (state.comparison, state.include_messages) {
        state.differences = Value::Null;
        state.differences_json.clear();
    }
    if let Some(total) =
        job["analysis"]["comparisons"][state.comparison]["different_messages"].as_u64()
    {
        ui.label(format!("{total} differing messages"));
    }
    let mut load = false;
    ui.horizontal_wrapped(|ui| {
        ui.label("Difference #");
        let r = ui.add(egui::DragValue::new(&mut state.difference_offset).range(0..=u64::MAX));
        point(diagnostic, "diff_offset", &r);
        let r = ui.button("View JSON");
        point(diagnostic, "diff_load", &r);
        load |= r.clicked();
        let r = ui.add_enabled(
            state.differences["offset"].as_u64().is_some_and(|v| v > 0),
            egui::Button::new("Previous"),
        );
        point(diagnostic, "diff_previous", &r);
        if r.clicked() {
            state.difference_offset = state.differences["offset"]
                .as_u64()
                .unwrap_or(0)
                .saturating_sub(1);
            load = true;
        }
        let r = ui.add_enabled(
            state.differences["has_more"] == true,
            egui::Button::new("Next"),
        );
        point(diagnostic, "diff_next", &r);
        if r.clicked() {
            state.difference_offset = state.differences["next_offset"].as_u64().unwrap_or(0);
            load = true;
        }
    });
    if load {
        state.queued_difference = Some(json!({"action":"differences", "id":job["id"],
            "comparison":state.comparison,"offset":state.difference_offset,"limit":1,
            "include_messages":state.include_messages}));
    }
    ui.weak("Indexes start at 0. Clock values are nanoseconds. Missing fields differ from zero.");
    if !state.differences_json.is_empty() {
        if let Some(first) = state.differences["diffs"]
            .as_array()
            .and_then(|v| v.first())
        {
            for side in ["left", "right"] {
                if first[side].is_null() {
                    ui.label(format!("{side}: message missing"));
                } else {
                    ui.add(
                        egui::Label::new(format!(
                            "{side}: {} · publish_time {} ns",
                            first[side]["topic"].as_str().unwrap_or("?"),
                            first[side]["publish_time"].as_str().unwrap_or("?")
                        ))
                        .wrap(),
                    );
                }
            }
            ui.label(format!(
                "{} field changes",
                first["field_changes"].as_array().map_or(0, Vec::len)
            ));
        }
        ui.label(format!(
            "Showing difference #{}",
            state.differences["offset"]
        ));
        if ui.button("Copy JSON list").clicked() {
            ui.ctx().copy_text(state.differences_json.clone());
        }
        // Virtualized lines avoid laying out thousands of trajectory fields on
        // every frame. Copy exports the complete JSON, without truncation.
        let lines: Vec<_> = state.differences_json.lines().collect();
        let height = ui.text_style_height(&egui::TextStyle::Monospace);
        egui::ScrollArea::both()
            .id_salt((
                "difference_json",
                state.comparison,
                state.differences["offset"].as_u64(),
            ))
            .max_height(380.0)
            .auto_shrink([false, false])
            .show_rows(ui, height, lines.len(), |ui, range| {
                for line in &lines[range] {
                    ui.add(egui::Label::new(egui::RichText::new(*line).monospace()).extend());
                }
            });
    }
}
#[cfg(target_arch = "wasm32")]
fn fetch(ctx: &AppContext<'_>, state: &mut State, request: Value) {
    let Some(origin) = web_sys::window().and_then(|w| w.location().origin().ok()) else {
        state.error = Some("Browser origin unavailable".into());
        return;
    };
    let reply = Reply::default();
    state.pending = Some(reply.clone());
    state.pending_action = request["action"].as_str().map(str::to_owned);
    state.requested = Some(web_time::Instant::now());
    let egui = ctx.egui_ctx.clone();
    let mut request = ehttp::Request::post(
        format!("{origin}/api/sim"),
        request.to_string().into_bytes(),
    );
    request.headers.insert("Content-Type", "application/json");
    ehttp::fetch(request, move |result| {
        *reply.lock() = Some(result.and_then(|response| {
            if !response.ok {
                return Err(format!(
                    "Simulation HTTP {}: {}",
                    response.status,
                    String::from_utf8_lossy(&response.bytes)
                ));
            }
            serde_json::from_slice(&response.bytes)
                .map_err(|e| format!("Invalid simulation response: {e}"))
        }));
        egui.request_repaint();
    });
}
#[cfg(not(target_arch = "wasm32"))]
fn fetch(_: &AppContext<'_>, state: &mut State, _: Value) {
    state.error = Some("Task management is available in the web viewer".into());
    state.requested = Some(web_time::Instant::now());
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pushed_jobs_preserve_submission_draft_and_selection() {
        let mut state = State::default();
        state.source = "draft.record".into();
        state.apply_jobs(
            &[
                json!({"id":"a","stage":"queued"}),
                json!({"id":"b","stage":"queued"}),
            ],
            true,
        );
        let selected = state.jobs[0].clone();
        state.inspect(&selected);
        state.filter = "a".into();
        let draft = state.config();
        state.pending = Some(Reply::default());
        state.pending_action = Some("enqueue".into());
        state.apply_jobs(&[json!({"id":"a","stage":"completed"})], false);
        assert_eq!(state.jobs.len(), 2);
        assert_eq!(state.inspected.as_ref().unwrap()["stage"], "completed");
        assert_eq!(state.filter, "a");
        assert_eq!(state.config(), draft);
        assert!(state.pending.is_some());
        assert_eq!(state.start_label(), "Starting…");
        state.receive(Err("Invalid map".into()));
        assert_eq!(state.start_label(), "Start simulation");
        state.apply_jobs(&[json!({"id":"b","stage":"failed"})], false);
        assert_eq!(state.error.as_deref(), Some("Invalid map"));
        assert_eq!(state.start_label(), "Start simulation");
        state.pending_action = Some("catalog".into());
        assert_eq!(state.start_label(), "Start simulation");
        state.pending_action = Some("enqueue_suite".into());
        assert_eq!(state.start_label(), "Starting…");
    }

    #[test]
    fn difference_reply_does_not_enqueue_or_replace_the_draft() {
        let mut state = State::default();
        state.source = "my-unsubmitted-scene".into();
        state.inspect(&json!({"id":"task-a","stage":"failed"}));
        let draft = state.config();
        let page = json!({"task_id":"task-a","comparison":0,"include_messages":false,
            "offset":21,"diffs":[{"stream_index":55,"field_changes":[{"field":"pose.position.z","left_value":0.4,"right_value":0.0}]}]});
        state.difference_offset = 21;
        state.receive(Ok(json!({"status":"ok","differences":page})));
        assert_eq!(state.differences, page);
        assert_eq!(
            serde_json::from_str::<Value>(&state.differences_json).unwrap(),
            page["diffs"]
        );
        assert_eq!(state.config(), draft);
        assert!(state.last_enqueued.is_none());
        state.inspect(&json!({"id":"task-b"}));
        state.receive(Ok(json!({"status":"ok","differences":page})));
        assert!(state.differences.is_null());
        assert!(state.differences_json.is_empty());
    }
    #[test]
    fn protobuf_json_float_values_round_trip_exactly() {
        let value: Value = serde_json::from_str("1.0963480266956085e-10").unwrap();
        assert_eq!(
            value.as_f64().unwrap().to_bits(),
            1.0963480266956085e-10_f64.to_bits()
        );
    }
    #[test]
    fn task_groups_preserve_fifo_and_terminal_statuses() {
        let jobs: Vec<_> = [
            ("q1", "queued"),
            ("r1", "map_update"),
            ("q2", "queued"),
            ("f1", "failed"),
            ("c1", "completed"),
            ("x1", "cancelled"),
            ("i1", "interrupted"),
        ]
        .into_iter()
        .map(|(id, stage)| json!({"id":id,"stage":stage}))
        .collect();
        let ids = |g| {
            grouped_jobs(&jobs, g, "")
                .iter()
                .map(|j| j["id"].as_str().unwrap().to_owned())
                .collect::<Vec<_>>()
        };
        assert_eq!(ids(Group::Queued), ["q1", "q2"]);
        assert_eq!(ids(Group::Running), ["r1"]);
        assert_eq!(ids(Group::Finished), ["i1", "x1", "c1", "f1"]);
        assert_eq!(
            grouped_jobs(&jobs, Group::Finished, "FAILED")[0]["id"],
            "f1"
        );
    }
    #[test]
    fn accepted_task_opens_tasks_but_failure_keeps_config() {
        let mut state = State::default();
        state.receive(Err("Invalid map".into()));
        assert!(state.tab == Tab::Config);
        assert!(state.last_enqueued.is_none());
        state.receive(Ok(json!({"status":"ok","id":"accepted"})));
        assert!(state.tab == Tab::Tasks);
        assert_eq!(state.last_enqueued.as_deref(), Some("accepted"));
    }
    #[test]
    fn inspecting_snapshot_never_changes_draft_or_task() {
        let mut state = State {
            source: "draft.record".into(),
            ..Default::default()
        };
        let before = state.config();
        let task = json!({"id":"old","config":{"source":"old.record","timeout_s":900,"future_option":true}});
        state.inspect(&task);
        assert!(state.tab == Tab::Tasks);
        assert_eq!(state.inspected.as_ref(), Some(&task));
        assert_eq!(state.config(), before);
        state.inspected = None;
        assert_eq!(state.config(), before);
    }
    #[test]
    fn opening_config_round_trips_all_fields_and_edits_only_a_copy() {
        let mut config = State::default().config();
        config["kind"] = json!("world");
        config["model"] = json!("kinematic_control");
        config["source"] = json!("scene.json");
        config["step_ms"] = json!(5);
        config["seed"] = json!(42);
        config["repeat"] = json!(3);
        config["timeout_s"] = json!(900);
        config["begin_s"] = json!(1.25);
        config["end_s"] = json!(9.0);
        config["future_option"] = json!({"keep":true});
        let job = json!({"id":"original", "config":config});
        let mut state = State::default();
        state.edit_config(&job).unwrap();
        assert!(state.tab == Tab::Config && state.inspected.is_none());
        assert_eq!(state.config(), config);
        state.seed = 99;
        state.source = "modified.json".into();
        assert_eq!(job["config"], config);
        assert_eq!(state.config()["seed"], 99);
        assert_eq!(state.config()["future_option"], config["future_option"]);
    }
    #[test]
    fn invalid_config_never_partially_overwrites_draft() {
        let mut state = State::default();
        let before = state.config();
        assert!(
            state
                .edit_config(&json!({"config":{"kind":"world"}}))
                .is_err()
        );
        assert_eq!(state.config(), before);
        let mut config = before.clone();
        config["modules"] = json!(["UNKNOWN"]);
        assert!(state.edit_config(&json!({"config":config})).is_err());
        assert_eq!(state.config(), before);
    }
    #[test]
    fn detail_refreshes_without_overwriting_edited_config() {
        let mut state = State::default();
        state.inspect(&json!({"id":"job", "stage":"queued"}));
        state.source = "draft.record".into();
        state.receive(Ok(
            json!({"status":"ok","jobs":[{"id":"job","stage":"simulation_running"}]}),
        ));
        assert_eq!(
            state.inspected.as_ref().unwrap()["stage"],
            "simulation_running"
        );
        assert_eq!(state.source, "draft.record");
        state.receive(Ok(json!({"status":"ok","id":"new"})));
        assert!(state.inspected.is_none() && state.tab == Tab::Tasks);
    }
}
