//! Task workspace: real status counts, suite grouping and bounded page rendering.
use super::{Group, State, dark_menu_frame, dropdown_trigger, grouped_jobs, point, theme};
use egui::{Color32, RichText, Stroke, pos2, vec2};
use serde_json::{Value, json};

const PAGE_SIZE: usize = 10;
const RUNNING: Color32 = Color32::from_rgb(96, 165, 250);
const SUCCESS: Color32 = Color32::from_rgb(132, 204, 112);
const FAILURE: Color32 = Color32::from_rgb(240, 137, 117);

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Status {
    All,
    Running,
    Queued,
    Failed,
    Completed,
    Cancelled,
    Interrupted,
}
impl Status {
    const OPTIONS: [(Self, &'static str, &'static str); 7] = [
        (Self::All, "全部状态", "all"),
        (Self::Running, "运行中", "running"),
        (Self::Queued, "排队中", "queued"),
        (Self::Failed, "失败", "failed"),
        (Self::Completed, "已完成", "completed"),
        (Self::Cancelled, "已取消", "cancelled"),
        (Self::Interrupted, "已中断", "interrupted"),
    ];
    fn of(job: &Value) -> Self {
        match job["stage"].as_str() {
            Some("queued") => Self::Queued,
            Some("failed") => Self::Failed,
            Some("completed") => Self::Completed,
            Some("cancelled") => Self::Cancelled,
            Some("interrupted") => Self::Interrupted,
            _ => Self::Running,
        }
    }
    fn rank(self) -> usize {
        match self {
            Self::Running => 0,
            Self::Queued => 1,
            Self::Failed => 2,
            Self::Completed => 3,
            Self::Cancelled | Self::Interrupted => 4,
            _ => 4,
        }
    }
    fn color(self) -> Color32 {
        match self {
            Self::Running => RUNNING,
            Self::Queued | Self::Completed => SUCCESS,
            Self::Failed => FAILURE,
            _ => theme::TEXT_DIM,
        }
    }
    fn label(self) -> &'static str {
        Self::OPTIONS[self as usize].1
    }
    fn key(self) -> &'static str {
        Self::OPTIONS[self as usize].2
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Source {
    All,
    World,
    Bag,
}
impl Source {
    const OPTIONS: [(Self, &'static str, &'static str); 3] = [
        (Self::All, "全部来源", "all"),
        (Self::World, "WorldSim", "world"),
        (Self::Bag, "LogSim", "bag"),
    ];
    fn key(self) -> &'static str {
        Self::OPTIONS[self as usize].2
    }
}

#[derive(Default)]
struct Counts([usize; 7]);
impl Counts {
    fn add(&mut self, job: &Value) {
        self.0[0] += 1;
        self.0[Status::of(job) as usize] += 1;
    }
    fn get(&self, status: Status) -> usize {
        self.0[status as usize]
    }
    fn diagnostic(&self) -> Value {
        Value::Object(
            Status::OPTIONS
                .iter()
                .map(|(status, _, key)| ((*key).into(), json!(self.get(*status))))
                .collect(),
        )
    }
}

struct Suite {
    key: String,
    name: String,
    counts: Counts,
    indices: Vec<usize>,
    newest: usize,
}

fn title(job: &Value) -> &str {
    for name in [
        &job["name"],
        &job["config"]["name"],
        &job["config"]["scenario_name"],
    ] {
        if let Some(name) = name.as_str().filter(|name| !name.is_empty()) {
            return name;
        }
    }
    let source = job["config"]["source"].as_str().unwrap_or("");
    let leaf = source.rsplit('/').next().unwrap_or(source);
    for suffix in [".worldsim.scenario.json", ".scenario.json", ".json"] {
        if let Some(name) = leaf.strip_suffix(suffix) {
            return name;
        }
    }
    if leaf.is_empty() {
        job["id"].as_str().unwrap_or("未命名任务")
    } else {
        leaf
    }
}

fn suites(state: &State) -> Vec<Suite> {
    let query = state.filter.trim().to_lowercase();
    let mut result: Vec<Suite> = Vec::new();
    let mut positions = std::collections::HashMap::new();
    for (index, job) in state.jobs.iter().enumerate() {
        let key = job["suite_id"]
            .as_str()
            .map(|id| format!("suite:{id}"))
            .unwrap_or_else(|| "single".into());
        let position = *positions.entry(key.clone()).or_insert_with(|| {
            result.push(Suite {
                key,
                name: job["suite_name"].as_str().unwrap_or("独立任务").into(),
                counts: Counts::default(),
                indices: Vec::new(),
                newest: index,
            });
            result.len() - 1
        });
        let suite = &mut result[position];
        suite.counts.add(job);
        suite.newest = index;
        let status = Status::of(job);
        let matches = (state.task_status == Status::All || state.task_status == status)
            && (state.task_source == Source::All
                || job["config"]["kind"] == state.task_source.key())
            && (query.is_empty()
                || format!(
                    "{} {} {} {} {} {}",
                    job["id"],
                    title(job),
                    job["config"]["source"],
                    job["stage"],
                    status.label(),
                    suite.name
                )
                .to_lowercase()
                .contains(&query));
        if matches {
            suite.indices.push(index);
        }
    }
    result.retain(|suite| !suite.indices.is_empty());
    for suite in &mut result {
        suite.indices.sort_by_key(|index| {
            let status = Status::of(&state.jobs[*index]);
            (
                status.rank(),
                if status.rank() <= 1 || suite.key != "single" {
                    *index
                } else {
                    usize::MAX - index
                },
            )
        });
    }
    result.sort_by_key(|suite| {
        let rank = suite
            .indices
            .iter()
            .map(|index| Status::of(&state.jobs[*index]).rank())
            .min()
            .unwrap_or(4)
            .min(2);
        (rank, std::cmp::Reverse(suite.newest))
    });
    result
}

