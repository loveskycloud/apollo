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
    newest: f64,
    sources: std::collections::BTreeSet<String>,
}

fn source_label(kind: &str) -> &str {
    match kind {
        "world" => "WorldSim",
        "bag" => "LogSim",
        _ => kind,
    }
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

fn submitted_at(job: &Value) -> f64 {
    job["created_at"]
        .as_f64()
        .filter(|value| value.is_finite())
        .or_else(|| {
            job["history"]
                .as_array()?
                .iter()
                .find(|entry| entry["stage"] == "queued")?["wall_time"]
                .as_f64()
                .filter(|value| value.is_finite())
        })
        // Old imported tasks without a submission timestamp appear last.
        .unwrap_or(f64::NEG_INFINITY)
}

fn suites(state: &State) -> Vec<Suite> {
    let query = state.filter.trim().to_lowercase();
    let mut result: Vec<Suite> = Vec::new();
    let mut positions = std::collections::HashMap::new();
    for (index, job) in state.jobs.iter().enumerate() {
        let key = job["suite_id"]
            .as_str()
            .map(|id| format!("suite:{id}"))
            .unwrap_or_else(|| format!("single:{}", job["id"].as_str().unwrap_or("")));
        let position = *positions.entry(key.clone()).or_insert_with(|| {
            result.push(Suite {
                key,
                name: job["suite_name"].as_str().unwrap_or("独立任务").into(),
                counts: Counts::default(),
                indices: Vec::new(),
                newest: submitted_at(job),
                sources: Default::default(),
            });
            result.len() - 1
        });
        let suite = &mut result[position];
        suite.counts.add(job);
        suite
            .sources
            .insert(source_label(job["config"]["kind"].as_str().unwrap_or("未知来源")).into());
        suite.newest = suite.newest.max(submitted_at(job));
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
        suite.indices.sort_by(|left, right| {
            submitted_at(&state.jobs[*right])
                .total_cmp(&submitted_at(&state.jobs[*left]))
                .then_with(|| {
                    state.jobs[*left]["suite_index"]
                        .as_u64()
                        .cmp(&state.jobs[*right]["suite_index"].as_u64())
                })
        });
    }
    result.sort_by(|left, right| right.newest.total_cmp(&left.newest));
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
    let r = dropdown_trigger(
        ui,
        if key == "status" && label == "全部状态" {
            "筛选"
        } else {
            label
        },
    );
    point(diagnostic, &format!("{key}_filter"), &r);
    egui::Popup::menu(&r)
        .id(egui::Id::new(("sim_task_filter", key)))
        .align(egui::RectAlign::BOTTOM_END)
        .align_alternatives(&[egui::RectAlign::TOP_END])
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_min_width(r.rect.width().max(150.0));
                for &(value, label, name) in options {
                    let r = ui.add_sized(
                        [ui.available_width(), 32.0],
                        egui::Button::selectable(
                            *selected == value,
                            RichText::new(label).color(theme::TEXT).size(14.0),
                        )
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
        .inner_margin(egui::Margin::symmetric(10, 6))
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
    ui.add_space(8.0);
    ui.horizontal(|ui| {
        let running = counts.get(Status::Running);
        let capacity = capacity.map(|n| format!("/{n}")).unwrap_or_default();
        ui.label(
            RichText::new(format!("并发 {running}{capacity}"))
                .size(14.0)
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
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(RichText::new("后台执行").size(12.0).color(theme::TEXT_DIM));
        });
    });
    ui.add_space(10.0);
    let previous = (state.task_status, state.task_source, state.filter.clone());
    let narrow = ui.available_width() < 620.0;
    ui.horizontal(|ui| {
        let width = (ui.available_width() - 4.0 * 8.0) / 5.0;
        ui.spacing_mut().item_spacing.x = 8.0;
        for (status, label) in [
            (Status::All, "全部"),
            (Status::Running, "运行中"),
            (Status::Queued, "排队"),
            (Status::Completed, "已完成"),
            (Status::Failed, "失败"),
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
        state.suite_pages.clear();
    }
    ui.add_space(10.0);
    ui.separator();
    ui.horizontal(|ui| {
        ui.label(
            RichText::new("最近任务")
                .size(15.0)
                .strong()
                .color(theme::TEXT),
        );
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(
                RichText::new("最近创建 ↓")
                    .size(12.0)
                    .color(theme::TEXT_DIM),
            );
        });
    });
    ui.add_space(4.0);

    let groups = suites(state);
    let total = groups
        .iter()
        .map(|suite| suite.indices.len())
        .sum::<usize>();
    let pages = groups.len().div_ceil(PAGE_SIZE).max(1);
    state.task_page = state.task_page.min(pages - 1);
    let start = state.task_page * PAGE_SIZE;
    let end = (start + PAGE_SIZE).min(groups.len());
    diagnostic["task_pagination"] = json!({"page":state.task_page+1,"pages":pages,"page_size":PAGE_SIZE,"total":total,"group_total":groups.len(),"unit":"groups"});
    diagnostic["task_status"] = json!(state.task_status.key());
    diagnostic["task_source"] = json!(state.task_source.key());
    diagnostic["task_rows"] = json!({});
    diagnostic["task_suites"] = json!([]);
    diagnostic["suite_pagination"] = json!({});
    diagnostic["task_popups"] = json!({});
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
    let height = (ui.available_height() - 64.0).max(0.0);
    let scroll = egui::ScrollArea::vertical()
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
                            "在「仿真配置」页选择场景并启动任务。"
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
                            state.suite_pages.clear();
                        }
                    }
                });
            }
            for suite in &groups[start..end] {
                let suite_total = suite.indices.len();
                let suite_page_count = suite_total.div_ceil(PAGE_SIZE).max(1);
                let suite_page = state.suite_pages.entry(suite.key.clone()).or_default();
                *suite_page = (*suite_page).min(suite_page_count - 1);
                let lo = *suite_page * PAGE_SIZE;
                let hi = (lo + PAGE_SIZE).min(suite_total);
                diagnostic["suite_pagination"][&suite.key] = json!({
                    "page":*suite_page+1,"pages":suite_page_count,
                    "page_size":PAGE_SIZE,"total":suite_total
                });
                egui::Frame::new()
                    .fill(theme::APP_BG)
                    .stroke(Stroke::new(1.0, theme::CARD_BG))
                    .corner_radius(8.0)
                    .show(ui, |ui| {
                        ui.spacing_mut().item_spacing.y = 0.0;
                        ui.set_width(ui.available_width());
                        if suite.key.starts_with("suite:") {
                            suite_header(ui, suite, state, diagnostic, action);
                        }
                        if !state.collapsed_suites.contains(&suite.key) {
                            for &index in &suite.indices[lo..hi] {
                                let job = state.jobs[index].clone();
                                let position =
                                    job["id"].as_str().and_then(|id| queue.get(id)).copied();
                                row(ui, &job, position, state, diagnostic, action, replay);
                            }
                            if suite_page_count > 1 {
                                ui.add_space(8.0);
                                ui.separator();
                                let page = state
                                    .suite_pages
                                    .get_mut(&suite.key)
                                    .expect("suite page was initialized before rendering");
                                pagination(
                                    ui,
                                    page,
                                    diagnostic,
                                    suite_page_count,
                                    &format!("共 {suite_total} 条"),
                                    &format!("suite_tasks_{}", suite.key),
                                );
                            }
                        }
                    });
                ui.add_space(10.0);
            }
        });
    diagnostic["task_list_rect"] = json!([
        scroll.inner_rect.left(),
        scroll.inner_rect.top(),
        scroll.inner_rect.right(),
        scroll.inner_rect.bottom()
    ]);
    ui.add_space(8.0);
    ui.separator();
    let footer = pagination(
        ui,
        &mut state.task_page,
        diagnostic,
        pages,
        &format!("{} 组 · {total} 条", groups.len()),
        "tasks",
    );
    diagnostic["task_footer_rect"] =
        json!([footer.left(), footer.top(), footer.right(), footer.bottom()]);
}

