//! Docked configuration and persistent FIFO tasks. Painting never runs a job.
use super::ad_shell::theme;
use re_ui::UiExt as _;
use re_viewer_context::AppContext;
use serde_json::{Value, json};

type Reply = std::sync::Arc<parking_lot::Mutex<Option<Result<Value, String>>>>;
const MODULES: [&str; 4] = ["PREDICTION", "PLANNING", "CONTROL", "ROUTING"];

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
    fn label(self) -> &'static str {
        match self {
            Self::Running => "Running",
            Self::Queued => "Queued",
            Self::Finished => "Finished",
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
    vehicle: String,
    profile: String,
    model: String,
    modules: [bool; 4],
    repeat: u32,
    seed: u32,
    step: u32,
    config_extra: serde_json::Map<String, Value>,
    config_from: Option<String>,
    catalog: Value,
    jobs: Vec<Value>,
    pending: Option<Reply>,
    requested: Option<web_time::Instant>,
    error: Option<String>,
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
}
impl Default for State {
    fn default() -> Self {
        Self {
            kind: "bag".into(),
            source: String::new(),
            map: String::new(),
            vehicle: String::new(),
            profile: String::new(),
            model: "perfect_planning".into(),
            modules: [true, true, true, false],
            repeat: 2,
            seed: 1,
            step: 10,
            config_extra: Default::default(),
            config_from: None,
            catalog: Value::Null,
            jobs: Vec::new(),
            pending: None,
            requested: None,
            error: None,
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
        }
    }
}
impl State {
    fn config(&self) -> Value {
        let mut config = self.config_extra.clone();
        let edited = json!({
            "kind":self.kind,"source":self.source,"map":self.map,"vehicle":self.vehicle,
            "profile":self.profile,"model":self.model,"seed":self.seed,"repeat":self.repeat,
            "step_ms":self.step,"modules":MODULES.iter().enumerate().filter(|(i,_)|self.modules[*i]).map(|(_,m)|*m).collect::<Vec<_>>()
        });
        config.extend(
            edited
                .as_object()
                .expect("config is an object literal")
                .clone(),
        );
        Value::Object(config)
    }
    fn receive(&mut self, reply: Result<Value, String>) {
        self.pending = None;
        match reply {
            Ok(reply) if reply["status"] == "ok" => {
                self.error = None;
                if !reply["catalog"].is_null() {
                    self.catalog = reply["catalog"].clone();
                }
                if let Some(jobs) = reply["jobs"].as_array() {
                    self.jobs = jobs.clone();
                    if let Some(selected) = &self.inspected
                        && let Some(updated) = jobs.iter().find(|j| j["id"] == selected["id"])
                    {
                        self.inspected = Some(updated.clone());
                    }
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
                    self.requested = None;
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
        self.kind = c.kind;
        self.source = c.source;
        self.map = c.map;
        self.vehicle = c.vehicle;
        self.profile = c.profile;
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
        state.pending = None;
        state.error = Some("Simulation service timed out; check the server log".into());
    }
    let mut action = None;
    let mut replay = None;
    let mut diagnostic = json!({"open":true});
    egui::Panel::left("ad_sim_secondary")
        .resizable(true)
        .drag_to_open(false)
        .default_size(410.0)
        .min_size(350.0)
        .frame(egui::Frame {
            fill: theme::PANEL_BG,
            inner_margin: egui::Margin::same(12),
            stroke: egui::Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.2)),
            ..Default::default()
        })
        .show_collapsible(ui, open, |ui| {
            let panel = ui.max_rect();
            diagnostic["panel_rect"] =
                json!([panel.left(), panel.top(), panel.right(), panel.bottom()]);
            // scene_editor structure: fixed tabs above one shared, scrolling content area.
            ui.horizontal(|ui| {
                let width = (ui.available_width() - ui.spacing().item_spacing.x) / 2.0;
                for tab in [Tab::Config, Tab::Tasks] {
                    let active = state.tab == tab;
                    let r = ui.add_sized(
                        [width, 34.0],
                        egui::Button::new(
                            egui::RichText::new(tab.label())
                                .size(12.0)
                                .color(if active { theme::TEXT } else { theme::TEXT_DIM }),
                        )
                        .frame(false),
                    );
                    if active {
                        ui.painter().line_segment(
                            [r.rect.left_bottom(), r.rect.right_bottom()],
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
            });
            ui.separator();
            if let Some(error) = &state.error {
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
                    ui.strong("Simulation detail");
                    let r = ui.button("Back to tasks");
                    point(&mut diagnostic, "back_to_tasks", &r);
                    if r.clicked() {
                        state.inspected = None;
                    }
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
                    ui.weak("FIFO · one active task · closing this panel does not stop jobs");
                    let r = ui.add(
                        egui::TextEdit::singleline(&mut state.filter)
                            .desired_width(ui.available_width())
                            .hint_text("Filter by task ID, source or status"),
                    );
                    point(&mut diagnostic, "filter", &r);
                    egui::ScrollArea::vertical()
                        .id_salt("sim_tasks_scroll")
                        .auto_shrink([false, false])
                        .show(ui, |ui| {
                            let jobs = state.jobs.clone();
                            for group in [Group::Running, Group::Queued, Group::Finished] {
                                let matching = grouped_jobs(&jobs, group, &state.filter);
                                diagnostic["groups"][group.key()] =
                                    json!(matching.iter().map(|j| &j["id"]).collect::<Vec<_>>());
                                ui.add_space(8.0);
                                let heading = ui.label(
                                    egui::RichText::new(format!(
                                        "{} ({})",
                                        group.label(),
                                        matching.len()
                                    ))
                                    .strong(),
                                );
                                diagnostic["group_y"][group.key()] = json!(heading.rect.top());
                                if matching.is_empty() {
                                    ui.weak(if state.filter.is_empty() {
                                        "No tasks"
                                    } else {
                                        "No matching tasks"
                                    });
                                }
                                for job in matching {
                                    task_card(
                                        ui,
                                        job,
                                        &mut state,
                                        &mut diagnostic,
                                        &mut action,
                                        &mut replay,
                                        false,
                                    );
                                }
                                ui.separator();
                            }
                        });
                }
            }
        });
    if state.pending.is_none() {
        if let Some(action) = action.or_else(|| state.queued_difference.take()) {
            fetch(ctx, &mut state, action);
        } else if state
            .requested
            .is_none_or(|t| t.elapsed().as_millis() >= 1000)
        {
            let action = if state.catalog.is_null() {
                "catalog"
            } else {
                "list"
            };
            fetch(ctx, &mut state, json!({"action":action}));
        }
    }
    diagnostic["jobs"] = json!(state.jobs);
    diagnostic["error"] = json!(state.error);
    diagnostic["catalog_ready"] = json!(!state.catalog.is_null());
    diagnostic["pending"] = json!(state.pending.is_some());
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
    ctx.egui_ctx.data_mut(|d| {
        d.insert_temp(egui::Id::new("ad_sim_diagnostic"), diagnostic);
        d.insert_temp(id, state);
    });
    ctx.egui_ctx
        .request_repaint_after(std::time::Duration::from_millis(250));
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
    if let Some(id) = &state.config_from {
        ui.weak(format!("Based on task {id} · starts a new task"));
    } else {
        ui.weak("New task · one virtual clock · isolated configuration");
    }
    ui.horizontal(|ui| {
        for (kind, label) in [("bag", "LogSim / bag"), ("world", "WorldSim / JSON")] {
            let r = ui.selectable_value(&mut state.kind, kind.into(), label);
            point(diagnostic, kind, &r);
        }
    });
    diagnostic["Scenario"] = picker(
        ui,
        "Scenario",
        &mut state.source,
        &catalog[if state.kind == "bag" {
            "bags"
        } else {
            "worlds"
        }],
        false,
    );
    diagnostic["Map"] = picker(ui, "Map", &mut state.map, &catalog["maps"], false);
    diagnostic["Vehicle config"] = picker(
        ui,
        "Vehicle config",
        &mut state.vehicle,
        &catalog["vehicles"],
        false,
    );
    diagnostic["Profile"] = picker(
        ui,
        "Profile",
        &mut state.profile,
        &catalog["profiles"],
        true,
    );
    ui.label("Algorithm modules");
    ui.horizontal_wrapped(|ui| {
        for (index, module) in MODULES.iter().enumerate() {
            let r = ui.re_checkbox(&mut state.modules[index], *module);
            point(diagnostic, module, &r);
        }
    });
    if state.kind == "world" {
        ui.weak("World closed loop requires Routing + Prediction + Planning.");
        egui::ComboBox::from_label("Ego model")
            .selected_text(&state.model)
            .show_ui(ui, |ui| {
                ui.selectable_value(
                    &mut state.model,
                    "perfect_planning".into(),
                    "Perfect planning trajectory",
                );
                ui.selectable_value(
                    &mut state.model,
                    "kinematic_control".into(),
                    "Kinematic control (acceleration + steering)",
                );
            });
        egui::ComboBox::from_label("Step / ms")
            .selected_text(state.step.to_string())
            .show_ui(ui, |ui| {
                for step in [1, 2, 5, 10] {
                    ui.selectable_value(&mut state.step, step, step.to_string());
                }
            });
    }
    ui.horizontal(|ui| {
        ui.label("Seed");
        let r = ui.add(egui::DragValue::new(&mut state.seed));
        point(diagnostic, "seed", &r);
        ui.label("Runs");
        let r = ui.add(egui::DragValue::new(&mut state.repeat).range(1..=3));
        point(diagnostic, "runs", &r);
    });
    ui.weak("2+ runs compare message order, timestamps and exact algorithm values. Wall-time profiling is excluded; raw differences remain in analysis.json.");
    if !state.config_extra.is_empty() {
        egui::CollapsingHeader::new("Additional configuration").show(ui, |ui| {
            for (key, value) in &mut state.config_extra {
                if ["begin_s", "end_s", "timeout_s"].contains(&key.as_str()) {
                    ui.horizontal(|ui| {
                        ui.label(key);
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
                    ui.add(egui::Label::new(format!("{key}: {value} (preserved)")).wrap());
                }
            }
        });
    }
    let r = ui.add_enabled(
        state.pending.is_none(),
        egui::Button::new("Start simulation")
            .fill(theme::ACCENT_STRONG)
            .min_size(egui::vec2(ui.available_width(), 32.0)),
    );
    point(diagnostic, "enqueue", &r);
    if r.clicked() {
        *action = Some(json!({"action":"enqueue","config":state.config()}));
    }
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
            "vehicle" => "Vehicle config",
            "profile" => "Profile",
            "modules" => "Algorithm modules",
            "model" => "Ego model",
            "repeat" => "Runs",
            "seed" => "Seed",
            "step_ms" => "Step / ms",
            "timeout_s" => "Timeout / s",
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
fn task_card(
    ui: &mut egui::Ui,
    job: &Value,
    state: &mut State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
    replay: &mut Option<(String, bool)>,
    detail: bool,
) {
    let job_id = job["id"].as_str().unwrap_or("Unknown task");
    ui.push_id(job_id, |ui| {
        ui.group(|ui| {
            ui.set_width(ui.available_width());
            let r = ui.add_sized(
                [ui.available_width(), 24.0],
                egui::Button::new(format!(
                    "{} · {}",
                    job["config"]["kind"].as_str().unwrap_or("?"),
                    job_id
                ))
                .frame(false),
            );
            point(diagnostic, &format!("inspect_{job_id}"), &r);
            if r.on_hover_text("View simulation detail").clicked() {
                state.inspect(job);
            }
            let r = ui.button("View config").on_hover_text(
                "Open this configuration in the editor; starting creates a new task",
            );
            point(diagnostic, &format!("view_config_{job_id}"), &r);
            if r.clicked()
                && let Err(error) = state.edit_config(job)
            {
                state.error = Some(error);
            }
            if let Some(source) = job["config"]["source"].as_str() {
                ui.add(egui::Label::new(source.rsplit('/').next().unwrap_or(source)).truncate())
                    .on_hover_text(source);
            }
            let stage = job["stage"].as_str().unwrap_or("Unknown stage");
            let color = match stage {
                "completed" => egui::Color32::LIGHT_GREEN,
                "failed" => egui::Color32::LIGHT_RED,
                "cancelled" | "interrupted" => egui::Color32::YELLOW,
                _ => theme::TEXT_DIM,
            };
            ui.colored_label(color, stage);
            if let Some(run) = job["run"].as_u64() {
                ui.weak(format!("Run {run} / {}", job["config"]["repeat"]));
            }
            if let Some(progress) = job["progress"].as_f64() {
                ui.add(egui::ProgressBar::new(progress as f32 / 100.0).show_percentage());
            }
            if let Some(error) = job["error"].as_str() {
                ui.add(
                    egui::Label::new(
                        egui::RichText::new(error.lines().next().unwrap_or(error))
                            .color(egui::Color32::LIGHT_RED),
                    )
                    .wrap(),
                );
            }
            if detail
                && Group::for_stage(stage) == Group::Finished
                && job["outputs"].as_array().is_some_and(|v| v.len() >= 2)
            {
                difference_inspector(ui, job, state, diagnostic);
            }
            egui::CollapsingHeader::new("Stages / analysis")
                .id_salt(("stages", detail))
                .default_open(detail)
                .show(ui, |ui| {
                    if let Some(error) = job["error"].as_str() {
                        ui.add(egui::Label::new(error).wrap());
                    }
                    if let Some(history) = job["history"].as_array() {
                        for entry in history {
                            ui.label(entry["stage"].as_str().unwrap_or("Unknown stage"));
                        }
                    }
                    if !job["analysis"].is_null() {
                        ui.label(format!("Determinism: {}", job["analysis"]["determinism"]));
                        ui.add(
                            egui::Label::new(job["analysis"]["topic_message_counts"].to_string())
                                .wrap(),
                        );
                    }
                    if let Some(path) = job["log_path"].as_str() {
                        ui.add(egui::Label::new(format!("Log: {path}")).wrap());
                    }
                    if detail && !job["simulation"].is_null() {
                        ui.add(
                            egui::Label::new(format!("Simulation: {}", job["simulation"])).wrap(),
                        );
                    }
                    if detail && !job["analysis"].is_null() {
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
            if detail {
                egui::CollapsingHeader::new("Submitted configuration snapshot").show(ui, |ui| {
                    config_snapshot(ui, &job["config"]);
                });
            }
            if Group::for_stage(stage) != Group::Finished {
                let r = ui.add_enabled(state.pending.is_none(), egui::Button::new("Cancel task"));
                point(diagnostic, &format!("cancel_{job_id}"), &r);
                if r.clicked() {
                    *action = Some(json!({"action":"cancel","id":job_id}));
                }
            }
            // An output still being written is not a completed replay artifact.
            if Group::for_stage(stage) == Group::Finished
                && let Some(outputs) = job["outputs"].as_array()
            {
                for (index, path) in outputs.iter().enumerate() {
                    if let Some(path) = path.as_str() {
                        let r = ui.button(format!("Replay bag / run {}", index + 1));
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
        ui.add_space(6.0);
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
fn picker(
    ui: &mut egui::Ui,
    label: &str,
    selected: &mut String,
    values: &Value,
    optional: bool,
) -> Value {
    ui.label(label);
    egui::ComboBox::from_id_salt(label)
        .width(ui.available_width())
        .selected_text(if selected.is_empty() {
            if optional {
                "Workspace defaults"
            } else {
                "Choose…"
            }
        } else {
            selected.rsplit('/').next().unwrap_or(selected)
        })
        .show_ui(ui, |ui| {
            ui.set_max_width(560.0);
            if optional {
                ui.selectable_value(selected, String::new(), "Workspace defaults");
            }
            if let Some(values) = values.as_array() {
                for item in values {
                    if let Some(path) = item.as_str() {
                        ui.selectable_value(
                            selected,
                            path.to_owned(),
                            path.rsplit('/').next().unwrap_or(path),
                        )
                        .on_hover_text(path);
                    }
                }
            }
        });
    let r = ui.add(
        egui::TextEdit::singleline(selected)
            .desired_width(ui.available_width())
            .hint_text("Or enter a server path"),
    );
    json!([r.rect.center().x, r.rect.center().y])
}
#[cfg(target_arch = "wasm32")]
fn fetch(ctx: &AppContext<'_>, state: &mut State, request: Value) {
    let Some(origin) = web_sys::window().and_then(|w| w.location().origin().ok()) else {
        state.error = Some("Browser origin unavailable".into());
        return;
    };
    let reply = Reply::default();
    state.pending = Some(reply.clone());
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