fn duration(job: &Value) -> Option<String> {
    let history = job["history"].as_array()?;
    let start = history.iter().find(|entry| entry["stage"] != "queued")?["wall_time"].as_f64()?;
    let end = history.iter().rev().find(|entry| {
        matches!(
            entry["stage"].as_str(),
            Some("completed" | "failed" | "cancelled" | "interrupted")
        )
    })?["wall_time"]
        .as_f64()?;
    if !start.is_finite() || !end.is_finite() || end < start {
        return None;
    }
    let seconds = (end - start).round() as u64;
    Some(if seconds >= 3600 {
        format!("耗时 {} 小时 {} 分", seconds / 3600, seconds % 3600 / 60)
    } else if seconds >= 60 {
        format!("耗时 {} 分 {} 秒", seconds / 60, seconds % 60)
    } else {
        format!("耗时 {seconds} 秒")
    })
}

fn status_text(job: &Value, queue_position: Option<usize>) -> (&'static str, String) {
    match Status::of(job) {
        Status::Running => (
            "运行中",
            match job["stage"].as_str() {
                Some("data_preparation") => "准备输入数据".into(),
                Some("map_update") => "加载地图".into(),
                Some("profile_update") => "应用车辆配置".into(),
                Some("model_update") => "加载模型".into(),
                Some("result_analysis") => "分析仿真结果".into(),
                Some("simulation_start") => "启动仿真".into(),
                Some("simulation_end") => "仿真运行结束".into(),
                _ => job["progress"]
                    .as_f64()
                    .map(|p| format!("{:.0}%", p.clamp(0.0, 100.0)))
                    .unwrap_or_else(|| "正在执行".into()),
            },
        ),
        Status::Queued => (
            "排队中",
            queue_position
                .map(|n| format!("#{n} · 等待资源"))
                .unwrap_or_else(|| "等待资源".into()),
        ),
        Status::Completed => (
            "已完成",
            duration(job).unwrap_or_else(|| "仿真任务已完成".into()),
        ),
        Status::Cancelled => (
            "已取消",
            duration(job).unwrap_or_else(|| "任务已取消".into()),
        ),
        Status::Interrupted => ("已中断", "服务中断，请复用配置重新提交".into()),
        Status::Failed => {
            let analysis = &job["analysis"];
            let error = job["error"].as_str().unwrap_or("任务执行失败，请查看详情");
            if analysis["planning_continuity"]["status"] == "FAIL" {
                ("校验失败", "规划轨迹为空、无效或发布不连续".into())
            } else if analysis["collision"]["status"] == "FAIL" {
                ("校验失败", "仿真检测到碰撞".into())
            } else if analysis["scenario_expectation"]["status"] == "FAIL" {
                ("分析失败", "结果与场景预期不一致".into())
            } else if analysis["determinism"] == "FAIL" {
                ("校验失败", "多次运行的一致性检查失败".into())
            } else {
                ("执行失败", error.lines().next().unwrap_or(error).into())
            }
        }
        Status::All => unreachable!("All is a filter, not a job status"),
    }
}

fn choice<T: Copy + PartialEq>(
    ui: &mut egui::Ui,
    selected: &mut T,
    options: &[(T, &str, &str)],
    key: &str,
    diagnostic: &mut Value,
) {
    let label = options
        .iter()
        .find(|(value, _, _)| value == selected)
        .expect("known filter")
        .1;
    let r = dropdown_trigger(ui, label);
    point(diagnostic, &format!("{key}_filter"), &r);
    egui::Popup::menu(&r)
        .id(egui::Id::new(("sim_task_filter", key)))
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_min_width(r.rect.width().max(150.0));
                for &(value, label, name) in options {
                    let r = ui.add_sized(
                        [ui.available_width(), 32.0],
                        egui::Button::new(RichText::new(label).color(theme::TEXT).size(14.0))
                            .fill(if *selected == value {
                                theme::CARD_BG_HOVER
                            } else {
                                Color32::TRANSPARENT
                            })
                            .corner_radius(5.0),
                    );
                    point(diagnostic, &format!("{key}_option_{name}"), &r);
                    if r.clicked() {
                        *selected = value;
                        ui.close();
                    }
                }
            });
        });
}