fn suite_header(
    ui: &mut egui::Ui,
    suite: &Suite,
    state: &mut State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
) {
    let collapsed = state.collapsed_suites.contains(&suite.key);
    let (rect, _) = ui.allocate_exact_size(vec2(ui.available_width(), 66.0), egui::Sense::hover());
    let response = ui.interact(
        egui::Rect::from_min_max(rect.min, rect.right_bottom() - vec2(44.0, 0.0)),
        ui.id().with(("suite_collapse", &suite.key)),
        egui::Sense::click(),
    );
    let center = rect.left_top() + vec2(18.0, 25.0);
    let points = if collapsed {
        vec![
            center + vec2(-2.0, -4.0),
            center + vec2(2.0, 0.0),
            center + vec2(-2.0, 4.0),
        ]
    } else {
        vec![
            center + vec2(-4.0, 2.0),
            center + vec2(0.0, -2.0),
            center + vec2(4.0, 2.0),
        ]
    };
    ui.painter()
        .add(egui::Shape::line(points, Stroke::new(1.8, theme::TEXT_DIM)));
    let total = suite.counts.get(Status::All);
    let status = [
        Status::Running,
        Status::Queued,
        Status::Failed,
        Status::Interrupted,
        Status::Cancelled,
    ]
    .into_iter()
    .find(|status| suite.counts.get(*status) > 0)
    .unwrap_or(Status::Completed);
    let status_width = 128.0;
    let title_rect = egui::Rect::from_min_size(
        rect.min + vec2(36.0, 10.0),
        vec2((rect.width() - status_width - 96.0).max(60.0), 24.0),
    );
    left_label(
        ui,
        title_rect,
        egui::Label::new(
            RichText::new(&suite.name)
                .size(15.0)
                .strong()
                .color(theme::TEXT),
        )
        .truncate(),
    )
    .on_hover_text(&suite.name);
    let source = suite
        .sources
        .iter()
        .cloned()
        .collect::<Vec<_>>()
        .join(" / ");
    left_label(
        ui,
        egui::Rect::from_min_size(rect.min + vec2(36.0, 37.0), vec2(rect.width() - 52.0, 20.0)),
        egui::Label::new(
            RichText::new(format!("{total} 个场景 · {source}"))
                .size(12.0)
                .color(theme::TEXT_DIM),
        )
        .truncate(),
    );
    let status_origin = pos2(rect.right() - status_width - 40.0, rect.top() + 23.0);
    status_icon(
        ui,
        status_origin,
        status,
        suite.counts.get(Status::Completed) as f32 / total.max(1) as f32,
    );
    left_label(
        ui,
        egui::Rect::from_min_size(
            status_origin + vec2(17.0, -12.0),
            vec2(status_width - 23.0, 24.0),
        ),
        egui::Label::new(
            RichText::new(format!(
                "{} {}/{total}",
                status.label(),
                suite.counts.get(status)
            ))
            .size(12.0)
            .color(status.color()),
        )
        .truncate(),
    )
    .on_hover_text(format!(
        "运行 {} · 排队 {} · 完成 {} · 失败 {} · 取消 {} · 中断 {}",
        suite.counts.get(Status::Running),
        suite.counts.get(Status::Queued),
        suite.counts.get(Status::Completed),
        suite.counts.get(Status::Failed),
        suite.counts.get(Status::Cancelled),
        suite.counts.get(Status::Interrupted)
    ));
    if !collapsed {
        ui.painter().hline(
            rect.x_range(),
            rect.bottom(),
            Stroke::new(1.0, theme::CARD_BG),
        );
    }
    point(diagnostic, &format!("suite_{}", suite.key), &response);
    diagnostic["task_suites"].as_array_mut().expect("suite diagnostics").push(json!({"key":suite.key,"name":suite.name,"source":source,"counts":suite.counts.diagnostic(),"collapsed":collapsed,"rect":[rect.left(),rect.top(),rect.right(),rect.bottom()]}));
    if response.clicked() {
        if collapsed {
            state.collapsed_suites.remove(&suite.key);
        } else {
            state.collapsed_suites.insert(suite.key.clone());
        }
    }
    // Positioned controls must not move the list cursor back into the header.
    let mut menu_ui = ui.new_child(egui::UiBuilder::new().max_rect(rect));
    suite_menu(&mut menu_ui, rect, suite, state, diagnostic, action);
}

