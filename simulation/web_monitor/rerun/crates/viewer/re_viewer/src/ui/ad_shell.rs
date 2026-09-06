//! AD shell: leftmost square nav (Source / Layout / Panel / Sim) + left secondary drawers.
//! Matches the Carolanne-purple web_monitor prototype.

use egui::{Color32, CornerRadius, Pos2, Rect, RichText, Sense, Stroke, StrokeKind, Ui, Vec2};
use re_data_source::LogDataSource;
use re_data_ui::DataUi as _;
use re_entity_db::InstancePath;
use re_log_channel::LogSource;
use re_log_types::{
    ApplicationId, EntityPath, FileSource, RecordingId, StoreId, TimeInt, Timeline, TimelineName,
    TimestampFormat,
};
use re_ui::{UICommand, UICommandSender as _};
use re_viewer_context::{
    AppContext, StoreViewContext, SystemCommand, SystemCommandSender as _, TimeControlCommand,
    UiLayout, ViewerContext,
};

const PLANNER_RBL: &[u8] = include_bytes!("../../layouts/planner.rbl");
const PERCEPTION_RBL: &[u8] = include_bytes!("../../layouts/perception.rbl");
const CONTROL_RBL: &[u8] = include_bytes!("../../layouts/control.rbl");

/// Stable ApplicationId for AD layouts. Independent of whether a bag is open.
const AD_APPLICATION_ID: &str = "apollo_ad_viewer";
/// Placeholder recording so blueprint activation has something to attach to (data optional).
const AD_LAYOUT_WORKSPACE_RECORDING_ID: &str = "ad_layout_workspace";

fn ad_application_id() -> ApplicationId {
    ApplicationId::from_static_str(AD_APPLICATION_ID)
}

fn ad_layout_workspace_store_id() -> StoreId {
    StoreId::recording(ad_application_id(), AD_LAYOUT_WORKSPACE_RECORDING_ID)
}

pub mod theme {
    use egui::Color32;

    pub const APP_BG: Color32 = Color32::from_rgb(0x1E, 0x1A, 0x28);
    pub const RAIL_BG: Color32 = Color32::from_rgb(0x16, 0x13, 0x20);
    pub const PANEL_BG: Color32 = Color32::from_rgb(0x2A, 0x23, 0x3A);
    pub const CARD_BG: Color32 = Color32::from_rgb(0x3A, 0x31, 0x50);
    pub const CARD_BG_HOVER: Color32 = Color32::from_rgb(0x4A, 0x3F, 0x66);
    pub const ACCENT: Color32 = Color32::from_rgb(0xB8, 0x94, 0xF6);
    pub const ACCENT_STRONG: Color32 = Color32::from_rgb(0x9F, 0x7A, 0xEA);
    pub const TEXT: Color32 = Color32::from_rgb(0xF3, 0xEE, 0xFF);
    pub const TEXT_DIM: Color32 = Color32::from_rgb(0xC4, 0xB5, 0xFD);
    pub const PIN_ACTIVE: Color32 = Color32::from_rgb(0xDD, 0xD6, 0xFE);
}

/// Apply Carolanne purple overrides on top of Rerun design tokens.
pub fn apply_theme(ctx: &egui::Context) {
    ctx.style_mut_of(egui::Theme::Dark, |style| {
        let v = &mut style.visuals;
        v.panel_fill = theme::APP_BG;
        v.window_fill = theme::PANEL_BG;
        v.extreme_bg_color = theme::RAIL_BG;
        v.faint_bg_color = theme::PANEL_BG;
        v.code_bg_color = theme::CARD_BG;
        v.widgets.noninteractive.weak_bg_fill = theme::APP_BG;
        v.widgets.noninteractive.bg_fill = theme::APP_BG;
        v.widgets.inactive.bg_fill = theme::CARD_BG;
        v.widgets.hovered.weak_bg_fill = theme::CARD_BG_HOVER;
        v.widgets.hovered.bg_fill = theme::CARD_BG_HOVER;
        v.widgets.active.weak_bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.55);
        v.widgets.active.bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.55);
        v.selection.bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.45);
        v.selection.stroke = Stroke::new(1.0, theme::ACCENT);
        v.hyperlink_color = theme::ACCENT;
        v.widgets.noninteractive.bg_stroke.color = theme::ACCENT.gamma_multiply(0.25);
    });
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum AdNavId {
    Source,
    Layout,
    Panel,
    Sim,
}

impl AdNavId {
    fn label(self) -> &'static str {
        match self {
            Self::Source => "Source",
            Self::Layout => "Layout",
            Self::Panel => "Panel",
            Self::Sim => "Sim",
        }
    }

    fn all() -> [Self; 4] {
        [Self::Source, Self::Layout, Self::Panel, Self::Sim]
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum AdLayoutKind {
    Perception,
    Planning,
    Control,
    Custom,
}

impl AdLayoutKind {
    pub fn label(self) -> &'static str {
        match self {
            Self::Perception => "Perception layout",
            Self::Planning => "Planning layout",
            Self::Control => "Control layout",
            Self::Custom => "Custom layout",
        }
    }

    pub fn file_name(self) -> Option<&'static str> {
        match self {
            Self::Perception => Some("perception.rbl"),
            Self::Planning => Some("planner.rbl"),
            Self::Control => Some("control.rbl"),
            Self::Custom => None,
        }
    }

    pub fn bytes(self) -> Option<&'static [u8]> {
        match self {
            Self::Perception => Some(PERCEPTION_RBL),
            Self::Planning => Some(PLANNER_RBL),
            Self::Control => Some(CONTROL_RBL),
            Self::Custom => None,
        }
    }

    pub fn all() -> [Self; 4] {
        [
            Self::Perception,
            Self::Planning,
            Self::Control,
            Self::Custom,
        ]
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize, Default)]
pub enum SourceOpenMode {
    #[default]
    Local,
    Scenario,
    TripSegment,
}

impl SourceOpenMode {
    fn label(self) -> &'static str {
        match self {
            Self::Local => "From local data",
            Self::Scenario => "From scenario ID",
            Self::TripSegment => "Trip segment",
        }
    }

    fn all() -> [Self; 3] {
        [Self::Local, Self::Scenario, Self::TripSegment]
    }
}

#[derive(serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct AdShell {
    /// Highlighted nav square (prototype default: Source).
    pub active_nav: AdNavId,
    /// Source secondary drawer open.
    pub source_open: bool,
    /// Layout secondary drawer open.
    pub layout_open: bool,
    /// Panel secondary drawer open (edit current layout).
    pub panel_open: bool,
    pub sim_modal_open: bool,
    pub active_layout: Option<AdLayoutKind>,
    pub default_layout: Option<AdLayoutKind>,
    /// Which open-source mode is selected in the Source drawer.
    source_open_mode: SourceOpenMode,
    /// User-entered / last-opened identifiers (shown in Source props).
    scenario_id: String,
    trip_id: String,
    car_id: String,
    /// Last local bag / recording path opened via Source.
    local_bag: String,
    /// Draft fields for the Open section (not yet committed).
    open_scenario_draft: String,
    open_car_id_draft: String,
    open_start_ts_draft: String,
    open_end_ts_draft: String,
    /// Local path typed by the user (server-side path for --recording / native open).
    open_local_path_draft: String,
    /// Last open attempt status shown under Local controls.
    #[serde(skip)]
    open_status_msg: String,
    /// Active convert job id (Apollo .record → MCAP).
    #[serde(skip)]
    convert_job_id: Option<String>,
    #[serde(skip)]
    convert_last_poll: f64,
    #[serde(skip)]
    convert_opened_output: Option<String>,
    #[serde(skip)]
    applied_default_once: bool,
    /// Last recording we bound the active AD layout to (re-apply on new opens).
    #[serde(skip)]
    layout_bound_store_id: Option<StoreId>,
    /// Topic View overlay (Panel → Add Topic view).
    #[serde(skip)]
    topic_view_open: bool,
    /// Draft topic path (typed or picked from the bag list).
    #[serde(skip)]
    topic_input: String,
    /// Confirmed topic path; LatestAt data is shown for this entity.
    #[serde(skip)]
    topic_bound: Option<EntityPath>,
    /// Request focus on the topic input (so empty-prefix suggestions show).
    #[serde(skip)]
    topic_view_focus_input: bool,
    /// MCAP summary channel topics (from host `/api/mcap_topics`, not EntityDb scan).
    #[serde(skip)]
    mcap_topic_list: Vec<String>,
    #[serde(skip)]
    mcap_topic_list_path: String,
}