fn search(ui: &mut egui::Ui, state: &mut State, diagnostic: &mut Value) {
    egui::Frame::new()
        .fill(theme::PANEL_BG)
        .stroke(Stroke::new(1.0, theme::CARD_BG_HOVER))
        .corner_radius(8.0)
        .inner_margin(egui::Margin::symmetric(10, 8))
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                let (rect, _) = ui.allocate_exact_size(vec2(18.0, 18.0), egui::Sense::hover());
                ui.painter().circle_stroke(
                    rect.center() - vec2(2.0, 2.0),
                    6.0,
                    Stroke::new(1.5, theme::TEXT_DIM),
                );
                ui.painter().line_segment(
                    [
                        rect.center() + vec2(2.0, 2.0),
                        rect.right_bottom() - vec2(1.0, 1.0),
                    ],
                    Stroke::new(1.5, theme::TEXT_DIM),
                );
                let r = ui.add(
                    egui::TextEdit::singleline(&mut state.filter)
                        .desired_width(ui.available_width())
                        .font(egui::FontId::proportional(14.0))
                        .frame(egui::Frame::NONE)
                        .text_color(theme::TEXT)
                        .hint_text(
                            RichText::new("搜索任务 ID、场景名或状态…").color(theme::TEXT_DIM),
                        ),
                );
                point(diagnostic, "filter", &r);
            });
        });
}

pub(super) fn show(
    ui: &mut egui::Ui,
    state: &mut State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
    replay: &mut Option<(String, bool)>,
) {
    let mut counts = Counts::default();
    for job in &state.jobs {
        counts.add(job);
    }
    diagnostic["task_counts"] = counts.diagnostic();
    let capacity = state.catalog["max_concurrency"].as_u64();
    diagnostic["task_capacity"] = json!(capacity);
    ui.add_space(10.0);
    ui.horizontal(|ui| {
        let running = counts.get(Status::Running);
        let capacity = capacity.map(|n| format!("/{n}")).unwrap_or_default();
        ui.label(
            RichText::new(format!("运行 {running}{capacity}  ·  后台执行"))
                .size(15.0)
                .color(theme::TEXT),
        );
        let (rect, response) = ui.allocate_exact_size(vec2(18.0, 18.0), egui::Sense::hover());
        ui.painter()
            .circle_stroke(rect.center(), 7.0, Stroke::new(1.2, theme::TEXT_DIM));
        ui.painter().text(
            rect.center(),
            egui::Align2::CENTER_CENTER,
            "i",
            egui::FontId::proportional(12.0),
            theme::TEXT_DIM,
        );
        response.on_hover_text(
            "关闭面板不会停止任务。显示服务并发上限；每个场景集还受提交时的并发设置限制。",
        );
    });
    ui.add_space(14.0);
    let previous = (state.task_status, state.task_source, state.filter.clone());
    let narrow = ui.available_width() < 620.0;
    ui.horizontal(|ui| {
        let width = (ui.available_width() - 4.0 * 8.0) / 5.0;
        ui.spacing_mut().item_spacing.x = 8.0;
        for (status, label) in [
            (Status::All, "全部"),
            (Status::Running, "运行中"),
            (Status::Queued, "排队"),
            (Status::Failed, "失败"),
            (Status::Completed, "已完成"),
        ] {
            let active = state.task_status == status;
            let text = format!(
                "{label}{}{count}",
                if narrow { "\n" } else { "  " },
                count = counts.get(status)
            );
            let r = ui.add_sized(
                [width, if narrow { 52.0 } else { 44.0 }],
                egui::Button::new(
                    RichText::new(text)
                        .size(if narrow { 12.0 } else { 14.0 })
                        .color(theme::TEXT),
                )
                .fill(if active {
                    theme::ACCENT_STRONG.gamma_multiply(0.4)
                } else {
                    theme::PANEL_BG
                })
                .stroke(Stroke::new(
                    if active { 1.5 } else { 1.0 },
                    if active {
                        theme::ACCENT
                    } else {
                        theme::CARD_BG
                    },
                ))
                .corner_radius(8.0),
            );
            point(diagnostic, &format!("status_chip_{}", status.key()), &r);
            if r.clicked() {
                state.task_status = status;
            }
        }
    });
    ui.add_space(12.0);
    if narrow {
        search(ui, state, diagnostic);
        ui.add_space(8.0);
    }
    ui.horizontal(|ui| {
        let width = ui.available_width();
        let filter_width = if narrow {
            (width - 8.0) / 2.0
        } else {
            (width * 0.235).min(180.0)
        };
        if !narrow {
            ui.allocate_ui(vec2(width - 2.0 * filter_width - 16.0, 36.0), |ui| {
                search(ui, state, diagnostic);
            });
        }
        ui.allocate_ui(vec2(filter_width, 36.0), |ui| {
            choice(
                ui,
                &mut state.task_source,
                &Source::OPTIONS,
                "source",
                diagnostic,
            );
        });
        ui.allocate_ui(vec2(filter_width, 36.0), |ui| {
            choice(
                ui,
                &mut state.task_status,
                &Status::OPTIONS,
                "status",
                diagnostic,
            );
        });
    });
    if previous != (state.task_status, state.task_source, state.filter.clone()) {
        state.task_page = 0;
    }
    ui.add_space(16.0);

    let groups = suites(state);
    let total = groups
        .iter()
        .map(|suite| suite.indices.len())
        .sum::<usize>();
    let pages = total.div_ceil(PAGE_SIZE).max(1);
    state.task_page = state.task_page.min(pages - 1);
    let start = state.task_page * PAGE_SIZE;
    let end = (start + PAGE_SIZE).min(total);
    diagnostic["task_pagination"] =
        json!({"page":state.task_page+1,"pages":pages,"page_size":PAGE_SIZE,"total":total});
    diagnostic["task_status"] = json!(state.task_status.key());
    diagnostic["task_source"] = json!(state.task_source.key());
    diagnostic["task_rows"] = json!({});
    diagnostic["task_suites"] = json!([]);
    for group in [Group::Running, Group::Queued, Group::Finished] {
        diagnostic["groups"][group.key()] = json!(
            grouped_jobs(&state.jobs, group, &state.filter)
                .iter()
                .map(|j| &j["id"])
                .collect::<Vec<_>>()
        );
    }
    let queue: std::collections::HashMap<_, _> = state
        .jobs
        .iter()
        .filter(|j| Status::of(j) == Status::Queued)
        .enumerate()
        .map(|(index, job)| (job["id"].as_str().unwrap_or(""), index + 1))
        .collect();
    let queue: std::collections::HashMap<String, usize> = queue
        .into_iter()
        .map(|(id, index)| (id.into(), index))
        .collect();
    let height = (ui.available_height() - 52.0).max(80.0);
    egui::ScrollArea::vertical()
        .id_salt((
            "sim_tasks_scroll",
            state.task_page,
            state.task_status.key(),
            state.task_source.key(),
            &state.filter,
        ))
        .max_height(height)
        .min_scrolled_height(height)
        .auto_shrink([false, false])
        .show(ui, |ui| {
            if total == 0 {
                ui.add_space(40.0);
                ui.vertical_centered(|ui| {
                    ui.label(
                        RichText::new(if state.jobs.is_empty() {
                            "还没有仿真任务"
                        } else {
                            "没有符合筛选条件的任务"
                        })
                        .size(17.0)
                        .color(theme::TEXT),
                    );
                    ui.add_space(8.0);
                    ui.label(
                        RichText::new(if state.jobs.is_empty() {
                            "点击右上角「新建任务」开始配置。"
                        } else {
                            "调整搜索词，或清除筛选后查看全部任务。"
                        })
                        .size(13.0)
                        .color(theme::TEXT_DIM),
                    );
                    if !state.jobs.is_empty() {
                        let r = ui.add(
                            egui::Button::new(RichText::new("清除筛选").color(theme::TEXT))
                                .fill(theme::CARD_BG),
                        );
                        point(diagnostic, "clear_task_filters", &r);
                        if r.clicked() {
                            state.filter.clear();
                            state.task_status = Status::All;
                            state.task_source = Source::All;
                            state.task_page = 0;
                        }
                    }
                });
            }
            let mut offset = 0;
            for suite in &groups {
                let lo = start.saturating_sub(offset).min(suite.indices.len());
                let hi = end.saturating_sub(offset).min(suite.indices.len());
                offset += suite.indices.len();
                if lo == hi {
                    continue;
                }
                suite_header(ui, suite, state, diagnostic);
                if !state.collapsed_suites.contains(&suite.key) {
                    for &index in &suite.indices[lo..hi] {
                        let job = state.jobs[index].clone();
                        let position = job["id"].as_str().and_then(|id| queue.get(id)).copied();
                        row(ui, &job, position, state, diagnostic, action, replay);
                        ui.add_space(8.0);
                    }
                }
                ui.add_space(4.0);
            }
        });
    ui.add_space(8.0);
    ui.separator();
    pagination(ui, state, diagnostic, pages, total);
}