fn suite_menu(
    ui: &mut egui::Ui,
    rect: egui::Rect,
    suite: &Suite,
    state: &State,
    diagnostic: &mut Value,
    action: &mut Option<Value>,
) {
    let id = suite.key.strip_prefix("suite:").expect("suite header");
    let menu_rect = egui::Rect::from_min_size(
        pos2(rect.right() - 40.0, rect.top() + 9.0),
        vec2(28.0, 28.0),
    );
    let menu = row_button(ui, menu_rect, "", true).on_hover_text("场景集操作");
    for dx in [-5.0, 0.0, 5.0] {
        ui.painter()
            .circle_filled(menu_rect.center() + vec2(dx, 0.0), 1.4, theme::TEXT_DIM);
    }
    point(diagnostic, &format!("suite_menu_{id}"), &menu);
    let running = suite.counts.get(Status::Running);
    let unfinished = running + suite.counts.get(Status::Queued);
    if let Some(popup) = egui::Popup::menu(&menu)
        .id(ui.id().with(("suite_menu", id)))
        .align(egui::RectAlign::BOTTOM_END)
        .align_alternatives(&[egui::RectAlign::TOP_END])
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_width(180.0);
                ui.label(
                    RichText::new(format!("整组 {} 个场景", suite.counts.get(Status::All)))
                        .size(12.0)
                        .color(theme::TEXT_DIM),
                );
                for (command, label, enabled, color, hint) in [
                    (
                        "cancel_suite",
                        "全部取消",
                        unfinished > 0,
                        FAILURE,
                        "取消整组排队及运行中的任务，保留已结束任务",
                    ),
                    (
                        "retry_suite",
                        "全部重试",
                        unfinished == 0,
                        theme::TEXT,
                        "整组结束后，按原配置重新提交所有场景并保留原记录",
                    ),
                    (
                        "delete_suite",
                        "全部删除",
                        running == 0,
                        FAILURE,
                        "删除整组任务及录包、结果和回放缓存；运行中需先取消",
                    ),
                ] {
                    if command == "delete_suite" {
                        ui.separator();
                    }
                    let enabled = enabled && state.pending.is_none();
                    let r = ui
                        .add_enabled(
                            enabled,
                            egui::Button::selectable(false, RichText::new(label).color(color))
                                .min_size(vec2(ui.available_width(), 30.0)),
                        )
                        .on_hover_text(hint);
                    point(diagnostic, &format!("{command}_{id}"), &r);
                    diagnostic[format!("{command}_{id}_enabled")] = json!(enabled);
                    if r.clicked() {
                        *action = Some(json!({"action":command,"suite_id":id}));
                        ui.close();
                    }
                }
            });
        })
    {
        let r = popup.response.rect;
        diagnostic["task_popups"][format!("suite_{id}")] =
            json!([r.left(), r.top(), r.right(), r.bottom()]);
    }
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
    let (rect, response) = ui.allocate_exact_size(
        vec2(ui.available_width(), if narrow { 110.0 } else { 82.0 }),
        egui::Sense::hover(),
    );
    let popup_id = ui.id().with("task_popup");
    let replay_id = ui.id().with("replay_popup");
    let active =
        egui::Popup::is_id_open(ui.ctx(), popup_id) || egui::Popup::is_id_open(ui.ctx(), replay_id);
    if response.hovered() || active {
        ui.painter()
            .rect_filled(rect.shrink(1.0), 6.0, theme::CARD_BG.gamma_multiply(0.65));
    }
    ui.painter().hline(
        rect.x_range(),
        rect.bottom(),
        Stroke::new(1.0, theme::CARD_BG),
    );
    let action_width = 262.0;
    let action_left = rect.right() - action_width - 12.0;
    let status_left = if narrow {
        rect.right() - 116.0
    } else {
        action_left
    };
    let title_rect = egui::Rect::from_min_size(
        rect.min + vec2(14.0, 10.0),
        vec2((status_left - rect.left() - 30.0).max(60.0), 24.0),
    );
    let title_response = left_label(
        ui,
        title_rect,
        egui::Label::new(
            RichText::new(title(job))
                .size(14.0)
                .strong()
                .color(theme::TEXT),
        )
        .truncate()
        .sense(egui::Sense::click()),
    );
    point(diagnostic, &format!("inspect_{id}"), &title_response);
    if title_response
        .on_hover_text(format!(
            "{}\n{id}",
            job["config"]["source"].as_str().unwrap_or("")
        ))
        .clicked()
    {
        state.inspect(job);
    }
    let status_origin = pos2(status_left + 10.0, rect.top() + 22.0);
    status_icon(ui, status_origin, status, progress);
    let status_rect = egui::Rect::from_min_size(
        status_origin + vec2(18.0, -12.0),
        vec2(rect.right() - status_origin.x - 30.0, 24.0),
    );
    left_label(
        ui,
        status_rect,
        egui::Label::new(RichText::new(label).size(13.0).color(status.color())).truncate(),
    )
    .on_hover_text(job["error"].as_str().unwrap_or(&subtitle));
    let short_id: String = id.chars().take(8).collect();
    let run = job["run"]
        .as_u64()
        .map(|n| n.to_string())
        .unwrap_or_else(|| "—".into());
    let repeat = job["config"]["repeat"]
        .as_u64()
        .map(|n| n.to_string())
        .unwrap_or_else(|| "—".into());
    let timing = duration(job).unwrap_or_else(|| subtitle.clone());
    let source = if job["suite_id"].is_string() {
        String::new()
    } else {
        format!(
            " · {}",
            source_label(job["config"]["kind"].as_str().unwrap_or("未知来源"))
        )
    };
    let metadata = format!("ID: {short_id} · 运行 {run}/{repeat} · {timing}{source}");
    let metadata_rect = egui::Rect::from_min_size(rect.min + vec2(14.0, 45.0), vec2(168.0, 22.0));
    left_label(
        ui,
        metadata_rect,
        egui::Label::new(
            RichText::new(format!("ID: {short_id} · 运行 {run}/{repeat}"))
                .size(12.0)
                .color(theme::TEXT_DIM),
        )
        .truncate(),
    )
    .on_hover_text(format!("{metadata}\n{subtitle}"));
    let timing_rect = egui::Rect::from_min_max(
        rect.min + vec2(190.0, 45.0),
        pos2(
            if narrow {
                rect.right() - 14.0
            } else {
                action_left - 12.0
            },
            rect.top() + 67.0,
        ),
    );
    left_label(
        ui,
        timing_rect,
        egui::Label::new(
            RichText::new(format!("{timing}{source}"))
                .size(12.0)
                .color(theme::TEXT_DIM),
        )
        .truncate(),
    )
    .on_hover_text(format!("{timing}{source}\n{subtitle}"));
    if status == Status::Running {
        let track = egui::Rect::from_min_size(
            rect.left_bottom() + vec2(14.0, -5.0),
            vec2(rect.width() - 28.0, 2.0),
        );
        ui.painter().rect_filled(track, 1.0, theme::CARD_BG);
        ui.painter().rect_filled(
            egui::Rect::from_min_size(track.min, vec2(track.width() * progress, 2.0)),
            1.0,
            RUNNING,
        );
    }
    let y = rect.bottom() - 38.0;
    let details_rect = egui::Rect::from_min_size(pos2(action_left, y), vec2(56.0, 28.0));
    let details = row_button(ui, details_rect, "详情", true);
    point(diagnostic, &format!("details_{id}"), &details);
    if details.clicked() {
        state.inspect(job);
    }
    let reuse_rect = egui::Rect::from_min_size(pos2(action_left + 62.0, y), vec2(82.0, 28.0));
    let reuse = row_button(ui, reuse_rect, "复用配置", true);
    point(diagnostic, &format!("view_config_{id}"), &reuse);
    if reuse.clicked()
        && let Err(error) = state.edit_config(job)
    {
        state.error = Some(error);
    }
    let can_replay = Group::for_stage(job["stage"].as_str().unwrap_or("")) == Group::Finished
        && job["outputs"]
            .as_array()
            .is_some_and(|outputs| outputs.iter().any(Value::is_string));
    let replay_rect = egui::Rect::from_min_size(pos2(action_left + 150.0, y), vec2(78.0, 28.0));
    let replay_button = row_button(ui, replay_rect, "回放", can_replay);
    let arrow = replay_rect.right_center() - vec2(10.0, 0.0);
    ui.painter().add(egui::Shape::convex_polygon(
        vec![
            arrow + vec2(-3.5, -2.0),
            arrow + vec2(3.5, -2.0),
            arrow + vec2(0.0, 2.5),
        ],
        if can_replay {
            theme::TEXT_DIM
        } else {
            theme::TEXT_DIM.gamma_multiply(0.4)
        },
        Stroke::NONE,
    ));
    point(diagnostic, &format!("replay_menu_{id}"), &replay_button);
    if let Some(popup) = egui::Popup::menu(&replay_button)
        .id(replay_id)
        .align(egui::RectAlign::BOTTOM_END)
        .align_alternatives(&[egui::RectAlign::TOP_END])
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_width(160.0);
                ui.set_max_height(220.0);
                egui::ScrollArea::vertical()
                    .max_height(220.0)
                    .show(ui, |ui| {
                        if let Some(outputs) = job["outputs"].as_array() {
                            for (index, path) in outputs
                                .iter()
                                .enumerate()
                                .filter_map(|(i, p)| p.as_str().map(|p| (i, p)))
                            {
                                let r = ui.add_sized(
                                    [ui.available_width(), 30.0],
                                    egui::Button::selectable(
                                        false,
                                        RichText::new(format!("第 {} 次运行", index + 1))
                                            .color(theme::TEXT),
                                    ),
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
        })
    {
        let r = popup.response.rect;
        diagnostic["task_popups"][format!("replay_{id}")] =
            json!([r.left(), r.top(), r.right(), r.bottom()]);
    }
    let menu_rect = egui::Rect::from_min_size(pos2(action_left + 234.0, y), vec2(28.0, 28.0));
    let menu = row_button(ui, menu_rect, "", true);
    for dx in [-5.0, 0.0, 5.0] {
        ui.painter()
            .circle_filled(menu_rect.center() + vec2(dx, 0.0), 1.4, theme::TEXT_DIM);
    }
    point(diagnostic, &format!("task_menu_{id}"), &menu);
    if let Some(popup) = egui::Popup::menu(&menu)
        .id(popup_id)
        .align(egui::RectAlign::BOTTOM_END)
        .align_alternatives(&[egui::RectAlign::TOP_END])
        .show(|ui| {
            dark_menu_frame(ui, |ui| {
                ui.set_width(160.0);
                if Group::for_stage(job["stage"].as_str().unwrap_or("")) != Group::Finished {
                    let r = ui.add_enabled(
                        state.pending.is_none(),
                        egui::Button::selectable(false, RichText::new("取消任务").color(FAILURE))
                            .min_size(vec2(ui.available_width(), 30.0)),
                    );
                    point(diagnostic, &format!("cancel_{id}"), &r);
                    if r.clicked() {
                        *action = Some(json!({"action":"cancel","id":id}));
                        ui.close();
                    }
                    ui.separator();
                }
                let can_delete = matches!(
                    job["stage"].as_str(),
                    Some("queued" | "completed" | "failed" | "cancelled" | "interrupted")
                );
                let r = ui
                    .add_enabled(
                        state.pending.is_none() && can_delete,
                        egui::Button::selectable(false, RichText::new("删除任务").color(FAILURE))
                            .min_size(vec2(ui.available_width(), 30.0)),
                    )
                    .on_hover_text(if can_delete {
                        "删除任务及其录包、结果和回放缓存"
                    } else {
                        "请先取消任务，等待运行结束后再删除"
                    });
                point(diagnostic, &format!("delete_{id}"), &r);
                if r.clicked() {
                    *action = Some(json!({"action":"delete","id":id}));
                    ui.close();
                }
            });
        })
    {
        let r = popup.response.rect;
        diagnostic["task_popups"][format!("more_{id}")] =
            json!([r.left(), r.top(), r.right(), r.bottom()]);
    }
    diagnostic["task_rows"][id] = json!({"title":title(job),"created_at":job["created_at"],"stage":job["stage"],"status_label":label,"subtitle":subtitle,"metadata":metadata,"progress":job["progress"],"queue_position":queue_position,"rect":[rect.left(),rect.top(),rect.right(),rect.bottom()],"status_rect":[status_rect.left(),status_rect.top(),status_rect.right(),status_rect.bottom()],"actions_y":y});
}

fn row_button(ui: &mut egui::Ui, rect: egui::Rect, text: &str, enabled: bool) -> egui::Response {
    ui.scope_builder(egui::UiBuilder::new().max_rect(rect), |ui| {
        ui.add_enabled_ui(enabled, |ui| {
            ui.add_sized(
                rect.size(),
                egui::Button::new(RichText::new(text).size(12.0).color(theme::TEXT))
                    .fill(theme::CARD_BG)
                    .stroke(Stroke::new(1.0, theme::CARD_BG_HOVER))
                    .corner_radius(6.0),
            )
        })
        .inner
    })
    .inner
}

fn pagination(
    ui: &mut egui::Ui,
    current_page: &mut usize,
    diagnostic: &mut Value,
    pages: usize,
    summary: &str,
    key: &str,
) -> egui::Rect {
    let footer = ui.push_id(key, |ui| {
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 6.0;
            ui.add_sized(
                [120.0, 32.0],
                egui::Label::new(RichText::new(summary).size(13.0).color(theme::TEXT_DIM)),
            );
            ui.allocate_ui_with_layout(
                vec2(ui.available_width(), 32.0),
                egui::Layout::right_to_left(egui::Align::Center),
                |ui| {
                    let button = |text: &str, active: bool| {
                        egui::Button::new(RichText::new(text).size(13.0).color(theme::TEXT))
                            .fill(if active {
                                theme::ACCENT_STRONG
                            } else {
                                theme::PANEL_BG
                            })
                            .stroke(Stroke::new(1.0, theme::CARD_BG))
                            .corner_radius(6.0)
                            .min_size(vec2(32.0, 32.0))
                    };
                    let next = ui.add_enabled(*current_page + 1 < pages, button("›", false));
                    point(diagnostic, &format!("{key}_next_page"), &next);
                    if next.clicked() {
                        *current_page += 1;
                    }
                    if ui.available_width() < 330.0 {
                        ui.label(
                            RichText::new(format!("{} / {pages}", *current_page + 1))
                                .color(theme::TEXT),
                        );
                    } else {
                        let mut visible = vec![0, pages - 1, *current_page];
                        if *current_page > 0 {
                            visible.push(*current_page - 1);
                        }
                        if *current_page + 1 < pages {
                            visible.push(*current_page + 1);
                        }
                        if *current_page == 0 && pages > 2 {
                            visible.push(2);
                        }
                        visible.sort_unstable();
                        visible.dedup();
                        let mut last = None;
                        for page in visible.into_iter().rev() {
                            if last.is_some_and(|last| last > page + 1) {
                                ui.label(RichText::new("…").color(theme::TEXT_DIM));
                            }
                            let r = ui.add(button(&(page + 1).to_string(), page == *current_page));
                            point(diagnostic, &format!("{key}_page_{}", page + 1), &r);
                            if r.clicked() {
                                *current_page = page;
                            }
                            last = Some(page);
                        }
                    }
                    let previous = ui.add_enabled(*current_page > 0, button("‹", false));
                    point(diagnostic, &format!("{key}_previous_page"), &previous);
                    if previous.clicked() {
                        *current_page -= 1;
                    }
                },
            );
        })
    });
    footer.inner.response.rect
}