impl Default for AdShell {
    fn default() -> Self {
        Self {
            active_nav: AdNavId::Source,
            source_open: false,
            layout_open: false,
            panel_open: false,
            sim_modal_open: false,
            active_layout: Some(AdLayoutKind::Perception),
            default_layout: Some(AdLayoutKind::Perception),
            source_open_mode: SourceOpenMode::Local,
            scenario_id: String::new(),
            trip_id: String::new(),
            car_id: String::new(),
            local_bag: String::new(),
            open_scenario_draft: String::new(),
            open_car_id_draft: String::new(),
            open_start_ts_draft: String::new(),
            open_end_ts_draft: String::new(),
            open_local_path_draft: String::new(),
            open_status_msg: String::new(),
            convert_job_id: None,
            convert_last_poll: 0.0,
            convert_opened_output: None,
            applied_default_once: false,
            layout_bound_store_id: None,
            topic_view_open: false,
            topic_input: String::new(),
            topic_bound: None,
            topic_view_focus_input: false,
            mcap_topic_list: Vec::new(),
            mcap_topic_list_path: String::new(),
        }
    }
}

/// Back-compat name kept for clarity in older notes.
#[allow(dead_code)]
pub type AdLayoutPanel = AdShell;

impl AdShell {
    /// Full recording path: delegates to [`Self::show_with_app_ctx`].
    pub fn show(&mut self, ctx: &ViewerContext<'_>, ui: &mut Ui) {
        self.show_with_app_ctx(&ctx.app_ctx, ui);
    }

    /// Product chrome that works on welcome / examples (no `ViewerContext`).
    ///
    /// Layout apply and Source open use `command_sender`. Recording props soft-degrade
    /// when there is no active store.
    pub fn show_with_app_ctx(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        self.ensure_default_applied(ctx);
        self.ensure_layout_for_active_recording(ctx);
        #[cfg(target_arch = "wasm32")]
        {
            // Keep convert/open feedback alive even when Source drawer is closed.
            self.poll_convert_job(ctx);
            self.take_host_open_status();
            self.take_mcap_topic_list();
        }
        self.show_rail(ui);
        self.show_source_secondary(ctx, ui);
        self.show_layout_secondary(ctx, ui);
        self.show_panel_secondary(ctx, ui);
        self.show_topic_view_window(ctx, ui);
        self.show_sim_modal(ui);
    }

    fn ensure_default_applied(&mut self, ctx: &AppContext<'_>) {
        if self.applied_default_once {
            return;
        }
        // Layout is independent of data: seed a workspace recording if needed and show panels.
        self.applied_default_once = true;
        if let Some(kind) = self.default_layout {
            self.apply_layout(ctx, kind);
        }
    }

    /// When a new recording becomes active (e.g. host-streamed .rrd), retarget the AD layout
    /// onto that recording's ApplicationId so views bind to the real data.
    fn ensure_layout_for_active_recording(&mut self, ctx: &AppContext<'_>) {
        let Some(rec) = ctx.active_recording() else {
            return;
        };
        let store_id = rec.store_id().clone();
        if self.layout_bound_store_id.as_ref() == Some(&store_id) {
            // Re-open of the same recording: still clear transient "streaming/loading" status.
            if self.open_status_msg.contains("streaming")
                || self.open_status_msg.contains("loading")
                || self.open_status_msg.contains("Loading")
            {
                if let Some(out) = self.convert_opened_output.clone() {
                    self.open_status_msg = format!("Loaded {out}");
                } else {
                    self.open_status_msg = format!("Loaded {}", rec.application_id());
                }
            }
            return;
        }
        let Some(kind) = self.active_layout.or(self.default_layout) else {
            return;
        };
        re_log::info!(
            "Active recording changed → re-apply layout {} on {}",
            kind.label(),
            store_id
        );
        self.layout_bound_store_id = Some(store_id);
        if let Some(out) = self.convert_opened_output.clone() {
            self.open_status_msg = format!("Loaded {out}");
        } else if self.open_status_msg.contains("loading")
            || self.open_status_msg.contains("streaming")
            || self.open_status_msg.contains("Loading")
        {
            self.open_status_msg = format!("Loaded {}", rec.application_id());
        }
        self.apply_layout(ctx, kind);

        // Default playback clock: Cyber publish time (MCAP message_publish_time).
        // Operators can switch to message_log_time via the media-bar dropdown.
        let publish = TimelineName::from("message_publish_time");
        if rec.timelines().contains_key(&publish) {
            ctx.send_time_commands_to_active_recording([
                TimeControlCommand::Pause,
                TimeControlCommand::SetActiveTimeline(publish),
            ]);
            re_log::info!("AD playback timeline → message_publish_time (publish time)");
        }
    }