fn suite_header(ui: &mut egui::Ui, suite: &Suite, state: &mut State, diagnostic: &mut Value) {
    let collapsed = state.collapsed_suites.contains(&suite.key);
    let narrow = ui.available_width() < 620.0;
    let (rect, response) = ui.allocate_exact_size(
        vec2(ui.available_width(), if narrow { 62.0 } else { 44.0 }),
        egui::Sense::click(),
    );
    ui.painter()
        .rect_filled(rect, 8.0, theme::CARD_BG.gamma_multiply(0.65));
    let center = rect.left_top() + vec2(18.0, 22.0);
    let points = if collapsed {
        vec![
            center + vec2(-2.0, -4.0),
            center + vec2(2.0, 0.0),
            center + vec2(-2.0, 4.0),
        ]
    } else {
        vec![
            center + vec2(-4.0, -2.0),
            center + vec2(0.0, 2.0),
            center + vec2(4.0, -2.0),
        ]
    };
    ui.painter()
        .add(egui::Shape::line(points, Stroke::new(1.8, theme::TEXT)));
    let title_rect = egui::Rect::from_min_size(
        rect.left_top() + vec2(36.0, 10.0),
        vec2(
            if narrow {
                rect.width() - 50.0
            } else {
                (rect.width() - 300.0).max(140.0)
            },
            24.0,
        ),
    );
    ui.painter()
        .with_clip_rect(title_rect.intersect(ui.clip_rect()))
        .text(
            title_rect.left_center(),
            egui::Align2::LEFT_CENTER,
            format!(
                "{} · {} {}",
                suite.name,
                suite.counts.get(Status::All),
                if suite.key == "single" {
                    "任务"
                } else {
                    "场景"
                }
            ),
            egui::FontId::proportional(15.0),
            theme::TEXT,
        );
    let summary = format!(
        "运行 {}   排队 {}   失败 {}   完成 {}",
        suite.counts.get(Status::Running),
        suite.counts.get(Status::Queued),
        suite.counts.get(Status::Failed),
        suite.counts.get(Status::Completed)
    );
    ui.painter().text(
        if narrow {
            rect.left_top() + vec2(36.0, 46.0)
        } else {
            rect.right_center() - vec2(14.0, 0.0)
        },
        if narrow {
            egui::Align2::LEFT_CENTER
        } else {
            egui::Align2::RIGHT_CENTER
        },
        summary,
        egui::FontId::proportional(12.0),
        theme::TEXT_DIM,
    );
    point(diagnostic, &format!("suite_{}", suite.key), &response);
    diagnostic["task_suites"].as_array_mut().expect("suite diagnostics").push(json!({"key":suite.key,"name":suite.name,"counts":suite.counts.diagnostic(),"collapsed":collapsed}));
    if response.clicked() {
        if collapsed {
            state.collapsed_suites.remove(&suite.key);
        } else {
            state.collapsed_suites.insert(suite.key.clone());
        }
    }
    ui.add_space(8.0);
}