#[cfg(test)]
mod tests {
    use super::*;
    fn render(state: &mut State) -> Value {
        let ctx = egui::Context::default();
        let mut diagnostic = json!({});
        let mut output = ctx.run_ui(
            egui::RawInput {
                screen_rect: Some(egui::Rect::from_min_size(
                    egui::Pos2::ZERO,
                    vec2(1000.0, 2400.0),
                )),
                ..Default::default()
            },
            |ui| {
                show(ui, state, &mut diagnostic, &mut None, &mut None);
            },
        );
        output.textures_delta.clear();
        diagnostic
    }

    fn regression_jobs() -> Vec<Value> {
        (0..308)
            .map(|i| {
                let parking = i < 294;
                let mut task = job(
                    &format!("task-{i}"),
                    "completed",
                    "world",
                    if parking { "parking" } else { "ordinary" },
                );
                task["created_at"] = json!(if parking { 2 } else { 1 });
                task["suite_index"] = json!(i);
                task
            })
            .collect()
    }

    #[test]
    fn large_suite_does_not_hide_other_suites_when_expanded_or_collapsed() {
        let mut state = State::default();
        state.jobs = regression_jobs();
        state.task_page = 30; // Clamp an existing page from the old flat pagination.
        let diagnostic = render(&mut state);
        assert_eq!(diagnostic["task_counts"]["completed"], 308);
        assert_eq!(diagnostic["task_pagination"]["total"], 308);
        assert_eq!(diagnostic["task_pagination"]["group_total"], 2);
        assert_eq!(diagnostic["task_pagination"]["pages"], 1);
        assert_eq!(diagnostic["task_suites"].as_array().unwrap().len(), 2);
        assert_eq!(diagnostic["task_rows"].as_object().unwrap().len(), 20);
        assert!(!diagnostic["task_rows"]["task-294"].is_null());

        state.collapsed_suites.insert("suite:parking".into());
        let diagnostic = render(&mut state);
        assert_eq!(diagnostic["task_suites"].as_array().unwrap().len(), 2);
        assert_eq!(diagnostic["task_rows"].as_object().unwrap().len(), 10);
        assert!(!diagnostic["task_rows"]["task-294"].is_null());
        assert!(diagnostic["task_rows"]["task-0"].is_null());
        assert_eq!(diagnostic["task_pagination"]["pages"], 1);
    }