    fn show_rail(&mut self, ui: &mut Ui) {
        egui::Panel::left("ad_nav_rail")
            .exact_size(76.0)
            .resizable(false)
            .frame(egui::Frame {
                fill: theme::RAIL_BG,
                inner_margin: egui::Margin::symmetric(10, 12),
                stroke: Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.15)),
                ..Default::default()
            })
            .show_inside(ui, |ui| {
                // Brand mark only (no product name text).
                let (logo_rect, _) = ui.allocate_exact_size(Vec2::splat(52.0), Sense::hover());
                paint_brand_logo(ui.painter(), logo_rect);
                ui.add_space(14.0);

                for id in AdNavId::all() {
                    self.nav_square(ui, id);
                    ui.add_space(10.0);
                }
            });
    }

    fn nav_square(&mut self, ui: &mut Ui, id: AdNavId) {
        let active = self.active_nav == id
            || (id == AdNavId::Source && self.source_open)
            || (id == AdNavId::Layout && self.layout_open)
            || (id == AdNavId::Panel && self.panel_open);
        let size = Vec2::splat(56.0);
        let (rect, response) = ui.allocate_exact_size(size, Sense::click());
        let hovered = response.hovered();

        let fill = if active {
            theme::ACCENT_STRONG
        } else if hovered {
            theme::CARD_BG_HOVER
        } else {
            theme::CARD_BG
        };
        ui.painter()
            .rect_filled(rect, CornerRadius::same(10), fill);

        let icon_color = if active {
            Color32::WHITE
        } else {
            theme::TEXT_DIM
        };
        paint_nav_icon(ui.painter(), id, rect.center() - Vec2::new(0.0, 8.0), icon_color);

        ui.painter().text(
            rect.center() + Vec2::new(0.0, 16.0),
            egui::Align2::CENTER_CENTER,
            id.label(),
            egui::FontId::proportional(11.0),
            if active {
                Color32::WHITE
            } else {
                theme::TEXT
            },
        );

        if response.clicked() {
            match id {
                AdNavId::Source => {
                    self.active_nav = AdNavId::Source;
                    self.source_open = !self.source_open;
                    self.layout_open = false;
                    self.panel_open = false;
                    self.sim_modal_open = false;
                }
                AdNavId::Layout => {
                    self.active_nav = AdNavId::Layout;
                    self.layout_open = !self.layout_open;
                    self.source_open = false;
                    self.panel_open = false;
                    self.sim_modal_open = false;
                }
                AdNavId::Panel => {
                    self.active_nav = AdNavId::Panel;
                    self.panel_open = !self.panel_open;
                    self.source_open = false;
                    self.layout_open = false;
                    self.sim_modal_open = false;
                }
                AdNavId::Sim => {
                    self.active_nav = AdNavId::Sim;
                    self.source_open = false;
                    self.layout_open = false;
                    self.panel_open = false;
                    self.sim_modal_open = true;
                }
            }
        }
    }

    fn show_layout_secondary(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        let mut layout_open_flag = self.layout_open;
        egui::Panel::left("ad_layout_secondary")
            .resizable(true)
            .drag_to_open(false)
            .default_size(220.0)
            .min_size(180.0)
            .frame(egui::Frame {
                fill: theme::PANEL_BG,
                inner_margin: egui::Margin::same(12),
                stroke: Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.2)),
                ..Default::default()
            })
            .show_collapsible(ui, &mut layout_open_flag, |ui| {
                ui.label(
                    RichText::new("Layouts")
                        .strong()
                        .size(15.0)
                        .color(theme::TEXT),
                );
                ui.add_space(8.0);

                for kind in AdLayoutKind::all() {
                    self.layout_row(ctx, ui, kind);
                }
            });
        self.layout_open = layout_open_flag;
    }
    fn layout_row(&mut self, ctx: &AppContext<'_>, ui: &mut Ui, kind: AdLayoutKind) {
        let selected = self.active_layout == Some(kind);
        let pinned = self.default_layout == Some(kind);
        let height = 36.0;
        let full = ui.available_width();
        let (rect, response) = ui.allocate_exact_size(Vec2::new(full, height), Sense::click());

        let fill = if selected {
            theme::ACCENT_STRONG.gamma_multiply(0.45)
        } else if response.hovered() {
            theme::CARD_BG_HOVER
        } else {
            Color32::TRANSPARENT
        };
        ui.painter()
            .rect_filled(rect, CornerRadius::same(6), fill);
        if selected {
            ui.painter().rect_filled(
                Rect::from_min_size(rect.left_top(), Vec2::new(3.0, rect.height())),
                CornerRadius::ZERO,
                theme::ACCENT,
            );
        }

        // Text only — no leading icons.
        ui.painter().text(
            rect.left_center() + Vec2::new(12.0, 0.0),
            egui::Align2::LEFT_CENTER,
            kind.label(),
            egui::FontId::proportional(13.0),
            theme::TEXT,
        );

        // Pin control on the right.
        let pin_rect = Rect::from_center_size(
            Pos2::new(rect.right() - 16.0, rect.center().y),
            Vec2::splat(22.0),
        );
        let pin_id = ui.id().with("pin").with(kind.label());
        let pin_resp = ui.interact(pin_rect, pin_id, Sense::click());
        let pin_bg = if pinned {
            theme::ACCENT_STRONG
        } else if pin_resp.hovered() {
            theme::CARD_BG
        } else {
            Color32::TRANSPARENT
        };
        ui.painter()
            .circle_filled(pin_rect.center(), 10.0, pin_bg);
        paint_nail(
            ui.painter(),
            pin_rect.center(),
            if pinned {
                theme::PIN_ACTIVE
            } else {
                theme::TEXT_DIM
            },
        );
        if pin_resp
            .on_hover_text("Pin as default layout")
            .clicked()
        {
            self.default_layout = Some(kind);
            re_log::info!("Pinned default layout: {}", kind.label());
        }

        if response.clicked() {
            self.apply_layout(ctx, kind);
        }

        ui.add_space(4.0);
    }

    fn show_panel_secondary(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        let mut panel_open_flag = self.panel_open;
        egui::Panel::left("ad_panel_secondary")
            .resizable(true)
            .drag_to_open(false)
            .default_size(220.0)
            .min_size(180.0)
            .frame(egui::Frame {
                fill: theme::PANEL_BG,
                inner_margin: egui::Margin::same(12),
                stroke: Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.2)),
                ..Default::default()
            })
            .show_collapsible(ui, &mut panel_open_flag, |ui| {
                ui.label(
                    RichText::new("Edit current Layout")
                        .strong()
                        .size(15.0)
                        .color(theme::TEXT),
                );
                ui.label(
                    RichText::new("Add or modify visualizations in this layout")
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                );
                ui.add_space(10.0);

                for label in ["Add grid", "Add lines", "Add points", "Add Topic view"] {
                    let full = ui.available_width();
                    let (rect, response) =
                        ui.allocate_exact_size(Vec2::new(full, 34.0), Sense::click());
                    let fill = if response.hovered() {
                        theme::CARD_BG_HOVER
                    } else {
                        Color32::TRANSPARENT
                    };
                    ui.painter()
                        .rect_filled(rect, CornerRadius::same(6), fill);
                    ui.painter().text(
                        rect.left_center() + Vec2::new(12.0, 0.0),
                        egui::Align2::LEFT_CENTER,
                        label,
                        egui::FontId::proportional(13.0),
                        theme::TEXT,
                    );
                    if response.clicked() {
                        if label == "Add Topic view" {
                            self.topic_view_open = true;
                            self.topic_view_focus_input = true;
                            #[cfg(target_arch = "wasm32")]
                            self.ensure_mcap_topic_list(ctx);

                            re_log::info!("Opening Topic View panel");
                        } else {
                            re_log::info!("Panel tool (stub): {label}");
                        }
                    }
                    ui.add_space(2.0);
                }
            });
        self.panel_open = panel_open_flag;
    }

    /// Floating Topic View: type/pick a bag entity path (Enter to bind),
    /// then show LatestAt data for the active timeline playhead.
    /// Prefix autocomplete lists matching topics (empty prefix = all, lexicographic).
    fn show_topic_view_window(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        if !self.topic_view_open {
            return;
        }
        let mut open = self.topic_view_open;
        egui::Window::new("Topic View")
            .open(&mut open)
            .collapsible(true)
            .resizable(true)
            .default_size([420.0, 520.0])
            // Keep clear of the Source drawer (~356px) so Open path stays clickable.
            .default_pos([420.0, 72.0])
            .frame(egui::Frame {
                fill: theme::PANEL_BG,
                inner_margin: egui::Margin::same(12),
                corner_radius: CornerRadius::same(10),
                stroke: Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.4)),
                ..Default::default()
            })
            .show(ui.ctx(), |ui| {
                ui.label(
                    RichText::new("Type a topic and press Enter · click a suggestion to select")
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                );
                ui.add_space(8.0);

                let Some(_db) = ctx.active_recording() else {
                    ui.label(
                        RichText::new("Open a bag / recording first.")
                            .size(13.0)
                            .color(theme::ACCENT),
                    );
                    return;
                };

                // Candidates from MCAP summary channel list (cached at open). Cheap prefix filter.
                let prefix = self.topic_input.trim().to_ascii_lowercase();
                let pref = prefix.trim_start_matches('/');
                let suggestions: Vec<String> = self
                    .mcap_topic_list
                    .iter()
                    .filter(|t| {
                        if pref.is_empty() {
                            return true;
                        }
                        t.to_ascii_lowercase()
                            .trim_start_matches('/')
                            .starts_with(pref)
                    })
                    .cloned()
                    .collect();

                ui.label(
                    RichText::new("Topic / entity path")
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                );
                ui.add_space(2.0);

                let edit_id = ui.id().with("ad_topic_input");
                let edit_response = ui.scope(|ui| {
                    ui.visuals_mut().extreme_bg_color = theme::CARD_BG;
                    ui.visuals_mut().override_text_color = Some(theme::TEXT);
                    ui.add(
                        egui::TextEdit::singleline(&mut self.topic_input)
                            .id(edit_id)
                            .desired_width(ui.available_width())
                            .hint_text(
                                RichText::new("e.g. camera/Front120 — Enter to load")
                                    .color(theme::TEXT_DIM),
                            )
                            .text_color(theme::TEXT),
                    )
                })
                .inner;

                if self.topic_view_focus_input {
                    edit_response.request_focus();
                    self.topic_view_focus_input = false;
                }

                // egui TextEdit loses focus on Enter before has_focus() stays true.
                let enter_pressed = ui.input(|i| i.key_pressed(egui::Key::Enter))
                    && (edit_response.has_focus() || edit_response.lost_focus());
                if enter_pressed {
                    self.bind_topic_from_input(&suggestions);
                }

                // Always show candidates — gating on TextEdit focus drops clicks:
                // the click unfocuses the field first, so the list vanishes mid-click.
                ui.add_space(4.0);
                egui::Frame::NONE
                    .fill(theme::CARD_BG)
                    .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.35)))
                    .corner_radius(CornerRadius::same(6))
                    .inner_margin(egui::Margin::symmetric(4, 4))
                    .show(ui, |ui| {
                        egui::ScrollArea::vertical()
                            .id_salt("ad_topic_suggest")
                            .max_height(180.0)
                            .show(ui, |ui| {
                                if suggestions.is_empty() {
                                    let msg = if self.mcap_topic_list.is_empty() {
                                        "No MCAP topic cache yet — re-open the .mcap"
                                    } else {
                                        "No matching topics"
                                    };
                                    ui.label(
                                        RichText::new(msg)
                                            .size(12.0)
                                            .color(theme::TEXT_DIM),
                                    );
                                    return;
                                }
                                const MAX_SHOWN: usize = 120;
                                let total = suggestions.len();
                                for topic in suggestions.iter().take(MAX_SHOWN) {
                                    let label = topic.clone();
                                    let selected = self
                                        .topic_bound
                                        .as_ref()
                                        .map(|p| topic_path_matches(p, &label))
                                        .unwrap_or(false);
                                    let resp = ui.add_sized(
                                        [ui.available_width(), 26.0],
                                        egui::Button::new(
                                            RichText::new(&label).color(theme::TEXT),
                                        )
                                        .fill(if selected {
                                            theme::ACCENT_STRONG.gamma_multiply(0.35)
                                        } else {
                                            Color32::TRANSPARENT
                                        })
                                        .frame(true),
                                    );
                                    if resp.clicked() {
                                        self.bind_topic(&label);
                                    }
                                }
                                if total > MAX_SHOWN {
                                    ui.label(
                                        RichText::new(format!(
                                            "…and {} more (type to narrow)",
                                            total - MAX_SHOWN
                                        ))
                                        .size(11.0)
                                        .color(theme::TEXT_DIM),
                                    );
                                }
                            });
                    });

                ui.add_space(10.0);
                ui.separator();
                ui.add_space(6.0);

                if let Some(path) = self.topic_bound.clone() {
                    let timeline_label = ctx
                        .active_time_ctrl()
                        .map(|tc| {
                            let name = tc.timeline_name().as_str().to_owned();
                            let t = tc
                                .time_int()
                                .map(|t| t.as_i64().to_string())
                                .unwrap_or_else(|| "—".into());
                            format!("{name} @ {t}")
                        })
                        .unwrap_or_else(|| "no timeline".into());
                    ui.label(
                        RichText::new(format!("Data · {path} · {timeline_label}"))
                            .strong()
                            .size(12.0)
                            .color(theme::TEXT),
                    );
                    ui.add_space(4.0);

                    let known = ctx
                        .active_recording()
                        .is_some_and(|db| db.is_known_entity(&path));
                    if !known {
                        ui.label(
                            RichText::new(
                                "Entity not in the recording yet (still streaming, or path mismatch).",
                            )
                            .size(12.0)
                            .color(theme::ACCENT),
                        );
                    }

                    if let Some(store_ctx) = StoreViewContext::for_active_recording(ctx) {
                        egui::ScrollArea::vertical()
                            .id_salt("ad_topic_view_data")
                            .show(ui, |ui| {
                                InstancePath::entity_all(path).data_ui(
                                    &store_ctx,
                                    ui,
                                    UiLayout::SelectionPanel,
                                );
                            });
                    } else {
                        ui.label(
                            RichText::new("No active store context")
                                .size(12.0)
                                .color(theme::TEXT_DIM),
                        );
                    }
                } else {
                    ui.label(
                        RichText::new("Click a topic or press Enter to load data at the playhead.")
                            .size(12.0)
                            .color(theme::TEXT_DIM),
                    );
                }
            });
        self.topic_view_open = open;
    }
    fn bind_topic_from_input(&mut self, suggestions: &[String]) {
        let typed = self.topic_input.trim();
        if typed.is_empty() {
            return;
        }
        let topic = suggestions
            .iter()
            .find(|t| {
                *t == typed || t.trim_start_matches('/') == typed.trim_start_matches('/')
            })
            .cloned()
            .unwrap_or_else(|| typed.to_owned());
        self.bind_topic(&topic);
    }

    fn bind_topic(&mut self, topic: &str) {
        let path = EntityPath::parse_forgiving(topic);
        self.topic_input = topic.to_owned();
        self.topic_bound = Some(path.clone());
        re_log::info!("Topic View bound to {path}");
    }

    fn show_source_secondary(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        let mut source_open_flag = self.source_open;
        egui::Panel::left("ad_source_secondary")
            .resizable(true)
            .drag_to_open(false)
            .default_size(280.0)
            .min_size(220.0)
            .frame(egui::Frame {
                fill: theme::PANEL_BG,
                inner_margin: egui::Margin::same(12),
                stroke: Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.2)),
                ..Default::default()
            })
            .show_collapsible(ui, &mut source_open_flag, |ui| {
                ui.label(
                    RichText::new("Source")
                        .strong()
                        .size(15.0)
                        .color(theme::TEXT),
                );
                ui.add_space(8.0);

                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        // Open controls first (as requested).
                        self.source_open_section(ctx, ui);
                        ui.add_space(14.0);
                        ui.separator();
                        ui.add_space(10.0);
                        self.source_props_section(ctx, ui);
                    });
            });
        self.source_open = source_open_flag;
    }
    fn source_props_section(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        let tz = ctx.app_options.timestamp_format;
        let db = ctx.active_recording();

        ui.label(
            RichText::new("Properties")
                .strong()
                .size(13.0)
                .color(theme::TEXT),
        );
        ui.label(
            RichText::new("Current recording")
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
        ui.add_space(6.0);

        let has_data = db.map(|d| d.store_info().is_some()).unwrap_or(false);
        if !has_data && self.local_bag.is_empty() && self.scenario_id.is_empty() && self.trip_id.is_empty()
        {
            ui.label(
                RichText::new("No recording loaded yet.")
                    .size(12.0)
                    .color(theme::TEXT_DIM),
            );
            ui.add_space(4.0);
        }

        // Prefer path from the live channel when a local file is loaded.
        if let Some(db) = db {
            if let Some(LogSource::File { path }) = db.data_source.as_ref() {
                let p = path.display().to_string();
                if self.local_bag != p {
                    self.local_bag = p;
                }
            }
        }

        let app_id = db
            .map(|d| d.application_id().as_str().to_owned())
            .unwrap_or_else(|| "—".into());
        let store_id = db
            .and_then(|d| d.store_info().map(|i| i.store_id.to_string()))
            .unwrap_or_else(|| "—".into());

        let (start_s, end_s) = recording_time_bounds(ctx);
        let source_s = db
            .and_then(|d| d.data_source.as_ref().map(describe_channel_source))
            .unwrap_or_else(|| {
                if !self.local_bag.is_empty() {
                    self.local_bag.clone()
                } else {
                    "—".into()
                }
            });

        // Prefer explicit Source-panel fields; fall back to parsing app/store id.
        let car = first_nonempty(&[
            self.car_id.as_str(),
            &extract_tagged(&app_id, "car"),
            &extract_tagged(&store_id, "car"),
        ]);
        let scenario = first_nonempty(&[
            self.scenario_id.as_str(),
            &extract_tagged(&app_id, "scenario"),
            &extract_tagged(&store_id, "scenario"),
        ]);
        let trip = first_nonempty(&[
            self.trip_id.as_str(),
            &extract_tagged(&app_id, "trip"),
            &extract_tagged(&store_id, "trip"),
        ]);
        let bag = if !self.local_bag.is_empty()
            && self.local_bag != "(choose a local .rrd / bag…)"
        {
            self.local_bag.clone()
        } else if db.is_some_and(|d| matches!(d.data_source.as_ref(), Some(LogSource::File { .. }))) {
            source_s.clone()
        } else {
            "—".into()
        };

        let started = db
            .and_then(|d| d.store_info().map(|i| format!("{:?}", i.store_source)))
            .unwrap_or_else(|| "—".into());
        let _ = &tz;

        prop_row(ui, "Application", &app_id);
        prop_row(ui, "Recording", &store_id);
        prop_row(ui, "Started", &started);
        prop_row(ui, "Start time", &start_s);
        prop_row(ui, "End time", &end_s);
        prop_row(ui, "Car ID", &car);
        prop_row(ui, "Scenario ID", &scenario);
        prop_row(ui, "Trip ID", &trip);
        prop_row(ui, "Local bag", &bag);
        prop_row(ui, "Connection", &source_s);
    }

    fn source_open_section(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        ui.label(
            RichText::new("Open data")
                .strong()
                .size(13.0)
                .color(theme::TEXT),
        );
        ui.label(
            RichText::new("Choose how to load a recording")
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
        ui.add_space(8.0);

        // Mode selector — compact stacked options.
        for mode in SourceOpenMode::all() {
            let selected = self.source_open_mode == mode;
            let full = ui.available_width();
            let (rect, response) = ui.allocate_exact_size(Vec2::new(full, 30.0), Sense::click());
            let fill = if selected {
                theme::ACCENT_STRONG.gamma_multiply(0.45)
            } else if response.hovered() {
                theme::CARD_BG_HOVER
            } else {
                theme::CARD_BG
            };
            ui.painter()
                .rect_filled(rect, CornerRadius::same(6), fill);
            if selected {
                ui.painter().rect_filled(
                    Rect::from_min_size(rect.left_top(), Vec2::new(3.0, rect.height())),
                    CornerRadius::ZERO,
                    theme::ACCENT,
                );
            }
            ui.painter().text(
                rect.left_center() + Vec2::new(12.0, 0.0),
                egui::Align2::LEFT_CENTER,
                mode.label(),
                egui::FontId::proportional(12.5),
                theme::TEXT,
            );
            if response.clicked() {
                self.source_open_mode = mode;
            }
            ui.add_space(4.0);
        }

        ui.add_space(10.0);

        match self.source_open_mode {
            SourceOpenMode::Local => {
                ui.label(
                    RichText::new("Open path / Browse: .rrd/.mcap stream on host. Apollo .record auto-converts to MCAP (cached) with progress, then loads.")
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                );
                ui.add_space(8.0);

                field_label(ui, "Server path (.record / .rrd / .mcap)");
                let path_edit = themed_text_edit(
                    ui,
                    &mut self.open_local_path_draft,
                    "/apollo_workspace/data/bag/slice_test.mcap",
                );
                ui.add_space(6.0);
                let can_path = !self.open_local_path_draft.trim().is_empty();
                let open_clicked = ui
                    .add_enabled(
                        can_path,
                        primary_button("Open path")
                            .min_size(Vec2::new(ui.available_width(), 32.0)),
                    )
                    .clicked();
                let open_enter = can_path
                    && path_edit.has_focus()
                    && ui.input(|i| i.key_pressed(egui::Key::Enter));
                if open_clicked || open_enter {
                    let path = self.open_local_path_draft.trim().to_owned();
                    self.try_open_local_path(ctx, &path);
                }

                ui.add_space(8.0);
                if action_button(ui, "Browse in browser…").clicked() {
                    #[cfg(target_arch = "wasm32")]
                    {
                        crate::web_tools::pick_local_recording_files(
                            ctx.command_sender().clone(),
                            ctx.egui_ctx.clone(),
                        );
                        self.open_status_msg =
                            "Browse → host stream (resolves file name under bag dirs; large files OK)."
                                .into();
                    }
                    #[cfg(not(target_arch = "wasm32"))]
                    {
                        ctx.command_sender().send_ui(UICommand::Open);
                        self.open_status_msg = "Opening native file dialog…".into();
                    }
                    self.scenario_id.clear();
                    self.trip_id.clear();
                    self.car_id.clear();
                }

                if !self.open_status_msg.is_empty() {
                    ui.add_space(8.0);
                    ui.label(
                        RichText::new(&self.open_status_msg)
                            .size(11.0)
                            .color(theme::ACCENT),
                    );
                }
            }
            SourceOpenMode::Scenario => {
                field_label(ui, "Scenario ID");
                themed_text_edit(ui, &mut self.open_scenario_draft, "scenario-xxxx");
                ui.add_space(8.0);
                let can = !self.open_scenario_draft.trim().is_empty();
                if ui
                    .add_enabled(
                        can,
                        primary_button("Open scenario")
                            .min_size(Vec2::new(ui.available_width(), 32.0)),
                    )
                    .clicked()
                {
                    let id = self.open_scenario_draft.trim().to_owned();
                    self.scenario_id = id.clone();
                    self.trip_id.clear();
                    self.car_id.clear();
                    self.local_bag.clear();
                    self.open_remote_id(ctx, "scenario", &id);
                }
            }
            SourceOpenMode::TripSegment => {
                field_label(ui, "Car ID");
                themed_text_edit(ui, &mut self.open_car_id_draft, "car-xxxx");
                ui.add_space(6.0);
                field_label(ui, "Start timestamp");
                themed_text_edit(ui, &mut self.open_start_ts_draft, "2024-01-01T00:00:00Z");
                ui.add_space(6.0);
                field_label(ui, "End timestamp");
                themed_text_edit(ui, &mut self.open_end_ts_draft, "2024-01-01T00:10:00Z");
                ui.add_space(8.0);
                let can = !self.open_car_id_draft.trim().is_empty()
                    && !self.open_start_ts_draft.trim().is_empty()
                    && !self.open_end_ts_draft.trim().is_empty();
                if ui
                    .add_enabled(
                        can,
                        primary_button("Open trip segment")
                            .min_size(Vec2::new(ui.available_width(), 32.0)),
                    )
                    .clicked()
                {
                    let car = self.open_car_id_draft.trim().to_owned();
                    let start = self.open_start_ts_draft.trim().to_owned();
                    let end = self.open_end_ts_draft.trim().to_owned();
                    self.car_id = car.clone();
                    self.trip_id = format!("{car}@{start}→{end}");
                    self.scenario_id.clear();
                    self.local_bag.clear();
                    self.open_trip_segment(ctx, &car, &start, &end);
                }
            }
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn take_host_open_status(&mut self) {
        if let Some(msg) = crate::web_tools::take_open_local_status() {
            self.open_status_msg = msg;
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn take_mcap_topic_list(&mut self) {
        let Some(body) = crate::web_tools::take_mcap_topics_json() else {
            return;
        };
        if let Some(path) = json_field(&body, "path") {
            self.mcap_topic_list_path = path;
        }
        if let Some(topics) = json_string_array(&body, "topics") {
            re_log::info!(
                "Topic View: cached {} MCAP summary topics from {}",
                topics.len(),
                self.mcap_topic_list_path
            );
            self.mcap_topic_list = topics;
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn ensure_mcap_topic_list(&mut self, ctx: &AppContext<'_>) {
        if !self.mcap_topic_list.is_empty() {
            return;
        }
        let path = if !self.local_bag.is_empty() {
            self.local_bag.clone()
        } else if !self.mcap_topic_list_path.is_empty() {
            self.mcap_topic_list_path.clone()
        } else if !self.open_local_path_draft.trim().is_empty() {
            self.open_local_path_draft.trim().to_owned()
        } else {
            String::new()
        };
        if path.to_ascii_lowercase().ends_with(".mcap") {
            crate::web_tools::request_host_mcap_topics(&path, ctx.egui_ctx.clone());
        } else {
            // Host already cached topics on last open_local — pull that.
            crate::web_tools::request_host_mcap_topics_cached(ctx.egui_ctx.clone());
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn poll_convert_job(&mut self, ctx: &AppContext<'_>) {
        if let Some(body) = crate::web_tools::take_convert_status_json() {
            self.apply_convert_status_json(ctx, &body);
        }

        let Some(job_id) = self.convert_job_id.clone() else {
            return;
        };
        let now = ctx.egui_ctx.input(|i| i.time);
        if now - self.convert_last_poll < 0.5 {
            ctx.egui_ctx
                .request_repaint_after(std::time::Duration::from_millis(200));
            return;
        }
        self.convert_last_poll = now;
        crate::web_tools::poll_host_convert_status(&job_id, ctx.egui_ctx.clone());
        ctx.egui_ctx
            .request_repaint_after(std::time::Duration::from_millis(400));
    }

    #[cfg(target_arch = "wasm32")]
    fn apply_convert_status_json(&mut self, ctx: &AppContext<'_>, body: &str) {
        let status = json_field(body, "status").unwrap_or_default();
        let progress = json_f64(body, "progress").unwrap_or(0.0);
        let message = json_field(body, "message").unwrap_or_default();
        if let Some(job_id) = json_field(body, "job_id") {
            self.convert_job_id = Some(job_id);
        }
        let pct = (progress * 100.0).clamp(0.0, 100.0);
        self.open_status_msg = format!("Record→MCAP {pct:.0}% — {message}");

        if status == "done" || status == "ready" {
            if let Some(out) = json_field(body, "output_path") {
                if self.convert_opened_output.as_deref() != Some(out.as_str()) {
                    self.convert_opened_output = Some(out.clone());
                    self.open_status_msg = format!("Conversion done — loading {out}…");
                    self.convert_job_id = None;
                    crate::web_tools::request_host_open_local(&out, ctx.egui_ctx.clone());
                    self.mcap_topic_list.clear();
                    crate::web_tools::request_host_mcap_topics(&out, ctx.egui_ctx.clone());
                }
            }
        } else if status == "error" {
            self.open_status_msg = format!("Conversion failed: {message}");
            self.convert_job_id = None;
        }
    }

    fn try_open_local_path(&mut self, ctx: &AppContext<'_>, path: &str) {
        let lower = path.to_ascii_lowercase();
        let is_record = lower.contains(".record")
            && !lower.ends_with(".rrd")
            && !lower.ends_with(".rbl");
        if is_record {
            self.local_bag = path.to_owned();
            self.scenario_id.clear();
            self.trip_id.clear();
            self.convert_opened_output = None;
            #[cfg(target_arch = "wasm32")]
            {
                self.open_status_msg = format!("Converting Apollo record → MCAP…\n{path}");
                self.convert_job_id = None;
                crate::web_tools::request_host_convert_record(path, ctx.egui_ctx.clone());
            }
            #[cfg(not(target_arch = "wasm32"))]
            {
                self.open_status_msg = format!(
                    "Native: convert with apollo_record_to_semantic_mcap.py then open .mcap:\n{path}"
                );
            }
            return;
        }
        if !(lower.ends_with(".rrd") || lower.ends_with(".rbl") || lower.ends_with(".mcap")) {
            self.open_status_msg = "Expected a .record / .rrd / .rbl / .mcap path.".into();
            re_log::warn!("{}", self.open_status_msg);
            return;
        }

        self.local_bag = path.to_owned();
        self.scenario_id.clear();
        self.trip_id.clear();

        #[cfg(not(target_arch = "wasm32"))]
        {
            use std::path::PathBuf;
            let p = PathBuf::from(path);
            if !p.exists() {
                self.open_status_msg = format!("Path not found: {path}");
                re_log::error!("{}", self.open_status_msg);
                return;
            }
            re_log::info!("Opening local path {path}");
            self.open_status_msg = format!("Loading {path}…");
            ctx.command_sender().send_system(SystemCommand::LoadDataSource(
                LogDataSource::File {
                    file_source: FileSource::FileDialog {
                        recommended_store_id: None,
                        force_store_info: true,
                    },
                    path: p,
                },
            ));
        }
        #[cfg(target_arch = "wasm32")]
        {
            // Host streams via gRPC proxy — never load bag bytes into WASM.
            self.open_status_msg = format!("Host streaming {path}…");
            crate::web_tools::request_host_open_local(path, ctx.egui_ctx.clone());
            if path.to_ascii_lowercase().ends_with(".mcap") {
                self.mcap_topic_list.clear();
                crate::web_tools::request_host_mcap_topics(path, ctx.egui_ctx.clone());
            }
        }
    }

    fn open_trip_segment(
        &mut self,
        ctx: &AppContext<'_>,
        car_id: &str,
        start: &str,
        end: &str,
    ) {
        let env_key = "WEB_MONITOR_TRIP_SEGMENT_URL";
        if let Ok(tmpl) = std::env::var(env_key) {
            let url = tmpl
                .replace("{car_id}", car_id)
                .replace("{start}", start)
                .replace("{end}", end)
                .replace("{id}", car_id);
            re_log::info!("Opening trip segment via {url}");
            self.load_remote_url(ctx, url);
        } else if let Ok(tmpl) = std::env::var("WEB_MONITOR_TRIP_URL") {
            let url = tmpl
                .replace("{car_id}", car_id)
                .replace("{start}", start)
                .replace("{end}", end)
                .replace("{id}", car_id);
            re_log::info!("Opening trip segment via WEB_MONITOR_TRIP_URL → {url}");
            self.load_remote_url(ctx, url);
        } else {
            re_log::info!(
                "Trip segment stored: car={car_id} start={start} end={end} \
                 (set {env_key} with {{car_id}}/{{start}}/{{end}} to auto-fetch RRD)"
            );
        }
    }

    fn load_remote_url(&self, ctx: &AppContext<'_>, url: String) {
        let file_source = FileSource::Uri;
        if let Some(ds) = LogDataSource::from_uri(
            file_source,
            &url,
            &re_data_source::FromUriOptions {
                accept_extensionless_http: true,
            },
        ) {
            ctx.command_sender()
                .send_system(SystemCommand::LoadDataSource(ds));
        } else if let Ok(parsed) = url::Url::parse(&url) {
            ctx.command_sender()
                .send_system(SystemCommand::LoadDataSource(LogDataSource::HttpUrl {
                    url: parsed,
                }));
        } else {
            re_log::warn!("Could not classify URL for loading: {url}");
        }
    }

    fn open_remote_id(&mut self, ctx: &AppContext<'_>, kind: &str, id: &str) {
        let env_key = match kind {
            "trip" => "WEB_MONITOR_TRIP_URL",
            "scenario" => "WEB_MONITOR_SCENARIO_URL",
            _ => "WEB_MONITOR_REMOTE_URL",
        };
        if let Ok(tmpl) = std::env::var(env_key) {
            let url = tmpl.replace("{id}", id);
            re_log::info!("Opening remote {kind} via {url}");
            self.load_remote_url(ctx, url);
        } else if id.starts_with("http://")
            || id.starts_with("https://")
            || id.starts_with("ws://")
            || id.starts_with("wss://")
        {
            re_log::info!("Opening remote {kind} URL: {id}");
            self.load_remote_url(ctx, id.to_owned());
        } else {
            re_log::info!(
                "Remote {kind} id stored: {id} (set {env_key} with {{id}} to auto-fetch RRD)"
            );
        }
    }

    fn show_sim_modal(&mut self, ui: &mut Ui) {
        if !self.sim_modal_open {
            return;
        }
        let mut open = self.sim_modal_open;
        egui::Window::new("Sim")
            .open(&mut open)
            .collapsible(false)
            .resizable(false)
            .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
            .frame(egui::Frame {
                fill: theme::PANEL_BG,
                inner_margin: egui::Margin::same(20),
                corner_radius: CornerRadius::same(10),
                stroke: Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.4)),
                ..Default::default()
            })
            .show(ui.ctx(), |ui| {
                ui.set_min_width(280.0);
                ui.label(
                    RichText::new("Local simulation")
                        .strong()
                        .size(16.0)
                        .color(theme::TEXT),
                );
                ui.add_space(8.0);
                ui.label(
                    RichText::new("Under development")
                        .size(14.0)
                        .color(theme::ACCENT),
                );
                ui.add_space(12.0);
                if ui.button("OK").clicked() {
                    self.sim_modal_open = false;
                }
            });
        self.sim_modal_open = open;
    }

    fn apply_layout(&mut self, ctx: &AppContext<'_>, kind: AdLayoutKind) {
        self.active_layout = Some(kind);
        let Some(name) = kind.file_name() else {
            re_log::info!("Custom layout selected (no blueprint file yet)");
            return;
        };
        let Some(bytes) = kind.bytes() else {
            return;
        };
        re_log::info!(
            "Applying AD layout {} ({} bytes, in-memory import)",
            kind.label(),
            bytes.len()
        );

        // IMPORTANT (0.37 web): do NOT use LoadDataSource(File) for embedded layouts.
        // Decode embedded bytes and push them via AddReceiver instead.
        //
        // Layout ≠ data: Rerun only *activates* a blueprint when a recording exists for
        // that ApplicationId. If none is open, seed a stable empty workspace recording,
        // then remap the .rbl onto it via opened_store_id.
        let ad_app = ad_application_id();
        let target_store_id = if let Some(rec) = ctx.active_recording() {
            rec.store_id().clone()
        } else if let Some(store_id) = ctx.store_hub().earliest_recording_for_app(&ad_app) {
            store_id
        } else if let Some(db) = ctx.store_bundle().recordings().next() {
            db.store_id().clone()
        } else {
            ad_layout_workspace_store_id()
        };

        let path = std::path::PathBuf::from(name);
        let (tx, rx) = re_log_channel::log_channel(LogSource::File { path: path.clone() });

        if !ctx.store_bundle().contains(&target_store_id) {
            re_log::info!(
                "Seeding layout workspace recording {} (no data required)",
                target_store_id
            );
            let seed = re_importer::prepare_store_info(&target_store_id, FileSource::Sdk);
            let _ = tx.send(seed.into());
        }

        let settings = re_importer::ImporterSettings {
            opened_store_id: Some(target_store_id.clone()),
            ..re_importer::ImporterSettings::recommended(RecordingId::random())
        };
        self.layout_bound_store_id = Some(target_store_id.clone());
        re_log::info!(
            "Layout {} retarget store_id={}",
            kind.label(),
            target_store_id
        );

        if let Err(err) = re_importer::import_from_file_contents(
            &settings,
            FileSource::Sdk,
            &path,
            std::borrow::Cow::Borrowed(bytes),
            &tx,
        ) {
            re_log::error!("Failed to import layout {name}: {err}");
            let _ = tx.quit(Some(Box::new(err)));
            return;
        }
        // import_from_file_contents already quits the sender when done.
        ctx.command_sender()
            .send_system(SystemCommand::AddReceiver(rx));
    }
}

fn prop_row(ui: &mut Ui, label: &str, value: &str) {
    ui.horizontal(|ui| {
        ui.set_min_width(ui.available_width());
        ui.label(
            RichText::new(label)
                .size(11.0)
                .color(theme::TEXT_DIM),
        );
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(
                RichText::new(value)
                    .size(12.0)
                    .color(theme::TEXT)
                    .strong(),
            );
        });
    });
    ui.add_space(4.0);
}

fn field_label(ui: &mut Ui, label: &str) {
    ui.label(RichText::new(label).size(11.0).color(theme::TEXT_DIM));
    ui.add_space(2.0);
}

fn themed_text_edit(ui: &mut Ui, text: &mut String, hint: &str) -> egui::Response {
    ui.scope(|ui| {
        ui.visuals_mut().extreme_bg_color = theme::CARD_BG;
        ui.visuals_mut().override_text_color = Some(theme::TEXT);
        ui.add(
            egui::TextEdit::singleline(text)
                .desired_width(ui.available_width())
                .hint_text(RichText::new(hint).color(theme::TEXT_DIM))
                .text_color(theme::TEXT),
        )
    })
    .inner
}

fn action_button(ui: &mut Ui, label: &str) -> egui::Response {
    ui.add(
        egui::Button::new(RichText::new(label).color(theme::TEXT))
            .fill(theme::CARD_BG)
            .min_size(Vec2::new(ui.available_width(), 34.0)),
    )
}

fn primary_button(label: &str) -> egui::Button<'static> {
    egui::Button::new(RichText::new(label).color(Color32::WHITE)).fill(theme::ACCENT_STRONG)
}

fn first_nonempty(candidates: &[&str]) -> String {
    for c in candidates {
        let t = c.trim();
        if !t.is_empty() && t != "—" {
            return t.to_owned();
        }
    }
    "—".into()
}

/// Pull `key=value` / `key:value` / `/key/value/` fragments from an id string.
fn extract_tagged(haystack: &str, key: &str) -> String {
    let lower = haystack.to_ascii_lowercase();
    let key_l = key.to_ascii_lowercase();
    for sep in ['=', ':'] {
        let needle = format!("{key_l}{sep}");
        if let Some(pos) = lower.find(&needle) {
            let rest = &haystack[pos + needle.len()..];
            let end = rest
                .find(|c: char| c == '/' || c == ',' || c == '&' || c.is_whitespace())
                .unwrap_or(rest.len());
            let v = rest[..end].trim();
            if !v.is_empty() {
                return v.to_owned();
            }
        }
    }
    // path-style: /car/<id>/
    let path_needle = format!("/{key_l}/");
    if let Some(pos) = lower.find(&path_needle) {
        let rest = &haystack[pos + path_needle.len()..];
        let end = rest.find('/').unwrap_or(rest.len());
        let v = rest[..end].trim();
        if !v.is_empty() {
            return v.to_owned();
        }
    }
    String::new()
}

fn describe_channel_source(src: &LogSource) -> String {
    match src {
        LogSource::File { path } => path.display().to_string(),
        LogSource::HttpStream { url, .. } => url.clone(),
        LogSource::MessageProxy(uri) => uri.to_string(),
        LogSource::RedapGrpcStream { uri, .. } => uri.to_string(),
        LogSource::Sdk => "SDK".into(),
        LogSource::Stdin => "stdin".into(),
        LogSource::RrdWebEvent => "web event".into(),
        LogSource::JsChannel { channel_name } => format!("js:{channel_name}"),
    }
}

fn recording_time_bounds(ctx: &AppContext<'_>) -> (String, String) {
    let Some(db) = ctx.active_recording() else {
        return ("—".into(), "—".into());
    };
    let tz = ctx.app_options.timestamp_format;
    if let Some(time_ctrl) = ctx.active_time_ctrl() {
        if let Some(timeline) = time_ctrl.timeline() {
            if let Some(range) = db.time_range_for(timeline.name()) {
                return (
                    format_bound(range.min(), timeline, tz),
                    format_bound(range.max(), timeline, tz),
                );
            }
        }
    }
    // Fall back to any timeline that has data.
    for tl in db.timelines().values() {
        if let Some(range) = db.time_range_for(tl.name()) {
            return (
                format_bound(range.min(), tl, tz),
                format_bound(range.max(), tl, tz),
            );
        }
    }
    ("—".into(), "—".into())
}

fn format_bound(t: TimeInt, timeline: &Timeline, tz: TimestampFormat) -> String {
    timeline.typ().format(t, tz)
}



fn paint_brand_logo(painter: &egui::Painter, rect: Rect) {
    let c = rect.center();
    let r = rect.width() * 0.42;

    // Soft purple tile.
    painter.rect_filled(rect, CornerRadius::same(12), theme::CARD_BG);
    painter.rect_stroke(
        rect.shrink(0.5),
        CornerRadius::same(12),
        Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.45)),
        StrokeKind::Inside);

    // Outer ring — "monitor / radar".
    painter.circle_stroke(c, r * 0.92, Stroke::new(1.6, theme::ACCENT));
    painter.circle_stroke(
        c,
        r * 0.58,
        Stroke::new(1.2, theme::ACCENT.gamma_multiply(0.7)),
    );

    // Crosshair arms.
    let arm = r * 0.78;
    painter.line_segment(
        [c - Vec2::new(arm, 0.0), c + Vec2::new(arm, 0.0)],
        Stroke::new(1.2, theme::ACCENT.gamma_multiply(0.55)),
    );
    painter.line_segment(
        [c - Vec2::new(0.0, arm), c + Vec2::new(0.0, arm)],
        Stroke::new(1.2, theme::ACCENT.gamma_multiply(0.55)),
    );

    // Center diamond (vehicle / focus).
    let d = r * 0.28;
    let diamond = [
        c + Vec2::new(0.0, -d),
        c + Vec2::new(d, 0.0),
        c + Vec2::new(0.0, d),
        c + Vec2::new(-d, 0.0),
    ];
    painter.add(egui::Shape::convex_polygon(
        diamond.to_vec(),
        theme::ACCENT_STRONG,
        Stroke::NONE,
    ));
}

fn paint_nav_icon(painter: &egui::Painter, id: AdNavId, center: Pos2, color: Color32) {
    match id {
        AdNavId::Source => {
            // stacked cylinders
            for dy in [-6.0_f32, 0.0, 6.0] {
                let r = Rect::from_center_size(center + Vec2::new(0.0, dy), Vec2::new(16.0, 5.0));
                painter.rect_stroke(r, CornerRadius::same(2), Stroke::new(1.4, color),
        StrokeKind::Inside);
            }
        }
        AdNavId::Layout => {
            let s = 5.0;
            let g = 3.0;
            for (x, y) in [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)] {
                let c = center + Vec2::new(x * (s + g) * 0.5, y * (s + g) * 0.5);
                painter.rect_stroke(
                    Rect::from_center_size(c, Vec2::splat(s)),
                    CornerRadius::same(1),
                    Stroke::new(1.4, color),
        StrokeKind::Inside);
            }
        }
        AdNavId::Panel => {
            // three bars
            for (i, h) in [(0.0_f32, 10.0), (1.0, 14.0), (2.0, 8.0)] {
                let x = center.x - 8.0 + i * 8.0;
                painter.line_segment(
                    [
                        Pos2::new(x, center.y + 7.0),
                        Pos2::new(x, center.y + 7.0 - h),
                    ],
                    Stroke::new(2.0, color),
                );
            }
        }
        AdNavId::Sim => {
            // simple car silhouette
            painter.rect_stroke(
                Rect::from_center_size(center + Vec2::new(0.0, 2.0), Vec2::new(18.0, 7.0)),
                CornerRadius::same(2),
                Stroke::new(1.4, color),
        StrokeKind::Inside);
            painter.rect_stroke(
                Rect::from_center_size(center - Vec2::new(0.0, 4.0), Vec2::new(12.0, 6.0)),
                CornerRadius::same(2),
                Stroke::new(1.4, color),
        StrokeKind::Inside);
            painter.circle_stroke(center + Vec2::new(-5.0, 6.0), 2.2, Stroke::new(1.2, color));
            painter.circle_stroke(center + Vec2::new(5.0, 6.0), 2.2, Stroke::new(1.2, color));
        }
    }
}

fn paint_nail(painter: &egui::Painter, center: Pos2, color: Color32) {
    let head = center + Vec2::new(0.0, -3.5);
    painter.circle_filled(head, 3.0, color);
    painter.line_segment(
        [head + Vec2::new(0.0, 1.5), center + Vec2::new(0.0, 6.0)],
        Stroke::new(1.6, color),
    );
    painter.circle_filled(center + Vec2::new(0.0, 6.5), 1.1, color);
}


fn topic_path_matches(path: &EntityPath, topic: &str) -> bool {
    let s = path.to_string();
    s == topic || s.trim_start_matches('/') == topic.trim_start_matches('/')
}

#[cfg(target_arch = "wasm32")]
fn json_string_array(text: &str, key: &str) -> Option<Vec<String>> {
    let needle = format!("\"{key}\"");
    let pos = text.find(&needle)?;
    let after = &text[pos + needle.len()..];
    let colon = after.find(':')?;
    let mut rest = after[colon + 1..].trim_start();
    if !rest.starts_with('[') {
        return None;
    }
    rest = &rest[1..];
    let mut out = Vec::new();
    let mut i = 0;
    let bytes = rest.as_bytes();
    while i < bytes.len() {
        while i < bytes.len() && (bytes[i].is_ascii_whitespace() || bytes[i] == b',') {
            i += 1;
        }
        if i < bytes.len() && bytes[i] == b']' {
            break;
        }
        if i >= bytes.len() || bytes[i] != b'"' {
            break;
        }
        i += 1;
        let mut s = String::new();
        while i < bytes.len() {
            match bytes[i] {
                b'"' => {
                    i += 1;
                    break;
                }
                b'\\' if i + 1 < bytes.len() => {
                    s.push(rest.as_bytes()[i + 1] as char);
                    i += 2;
                }
                c => {
                    s.push(c as char);
                    i += 1;
                }
            }
        }
        out.push(s);
    }
    out.sort();
    out.dedup();
    Some(out)
}

fn json_field(text: &str, key: &str) -> Option<String> {
    let pat = format!("\"{key}\":");
    let idx = text.find(&pat)?;
    let rest = text[idx + pat.len()..].trim_start();
    if rest.starts_with("null") {
        return None;
    }
    let bytes = rest.as_bytes();
    if !rest.starts_with('"') {
        return None;
    }
    let mut out = String::new();
    let mut i = 1usize;
    while i < bytes.len() {
        match bytes[i] {
            b'"' => return Some(out),
            b'\\' if i + 1 < bytes.len() => {
                out.push(bytes[i + 1] as char);
                i += 2;
            }
            b => {
                out.push(b as char);
                i += 1;
            }
        }
    }
    None
}

#[cfg(target_arch = "wasm32")]
fn json_f64(text: &str, key: &str) -> Option<f64> {
    let pat = format!("\"{key}\":");
    let idx = text.find(&pat)?;
    let rest = text[idx + pat.len()..].trim_start();
    let end = rest
        .find(|c: char| c == ',' || c == '}' || c.is_whitespace())
        .unwrap_or(rest.len());
    rest[..end].parse().ok()
}