fn badge(ui: &mut egui::Ui, rect: egui::Rect, text: &str) {
    ui.painter().rect_filled(rect, 5.0, theme::CARD_BG);
    ui.put(
        rect.shrink2(vec2(7.0, 2.0)),
        egui::Label::new(RichText::new(text).size(12.0).color(theme::TEXT)).truncate(),
    );
}

fn status_icon(ui: &egui::Ui, center: egui::Pos2, status: Status, progress: f32) {
    let color = status.color();
    let painter = ui.painter();
    let stroke = Stroke::new(1.8, color);
    if matches!(status, Status::Completed | Status::Failed) {
        painter.circle_filled(center, 10.0, color);
        let ink = Stroke::new(1.8, theme::RAIL_BG);
        if status == Status::Completed {
            painter.add(egui::Shape::line(
                vec![
                    center + vec2(-4.0, 0.0),
                    center + vec2(-1.0, 3.0),
                    center + vec2(5.0, -4.0),
                ],
                ink,
            ));
        } else {
            painter.line_segment([center + vec2(-3.0, -3.0), center + vec2(3.0, 3.0)], ink);
            painter.line_segment([center + vec2(-3.0, 3.0), center + vec2(3.0, -3.0)], ink);
        }
    } else {
        painter.circle_stroke(center, 10.0, Stroke::new(1.6, color.gamma_multiply(0.45)));
        if status == Status::Running {
            let points = (0..=24)
                .map(|i| {
                    let angle = -std::f32::consts::FRAC_PI_2
                        + std::f32::consts::TAU * progress.max(0.06) * i as f32 / 24.0;
                    center + vec2(angle.cos(), angle.sin()) * 10.0
                })
                .collect();
            painter.add(egui::Shape::line(points, Stroke::new(2.4, color)));
        } else if status == Status::Queued {
            painter.add(egui::Shape::line(
                vec![center + vec2(0.0, -6.0), center, center + vec2(5.0, 0.0)],
                stroke,
            ));
        } else {
            painter.line_segment([center - vec2(4.0, 0.0), center + vec2(4.0, 0.0)], stroke);
        }
    }
}

fn replay_value(job: &Value, path: &str) -> (String, bool) {
    (
        path.into(),
        job["config"]["modules"]
            .as_array()
            .is_some_and(|modules| modules.iter().any(|module| module == "CONTROL")),
    )
}

// Manual card text keeps its left edge even when the label is shorter than its slot.
fn left_label(ui: &mut egui::Ui, rect: egui::Rect, label: egui::Label) -> egui::Response {
    ui.new_child(
        egui::UiBuilder::new()
            .max_rect(rect)
            .layout(egui::Layout::left_to_right(egui::Align::Center)),
    )
    .add(label)
}

fn row(
    ui: &mut egui::Ui,
    job: &Value,
    queue_position: Option<usize>,
    state: &mut State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
    replay: &mut Option<(String, bool)>,
) {
    let id = job["id"].as_str().unwrap_or("unknown");
    ui.push_id(id, |ui| {
        row_contents(ui, job, queue_position, state, diagnostic, action, replay);
    });
}