    #[test]
    fn suite_pages_are_independent_and_clamped_after_filtering() {
        let mut state = State::default();
        state.jobs = regression_jobs();
        state.suite_pages.insert("suite:parking".into(), 29);
        state.suite_pages.insert("suite:ordinary".into(), 1);
        let diagnostic = render(&mut state);
        assert_eq!(diagnostic["task_rows"].as_object().unwrap().len(), 8);
        assert!(!diagnostic["task_rows"]["task-290"].is_null());
        assert!(!diagnostic["task_rows"]["task-304"].is_null());
        assert_eq!(diagnostic["suite_pagination"]["suite:parking"]["pages"], 30);
        assert_eq!(diagnostic["suite_pagination"]["suite:ordinary"]["pages"], 2);
        state.collapsed_suites.insert("suite:parking".into());
        assert_eq!(
            render(&mut state)["task_rows"].as_object().unwrap().len(),
            4
        );
        state.collapsed_suites.clear();
        assert_eq!(
            render(&mut state)["task_rows"].as_object().unwrap().len(),
            8
        );

        state.filter = "task-307".into();
        let diagnostic = render(&mut state);
        assert_eq!(diagnostic["task_pagination"]["total"], 1);
        assert_eq!(diagnostic["task_rows"].as_object().unwrap().len(), 1);
        assert_eq!(state.suite_pages["suite:ordinary"], 0);
        assert!(!diagnostic["task_rows"]["task-307"].is_null());
    }