fn row_contents(
    ui: &mut egui::Ui,
    job: &Value,
    queue_position: Option<usize>,
    state: &mut State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
    replay: &mut Option<(String, bool)>,
) {
    let id = job["id"].as_str().unwrap_or("unknown");
    let status = Status::of(job);
    let progress = (job["progress"].as_f64().unwrap_or(0.0) as f32 / 100.0).clamp(0.0, 1.0);
    let (label, subtitle) = status_text(job, queue_position);
    let narrow = ui.available_width() < 620.0;
    let (rect, _) = ui.allocate_exact_size(
        vec2(ui.available_width(), if narrow { 200.0 } else { 126.0 }),
        egui::Sense::hover(),
    );
    let failed = status == Status::Failed;
    ui.painter().rect(
        rect,
        8.0,
        theme::PANEL_BG,
        Stroke::new(
            1.0,
            if failed {
                FAILURE.gamma_multiply(0.75)
            } else {
                theme::CARD_BG
            },
        ),
        egui::StrokeKind::Inside,
    );
    if failed {
        ui.painter().rect_filled(
            egui::Rect::from_min_size(
                rect.left_top() + vec2(1.0, 6.0),
                vec2(4.0, rect.height() - 12.0),
            ),
            2.0,
            FAILURE,
        );
    }
    let left_width = if narrow {
        rect.width() - 68.0
    } else {
        (rect.width() * 0.58 - 26.0).max(200.0)
    };
    let title_rect =
        egui::Rect::from_min_size(rect.left_top() + vec2(18.0, 13.0), vec2(left_width, 25.0));
    let r = left_label(
        ui,
        title_rect,
        egui::Label::new(
            RichText::new(title(job))
                .size(17.0)
                .strong()
                .color(theme::TEXT),
        )
        .halign(egui::Align::Min)
        .truncate()
        .sense(egui::Sense::click()),
    );
    point(diagnostic, &format!("inspect_{id}"), &r);
    if r.on_hover_text(format!(
        "{}\n{id}",
        job["config"]["source"].as_str().unwrap_or("")
    ))
    .clicked()
    {
        state.inspect(job);
    }
    let short_id: String = id.chars().take(8).collect();
    let kind = job["config"]["kind"].as_str().unwrap_or("unknown");
    let run = job["run"]
        .as_u64()
        .map(|run| run.to_string())
        .unwrap_or_else(|| "—".into());
    let repeat = job["config"]["repeat"]
        .as_u64()
        .map(|run| run.to_string())
        .unwrap_or_else(|| "—".into());
    left_label(
        ui,
        egui::Rect::from_min_size(rect.left_top() + vec2(18.0, 43.0), vec2(left_width, 21.0)),
        egui::Label::new(
            RichText::new(format!("{kind} · {short_id} · run {run}/{repeat}"))
                .size(12.0)
                .color(theme::TEXT_DIM),
        )
        .halign(egui::Align::Min)
        .truncate(),
    );
    badge(
        ui,
        egui::Rect::from_min_size(rect.left_top() + vec2(18.0, 79.0), vec2(84.0, 26.0)),
        match kind {
            "world" => "WorldSim",
            "bag" => "LogSim",
            _ => kind,
        },
    );
    if let Some(suite) = job["suite_name"].as_str() {
        badge(
            ui,
            egui::Rect::from_min_size(
                rect.left_top() + vec2(110.0, 79.0),
                vec2((left_width - 100.0).clamp(70.0, 160.0), 26.0),
            ),
            suite,
        );
    }
    let status_origin = if narrow {
        rect.left_top() + vec2(18.0, 122.0)
    } else {
        pos2(rect.left() + rect.width() * 0.69, rect.top() + 23.0)
    };
    status_icon(ui, status_origin + vec2(10.0, 0.0), status, progress);
    let right_width = if narrow {
        rect.width() - 66.0
    } else {
        rect.right() - status_origin.x - 48.0
    };
    left_label(
        ui,
        egui::Rect::from_min_size(status_origin + vec2(30.0, -12.0), vec2(right_width, 24.0)),
        egui::Label::new(
            RichText::new(label)
                .size(15.0)
                .strong()
                .color(status.color()),
        )
        .halign(egui::Align::Min)
        .truncate(),
    );
    let subtitle_origin = status_origin + vec2(30.0, 14.0);
    left_label(
        ui,
        egui::Rect::from_min_size(subtitle_origin, vec2(right_width, 20.0)),
        egui::Label::new(RichText::new(&subtitle).size(12.0).color(theme::TEXT_DIM))
            .halign(egui::Align::Min)
            .truncate(),
    )
    .on_hover_text(job["error"].as_str().unwrap_or(&subtitle));
    if status == Status::Running && !narrow {
        let track = egui::Rect::from_min_size(
            status_origin + vec2(0.0, 44.0),
            vec2(right_width + 20.0, 6.0),
        );
        ui.painter().rect_filled(track, 3.0, theme::CARD_BG);
        ui.painter().rect_filled(
            egui::Rect::from_min_size(track.min, vec2(track.width() * progress, 6.0)),
            3.0,
            RUNNING,
        );
    }
    let menu_rect =
        egui::Rect::from_min_size(rect.right_top() + vec2(-36.0, 12.0), vec2(26.0, 28.0));
    let menu = ui.interact(menu_rect, ui.id().with("actions"), egui::Sense::click());
    if menu.hovered() {
        ui.painter()
            .rect_filled(menu_rect, 5.0, theme::CARD_BG_HOVER);
    }
    for dy in [-5.0, 0.0, 5.0] {
        ui.painter()
            .circle_filled(menu_rect.center() + vec2(0.0, dy), 1.5, theme::TEXT_DIM);
    }
    point(diagnostic, &format!("task_menu_{id}"), &menu);
    egui::Popup::menu(&menu)
        .id(ui.id().with("task_popup"))
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_min_width(160.0);
                let r = ui.add_sized(
                    [ui.available_width(), 30.0],
                    egui::Button::new(RichText::new("查看详情").color(theme::TEXT))
                        .fill(theme::PANEL_BG),
                );
                point(diagnostic, &format!("details_{id}"), &r);
                if r.clicked() {
                    state.inspect(job);
                    ui.close();
                }
                let r = ui.add_sized(
                    [ui.available_width(), 30.0],
                    egui::Button::new(RichText::new("复用配置").color(theme::TEXT))
                        .fill(theme::PANEL_BG),
                );
                point(diagnostic, &format!("view_config_{id}"), &r);
                if r.clicked() {
                    if let Err(error) = state.edit_config(job) {
                        state.error = Some(error);
                    }
                    ui.close();
                }
                if Group::for_stage(job["stage"].as_str().unwrap_or("")) != Group::Finished {
                    let r = ui.add_enabled(
                        state.pending.is_none(),
                        egui::Button::new(RichText::new("取消任务").color(FAILURE))
                            .fill(theme::PANEL_BG),
                    );
                    point(diagnostic, &format!("cancel_{id}"), &r);
                    if r.clicked() {
                        *action = Some(json!({"action":"cancel","id":id}));
                        ui.close();
                    }
                } else if let Some(outputs) = job["outputs"].as_array() {
                    for (index, path) in outputs
                        .iter()
                        .enumerate()
                        .filter_map(|(i, path)| path.as_str().map(|path| (i, path)))
                    {
                        let r = ui.add_sized(
                            [ui.available_width(), 30.0],
                            egui::Button::new(
                                RichText::new(format!("回放第 {} 次运行", index + 1))
                                    .color(theme::TEXT),
                            )
                            .fill(theme::PANEL_BG),
                        );
                        point(diagnostic, &format!("replay_{id}_{index}"), &r);
                        if r.clicked() {
                            *replay = Some(replay_value(job, path));
                            ui.close();
                        }
                    }
                }
            });
        });
    if failed || ui.rect_contains_pointer(rect) {
        let button_width = if narrow { 72.0 } else { 78.0 };
        let y = rect.bottom() - 36.0;
        let left = rect.right() - 3.0 * (button_width + 8.0) - 10.0;
        for (index, text) in ["查看详情", "复用配置", "回放"].into_iter().enumerate() {
            let can_replay = Group::for_stage(job["stage"].as_str().unwrap_or(""))
                == Group::Finished
                && job["outputs"][0].is_string();
            let button = egui::Button::new(RichText::new(text).size(12.0).color(theme::TEXT))
                .fill(if index == 0 && failed {
                    theme::ACCENT_STRONG
                } else {
                    theme::CARD_BG
                })
                .corner_radius(6.0);
            let rect = egui::Rect::from_min_size(
                pos2(left + index as f32 * (button_width + 8.0), y),
                vec2(button_width, 28.0),
            );
            let r = ui
                .scope_builder(egui::UiBuilder::new().max_rect(rect), |ui| {
                    ui.add_enabled_ui(index != 2 || can_replay, |ui| {
                        ui.add_sized(rect.size(), button)
                    })
                    .inner
                })
                .inner;
            let key = match index {
                0 => format!("details_{id}"),
                1 => format!("view_config_{id}"),
                _ => format!("replay_{id}_0"),
            };
            if r.enabled() {
                point(diagnostic, &key, &r);
            }
            if r.clicked() {
                match index {
                    0 => state.inspect(job),
                    1 => {
                        if let Err(error) = state.edit_config(job) {
                            state.error = Some(error);
                        }
                    }
                    _ => {
                        *replay = Some(replay_value(
                            job,
                            job["outputs"][0].as_str().expect("enabled replay"),
                        ));
                    }
                }
            }
        }
    }
    diagnostic["task_rows"][id] = json!({"title":title(job),"stage":job["stage"],"status_label":label,"subtitle":subtitle,"progress":job["progress"],"queue_position":queue_position,"rect":[rect.left(),rect.top(),rect.right(),rect.bottom()]});
}