    #[test]
    fn independent_tasks_still_paginate_without_losing_tasks() {
        let mut state = State::default();
        state.jobs = (0..23)
            .map(|i| {
                let mut task = job(&format!("task-{i}"), "completed", "world", "");
                task.as_object_mut().unwrap().remove("suite_id");
                task
            })
            .collect();
        let mut seen = std::collections::HashSet::new();
        for (page, count) in [10, 10, 3].into_iter().enumerate() {
            state.task_page = page;
            let diagnostic = render(&mut state);
            assert_eq!(diagnostic["task_pagination"]["pages"], 3);
            let rows = diagnostic["task_rows"].as_object().unwrap();
            assert_eq!(rows.len(), count);
            seen.extend(rows.keys().cloned());
        }
        assert_eq!(seen.len(), 23);
    }

    fn job(id: &str, stage: &str, kind: &str, suite: &str) -> Value {
        json!({"id":id,"stage":stage,"config":{"kind":kind,"source":format!("/{id}.worldsim.scenario.json")},"suite_id":suite,"suite_name":"会车专项"})
    }
    #[test]
    fn task_order_uses_submission_time_not_id_status_or_snapshot_order() {
        let mut state = State::default();
        state.jobs = vec![
            json!({"id":"z-old-running","stage":"simulation_running","created_at":10}),
            json!({"id":"b-member-two","stage":"failed","created_at":20,"suite_id":"batch","suite_index":2}),
            json!({"id":"a-new-completed","stage":"completed","created_at":30}),
            json!({"id":"z-member-one","stage":"queued","created_at":20,"suite_id":"batch","suite_index":1}),
            json!({"id":"legacy","stage":"completed","history":[{"stage":"queued","wall_time":25}]}),
        ];
        let ordered: Vec<_> = suites(&state)
            .into_iter()
            .flat_map(|group| group.indices)
            .collect();
        assert_eq!(ordered, [2, 4, 3, 1, 0]);
        state.jobs.reverse();
        let ids: Vec<_> = suites(&state)
            .into_iter()
            .flat_map(|group| group.indices)
            .map(|index| state.jobs[index]["id"].as_str().unwrap())
            .collect();
        assert_eq!(
            ids,
            [
                "a-new-completed",
                "legacy",
                "z-member-one",
                "b-member-two",
                "z-old-running"
            ]
        );
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
    fn suite_identity_and_fifo_do_not_drop_jobs() {
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