fn pagination(
    ui: &mut egui::Ui,
    state: &mut State,
    diagnostic: &mut Value,
    pages: usize,
    total: usize,
) {
    ui.horizontal(|ui| {
        ui.spacing_mut().item_spacing.x = 6.0;
        let button = |text: &str, active: bool| {
            egui::Button::new(RichText::new(text).size(13.0).color(theme::TEXT))
                .fill(if active {
                    theme::ACCENT_STRONG
                } else {
                    theme::PANEL_BG
                })
                .stroke(Stroke::new(1.0, theme::CARD_BG))
                .corner_radius(7.0)
                .min_size(vec2(32.0, 32.0))
        };
        let previous = ui.add_enabled(state.task_page > 0, button("‹", false));
        point(diagnostic, "tasks_previous_page", &previous);
        if previous.clicked() {
            state.task_page -= 1;
        }
        if ui.available_width() < 470.0 {
            ui.label(
                RichText::new(format!("{} / {pages}", state.task_page + 1)).color(theme::TEXT),
            );
        } else {
            let mut visible = vec![0, pages - 1, state.task_page];
            if state.task_page > 0 {
                visible.push(state.task_page - 1);
            }
            if state.task_page + 1 < pages {
                visible.push(state.task_page + 1);
            }
            if state.task_page == 0 && pages > 2 {
                visible.push(2);
            }
            visible.sort_unstable();
            visible.dedup();
            let mut last = None;
            for page in visible {
                if last.is_some_and(|last| page > last + 1) {
                    ui.label(RichText::new("…").color(theme::TEXT_DIM));
                }
                let r = ui.add(button(&(page + 1).to_string(), page == state.task_page));
                point(diagnostic, &format!("tasks_page_{}", page + 1), &r);
                if r.clicked() {
                    state.task_page = page;
                }
                last = Some(page);
            }
        }
        let next = ui.add_enabled(state.task_page + 1 < pages, button("›", false));
        point(diagnostic, "tasks_next_page", &next);
        if next.clicked() {
            state.task_page += 1;
        }
        ui.allocate_ui_with_layout(
            vec2(ui.available_width(), 32.0),
            egui::Layout::right_to_left(egui::Align::Center),
            |ui| {
                ui.label(
                    RichText::new(format!("共 {total} 条"))
                        .size(13.0)
                        .color(theme::TEXT_DIM),
                );
            },
        );
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    fn job(id: &str, stage: &str, kind: &str, suite: &str) -> Value {
        json!({"id":id,"stage":stage,"config":{"kind":kind,"source":format!("/{id}.worldsim.scenario.json")},"suite_id":suite,"suite_name":"会车专项"})
    }
    #[test]
    fn counts_and_filters_keep_cancelled_and_interrupted_distinct() {
        let mut state = State::default();
        state.jobs = vec![
            job("a", "simulation_running", "world", "one"),
            job("b", "queued", "world", "one"),
            job("c", "failed", "bag", "one"),
            job("d", "completed", "world", "one"),
            job("e", "cancelled", "world", "one"),
            job("f", "interrupted", "world", "one"),
        ];
        let groups = suites(&state);
        assert_eq!(groups[0].counts.get(Status::All), 6);
        assert_eq!(groups[0].counts.get(Status::Failed), 1);
        assert_eq!(groups[0].indices, vec![0, 1, 2, 3, 4, 5]);
        state.task_source = Source::Bag;
        assert_eq!(suites(&state)[0].indices, vec![2]);
        state.task_status = Status::Completed;
        assert!(suites(&state).is_empty());
        state.task_source = Source::All;
        state.task_status = Status::Cancelled;
        assert_eq!(suites(&state)[0].indices, vec![4]);
        state.filter = "会车".into();
        assert_eq!(suites(&state)[0].indices, vec![4]);
        state.filter = "不存在".into();
        assert!(suites(&state).is_empty());
    }
    #[test]
    fn suite_identity_fifo_and_pagination_do_not_drop_jobs() {
        let mut state = State::default();
        state.jobs = (0..23)
            .map(|i| {
                job(
                    &format!("task-{i}"),
                    "queued",
                    "world",
                    if i < 13 { "one" } else { "two" },
                )
            })
            .collect();
        let groups = suites(&state);
        assert_eq!(groups.len(), 2); // Same display name must not merge independent submissions.
        let indices: Vec<_> = groups
            .iter()
            .flat_map(|group| group.indices.iter().copied())
            .collect();
        assert_eq!(indices.len(), 23);
        assert_eq!(
            indices
                .chunks(PAGE_SIZE)
                .map(|page| page.len())
                .collect::<Vec<_>>(),
            [10, 10, 3]
        );
        assert!(
            groups
                .iter()
                .all(|group| group.indices.windows(2).all(|pair| pair[0] < pair[1]))
        );
        assert_eq!(
            indices
                .iter()
                .copied()
                .collect::<std::collections::HashSet<_>>()
                .len(),
            23
        );
    }
    #[test]
    fn summaries_use_real_progress_failure_evidence_and_wall_times() {
        let mut task = job("场景名", "failed", "world", "suite");
        task["analysis"] = json!({"planning_continuity":{"status":"FAIL"}});
        assert_eq!(title(&task), "场景名");
        assert_eq!(status_text(&task, None).0, "校验失败");
        task["stage"] = json!("completed");
        task["history"] = json!([{"stage":"queued","wall_time":0},{"stage":"simulation_start","wall_time":10},{"stage":"completed","wall_time":766}]);
        assert_eq!(
            status_text(&task, None),
            ("已完成", "耗时 12 分 36 秒".into())
        );
        task["stage"] = json!("queued");
        assert_eq!(status_text(&task, Some(2)).1, "#2 · 等待资源");
    }
}
