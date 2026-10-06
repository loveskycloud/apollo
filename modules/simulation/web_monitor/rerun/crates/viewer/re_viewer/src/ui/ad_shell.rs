//! AD shell: leftmost square nav (Source / Layout / Panel / Sim) + left secondary drawers.
//! Matches the Carolanne-purple web_monitor prototype.

use egui::{Color32, CornerRadius, Pos2, Rect, RichText, Sense, Stroke, StrokeKind, Ui, Vec2};
use re_data_source::LogDataSource;
use re_log_channel::LogSource;
use re_log_types::{
    AbsoluteTimeRange, ApplicationId, FileSource, RecordingId, StoreId, TimeInt, Timeline,
    TimelineName, TimestampFormat,
};
use re_sdk_types::blueprint::components::PlayState;
use re_ui::{UICommand, UICommandSender as _};
use re_viewer_context::{
    AppContext, SystemCommand, SystemCommandSender as _, TimeControlCommand, ViewerContext,
};

const PLANNER_RBL: &[u8] = include_bytes!("../../layouts/planner.rbl");
const PERCEPTION_RBL: &[u8] = include_bytes!("../../layouts/perception.rbl");
const CONTROL_RBL: &[u8] = include_bytes!("../../layouts/control.rbl");

/// Stable ApplicationId for AD layouts. Independent of whether a bag is open.
const AD_APPLICATION_ID: &str = "apollo_ad_viewer";
// Source and scene-layer surfaces follow the supplied source_and_layger reference.
const SOURCE_BG: Color32 = Color32::from_rgb(24, 24, 38);
const SOURCE_INPUT: Color32 = Color32::from_rgb(34, 33, 54);
const SOURCE_BORDER: Color32 = Color32::from_rgb(73, 65, 99);
const SOURCE_MUTED: Color32 = Color32::from_rgb(196, 184, 232);
/// Placeholder recording so blueprint activation has something to attach to (data optional).
const AD_LAYOUT_WORKSPACE_RECORDING_ID: &str = "ad_layout_workspace";
/// egui temp id: Cyber header `(begin_ns, end_ns)` for media-bar duration (not EntityDb scan).
pub(crate) const HEADER_TIME_RANGE_EGUI_ID: &str = "web_monitor_header_time_range_ns";

/// First playback window after open / convert (nanoseconds).
#[cfg(target_arch = "wasm32")]
const PLAYBACK_INITIAL_WINDOW_NS: i64 = 2_000_000_000;
/// Lookback so LatestAt at the playhead still has samples after a topic toggle.
#[cfg(target_arch = "wasm32")]
const PLAYBACK_LOOKBACK_NS: i64 = 1_000_000_000;
/// Subsequent prefetch window size.
#[cfg(target_arch = "wasm32")]
const PLAYBACK_STEP_WINDOW_NS: i64 = 2_000_000_000;
/// Request next window when playhead is within this of the loaded end.
#[cfg(target_arch = "wasm32")]
const PLAYBACK_PREFETCH_AHEAD_NS: i64 = 5_000_000_000;

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
    pub const SIM_CONFIG_ACCENT: Color32 = Color32::from_rgb(0x8B, 0x52, 0xFF);
    pub const TEXT: Color32 = Color32::from_rgb(0xF3, 0xEE, 0xFF);
    pub const TEXT_DIM: Color32 = Color32::from_rgb(0xC4, 0xB5, 0xFD);
    pub const PIN_ACTIVE: Color32 = Color32::from_rgb(0xDD, 0xD6, 0xFE);
}

/// Apply Carolanne purple overrides on top of Rerun design tokens.
pub fn apply_theme(ctx: &egui::Context) {
    let apply = |style: &mut egui::Style| {
        let v = &mut style.visuals;
        v.dark_mode = true;
        v.panel_fill = theme::APP_BG;
        v.window_fill = theme::PANEL_BG;
        v.extreme_bg_color = theme::CARD_BG;
        // egui 0.36 TextEdit uses this (not only extreme_bg). Rerun tokens leave a
        // near-black slab that fights AD chrome; force CARD_BG + light text.
        v.text_edit_bg_color = Some(theme::CARD_BG);
        v.faint_bg_color = theme::PANEL_BG;
        v.code_bg_color = theme::CARD_BG;
        v.override_text_color = Some(theme::TEXT);
        v.widgets.noninteractive.fg_stroke = Stroke::new(1.0, theme::TEXT);
        v.widgets.inactive.fg_stroke = Stroke::new(1.0, theme::TEXT);
        v.widgets.hovered.fg_stroke = Stroke::new(1.0, theme::TEXT);
        v.widgets.active.fg_stroke = Stroke::new(1.0, egui::Color32::WHITE);
        v.widgets.open.fg_stroke = Stroke::new(1.0, theme::TEXT);
        v.widgets.noninteractive.weak_bg_fill = theme::APP_BG;
        v.widgets.noninteractive.bg_fill = theme::APP_BG;
        v.widgets.inactive.bg_fill = theme::CARD_BG;
        v.widgets.inactive.weak_bg_fill = theme::CARD_BG;
        v.widgets.hovered.weak_bg_fill = theme::CARD_BG_HOVER;
        v.widgets.hovered.bg_fill = theme::CARD_BG_HOVER;
        v.widgets.active.weak_bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.55);
        v.widgets.active.bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.55);
        v.widgets.open.weak_bg_fill = theme::CARD_BG;
        v.widgets.open.bg_fill = theme::CARD_BG;
        v.selection.bg_fill = theme::ACCENT_STRONG.gamma_multiply(0.45);
        v.selection.stroke = Stroke::new(1.0, theme::ACCENT);
        v.hyperlink_color = theme::ACCENT;
        v.window_stroke = Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.35));
        v.widgets.noninteractive.bg_stroke.color = theme::ACCENT.gamma_multiply(0.25);
        v.widgets.inactive.bg_stroke = Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.28));
    };
    ctx.style_mut_of(egui::Theme::Dark, apply);
    // Pin Light too so a system/light preference cannot resurrect white TextEdit slabs
    // under AD's light TEXT color.
    ctx.style_mut_of(egui::Theme::Light, apply);
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
    fn display_label(self) -> &'static str {
        match self {
            Self::Perception => "感知布局",
            Self::Planning => "规划布局",
            Self::Control => "控制布局",
            Self::Custom => "自定义布局",
        }
    }
    fn workspace_title(self) -> &'static str {
        match self {
            Self::Perception => "感知调试",
            Self::Planning => "规划调试",
            Self::Control => "控制调试",
            Self::Custom => "自定义工作区",
        }
    }

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
    /// Kept for persisted UI state; Scenario mode UI was removed.
    #[serde(other)]
    LegacyOther,
}

impl SourceOpenMode {
    fn label(self) -> &'static str {
        match self {
            Self::Local | Self::LegacyOther => "本地文件",
        }
    }

    fn all() -> [Self; 1] {
        [Self::Local]
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
    #[serde(skip)]
    panel_search: String,
    #[serde(skip)]
    layout_notice: String,
    #[serde(alias = "sim_modal_open")]
    pub sim_open: bool,
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
    open_map_path_draft: String,
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
    /// Cyber record header begin/end (ns) from convert `meta` — Properties shows these as-is.
    #[serde(skip)]
    header_begin_ns: Option<i64>,
    #[serde(skip)]
    header_end_ns: Option<i64>,
    #[serde(skip)]
    applied_default_once: bool,
    /// Last recording we bound the active AD layout to (re-apply on new opens).
    #[serde(skip)]
    layout_bound_store_id: Option<StoreId>,
    /// Publish/Message clock clamped after MCAP timelines appear (streaming may lag).
    #[serde(skip)]
    playback_clock_ready: bool,
    #[serde(default)]
    custom_layout: Vec<u8>,
    #[serde(skip)]
    current_blueprint_id: Option<StoreId>,
    #[serde(skip)]
    pending_layout_clock: Option<(StoreId, TimelineName, i64, PlayState)>,
    /// Layouts bind to applications, not to each indexed window/session reset.
    #[serde(skip)]
    layout_applied_application: Option<ApplicationId>,
    /// MCAP summary channel topics (from host `/api/mcap_topics`, not EntityDb scan).
    #[serde(skip)]
    mcap_topic_list: Vec<String>,
    #[serde(skip)]
    mcap_topic_list_path: String,
    /// Prevent Topic View from stealing Enter when Source Open path handles it.
    #[serde(skip)]
    enter_handled_this_frame: bool,
    /// Panel: which MCAP topics are enabled for windowed playback.
    #[serde(skip)]
    playback_topic_enabled: std::collections::HashMap<String, bool>,
    #[serde(skip)]
    layer_visibility_key: String,
    /// MCAP path currently used for windowed playback.
    #[serde(skip)]
    playback_mcap_path: String,
    #[serde(skip)]
    playback_source: Option<super::ad_playback::RecordingSource>,
    #[serde(skip)]
    playback_recovery: Option<super::ad_playback::PlaybackBookmark>,
    #[serde(skip)]
    playback_restore_checked: bool,
    #[serde(skip)]
    playback_explicit_source: bool,
    #[serde(skip)]
    playback_saved_bookmark: String,
    /// Initial seek must be committed after the replacement blueprint activates.
    #[serde(skip)]
    playback_layout_pending: Option<StoreId>,
    /// Exclusive end of the last successfully requested window (ns).
    #[serde(skip)]
    playback_loaded_end_ns: Option<i64>,
    /// True while a `/api/playback_window` request is in flight.
    #[serde(skip)]
    playback_window_pending: bool,
    /// Last playback_window error (shown in viewport topics picker).
    #[serde(skip)]
    playback_window_error: Option<String>,
    /// Expandable Topics control anchored in the 3D viewport (not the Panel drawer).
    #[serde(skip)]
    topics_picker_expanded: bool,
    #[serde(skip)]
    layer_search: String,
    #[serde(skip)]
    collapsed_layers: std::collections::BTreeSet<String>,
    /// After a topic-set reset, seek here instead of bag begin (keep playhead).
    #[serde(skip)]
    playback_resume_ns: Option<i64>,
    /// Restore play/pause after topic-set reset.
    #[serde(skip)]
    playback_resume_was_playing: bool,
    /// Last playhead that fell inside the Cyber header range (stable across store swaps).
    #[serde(skip)]
    playback_last_playhead_ns: Option<i64>,
    /// Topic selection changed while a window request was in flight — re-apply when done.
    #[serde(skip)]
    playback_topics_dirty: bool,
    /// Last applied enabled-topic set (diff → only fetch newly checked topics).
    #[serde(skip)]
    playback_enabled_snapshot: Vec<String>,
    #[serde(skip)]
    playback_waiting_receipt: Option<String>,
    /// Recording confirmed by this session's stream receipt, not a cached route.
    #[serde(skip)]
    playback_recording: Option<StoreId>,
    /// Wait for the initial paused seek command to be applied, not just queued.
    #[serde(skip)]
    playback_initial_seek: Option<i64>,
    #[serde(skip)]
    playback_cached_ranges: Vec<(i64, i64)>,
    #[serde(skip)]
    playback_buffering: bool,
    #[serde(skip)]
    playback_request_started: Option<web_time::Instant>,
    /// Shared sim catalog (bags + maps) for Source pickers.
    #[serde(skip)]
    source_catalog: serde_json::Value,
    #[serde(skip)]
    source_catalog_pending:
        Option<std::sync::Arc<parking_lot::Mutex<Option<Result<serde_json::Value, String>>>>>,
    #[serde(skip)]
    source_catalog_at: Option<web_time::Instant>,
    #[serde(skip)]
    source_catalog_error: Option<String>,
}

impl Default for AdShell {
    fn default() -> Self {
        Self {
            active_nav: AdNavId::Source,
            source_open: false,
            layout_open: false,
            panel_open: false,
            panel_search: String::new(),
            layout_notice: String::new(),
            sim_open: false,
            active_layout: Some(AdLayoutKind::Perception),
            default_layout: Some(AdLayoutKind::Perception),
            source_open_mode: SourceOpenMode::Local,
            scenario_id: String::new(),
            trip_id: String::new(),
            car_id: String::new(),
            local_bag: String::new(),
            open_map_path_draft: String::new(),
            open_scenario_draft: String::new(),
            open_car_id_draft: String::new(),
            open_start_ts_draft: String::new(),
            open_end_ts_draft: String::new(),
            open_local_path_draft: String::new(),
            open_status_msg: String::new(),
            convert_job_id: None,
            convert_last_poll: 0.0,
            convert_opened_output: None,
            header_begin_ns: None,
            header_end_ns: None,
            applied_default_once: false,
            layout_bound_store_id: None,
            playback_clock_ready: false,
            custom_layout: Default::default(),
            current_blueprint_id: None,
            pending_layout_clock: None,
            layout_applied_application: None,
            mcap_topic_list: Vec::new(),
            mcap_topic_list_path: String::new(),
            enter_handled_this_frame: false,
            playback_topic_enabled: std::collections::HashMap::new(),
            layer_visibility_key: String::new(),
            playback_mcap_path: String::new(),
            playback_source: None,
            playback_recovery: None,
            playback_restore_checked: false,
            playback_explicit_source: false,
            playback_saved_bookmark: String::new(),
            playback_layout_pending: None,
            playback_loaded_end_ns: None,
            playback_window_pending: false,
            playback_window_error: None,
            topics_picker_expanded: false,
            layer_search: String::new(),
            collapsed_layers: Default::default(),
            playback_resume_ns: None,
            playback_resume_was_playing: false,
            playback_last_playhead_ns: None,
            playback_topics_dirty: false,
            playback_enabled_snapshot: Vec::new(),
            playback_waiting_receipt: None,
            playback_recording: None,
            playback_initial_seek: None,
            playback_cached_ranges: Vec::new(),
            playback_buffering: false,
            playback_request_started: None,
            source_catalog: serde_json::Value::Null,
            source_catalog_pending: None,
            source_catalog_at: None,
            source_catalog_error: None,
        }
    }
}

/// Back-compat name kept for clarity in older notes.
#[allow(dead_code)]
pub type AdLayoutPanel = AdShell;

impl AdShell {
    /// Full recording path: delegates to [`Self::show_with_app_ctx`].
    pub fn show(&mut self, ctx: &ViewerContext<'_>, ui: &mut Ui) {
        let blueprint_id = ctx.store_context.blueprint.store_id().clone();
        if self
            .playback_layout_pending
            .as_ref()
            .is_some_and(|old| old != &blueprint_id)
        {
            self.playback_layout_pending = None;
        }
        if self
            .pending_layout_clock
            .as_ref()
            .is_some_and(|(old, ..)| old != &blueprint_id)
        {
            let (_, timeline, time, play_state) =
                self.pending_layout_clock.take().expect("checked");
            if self.playback_initial_seek.is_none()
                && (self.playback_mcap_path.is_empty() || self.playback_clock_ready)
            {
                ctx.app_ctx.send_time_commands_to_active_recording(vec![
                    TimeControlCommand::SetActiveTimeline(timeline),
                    TimeControlCommand::SetTime(TimeInt::new_temporal(time).into()),
                    TimeControlCommand::SetPlayState(play_state),
                ]);
                self.playback_clock_ready = true;
                self.playback_last_playhead_ns = Some(time);
            }
        }
        self.current_blueprint_id = Some(blueprint_id);
        self.show_with_app_ctx(&ctx.app_ctx, ui);
    }

    /// Product chrome that works on welcome / examples (no `ViewerContext`).
    ///
    /// Layout apply and Source open use `command_sender`. Recording props soft-degrade
    /// when there is no active store.
    pub fn show_with_app_ctx(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        self.enter_handled_this_frame = false;
        self.ensure_default_applied(ctx);
        #[cfg(target_arch = "wasm32")]
        self.restore_playback_session(ctx);
        self.ensure_layout_for_active_recording(ctx);
        self.publish_header_time_range(&ctx.egui_ctx);
        #[cfg(target_arch = "wasm32")]
        {
            self.note_playback_playhead(ctx);
            // Keep convert/open feedback alive even when Source drawer is closed.
            self.poll_convert_job(ctx);
            self.take_host_open_status(ctx);
            self.take_pending_mcap_browse(ctx);
            self.take_mcap_topic_list(ctx);
            self.take_playback_window_status(ctx);
            self.ensure_playback_prefetch(ctx);
            self.remember_playback_session(ctx);
            self.show_playback_status(ctx);
            self.show_upload_progress_modal(ctx);
        }
        ctx.egui_ctx.data_mut(|d| {
            d.insert_temp(egui::Id::new("ad_layout_controls"), serde_json::json!({}))
        });
        self.show_rail(ui);
        self.show_source_secondary(ctx, ui);
        self.show_layout_secondary(ctx, ui);
        ctx.egui_ctx.data_mut(|d| {
            d.insert_temp(
                egui::Id::new("ad_debug_source"),
                super::ad_debug_view::Source {
                    topics: self.mcap_topic_list.clone(),
                    mcap: if self.indexed_source_ready(ctx) {
                        self.playback_mcap_path.clone()
                    } else {
                        String::new()
                    },
                    origin: self.header_begin_ns,
                    notice: self.source_notice(ctx),
                },
            );
            d.insert_temp(egui::Id::new("ad_debug_panel_state"), serde_json::json!([]));
        });
        super::ad_dashboard::publish(
            ctx,
            if self.indexed_source_ready(ctx) {
                &self.playback_mcap_path
            } else {
                ""
            },
            &self.source_notice(ctx),
        );
        if let Some((path, control)) = super::ad_sim::show(ctx, ui, &mut self.sim_open) {
            self.apply_layout(
                ctx,
                if control {
                    AdLayoutKind::Control
                } else {
                    AdLayoutKind::Planning
                },
            );
            ctx.egui_ctx
                .data_mut(|d| d.insert_temp(egui::Id::new("ad_sim_replay_layers"), true));
            self.open_map_path_draft.clear(); // Sim replay owns its immutable map snapshot.
            self.try_open_local_path(ctx, &path);
        }
    }

    /// Publish Cyber header begin/end so the media bar uses bag duration without scanning chunks.
    fn publish_header_time_range(&self, egui_ctx: &egui::Context) {
        if let (Some(b), Some(e)) = (self.header_begin_ns, self.header_end_ns) {
            if e > b {
                egui_ctx.data_mut(|d| {
                    d.insert_temp(egui::Id::new(HEADER_TIME_RANGE_EGUI_ID), (b, e));
                });
            }
        }
    }

    fn indexed_source_ready(&self, ctx: &AppContext<'_>) -> bool {
        self.playback_source.is_some()
            && !self.playback_mcap_path.is_empty()
            && self.playback_clock_ready
            && self.playback_initial_seek.is_none()
            && self.playback_layout_pending.is_none()
            && self
                .playback_recording
                .as_ref()
                .is_some_and(|id| ctx.active_recording().is_some_and(|r| r.store_id() == id))
    }

    fn source_notice(&self, ctx: &AppContext<'_>) -> String {
        if let Some(error) = &self.playback_window_error {
            return format!("Playback source error: {error}");
        }
        if !self.playback_mcap_path.is_empty() {
            return "Restoring source and synchronizing the current frame…".into();
        }
        if ctx
            .active_recording()
            .is_some_and(|r| !r.timelines().is_empty())
        {
            "This recording has no indexed source descriptor; it cannot query bag messages.".into()
        } else {
            "No bag is open.".into()
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn restore_playback_session(&mut self, ctx: &AppContext<'_>) {
        if self.playback_explicit_source
            || !self.playback_mcap_path.is_empty()
            || self.convert_job_id.is_some()
        {
            return;
        }
        if !self.playback_restore_checked {
            self.playback_restore_checked = true;
            // FileReader/XHR tasks cannot survive a page reload. Old versions
            // persisted their progress and left an undismissable 0% dialog.
            if let Some(mut progress) = crate::web_tools::peek_upload_progress()
                && matches!(progress.phase.as_str(), "reading" | "uploading")
            {
                progress.phase = "error".into();
                progress.message =
                    "Previous browser file read was interrupted. Choose the bag again from Source."
                        .into();
                crate::web_tools::set_upload_progress(&progress);
            }
            match crate::web_tools::playback_bookmark().and_then(|value| {
                value
                    .map(|v| {
                        serde_json::from_str::<super::ad_playback::PlaybackBookmark>(&v)
                            .map_err(|e| format!("Invalid saved playback session: {e}"))
                    })
                    .transpose()
            }) {
                Ok(Some(bookmark)) => {
                    if let Err(error) = bookmark.validate() {
                        self.playback_window_error = Some(error);
                        return;
                    }
                    self.begin_windowed_mcap_session(ctx, &bookmark.source.path, true);
                    self.playback_recovery = Some(bookmark);
                    return;
                }
                Ok(None) => {}
                Err(error) => {
                    self.playback_window_error = Some(error);
                    return;
                }
            }
        }
        if self.playback_window_error.is_some() {
            return;
        }
        let Some(recording) = ctx.active_recording() else {
            return;
        };
        let Some((_, text)) = recording.latest_at_component::<re_sdk_types::components::Text>(
            &super::ad_playback::SOURCE_ENTITY.into(),
            &re_chunk_store::LatestAtQuery::new("publish_time".into(), TimeInt::MAX),
            re_sdk_types::archetypes::TextDocument::descriptor_text().component,
        ) else {
            return;
        };
        let source =
            match serde_json::from_str::<super::ad_playback::RecordingSource>(&text.to_string()) {
                Ok(source) => source,
                Err(error) => {
                    self.playback_window_error = Some(format!("Invalid recording source: {error}"));
                    return;
                }
            };
        if let Err(error) = source.validate() {
            self.playback_window_error = Some(error);
            return;
        }
        let time_ns = recording
            .time_range_for(&"publish_time".into())
            .map(|r| r.min().as_i64());
        let Some(time_ns) = time_ns else {
            return;
        }; // static descriptor can arrive before payloads
        let layers = source.topics.iter().map(|t| (t.clone(), true)).collect();
        let bookmark = super::ad_playback::PlaybackBookmark {
            source,
            clock: "publish_time".into(),
            time_ns,
            layers,
        };
        self.begin_windowed_mcap_session(ctx, &bookmark.source.path, true);
        self.playback_recovery = Some(bookmark);
    }

    #[cfg(target_arch = "wasm32")]
    fn remember_playback_session(&mut self, ctx: &AppContext<'_>) {
        if !self.indexed_source_ready(ctx) {
            return;
        }
        let Some(tc) = ctx.active_time_ctrl() else {
            return;
        };
        let Some(time) = tc.time_int() else {
            return;
        };
        let bookmark = super::ad_playback::PlaybackBookmark {
            source: self.playback_source.clone().expect("ready source"),
            clock: tc.timeline_name().as_str().to_owned(),
            time_ns: time.as_i64(),
            layers: self.playback_topic_enabled.clone(),
        };
        let value = serde_json::to_string(&bookmark).expect("serializable session");
        if value != self.playback_saved_bookmark {
            match crate::web_tools::save_playback_bookmark(&value) {
                Ok(()) => self.playback_saved_bookmark = value,
                Err(error) => self.playback_window_error = Some(error),
            }
        }
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
        if !self.playback_mcap_path.is_empty() {
            let Some(expected) = self.playback_recording.as_ref() else {
                self.playback_clock_ready = false;
                return;
            };
            if ctx
                .active_recording()
                .is_none_or(|rec| rec.store_id() != expected)
            {
                self.playback_clock_ready = false;
                ctx.command_sender().send_system(SystemCommand::SetRoute(
                    re_viewer_context::Route::LocalRecording {
                        recording_id: expected.clone(),
                    },
                ));
                return;
            }
        }
        let Some(rec) = ctx.active_recording() else {
            return;
        };
        let store_id = rec.store_id().clone();
        let is_new_store = self.layout_bound_store_id.as_ref() != Some(&store_id);

        if is_new_store {
            let Some(kind) = self.active_layout.or(self.default_layout) else {
                return;
            };
            re_log::info!(
                "Active recording changed → re-apply layout {} on {}",
                kind.label(),
                store_id
            );
            self.layout_bound_store_id = Some(store_id);
            self.playback_clock_ready = false;
            if let Some(out) = self.convert_opened_output.clone() {
                self.open_status_msg = format!("Loaded {out}");
            } else if self.open_status_msg.contains("loading")
                || self.open_status_msg.contains("streaming")
                || self.open_status_msg.contains("Loading")
            {
                self.open_status_msg = format!("Loaded {}", rec.application_id());
            }
            if self.layout_applied_application.as_ref() != Some(rec.application_id()) {
                self.apply_layout(ctx, kind);
            }
        } else {
            // Same recording: clear transient "streaming/loading" status once data is attached.
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
        }

        // MCAP timelines often arrive a few frames after the store becomes active
        // (`open_local` streams). Retry until Publish/Message exists and clamp the
        // playhead into range — cursor at 0 with Cyber ~1e18 ns looks empty.
        if !self.playback_clock_ready
            || ctx.active_time_ctrl().is_some_and(|tc| {
                !matches!(tc.timeline_name().as_str(), "publish_time" | "message_time")
            })
        {
            if self.ensure_playback_clock(ctx, rec) {
                self.playback_clock_ready = true;
            } else {
                ctx.egui_ctx
                    .request_repaint_after(std::time::Duration::from_millis(100));
            }
        }
    }

    /// Windowed playback uses the same log-time clock as the host MCAP index.
    /// Returns true once a timeline exists; playhead/view prefer Cyber header bounds.
    fn ensure_playback_clock(
        &mut self,
        ctx: &AppContext<'_>,
        rec: &re_entity_db::EntityDb,
    ) -> bool {
        let publish = TimelineName::from("publish_time");
        let message = TimelineName::from("message_time");
        let preferred = self
            .playback_recovery
            .as_ref()
            .map(|b| TimelineName::try_new(&b.clock).expect("validated bookmark clock"));
        let timeline =
            if let Some(preferred) = preferred.filter(|t| rec.timelines().contains_key(t)) {
                preferred
            } else if rec.timelines().contains_key(&publish) {
                publish
            } else if rec.timelines().contains_key(&message) {
                message
            } else {
                return false;
            };

        let (min_ns, max_ns, from_header) =
            if let (Some(b), Some(e)) = (self.header_begin_ns, self.header_end_ns) {
                if e > b {
                    (b, e, true)
                } else if let Some(range) = rec.time_range_for(&timeline) {
                    (range.min().as_i64(), range.max().as_i64(), false)
                } else {
                    return false;
                }
            } else if let Some(range) = rec.time_range_for(&timeline) {
                (range.min().as_i64(), range.max().as_i64(), false)
            } else {
                return false;
            };

        let min_t = TimeInt::new_temporal(min_ns);
        let max_t = TimeInt::new_temporal(max_ns);
        let view_range = AbsoluteTimeRange::new(min_t, max_t);

        let data_range = rec.time_range_for(&timeline);
        let resume_ns = self.playback_resume_ns;
        // Initial open: wait until the first window has chunks so LatestAt isn't empty.
        if resume_ns.is_none() && data_range.is_none() {
            return false;
        }

        let resume_playing = self.playback_resume_was_playing;
        let seek_ns = if let Some(r) = resume_ns {
            r.clamp(min_ns, max_ns.max(min_ns))
        } else if let Some(range) = data_range {
            // Prefer first loaded sample — header begin alone can sit before any chunk.
            range.min().as_i64().clamp(min_ns, max_ns.max(min_ns))
        } else {
            min_ns
        };
        // Consume resume only after we actually seek (new store ready).
        self.playback_resume_ns = None;
        self.playback_resume_was_playing = false;
        self.playback_last_playhead_ns = Some(seek_ns);
        let seek_t = TimeInt::new_temporal(seek_ns);

        let mut cmds = vec![
            TimeControlCommand::SetActiveTimeline(timeline.clone()),
            TimeControlCommand::SetTimeView(re_viewer_context::TimeView::from(view_range)),
            // Header / resume seek must not wait for full stream — use unclamped SetTime.
            TimeControlCommand::SetTime(seek_t.into()),
        ];
        if resume_ns.is_some() {
            if resume_playing {
                cmds.push(TimeControlCommand::SetPlayState(PlayState::Playing));
            } else {
                cmds.push(TimeControlCommand::Pause);
            }
        } else {
            cmds.insert(0, TimeControlCommand::Pause);
            if !from_header {
                cmds.push(TimeControlCommand::SetTimeClamped(seek_t.into()));
            }
        }
        ctx.send_time_commands_to_active_recording(cmds);
        re_log::debug!(
            "AD playback timeline → {timeline} (seek={seek_ns}, range=[{min_ns},{max_ns}], header={from_header}, resume={})",
            resume_ns.is_some()
        );
        true
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
                ui.put(
                    logo_rect,
                    egui::Image::new(egui::include_image!("../../data/sim_scope.png"))
                        .fit_to_exact_size(logo_rect.size()),
                )
                .on_hover_text("sim scope");
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
        ui.painter().rect_filled(rect, CornerRadius::same(10), fill);

        let icon_color = if active {
            Color32::WHITE
        } else {
            theme::TEXT_DIM
        };
        paint_nav_icon(
            ui.painter(),
            id,
            rect.center() - Vec2::new(0.0, 8.0),
            icon_color,
        );

        ui.painter().text(
            rect.center() + Vec2::new(0.0, 16.0),
            egui::Align2::CENTER_CENTER,
            id.label(),
            egui::FontId::proportional(11.0),
            if active { Color32::WHITE } else { theme::TEXT },
        );

        if response.clicked() {
            match id {
                AdNavId::Source => {
                    self.active_nav = AdNavId::Source;
                    self.source_open = !self.source_open;
                    self.layout_open = false;
                    self.panel_open = false;
                    self.sim_open = false;
                }
                AdNavId::Layout => {
                    self.active_nav = AdNavId::Layout;
                    self.layout_open = !self.layout_open;
                    self.source_open = false;
                    self.panel_open = false;
                    self.sim_open = false;
                }
                AdNavId::Panel => {
                    self.active_nav = AdNavId::Panel;
                    self.panel_open = !self.panel_open;
                    self.source_open = false;
                    self.layout_open = false;
                    self.sim_open = false;
                }
                AdNavId::Sim => {
                    self.active_nav = AdNavId::Sim;
                    self.source_open = false;
                    self.layout_open = false;
                    self.panel_open = false;
                    self.sim_open = !self.sim_open;
                }
            }
        }
    }

    fn show_layout_secondary(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        let mut layout_open_flag = self.layout_open;
        egui::Panel::left("ad_layout_secondary")
            .resizable(true)
            .drag_to_open(false)
            .default_size(256.0)
            .min_size(224.0)
            .frame(layout_drawer_frame())
            .show_collapsible(ui, &mut layout_open_flag, |ui| {
                layout_control(ui, "layout_rect", ui.max_rect());
                drawer_heading(ui, "布局管理", &mut self.layout_open);
                ui.add_space(16.0);
                layout_section_label(ui, "布局预设");
                ui.add_space(8.0);
                for kind in AdLayoutKind::all() {
                    self.layout_row(ctx, ui, kind);
                }
                ui.add_space(14.0);
                ui.separator();
                ui.add_space(12.0);
                let edit = layout_action(ui, "管理面板", true);
                layout_control(ui, "manage_panels", edit.rect);
                if edit.clicked() {
                    self.layout_open = false;
                    self.panel_open = true;
                    self.active_nav = AdNavId::Panel;
                }
                ui.add_space(8.0);
                ui.label(
                    RichText::new("固定常用布局，下次打开时自动使用。")
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                );
            });
        self.layout_open &= layout_open_flag;
    }

    fn layout_row(&mut self, ctx: &AppContext<'_>, ui: &mut Ui, kind: AdLayoutKind) {
        let selected = self.active_layout == Some(kind);
        let pinned = self.default_layout == Some(kind);
        let available = kind != AdLayoutKind::Custom || !self.custom_layout.is_empty();
        let (rect, response) =
            ui.allocate_exact_size(Vec2::new(ui.available_width(), 80.0), Sense::click());
        layout_control(ui, &format!("layout_{}", kind.label()), rect);
        ui.painter().rect(
            rect,
            7.0,
            if selected {
                theme::ACCENT_STRONG.gamma_multiply(0.25)
            } else if response.hovered() {
                theme::CARD_BG_HOVER
            } else {
                theme::CARD_BG.gamma_multiply(0.35)
            },
            Stroke::new(
                1.0,
                theme::ACCENT.gamma_multiply(if selected { 0.85 } else { 0.25 }),
            ),
            StrokeKind::Inside,
        );
        let preview = Rect::from_min_size(
            rect.left_top() + Vec2::new(10.0, 15.0),
            Vec2::new(68.0, 50.0),
        );
        let tiles = match kind {
            AdLayoutKind::Perception => vec![
                (0.0, 0.0, 0.6, 1.0),
                (0.64, 0.0, 1.0, 0.47),
                (0.64, 0.53, 1.0, 1.0),
            ],
            AdLayoutKind::Planning => vec![
                (0.0, 0.0, 0.48, 0.57),
                (0.0, 0.63, 0.48, 1.0),
                (0.54, 0.0, 1.0, 0.47),
                (0.54, 0.53, 1.0, 1.0),
            ],
            AdLayoutKind::Control => vec![
                (0.0, 0.0, 0.48, 0.47),
                (0.54, 0.0, 1.0, 0.47),
                (0.0, 0.53, 0.48, 1.0),
                (0.54, 0.53, 1.0, 1.0),
            ],
            AdLayoutKind::Custom => vec![
                (0.0, 0.0, 1.0, 0.3),
                (0.0, 0.36, 0.48, 1.0),
                (0.54, 0.36, 1.0, 1.0),
            ],
        };
        for (x0, y0, x1, y1) in tiles {
            let tile = Rect::from_min_max(
                preview.min + Vec2::new(x0 * preview.width(), y0 * preview.height()),
                preview.min + Vec2::new(x1 * preview.width(), y1 * preview.height()),
            );
            ui.painter().rect(
                tile,
                2.0,
                theme::ACCENT.gamma_multiply(0.15),
                Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.55)),
                StrokeKind::Inside,
            );
        }
        ui.painter().text(
            rect.left_top() + Vec2::new(90.0, 27.0),
            egui::Align2::LEFT_CENTER,
            kind.display_label(),
            egui::FontId::proportional(13.0),
            if available {
                theme::TEXT
            } else {
                theme::TEXT_DIM.gamma_multiply(0.55)
            },
        );
        ui.painter().text(
            rect.left_top() + Vec2::new(90.0, 52.0),
            egui::Align2::LEFT_CENTER,
            if selected {
                "使用中"
            } else if available {
                "点击切换"
            } else {
                "尚未保存"
            },
            egui::FontId::proportional(11.0),
            theme::TEXT_DIM,
        );
        let pin_rect = Rect::from_center_size(
            Pos2::new(rect.right() - 17.0, rect.top() + 18.0),
            Vec2::splat(22.0),
        );
        let pin = ui.interact(
            pin_rect,
            ui.id().with(("pin", kind.label())),
            Sense::click(),
        );
        layout_control(ui, &format!("pin_{}", kind.label()), pin_rect);
        if pinned {
            ui.painter()
                .circle_filled(pin_rect.center(), 10.0, theme::ACCENT_STRONG);
        }
        paint_nail(
            ui.painter(),
            pin_rect.center(),
            if pinned {
                theme::PIN_ACTIVE
            } else {
                theme::TEXT_DIM
            },
        );
        if pin.on_hover_text("设为默认布局").clicked() && available {
            self.default_layout = Some(kind);
        }
        if response.clicked() && available {
            self.apply_layout(ctx, kind);
        }
        ui.add_space(10.0);
    }

    pub(crate) fn show_panel_secondary(
        &mut self,
        ctx: &re_viewer_context::ViewerContext<'_>,
        viewport: &re_viewport_blueprint::ViewportBlueprint,
        ui: &mut Ui,
    ) {
        use re_viewer_context::{Contents, DragAndDropPayload, VisitorControlFlow};
        let mut panel_open_flag = self.panel_open;
        egui::Panel::left("ad_panel_secondary")
            .resizable(true)
            .drag_to_open(false)
            .default_size(256.0)
            .min_size(224.0)
            .frame(layout_drawer_frame())
            .show_collapsible(ui, &mut panel_open_flag, |ui| {
                layout_control(ui, "panel_rect", ui.max_rect());
                drawer_heading(ui, "面板管理", &mut self.panel_open);
                ui.add_space(10.0);
                let search = ui.add_sized(
                    [ui.available_width(), 36.0],
                    egui::TextEdit::singleline(&mut self.panel_search)
                        .hint_text("搜索添加面板…")
                        .margin(egui::Margin::symmetric(10, 8)),
                );
                layout_control(ui, "panel_search", search.rect);
                ui.add_space(12.0);

                // Keep the two layout actions reachable while the library/list scrolls.
                egui::Panel::bottom("ad_panel_save_actions")
                    .frame(egui::Frame::new().inner_margin(egui::Margin::symmetric(0, 10)))
                    .show_inside(ui, |ui| {
                        ui.vertical(|ui| {
                            let save = layout_action(ui, "保存布局", true);
                            layout_control(ui, "save_layout", save.rect);
                            if save.clicked() {
                                match crate::saving::RrdSnapshot::blueprint(ctx.store_context.blueprint, None)
                                    .and_then(crate::saving::RrdSnapshot::encode)
                                {
                                    Ok(bytes) => {
                                        self.custom_layout = bytes;
                                        self.active_layout = Some(AdLayoutKind::Custom);
                                        self.default_layout = Some(AdLayoutKind::Custom);
                                        self.layout_notice = "已保存为自定义布局".into();
                                    }
                                    Err(err) => {
                                        self.layout_notice = format!("保存失败：{err}");
                                        re_log::error!("Failed to save custom layout: {err}");
                                    }
                                }
                            }
                            ui.add_space(6.0);
                            let export = layout_action(ui, "导出布局", false);
                            layout_control(ui, "export_layout", export.rect);
                            if export.clicked() {
                                use re_ui::RecordingCommandSender as _;
                                ctx.command_sender().send_recording_command(re_ui::RecordingCommand {
                                    recording_id: ctx.store_context.recording.store_id().clone(),
                                    kind: re_ui::RecordingCommandKind::SaveBlueprint,
                                });
                            }
                            if !self.layout_notice.is_empty() {
                                ui.add_space(4.0);
                                ui.label(RichText::new(&self.layout_notice).size(11.0).color(theme::TEXT_DIM));
                            }
                        });
                    });
                egui::ScrollArea::vertical()
                    .id_salt("ad_panel_edit_scroll")
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.spacing_mut().item_spacing.y = 4.0;
                        layout_section_label(ui, "面板库");
                        ui.add_space(8.0);
                        let search = self.panel_search.trim().to_lowercase();
                        let groups: &[(&str, &[(&str, &str, &str)])] = &[
                            ("场景与轨迹", &[("三维场景", "scene", "3D scene"), ("XY 轨迹", "trajectory", "Trajectory XY")]),
                            ("规划与控制", &[
                                ("规划曲线", "profile", "Planning profile"),
                                ("速度跟踪", "speed", "Speed tracking"),
                                ("转向反馈", "steering", "Steering feedback"),
                                ("跟踪误差", "errors", "Tracking errors"),
                                ("踏板", "pedals", "Pedals"),
                            ]),
                            ("数据与诊断", &[
                                ("规划消息", "planning", "Planning message"),
                                ("信号曲线", "plot", "Signal plot"),
                                ("值监视", "watch", "Value watch"),
                                ("状态变化", "states", "State transitions"),
                                ("话题健康", "health", "Topic health"),
                            ]),
                            ("更多工具", &[
                                ("消息检查器", "inspector", "Topic inspector"),
                                ("加速度跟踪", "acceleration", "Acceleration tracking"),
                                ("航向误差", "heading", "Heading error"),
                                ("控制器耗时", "runtime", "Controller runtime"),
                                ("完整控制仪表盘", "control", "Full control dashboard"),
                            ]),
                        ];
                        let mut matches = 0;
                        for (index, (group, entries)) in groups.iter().enumerate() {
                            let filtered: Vec<_> = entries.iter().filter(|(name, preset, english)| {
                                format!("{group} {name} {preset} {english}").to_lowercase().contains(&search)
                            }).collect();
                            if filtered.is_empty() { continue; }
                            matches += filtered.len();
                            library_group(ui, group, filtered.len(), index < 2, !search.is_empty(), |ui| {
                                for (name, preset, _) in filtered {
                                    let response = library_row(ui, name);
                                    layout_control(ui, &format!("add_{preset}"), response.rect);
                                    if response.clicked() {
                                        let mut view = if *preset == "scene" {
                                            re_viewport_blueprint::ViewBlueprint::new("3D".into(), re_viewer_context::RecommendedView {
                                                origin: "/lidar/up/points".into(),
                                                query_filter: re_log_types::EntityPathFilter::parse_forgiving(
                                                    "+ /lidar/**\n+ /vehicle/**\n+ /planning/**\n+ /perception/**\n+ /prediction/**\n+ /hdmap/**",
                                                ),
                                            })
                                        } else {
                                            let mut view = re_viewport_blueprint::ViewBlueprint::new_with_root_wildcard("AdDebug".into());
                                            view.space_origin = format!("/debug/{preset}").into();
                                            view
                                        };
                                        view.display_name = Some((*name).into());
                                        viewport.add_views(std::iter::once(view), None, None);
                                        viewport.mark_user_interaction(ctx);
                                        self.topics_picker_expanded = false;
                                        self.layout_notice.clear();
                                    }
                                }
                            });
                            ui.add_space(8.0);
                        }
                        let cameras: Vec<_> = self.mcap_topic_list.iter().filter(|topic| {
                            topic.starts_with("/camera/") && format!("相机 {topic}").to_lowercase().contains(&search)
                        }).collect();
                        if !cameras.is_empty() {
                            matches += cameras.len();
                            library_group(ui, "相机", cameras.len(), false, !search.is_empty(), |ui| {
                                for topic in cameras {
                                    let name = topic.trim_start_matches("/camera/");
                                    if library_row(ui, name).clicked() {
                                        let mut view = re_viewport_blueprint::ViewBlueprint::new("2D".into(), re_viewer_context::RecommendedView {
                                            origin: topic.clone().into(),
                                            query_filter: re_log_types::EntityPathFilter::parse_forgiving(&format!("+ {topic}/**")),
                                        });
                                        view.display_name = Some(name.into());
                                        viewport.add_views(std::iter::once(view), None, None);
                                        viewport.mark_user_interaction(ctx);
                                    }
                                }
                            });
                        }
                        if matches == 0 {
                            ui.label(RichText::new("没有匹配的面板").size(12.0).color(theme::TEXT_DIM));
                        }
                        ui.add_space(6.0);
                        let more = ui.add(egui::Button::new(RichText::new("更多类型 / 分屏 / 标签页…").size(11.0).color(theme::TEXT_DIM)).frame(false));
                        layout_control(ui, "more_types", more.rect);
                        if more.clicked() {
                            re_viewport_blueprint::ui::show_add_view_or_container_modal(viewport.root_container);
                        }
                        ui.add_space(12.0);
                        ui.separator();
                        ui.add_space(12.0);
                        layout_section_label(ui, &format!("已添加 · {}", viewport.views.len()));
                        ui.add_space(8.0);
                        let mut ordered_views = Vec::new();
                        let _ = viewport.visit_contents(&mut |contents, _| {
                            if let Contents::View(id) = contents { ordered_views.push(*id); }
                            VisitorControlFlow::<()>::Continue
                        });
                        for id in ordered_views {
                            let Some(view) = viewport.view(&id) else { continue; };
                            let row = egui::Frame::new()
                                .fill(theme::CARD_BG.gamma_multiply(0.4))
                                .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.3)))
                                .corner_radius(6.0)
                                .inner_margin(egui::Margin::symmetric(6, 5))
                                .show(ui, |ui| {
                                    ui.set_width(ui.available_width());
                                    ui.horizontal(|ui| {
                                        ui.spacing_mut().item_spacing.x = 4.0;
                                        let (rect, drag) = ui.allocate_exact_size(Vec2::new(16.0, 24.0), Sense::drag());
                                        paint_grip(ui.painter(), rect.center());
                                        drag.dnd_set_drag_payload(DragAndDropPayload::Contents { contents: vec![Contents::View(id)] });
                                        layout_control(ui, &format!("drag_{id}"), rect);
                                        let title = panel_display_name(view.display_name.as_deref().unwrap_or("未命名面板"));
                                        let title_width = (ui.available_width() - 64.0).max(24.0);
                                        let (title_rect, response) = ui.allocate_exact_size(Vec2::new(title_width, 26.0), Sense::click());
                                        ui.painter().with_clip_rect(title_rect.intersect(ui.clip_rect())).text(title_rect.left_center(), egui::Align2::LEFT_CENTER, title, egui::FontId::proportional(13.0), theme::TEXT);
                                        if response.clicked() { viewport.focus_tab(id); }
                                        let mut visible = view.visible;
                                        let visibility = re_ui::ad_panel_icon_button(ui, if visible { &re_ui::icons::VISIBLE } else { &re_ui::icons::INVISIBLE }, if visible { "隐藏面板" } else { "显示面板" });
                                        if visibility.clicked() { visible = !visible; }
                                        layout_control(ui, &format!("visible_{id}"), visibility.rect);
                                        if visible != view.visible {
                                            viewport.set_content_visibility(ctx, &Contents::View(id), visible);
                                            viewport.mark_user_interaction(ctx);
                                            self.layout_notice.clear();
                                        }
                                        let menu = re_ui::ad_panel_more_button(ui);
                                        layout_control(ui, &format!("menu_{id}"), menu.rect);
                                        egui::Popup::menu(&menu).id(egui::Id::new(("ad_panel_more", id))).show(|ui| {
                                            let focus = ui.button("定位面板");
                                            if focus.clicked() { viewport.focus_tab(id); ui.close(); }
                                            let remove = ui.button("删除面板");
                                            layout_control(ui, &format!("remove_{id}"), remove.rect);
                                            if remove.clicked() {
                                                viewport.remove_contents(Contents::View(id));
                                                viewport.mark_user_interaction(ctx);
                                                self.layout_notice.clear();
                                                ui.close();
                                            }
                                        });
                                    });
                                }).response;
                            if let Some(payload) = row.dnd_release_payload::<DragAndDropPayload>()
                                && let DragAndDropPayload::Contents { contents } = payload.as_ref()
                                && !contents.contains(&Contents::View(id))
                                && let Some((parent, position)) = viewport.find_parent_and_position_index(&Contents::View(id))
                            {
                                viewport.move_contents(contents.clone(), parent, position);
                                viewport.mark_user_interaction(ctx);
                                self.layout_notice.clear();
                            }
                            ui.add_space(6.0);
                        }
                        if viewport.views.is_empty() {
                            ui.label(RichText::new("从面板库添加面板").size(12.0).color(theme::TEXT_DIM));
                        }
                    });
            });
        // Both the heading and egui's resize-to-close interaction can close the drawer.
        self.panel_open &= panel_open_flag;
        self.show_layout_workspace_header(ctx, ui);
    }

    fn show_layout_workspace_header(&mut self, ctx: &ViewerContext<'_>, ui: &mut Ui) {
        if !self.panel_open && !self.layout_open {
            return;
        }
        egui::Panel::top("ad_layout_workspace_header")
            .frame(
                egui::Frame::new()
                    .fill(theme::RAIL_BG)
                    .inner_margin(egui::Margin::symmetric(12, 8)),
            )
            .show_inside(ui, |ui| {
                ui.horizontal(|ui| {
                    let kind = self.active_layout.unwrap_or(AdLayoutKind::Perception);
                    ui.label(
                        RichText::new(kind.workspace_title())
                            .size(18.0)
                            .strong()
                            .color(theme::TEXT),
                    );
                    ui.add_space(8.0);
                    let selected = ui.add(
                        egui::Button::new(kind.display_label()).min_size(Vec2::new(125.0, 30.0)),
                    );
                    layout_control(ui, "layout_selector", selected.rect);
                    let c = selected.rect.right_center() - Vec2::new(12.0, 0.0);
                    ui.painter().add(egui::Shape::convex_polygon(
                        vec![
                            c + Vec2::new(-4.0, -2.0),
                            c + Vec2::new(4.0, -2.0),
                            c + Vec2::new(0.0, 3.0),
                        ],
                        theme::TEXT_DIM,
                        Stroke::NONE,
                    ));
                    egui::Popup::menu(&selected).show(|ui| {
                        for kind in AdLayoutKind::all() {
                            let available =
                                kind != AdLayoutKind::Custom || !self.custom_layout.is_empty();
                            let response = ui.add_enabled(
                                available,
                                egui::Button::new(kind.display_label())
                                    .min_size(Vec2::new(140.0, 28.0)),
                            );
                            layout_control(ui, &format!("select_{}", kind.label()), response.rect);
                            if response.clicked() {
                                self.apply_layout(&ctx.app_ctx, kind);
                                ui.close();
                            }
                        }
                    });
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui
                            .add_sized([74.0, 30.0], egui::Button::new("全屏"))
                            .clicked()
                        {
                            ctx.command_sender().send_ui(UICommand::ToggleFullscreen);
                        }
                        if ui
                            .add_sized([74.0, 30.0], egui::Button::new("设置"))
                            .clicked()
                        {
                            ctx.command_sender().send_ui(UICommand::Settings);
                        }
                    });
                });
            });
    }

    /// Global display switches, applied to each spatial view without deleting cached data.
    pub(crate) fn show_viewport_layers_picker(
        &mut self,
        ctx: &re_viewer_context::ViewerContext<'_>,
        viewport: &re_viewport_blueprint::ViewportBlueprint,
        ui: &mut Ui,
    ) {
        let layers = super::ad_layers::catalog(&self.mcap_topic_list);
        let mut hitboxes = std::collections::BTreeMap::<String, [f32; 2]>::new();
        let mut changed = false;
        let mut controls = std::collections::BTreeMap::<String, [f32; 2]>::new();
        let mut rows = std::collections::BTreeMap::<String, serde_json::Value>::new();
        let mut panel_rect = None;
        let bounds = ui.available_rect_before_wrap();
        let width = (bounds.width() - 20.0).clamp(240.0, 320.0);
        egui::Area::new(egui::Id::new("ad_viewport_topics_picker"))
            .fixed_pos(bounds.min + Vec2::new(10.0, 31.0))
            .order(egui::Order::Foreground)
            .show(ui.ctx(), |ui| {
                source_style(ui);
                ui.spacing_mut().item_spacing.y = 0.0;
                let (rect, toggle) = ui.allocate_exact_size(Vec2::new(68.0, 28.0), Sense::click());
                ui.painter().rect(
                    rect,
                    5.0,
                    SOURCE_INPUT,
                    Stroke::new(1.0, SOURCE_BORDER),
                    StrokeKind::Inside,
                );
                ui.painter().text(
                    rect.left_center() + Vec2::new(9.0, 0.0),
                    egui::Align2::LEFT_CENTER,
                    "图层",
                    egui::FontId::proportional(14.0),
                    theme::TEXT,
                );
                paint_layer_arrow(ui, rect.right_center() - Vec2::new(14.0, 0.0), true);
                controls.insert("toggle".into(), [rect.center().x, rect.center().y]);
                if toggle.clicked() {
                    self.topics_picker_expanded = !self.topics_picker_expanded;
                }
                if !self.topics_picker_expanded {
                    return;
                }
                ui.add_space(10.0);
                let frame = egui::Frame::new()
                    .fill(SOURCE_BG)
                    .inner_margin(egui::Margin {
                        left: 16,
                        right: 16,
                        top: 14,
                        bottom: 10,
                    })
                    .corner_radius(6.0)
                    .stroke(Stroke::new(1.0, SOURCE_BORDER))
                    .show(ui, |ui| {
                        ui.set_width(width - 34.0);
                        // The collapsed Area remembers its 28 px height. Give the
                        // tree a screen-bounded content area when it opens again.
                        ui.set_max_height((ui.ctx().content_rect().height() - 120.0).max(150.0));
                        let (header, _) = ui.allocate_exact_size(
                            Vec2::new(ui.available_width(), 24.0),
                            Sense::hover(),
                        );
                        ui.painter().text(
                            header.left_center(),
                            egui::Align2::LEFT_CENTER,
                            "图层",
                            egui::FontId::proportional(16.0),
                            theme::TEXT,
                        );
                        let close_rect = Rect::from_center_size(
                            header.right_center() - Vec2::new(8.0, 0.0),
                            Vec2::splat(24.0),
                        );
                        let close =
                            ui.interact(close_rect, ui.id().with("close_layers"), Sense::click());
                        let c = close_rect.center();
                        for sign in [-1.0, 1.0] {
                            ui.painter().line_segment(
                                [
                                    c + Vec2::new(-5.0, -5.0 * sign),
                                    c + Vec2::new(5.0, 5.0 * sign),
                                ],
                                Stroke::new(1.2, SOURCE_MUTED),
                            );
                        }
                        controls.insert("close".into(), [c.x, c.y]);
                        if close.clicked() {
                            self.topics_picker_expanded = false;
                        }
                        ui.add_space(10.0);
                        let (toolbar, _) = ui.allocate_exact_size(
                            Vec2::new(ui.available_width(), 38.0),
                            Sense::hover(),
                        );
                        let search_rect =
                            Rect::from_min_max(toolbar.min, toolbar.max - Vec2::new(96.0, 0.0));
                        let search = layer_search_field(ui, search_rect, &mut self.layer_search);
                        controls.insert(
                            "search".into(),
                            [search.rect.center().x, search.rect.center().y],
                        );
                        for (index, key, label, value) in
                            [(0, "all", "全选", true), (1, "none", "清空", false)]
                        {
                            let r = Rect::from_min_size(
                                Pos2::new(
                                    toolbar.right() - 88.0 + index as f32 * 48.0,
                                    toolbar.top(),
                                ),
                                Vec2::new(40.0, 38.0),
                            );
                            let response = ui.interact(r, ui.id().with(key), Sense::click());
                            ui.painter().text(
                                r.center(),
                                egui::Align2::CENTER_CENTER,
                                label,
                                egui::FontId::proportional(14.0),
                                if response.hovered() {
                                    theme::TEXT
                                } else {
                                    SOURCE_MUTED
                                },
                            );
                            controls.insert(key.into(), [r.center().x, r.center().y]);
                            if response.clicked() {
                                for layer in &layers {
                                    for topic in &layer.topics {
                                        self.playback_topic_enabled.insert(topic.clone(), value);
                                    }
                                }
                                changed = true;
                            }
                        }
                        ui.add_space(8.0);
                        let max_height = (ui.ctx().content_rect().bottom() - bounds.top() - 180.0)
                            .clamp(60.0, 520.0);
                        egui::ScrollArea::vertical()
                            .id_salt("ad_layers_scroll")
                            .max_height(max_height)
                            .show(ui, |ui| {
                                let mut first = true;
                                for prefix in [
                                    "map",
                                    "sensing",
                                    "planning",
                                    "localization",
                                    "perception",
                                    "prediction",
                                ] {
                                    let visible = layers.iter().any(|l| {
                                        l.path.starts_with(&format!("{prefix}/"))
                                            && super::ad_layers::matches(l, &self.layer_search)
                                    });
                                    if visible {
                                        if !first {
                                            ui.add_space(4.0);
                                        }
                                        first = false;
                                        changed |= self.draw_layer_branch(
                                            ui,
                                            prefix,
                                            &layers,
                                            &mut hitboxes,
                                            &mut controls,
                                            &mut rows,
                                        );
                                    }
                                }
                                if first {
                                    ui.label(
                                        RichText::new(if layers.is_empty() {
                                            "打开录制文件后显示图层"
                                        } else {
                                            "没有匹配的图层"
                                        })
                                        .size(13.0)
                                        .color(SOURCE_MUTED),
                                    );
                                }
                            });
                        // Keep genuine empty geometry visible instead of making a
                        // checked layer with no current data look like a UI failure.
                        for layer in &layers {
                            if (layer.path.starts_with("prediction/")
                                || layer.path.starts_with("perception/")
                                || layer.path == "planning/trajectory")
                                && layer.topics.iter().any(|t| {
                                    self.playback_topic_enabled.get(t).copied().unwrap_or(false)
                                })
                            {
                                let component = if layer.path == "planning/trajectory" {
                                    re_sdk_types::archetypes::Mesh3D::descriptor_vertex_positions()
                                        .component
                                } else {
                                    re_sdk_types::archetypes::LineStrips3D::descriptor_strips()
                                        .component
                                };
                                let empty = layer.entities.iter().any(|entity| {
                                    ctx.store_context
                                        .recording
                                        .latest_at(
                                            &ctx.current_query(),
                                            &entity.as_str().into(),
                                            [component],
                                        )
                                        .component_batch_raw(component)
                                        .is_some_and(|batch| batch.is_empty())
                                });
                                if empty {
                                    ui.label(
                                        RichText::new(format!(
                                            "{}：当前无几何数据",
                                            super::ad_layers::label(&layer.path)
                                        ))
                                        .size(12.0)
                                        .color(SOURCE_MUTED),
                                    );
                                }
                            }
                        }
                        if let Some(error) = &self.playback_window_error {
                            ui.add_space(6.0);
                            ui.colored_label(Color32::LIGHT_RED, error);
                        }
                    });
                let r = frame.response.rect;
                panel_rect = Some([r.left(), r.top(), r.right(), r.bottom()]);
            });
        #[cfg(target_arch = "wasm32")]
        if changed {
            self.apply_playback_topic_selection(&ctx.app_ctx);
        }
        let _ = changed;
        use re_viewer_context::BlueprintContext as _;
        let state = layers.iter().map(|layer| {
            let enabled = layer.topics.iter().any(|t| self.playback_topic_enabled.get(t).copied().unwrap_or(false));
            serde_json::json!({"path":layer.path,"enabled":enabled,"topics":layer.topics,"entities":layer.entities,"checkbox":hitboxes.get(&layer.path)})
        }).collect::<Vec<_>>();
        let spatial = viewport
            .views
            .values()
            .filter(|view| matches!(view.class_identifier().as_str(), "3D" | "2D"))
            .collect::<Vec<_>>();
        let key = format!(
            "{:?}:{:?}:{:?}:{}",
            ctx.store_context.recording.store_id(),
            ctx.store_context.blueprint.store_id(),
            spatial.iter().map(|v| v.id).collect::<Vec<_>>(),
            serde_json::to_string(
                &layers
                    .iter()
                    .map(|l| (
                        &l.path,
                        l.topics.iter().any(|t| self
                            .playback_topic_enabled
                            .get(t)
                            .copied()
                            .unwrap_or(false))
                    ))
                    .collect::<Vec<_>>()
            )
            .expect("layer state")
        );
        if key != self.layer_visibility_key {
            self.layer_visibility_key = key;
            for view in &spatial {
                for layer in &layers {
                    let visible = layer
                        .topics
                        .iter()
                        .any(|t| self.playback_topic_enabled.get(t).copied().unwrap_or(false));
                    for entity in &layer.entities {
                        let path =
                            re_viewport_blueprint::ViewContents::base_override_path_for_entity(
                                view.id,
                                &entity.as_str().into(),
                            );
                        ctx.save_blueprint_archetype(
                            path,
                            &re_sdk_types::blueprint::archetypes::EntityBehavior::update_fields()
                                .with_visible(visible),
                        );
                    }
                }
            }
        }
        let rendered = spatial.iter().map(|view| {
            let query = ctx.lookup_query_result(view.id);
            serde_json::json!({"name":view.display_name,"entities":query.tree.iter_data_results().filter(|r|!r.visualizer_instructions.is_empty()).map(|r|
                serde_json::json!({"path":r.entity_path.to_string(),"visible":r.is_visible()})).collect::<Vec<_>>()})
        }).collect::<Vec<_>>();
        ctx.egui_ctx().data_mut(|d| {
            d.insert_temp(
                egui::Id::new("ad_layer_state"),
                serde_json::json!({"layers":state,"views":rendered,"nodes":hitboxes,"controls":controls,"rows":rows,"expanded":self.topics_picker_expanded,"filter":self.layer_search,"panel_rect":panel_rect}),
            )
        });
    }

    fn draw_layer_branch(
        &mut self,
        ui: &mut Ui,
        prefix: &str,
        layers: &[super::ad_layers::Layer],
        hitboxes: &mut std::collections::BTreeMap<String, [f32; 2]>,
        controls: &mut std::collections::BTreeMap<String, [f32; 2]>,
        rows: &mut std::collections::BTreeMap<String, serde_json::Value>,
    ) -> bool {
        let members = layers
            .iter()
            .filter(|l| l.path == prefix || l.path.starts_with(&format!("{prefix}/")))
            .collect::<Vec<_>>();
        if !members
            .iter()
            .any(|l| super::ad_layers::matches(l, &self.layer_search))
        {
            return false;
        }
        let enabled = members
            .iter()
            .filter(|l| {
                l.topics
                    .iter()
                    .any(|t| self.playback_topic_enabled.get(t).copied().unwrap_or(false))
            })
            .count();
        let on = enabled == members.len();
        let partial = enabled > 0 && !on;
        let branch = members.len() != 1 || members[0].path != prefix;
        let expanded =
            !self.collapsed_layers.contains(prefix) || !self.layer_search.trim().is_empty();
        let depth = prefix.matches('/').count() as f32;
        let (row, _) =
            ui.allocate_exact_size(Vec2::new(ui.available_width(), 28.0), Sense::hover());
        let check = Rect::from_center_size(
            row.left_center() + Vec2::new(28.0 + 20.0 * depth, 0.0),
            Vec2::splat(16.0),
        );
        let click_rect = Rect::from_min_max(Pos2::new(check.left() - 3.0, row.top()), row.max);
        let response = ui.interact(
            click_rect,
            ui.id().with(("layer_checkbox", prefix)),
            Sense::click(),
        );
        if response.hovered() {
            ui.painter()
                .rect_filled(click_rect, 4.0, Color32::from_rgb(37, 33, 52));
        }
        ui.painter().rect(
            check,
            4.0,
            if on || partial {
                Color32::from_rgb(163, 116, 246)
            } else {
                SOURCE_INPUT
            },
            Stroke::new(
                1.0,
                if on || partial {
                    theme::ACCENT
                } else {
                    SOURCE_BORDER
                },
            ),
            StrokeKind::Inside,
        );
        if on {
            ui.painter().add(egui::Shape::line(
                vec![
                    check.min + Vec2::new(3.5, 8.0),
                    check.min + Vec2::new(6.5, 11.0),
                    check.min + Vec2::new(12.5, 4.5),
                ],
                Stroke::new(1.8, Color32::WHITE),
            ));
        } else if partial {
            ui.painter().hline(
                (check.left() + 4.0)..=(check.right() - 4.0),
                check.center().y,
                Stroke::new(1.8, Color32::WHITE),
            );
        }
        let label = super::ad_layers::label(prefix);
        ui.painter().text(
            Pos2::new(check.right() + 11.0, row.center().y),
            egui::Align2::LEFT_CENTER,
            label,
            egui::FontId::proportional(14.0),
            theme::TEXT,
        );
        if ui.clip_rect().contains_rect(check) {
            hitboxes.insert(prefix.into(), [check.center().x, check.center().y]);
        }
        rows.insert(prefix.into(),serde_json::json!({"label":label,"rect":[row.left(),row.top(),row.right(),row.bottom()],"checked":on,"partial":partial,"expanded":expanded}));
        let mut changed = false;
        if response.clicked() {
            for layer in &members {
                for topic in &layer.topics {
                    self.playback_topic_enabled.insert(topic.clone(), !on);
                }
            }
            changed = true;
        }
        response.on_hover_text(
            members
                .iter()
                .flat_map(|l| l.topics.iter())
                .cloned()
                .collect::<Vec<_>>()
                .join("\n"),
        );
        if branch {
            let center = row.left_center() + Vec2::new(8.0 + 20.0 * depth, 0.0);
            paint_layer_arrow(ui, center, expanded);
            let response = ui.interact(
                Rect::from_center_size(center, Vec2::new(18.0, 28.0)),
                ui.id().with(("layer_collapse", prefix)),
                Sense::click(),
            );
            controls.insert(format!("collapse_{prefix}"), [center.x, center.y]);
            if response.clicked() && self.layer_search.trim().is_empty() {
                if expanded {
                    self.collapsed_layers.insert(prefix.into());
                } else {
                    self.collapsed_layers.remove(prefix);
                }
            }
            if expanded {
                let children = members
                    .iter()
                    .filter_map(|l| l.path.strip_prefix(&format!("{prefix}/")))
                    .map(|p| p.split('/').next().expect("layer child"))
                    .collect::<std::collections::BTreeSet<_>>();
                for child in children {
                    changed |= self.draw_layer_branch(
                        ui,
                        &format!("{prefix}/{child}"),
                        layers,
                        hitboxes,
                        controls,
                        rows,
                    );
                }
            }
        }
        changed
    }

    fn show_source_secondary(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        self.poll_source_catalog();
        if self.source_open {
            self.ensure_source_catalog(ctx);
        }
        let mut source_open_flag = self.source_open;
        let mut close = false;
        egui::Panel::left("ad_source_secondary_v2")
            .resizable(true)
            .drag_to_open(false)
            .default_size(432.0)
            .min_size(320.0)
            .max_size((ui.available_width() - 380.0).max(320.0))
            .frame(
                egui::Frame::new()
                    .fill(SOURCE_BG)
                    .inner_margin(egui::Margin {
                        left: 18,
                        right: 18,
                        top: 16,
                        bottom: 16,
                    })
                    .corner_radius(4.0)
                    .stroke(Stroke::new(1.0, SOURCE_BORDER)),
            )
            .show_collapsible(ui, &mut source_open_flag, |ui| {
                source_style(ui);
                ui.spacing_mut().item_spacing.y = 0.0;
                ui.ctx().data_mut(|d| {
                    d.insert_temp(
                        egui::Id::new("ad_source_ui"),
                        serde_json::json!({"controls":{},"properties":[],"open":true}),
                    )
                });
                source_control_rect(ui, "panel", ui.max_rect());
                ui.horizontal(|ui| {
                    source_label(
                        ui,
                        "数据源",
                        18.0,
                        true,
                        Vec2::new(ui.available_width() - 36.0, 24.0),
                    );
                    let more = re_ui::ad_panel_more_button(ui);
                    source_control_rect(ui, "more", more.rect);
                    egui::Popup::menu(&more)
                        .align(egui::RectAlign::BOTTOM_END)
                        .show(|ui| {
                            source_style(ui);
                            let path = if self.local_bag.is_empty() {
                                &self.open_local_path_draft
                            } else {
                                &self.local_bag
                            };
                            if ui
                                .add_enabled(
                                    !path.is_empty(),
                                    egui::Button::new("复制录制文件路径"),
                                )
                                .clicked()
                            {
                                ui.ctx().copy_text(path.clone());
                                ui.close();
                            }
                            if ui.button("刷新地图列表").clicked() {
                                self.source_catalog_at = None;
                                ui.close();
                            }
                            if ui.button("收起侧栏").clicked() {
                                close = true;
                                ui.close();
                            }
                        });
                });
                ui.add_space(16.0);
                let (line, _) =
                    ui.allocate_exact_size(Vec2::new(ui.available_width(), 1.0), Sense::hover());
                ui.painter().hline(
                    line.x_range(),
                    line.center().y,
                    Stroke::new(1.0, SOURCE_BORDER),
                );
                ui.add_space(16.0);
                egui::ScrollArea::vertical()
                    .id_salt("ad_source_scroll")
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        self.source_open_section(ctx, ui);
                        ui.add_space(24.0);
                        self.source_props_section(ctx, ui);
                    });
            });
        self.source_open = source_open_flag && !close;
        ui.ctx().data_mut(|d| {
            let key = egui::Id::new("ad_source_ui");
            if let Some(mut state) = d.get_temp::<serde_json::Value>(key) {
                state["open"] = serde_json::json!(self.source_open);
                if !self.source_open {
                    state["controls"] = serde_json::json!({});
                }
                d.insert_temp(key, state);
            }
        });
    }

    fn poll_source_catalog(&mut self) {
        let Some(pending) = &self.source_catalog_pending else {
            return;
        };
        let Some(result) = pending.lock().take() else {
            return;
        };
        self.source_catalog_pending = None;
        self.source_catalog_at = Some(web_time::Instant::now());
        match result {
            Ok(value) if value["status"] == "error" => {
                self.source_catalog_error = Some(
                    value["message"]
                        .as_str()
                        .unwrap_or("Invalid catalog response")
                        .into(),
                );
            }
            Ok(value) => {
                self.source_catalog_error = None;
                if let Some(catalog) = value.get("catalog") {
                    self.source_catalog = catalog.clone();
                } else {
                    self.source_catalog = value;
                }
                self.source_catalog_at = Some(web_time::Instant::now());
            }
            Err(err) => {
                re_log::warn!("Source catalog failed: {err}");
                self.source_catalog_error = Some(err);
            }
        }
    }

    fn ensure_source_catalog(&mut self, ctx: &AppContext<'_>) {
        if self.source_catalog_pending.is_some() {
            return;
        }
        let stale = self
            .source_catalog_at
            .is_none_or(|t| t.elapsed().as_secs() >= 30);
        if !stale {
            return;
        }
        #[cfg(target_arch = "wasm32")]
        {
            let reply = std::sync::Arc::new(parking_lot::Mutex::new(None));
            self.source_catalog_pending = Some(reply.clone());
            let egui = ctx.egui_ctx.clone();
            let Some(origin) = web_sys::window().and_then(|w| w.location().origin().ok()) else {
                self.source_catalog_pending = None;
                return;
            };
            let mut request = ehttp::Request::post(
                format!("{origin}/api/sim"),
                br#"{"action":"catalog"}"#.to_vec(),
            );
            request.headers.insert("Content-Type", "application/json");
            ehttp::fetch(request, move |result| {
                *reply.lock() = Some(result.and_then(|response| {
                    if !response.ok {
                        return Err(format!(
                            "catalog HTTP {}: {}",
                            response.status,
                            String::from_utf8_lossy(&response.bytes)
                        ));
                    }
                    serde_json::from_slice(&response.bytes)
                        .map_err(|e| format!("Invalid catalog: {e}"))
                }));
                egui.request_repaint();
            });
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            let _ = ctx;
        }
    }
    fn source_props_section(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        let tz = ctx.app_options.timestamp_format;
        let db = ctx.active_recording();
        if let Some(LogSource::File { path }) = db.and_then(|d| d.data_source.as_ref()) {
            if path.extension().and_then(|e| e.to_str()) != Some("rbl") {
                self.local_bag = path.display().to_string();
            }
        }
        source_label(
            ui,
            "数据概览",
            14.0,
            true,
            Vec2::new(ui.available_width(), 22.0),
        );
        ui.add_space(4.0);
        let app_id = db
            .map(|d| d.application_id().as_str().to_owned())
            .unwrap_or_default();
        let store_id = db
            .and_then(|d| d.store_info().map(|i| i.store_id.to_string()))
            .unwrap_or_default();
        let (start, end) = if let (Some(b), Some(e)) = (self.header_begin_ns, self.header_end_ns) {
            (format_source_time(b, tz), format_source_time(e, tz))
        } else {
            recording_time_bounds(ctx)
        };
        let duration = match (self.header_begin_ns, self.header_end_ns) {
            (Some(b), Some(e)) if e >= b => format!("{:.2} s", (e - b) as f64 / 1e9),
            _ => "—".into(),
        };
        // Apollo recordings contain original /apollo messages plus derived render
        // channels. Count original topics, not the generated map/vehicle geometry.
        let apollo = self
            .mcap_topic_list
            .iter()
            .filter(|t| t.starts_with("/apollo/"))
            .count();
        let topics = if self.mcap_topic_list_path.is_empty() {
            "—".into()
        } else if apollo > 0 {
            apollo.to_string()
        } else {
            self.mcap_topic_list.len().to_string()
        };
        let car = first_nonempty(&[
            &self.car_id,
            &extract_tagged(&app_id, "car"),
            &extract_tagged(&store_id, "car"),
        ]);
        let scenario = first_nonempty(&[
            &self.scenario_id,
            &extract_tagged(&app_id, "scenario"),
            &extract_tagged(&store_id, "scenario"),
        ]);
        let trip = first_nonempty(&[
            &self.trip_id,
            &extract_tagged(&app_id, "trip"),
            &extract_tagged(&store_id, "trip"),
        ]);
        for (label, value) in [
            ("时长", duration),
            ("Topic 数量", topics),
            ("起始时间", start),
            ("结束时间", end),
            ("车辆 ID", car),
            ("场景 ID", scenario),
            ("行程 ID", trip),
        ] {
            prop_row(ui, label, &value);
        }
    }

    fn source_open_section(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        self.source_open_mode = SourceOpenMode::Local;
        source_label(
            ui,
            "数据加载",
            14.0,
            true,
            Vec2::new(ui.available_width(), 20.0),
        );
        ui.add_space(8.0);
        let (row, _) =
            ui.allocate_exact_size(Vec2::new(ui.available_width(), 38.0), Sense::hover());
        ui.painter().text(
            row.left_center(),
            egui::Align2::LEFT_CENTER,
            "来源",
            egui::FontId::proportional(14.0),
            theme::TEXT,
        );
        let combo = Rect::from_min_max(row.min + Vec2::new(104.0, 0.0), row.max);
        source_mode_picker(
            &mut ui.new_child(egui::UiBuilder::new().max_rect(combo)),
            &mut self.source_open_mode,
        );
        ui.add_space(14.0);
        source_field_label(ui, "录制文件");
        let path = if !self.open_local_path_draft.is_empty() {
            &self.open_local_path_draft
        } else {
            &self.local_bag
        };
        let bag_label = if path.is_empty() {
            "选择录制文件…"
        } else {
            path.rsplit('/').next().unwrap_or(path)
        };
        let choose = source_choose_trigger(ui, bag_label)
            .on_hover_text(format!("{path}\n{}", self.open_status_msg));
        source_control_rect(ui, "Choose a bag", choose.rect);
        #[cfg(target_arch = "wasm32")]
        if choose.clicked() {
            crate::web_tools::pick_local_recording_files(
                ctx.egui_ctx.clone(),
                self.open_map_path_draft.clone(),
            );
        }
        #[cfg(not(target_arch = "wasm32"))]
        if choose.clicked() {
            self.open_status_msg = "请在网页端选择本地录制文件。".into();
        }
        let _ = ctx;
        ui.add_space(16.0);
        source_field_label(ui, "地图");
        if let Some(error) = &self.source_catalog_error {
            ui.colored_label(Color32::LIGHT_RED, format!("地图列表加载失败：{error}"));
        }
        let maps = self.source_catalog.get("maps").cloned().unwrap_or_default();
        source_path_picker(
            ui,
            "source_map",
            &mut self.open_map_path_draft,
            &maps,
            true,
            "不叠加地图",
        );
        // Routine load progress is shown in the existing progress modal; failures
        // remain explicit here and full status is available on the file tooltip.
        let status = self.open_status_msg.to_lowercase();
        if [
            "error",
            "failed",
            "invalid",
            "cannot",
            "unsupported",
            "失败",
            "错误",
            "请在",
        ]
        .iter()
        .any(|s| status.contains(s))
        {
            ui.add_space(8.0);
            ui.colored_label(Color32::LIGHT_RED, &self.open_status_msg);
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn take_host_open_status(&mut self, ctx: &AppContext<'_>) {
        if let Some(path) = crate::web_tools::take_uploaded_bag_path() {
            self.open_local_path_draft = path.clone();
            self.local_bag = path;
        }
        let Some(msg) = crate::web_tools::take_open_local_status() else {
            return;
        };
        // Cached MCAP was deleted after a prior convert — re-run conversion instead of sticking on 400.
        let cache_miss = msg.contains("file not found")
            && (msg.contains(".wm_mcap_cache") || msg.contains(".mcap"));
        let record = self.local_bag.clone();
        let is_record = {
            let lower = record.to_ascii_lowercase();
            lower.contains(".record") && !lower.ends_with(".rrd") && !lower.ends_with(".rbl")
        };
        if cache_miss && is_record {
            re_log::warn!("Cached MCAP missing after convert; re-converting {record}");
            self.convert_opened_output = None;
            self.convert_job_id = None;
            self.layout_bound_store_id = None;
            self.playback_clock_ready = false;
            self.mcap_topic_list.clear();
            self.open_status_msg = format!("Cached MCAP missing — reconverting…\n{record}");
            seed_convert_progress_modal(&record, "Cached MCAP missing — reconverting…");
            crate::web_tools::request_host_convert_record_with_map(
                &record,
                self.open_map_path_draft.trim(),
                ctx.egui_ctx.clone(),
            );
            return;
        }
        self.open_status_msg = msg;
        // Non-record opens finish without a convert job — dismiss the modal once
        // the host reports loaded / streamed / error after upload.
        let lower = self.open_status_msg.to_ascii_lowercase();
        if lower.starts_with("loaded ")
            || lower.contains("host streaming")
            || lower.contains("preparing windowed")
        {
            // Keep modal briefly for "preparing"; clear on loaded/streaming.
            if lower.starts_with("loaded ") || lower.contains("host streaming") {
                crate::web_tools::clear_upload_progress();
            }
        } else if lower.contains("upload failed")
            || lower.contains("upload request failed")
            || lower.contains("unsupported file")
            || lower.contains("failed to read")
        {
            // Error already mirrored into upload progress by web_tools.
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn show_upload_progress_modal(&mut self, ctx: &AppContext<'_>) {
        let Some(progress) = crate::web_tools::peek_upload_progress() else {
            return;
        };
        if !progress.active && progress.phase != "error" {
            return;
        }

        let title = match progress.phase.as_str() {
            "reading" => "Reading bag",
            "uploading" => "Uploading bag",
            "opening" => "Opening bag",
            "converting" => "Converting to MCAP",
            "error" => "Failed",
            _ => "Bag transfer",
        };
        let is_error = progress.phase == "error";
        let fraction = if is_error {
            0.0
        } else if progress.total > 0 || progress.fraction > 0.0 {
            progress.fraction.clamp(0.0, 1.0)
        } else {
            // Indeterminate: animate a soft pulse while waiting.
            let t = ctx.egui_ctx.input(|i| i.time) as f32;
            0.15 + 0.35 * (t * 2.5).sin().abs()
        };
        let show_pct = !is_error
            && (progress.phase == "converting"
                || (progress.total > 0 && progress.phase != "opening"));

        ctx.egui_ctx
            .request_repaint_after(std::time::Duration::from_millis(50));

        egui::Window::new(title)
            .id(egui::Id::new("ad_upload_progress_modal"))
            .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
            .collapsible(false)
            .resizable(false)
            .title_bar(true)
            .frame(
                egui::Frame::new()
                    .fill(theme::PANEL_BG)
                    .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.45)))
                    .corner_radius(10.0)
                    .inner_margin(egui::Margin::symmetric(16, 14))
                    .shadow(egui::Shadow {
                        offset: [0, 8],
                        blur: 24,
                        spread: 0,
                        color: Color32::from_black_alpha(160),
                    }),
            )
            .show(ctx.egui_ctx, |ui| {
                ui.set_min_width(420.0);
                ui.set_max_width(480.0);
                ui.visuals_mut().override_text_color = Some(theme::TEXT);

                if !progress.filename.is_empty() {
                    ui.label(
                        RichText::new(&progress.filename)
                            .size(13.0)
                            .strong()
                            .color(theme::TEXT),
                    );
                    ui.add_space(4.0);
                }
                ui.label(
                    RichText::new(sanitize_status_text(&progress.message))
                        .size(12.0)
                        .color(if is_error {
                            Color32::from_rgb(0xFE, 0xCA, 0xCA)
                        } else {
                            theme::TEXT_DIM
                        }),
                );
                ui.add_space(12.0);

                let mut bar = egui::ProgressBar::new(fraction)
                    .desired_width(ui.available_width())
                    .fill(if is_error {
                        Color32::from_rgb(0xF8, 0x71, 0x71)
                    } else {
                        theme::ACCENT_STRONG.gamma_multiply(0.9)
                    });
                if show_pct {
                    bar = bar.show_percentage();
                }
                ui.add(bar);

                if progress.total > 0 && matches!(progress.phase.as_str(), "reading" | "uploading")
                {
                    ui.add_space(6.0);
                    ui.label(
                        RichText::new(format!(
                            "{} / {}",
                            format_upload_bytes(progress.loaded),
                            format_upload_bytes(progress.total)
                        ))
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                    );
                }

                if is_error {
                    ui.add_space(12.0);
                    let dismiss = ui.add(
                        egui::Button::new(
                            RichText::new("Dismiss").size(12.0).color(Color32::WHITE),
                        )
                        .fill(theme::ACCENT_STRONG)
                        .corner_radius(6.0)
                        .min_size(egui::vec2(ui.available_width(), 32.0)),
                    );
                    if dismiss.clicked() {
                        crate::web_tools::clear_upload_progress();
                    }
                }
            });
    }

    #[cfg(target_arch = "wasm32")]
    fn take_pending_mcap_browse(&mut self, ctx: &AppContext<'_>) {
        let Some(win) = web_sys::window() else {
            return;
        };
        let Some(store) = win.session_storage().ok().flatten() else {
            return;
        };
        let Ok(Some(path)) = store.get_item("wm_pending_mcap") else {
            return;
        };
        let _ = store.remove_item("wm_pending_mcap");
        if path.is_empty() {
            return;
        }
        self.begin_windowed_mcap_session(ctx, &path, /*fetch_topics=*/ true);
    }

    #[cfg(target_arch = "wasm32")]
    fn take_mcap_topic_list(&mut self, ctx: &AppContext<'_>) {
        let Some(body) = crate::web_tools::take_mcap_topics_json() else {
            return;
        };
        if json_field(&body, "requested_path").is_some_and(|path| path != self.playback_mcap_path) {
            return;
        }
        if let Some(error) = json_field(&body, "error") {
            self.playback_window_error = Some(error);
            return;
        }
        let parsed = serde_json::from_str::<serde_json::Value>(&body).and_then(|v| {
            serde_json::from_value::<super::ad_playback::RecordingSource>(v["source"].clone())
        });
        let source = match parsed {
            Ok(source) => source,
            Err(error) => {
                self.playback_window_error = Some(format!(
                    "Missing/invalid recording source identity: {error}"
                ));
                return;
            }
        };
        if let Err(error) = source.validate() {
            self.playback_window_error = Some(error);
            return;
        }
        if self
            .playback_recovery
            .as_ref()
            .is_some_and(|b| !b.source.same_file(&source))
        {
            self.playback_window_error = Some("Recording source changed on disk; recovery refused to mix cached geometry with another file. Open the intended source explicitly.".into());
            return;
        }
        self.playback_source = Some(source);
        if let Some(path) = json_field(&body, "path") {
            self.mcap_topic_list_path = path.clone();
            if self.playback_mcap_path.is_empty() || self.playback_mcap_path != path {
                self.playback_mcap_path = path;
            }
        }
        if let Some(v) = json_i64(&body, "begin_ns") {
            if v > 0 && self.header_begin_ns.is_none() {
                self.header_begin_ns = Some(v);
            }
        }
        if let Some(v) = json_i64(&body, "end_ns") {
            if v > 0 && self.header_end_ns.is_none() {
                self.header_end_ns = Some(v);
            }
        }
        if let Some(topics) = json_string_array(&body, "topics") {
            re_log::info!(
                "Panel: cached {} MCAP summary topics from {}",
                topics.len(),
                self.mcap_topic_list_path
            );
            self.mcap_topic_list = topics;
            self.seed_playback_topic_defaults();
            if let Some(bookmark) = &self.playback_recovery {
                for (topic, enabled) in &mut self.playback_topic_enabled {
                    *enabled = bookmark.layers.get(topic).copied().unwrap_or(false);
                }
            }
            if ctx
                .egui_ctx
                .data_mut(|d| d.remove_temp::<bool>(egui::Id::new("ad_sim_replay_layers")))
                .unwrap_or(false)
            {
                for layer in super::ad_layers::catalog(&self.mcap_topic_list) {
                    if layer.path.starts_with("planning/")
                        || layer.path.starts_with("perception/")
                        || layer.path.starts_with("prediction/")
                    {
                        for topic in layer.topics {
                            self.playback_topic_enabled.insert(topic, true);
                        }
                    }
                }
            }
            // Open the viewport Topics control — do not hijack the Panel drawer.
            self.topics_picker_expanded = true;
            self.request_playback_from_start(ctx, /*reset=*/ true);
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn take_playback_window_status(&mut self, ctx: &AppContext<'_>) {
        let Some(body) = self
            .playback_waiting_receipt
            .take()
            .or_else(crate::web_tools::take_playback_window_json)
        else {
            return;
        };
        if json_field(&body, "path").is_some_and(|path| path != self.playback_mcap_path) {
            // An earlier bag's async response cannot initialize the new session.
            return;
        }
        let receipt = if json_field(&body, "status").as_deref() == Some("ok") {
            match super::ad_playback::WindowReceipt::parse(&body) {
                Ok(receipt) => Some(receipt),
                Err(err) => {
                    self.playback_window_pending = false;
                    self.playback_window_error = Some(format!("Invalid playback response: {err}"));
                    return;
                }
            }
        } else {
            None
        };
        if let Some(receipt) = &receipt {
            // A persisted route or a blueprint can still select the workspace.
            // Locate this request's unique receipt in ALL received recordings,
            // then activate its owner; never wait for data in the empty workspace.
            let owner = ctx
                .store_bundle()
                .recordings()
                .find(|rec| {
                    rec.sorted_entity_paths()
                        .any(|path| path.to_string() == receipt.receipt)
                })
                .map(|rec| rec.store_id().clone());
            if owner.is_some() {
                self.playback_recording = owner.clone();
            }
            if let Some(owner) = owner.as_ref().filter(|owner| {
                ctx.active_recording()
                    .is_none_or(|rec| rec.store_id() != *owner)
            }) {
                ctx.command_sender().send_system(SystemCommand::SetRoute(
                    re_viewer_context::Route::LocalRecording {
                        recording_id: owner.clone(),
                    },
                ));
                self.playback_waiting_receipt = Some(body);
                ctx.egui_ctx.request_repaint();
                return;
            }
            if owner.is_none() {
                self.playback_waiting_receipt = Some(body);
                ctx.egui_ctx
                    .request_repaint_after(std::time::Duration::from_millis(50));
                return;
            }
        }
        self.playback_window_pending = false;
        if let Some(receipt) = receipt {
            let reset = receipt.reset;
            if let Some(end) = json_i64(&body, "end_ns") {
                if reset {
                    // Topic-set reset replaces prior windows — do not keep old loaded_end.
                    self.playback_loaded_end_ns = Some(end);
                } else {
                    self.playback_loaded_end_ns = Some(
                        self.playback_loaded_end_ns
                            .map(|prev| prev.max(end))
                            .unwrap_or(end),
                    );
                }
            }
            self.playback_window_error = None;
            let begin = receipt.begin_ns;
            let end = receipt.end_ns;
            let (cache_begin, _) = receipt.cached_range();
            if end > cache_begin && !self.playback_topics_dirty {
                self.playback_cached_ranges.push((cache_begin, end));
                self.playback_cached_ranges.sort_unstable();
                let mut merged: Vec<(i64, i64)> = Vec::new();
                for &(b, e) in &self.playback_cached_ranges {
                    if let Some(last) = merged.last_mut().filter(|last| b <= last.1) {
                        last.1 = last.1.max(e);
                    } else {
                        merged.push((b, e));
                    }
                }
                self.playback_cached_ranges = merged;
            }
            ctx.egui_ctx.data_mut(|d| {
                d.insert_temp(
                    egui::Id::new("web_monitor_cached_ranges_ns"),
                    self.playback_cached_ranges.clone(),
                )
            });
            if reset {
                self.playback_buffering = false;
                self.playback_resume_ns = Some(receipt.seek_ns);
                self.playback_initial_seek = Some(receipt.seek_ns);
                self.playback_resume_was_playing = false;
                self.playback_clock_ready = false;
            }
            // Decoders must run while paused too; arrival is an explicit repaint.
            ctx.egui_ctx.request_repaint();
            self.open_status_msg = format!(
                "Loaded window [{begin}, {end}) — {} topics",
                self.enabled_playback_topics().len()
            );
        } else {
            let msg = json_field(&body, "message")
                .unwrap_or_else(|| format!("playback_window error: {body}"));
            if msg.contains("busy") {
                // Host still importing prior window — retry prefetch / dirty apply next frames.
                self.playback_window_error = None;
                self.playback_topics_dirty = true;
            } else {
                self.playback_window_error = Some(msg.clone());
                self.open_status_msg = format!("Window load failed: {msg}");
                re_log::error!("playback_window: {msg}");
            }
        }
        if self.playback_topics_dirty {
            self.apply_playback_topic_selection(ctx);
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn note_playback_playhead(&mut self, ctx: &AppContext<'_>) {
        let (Some(b), Some(e)) = (self.header_begin_ns, self.header_end_ns) else {
            return;
        };
        if e <= b {
            return;
        }
        let Some(tc) = ctx.active_time_ctrl() else {
            return;
        };
        let Some(t) = tc.time_int().map(|t| t.as_i64()) else {
            return;
        };
        if t >= b && t <= e {
            self.playback_last_playhead_ns = Some(t);
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn seed_playback_topic_defaults(&mut self) {
        for layer in super::ad_layers::catalog(&self.mcap_topic_list) {
            for topic in layer.topics {
                self.playback_topic_enabled
                    .entry(topic)
                    .or_insert(layer.default_on);
            }
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn enabled_playback_topics(&self) -> Vec<String> {
        let mut topics = super::ad_layers::catalog(&self.mcap_topic_list)
            .into_iter()
            .flat_map(|layer| layer.topics)
            .filter(|t| self.playback_topic_enabled.get(t).copied().unwrap_or(false))
            .collect::<std::collections::BTreeSet<_>>();
        // Coordinate dependencies can stay loaded while their drawings are hidden.
        if !topics.is_empty() {
            for dependency in ["/vehicle", "/tf", "/tf_static", "/foxglove/tf"] {
                if self.mcap_topic_list.iter().any(|t| t == dependency) {
                    topics.insert(dependency.to_owned());
                }
            }
        }
        topics.into_iter().collect()
    }

    #[cfg(target_arch = "wasm32")]
    fn begin_windowed_mcap_session(
        &mut self,
        ctx: &AppContext<'_>,
        path: &str,
        fetch_topics: bool,
    ) {
        self.playback_restore_checked = true;
        self.playback_source = None;
        self.playback_recovery = None;
        self.header_begin_ns = None;
        self.header_end_ns = None;
        self.pending_layout_clock = None;
        self.playback_layout_pending = None;
        self.local_bag = path.to_owned();
        self.convert_opened_output = Some(path.to_owned());
        self.playback_mcap_path = path.to_owned();
        self.playback_loaded_end_ns = None;
        self.playback_window_pending = false;
        self.playback_window_error = None;
        self.playback_waiting_receipt = None;
        self.playback_recording = None;
        self.playback_initial_seek = None;
        self.playback_cached_ranges.clear();
        self.playback_buffering = false;
        self.playback_request_started = None;
        ctx.egui_ctx.data_mut(|d| {
            d.remove::<Vec<(i64, i64)>>(egui::Id::new("web_monitor_cached_ranges_ns"))
        });
        self.playback_resume_ns = None;
        self.playback_resume_was_playing = false;
        self.playback_last_playhead_ns = None;
        self.playback_topics_dirty = false;
        self.playback_enabled_snapshot.clear();
        self.layout_bound_store_id = None;
        self.playback_clock_ready = false;
        self.mcap_topic_list.clear();
        self.playback_topic_enabled.clear();
        self.open_status_msg = format!("Windowed playback — fetching topics for {path}…");
        self.topics_picker_expanded = true;
        crate::web_tools::clear_upload_progress();
        if fetch_topics {
            crate::web_tools::request_host_mcap_topics(path, ctx.egui_ctx.clone());
        }
    }

    /// Apply topic checkboxes at the playhead without wiping the recording.
    ///
    /// Important: do **not** `reset=true` here. A new RecordingId drops already-loaded
    /// camera pinhole/calib (often only present near bag begin), so Spatial2D views go blank
    /// when the user toggles pose/points. Additive merge keeps cameras alive.
    #[cfg(target_arch = "wasm32")]
    fn apply_playback_topic_selection(&mut self, ctx: &AppContext<'_>) {
        if self.playback_mcap_path.is_empty() && self.convert_opened_output.is_none() {
            self.playback_window_error = Some("No MCAP path — open a bag first".into());
            return;
        }
        if self.playback_window_pending {
            self.playback_topics_dirty = true;
            return;
        }
        self.playback_topics_dirty = false;

        let Some(bag_begin) = self.header_begin_ns else {
            self.playback_window_error =
                Some("No begin_ns — wait for MCAP summary / convert meta".into());
            return;
        };
        let Some(bag_end) = self.header_end_ns else {
            self.playback_window_error =
                Some("No end_ns — wait for MCAP summary / convert meta".into());
            return;
        };
        if bag_end <= bag_begin {
            self.playback_window_error = Some(format!(
                "Invalid header range begin={bag_begin} end={bag_end}"
            ));
            return;
        }

        let enabled_now = self.enabled_playback_topics();
        let prev = self.playback_enabled_snapshot.clone();
        let added: Vec<String> = enabled_now
            .iter()
            .filter(|t| !prev.iter().any(|p| p == *t))
            .cloned()
            .collect();
        let removed: Vec<String> = prev
            .iter()
            .filter(|t| !enabled_now.iter().any(|n| n == *t))
            .cloned()
            .collect();
        self.playback_enabled_snapshot = enabled_now.clone();
        if !added.is_empty() || !removed.is_empty() {
            self.playback_cached_ranges.clear();
            ctx.egui_ctx.data_mut(|d| {
                d.remove::<Vec<(i64, i64)>>(egui::Id::new("web_monitor_cached_ranges_ns"))
            });
        }

        if added.is_empty() {
            if !removed.is_empty() {
                // Cannot surgically unload entities without a new recording (which kills cameras).
                self.open_status_msg = format!(
                    "Unchecked {} topic(s) — no new data will load for them (reopen bag to fully drop).",
                    removed.len()
                );
                self.playback_window_error = None;
            }
            return;
        }

        let (playhead, was_playing) = self.current_playback_playhead(ctx, bag_begin, bag_end);
        // Keep playhead stable; do not force a store swap.
        self.playback_resume_ns = Some(playhead);
        self.playback_resume_was_playing = was_playing;
        self.playback_last_playhead_ns = Some(playhead);

        let win_begin = playhead.saturating_sub(PLAYBACK_LOOKBACK_NS).max(bag_begin);
        let mut win_end = (playhead + PLAYBACK_INITIAL_WINDOW_NS).min(bag_end);
        if win_end <= win_begin {
            win_end = bag_end.max(win_begin + 1);
        }

        // The host resolves the preceding IDR from its index for each camera.

        re_log::info!(
            "Topics enabled → merge window [{win_begin}, {win_end}) resume={playhead} added={}",
            added.len()
        );
        // Do not rewind loaded_end on merge — prefetch should continue forward.
        self.request_playback_window_for_topics(
            ctx,
            win_begin,
            win_end,
            /*reset=*/ false,
            &enabled_now,
        );
    }

    #[cfg(target_arch = "wasm32")]
    fn current_playback_playhead(
        &self,
        ctx: &AppContext<'_>,
        bag_begin: i64,
        bag_end: i64,
    ) -> (i64, bool) {
        let was_playing = ctx
            .active_time_ctrl()
            .map(|tc| matches!(tc.play_state(), PlayState::Playing | PlayState::Following))
            .unwrap_or(false);
        if let Some(tc) = ctx.active_time_ctrl() {
            if let Some(t) = tc.time_int().map(|t| t.as_i64()) {
                if t >= bag_begin && t <= bag_end {
                    return (t, was_playing);
                }
            }
        }
        if let Some(t) = self.playback_last_playhead_ns {
            if t >= bag_begin && t <= bag_end {
                return (t, was_playing);
            }
        }
        if let Some(t) = self.playback_resume_ns {
            if t >= bag_begin && t <= bag_end {
                return (t, was_playing);
            }
        }
        (bag_begin, was_playing)
    }

    #[cfg(target_arch = "wasm32")]
    fn request_playback_from_start(&mut self, ctx: &AppContext<'_>, reset: bool) {
        let Some(bag_begin) = self.header_begin_ns else {
            self.playback_window_error =
                Some("No begin_ns — wait for MCAP summary / convert meta".into());
            return;
        };
        let Some(bag_end) = self.header_end_ns else {
            self.playback_window_error =
                Some("No end_ns — wait for MCAP summary / convert meta".into());
            return;
        };
        if bag_end <= bag_begin {
            self.playback_window_error = Some(format!(
                "Invalid header range begin={bag_begin} end={bag_end}"
            ));
            return;
        }
        // Initial open: seek to first loaded sample (ensure_playback_clock waits for chunks).
        let begin = self.playback_recovery.as_ref().map_or(bag_begin, |b| {
            b.time_ns.clamp(bag_begin, bag_end.saturating_sub(1))
        });
        self.playback_resume_ns = None;
        self.playback_resume_was_playing = false;
        self.playback_last_playhead_ns = Some(begin);
        let win_end = (begin + PLAYBACK_INITIAL_WINDOW_NS).min(bag_end);
        // Inclusive MCAP stats end_time → half-open window needs +1 when equal.
        let win_end = if win_end <= begin {
            bag_end.max(begin + 1)
        } else {
            win_end
        };
        self.playback_loaded_end_ns = Some(begin);
        let topics = self.enabled_playback_topics();
        self.playback_enabled_snapshot = topics.clone();
        self.request_playback_window_for_topics(ctx, begin, win_end, reset, &topics);
    }

    #[cfg(target_arch = "wasm32")]
    fn request_playback_window(
        &mut self,
        ctx: &AppContext<'_>,
        begin_ns: i64,
        end_ns: i64,
        reset: bool,
    ) {
        let topics = self.enabled_playback_topics();
        self.request_playback_window_for_topics(ctx, begin_ns, end_ns, reset, &topics);
    }

    #[cfg(target_arch = "wasm32")]
    fn request_playback_window_for_topics(
        &mut self,
        ctx: &AppContext<'_>,
        begin_ns: i64,
        end_ns: i64,
        reset: bool,
        topics: &[String],
    ) {
        if self.playback_window_pending {
            return;
        }
        if reset {
            self.playback_recording = None;
            self.playback_initial_seek = None;
            self.playback_clock_ready = false;
            self.playback_cached_ranges.clear();
            self.playback_buffering = false;
            self.pending_layout_clock = None;
            if let Err(error) = self.ensure_playback_connection(ctx) {
                self.playback_window_error = Some(error);
                return;
            }
        }
        let mcap = if !self.playback_mcap_path.is_empty() {
            self.playback_mcap_path.clone()
        } else if let Some(out) = self.convert_opened_output.clone() {
            out
        } else {
            self.playback_window_error = Some("No MCAP path for playback_window".into());
            return;
        };
        if topics.is_empty() && !reset {
            self.playback_window_error =
                Some("No topics enabled — check topics in the Topics picker".into());
            self.open_status_msg = "No topics enabled — nothing loaded.".into();
            return;
        }
        if end_ns <= begin_ns {
            self.playback_window_error = Some(format!("Invalid window [{begin_ns}, {end_ns})"));
            return;
        }
        self.playback_mcap_path = mcap.clone();
        self.playback_window_pending = true;
        self.playback_request_started = Some(web_time::Instant::now());
        self.playback_window_error = None;
        self.open_status_msg = format!(
            "Loading window [{begin_ns}, {end_ns}) — {} topics…",
            topics.len()
        );
        crate::web_tools::request_host_playback_window(
            &mcap,
            begin_ns,
            end_ns,
            reset,
            topics,
            ctx.egui_ctx.clone(),
        );
    }

    #[cfg(target_arch = "wasm32")]
    fn ensure_playback_connection(&self, ctx: &AppContext<'_>) -> Result<(), String> {
        let endpoint = crate::web_tools::playback_proxy_url()?;
        let Some(LogDataSource::RedapProxy(uri)) =
            LogDataSource::from_uri(FileSource::Uri, &endpoint, &Default::default())
        else {
            return Err(format!("Invalid playback proxy: {endpoint}"));
        };
        let source = LogSource::MessageProxy(uri.clone());
        if !ctx
            .connected_receivers
            .sources()
            .iter()
            .any(|current| **current == source)
        {
            re_log::info!("Connecting host playback stream: {endpoint}");
            ctx.command_sender()
                .send_system(SystemCommand::LoadDataSource(LogDataSource::RedapProxy(
                    uri,
                )));
        }
        Ok(())
    }

    #[cfg(target_arch = "wasm32")]
    fn show_playback_status(&mut self, ctx: &AppContext<'_>) {
        let endpoint = crate::web_tools::playback_proxy_url();
        if self.playback_window_pending {
            if let Ok(endpoint) = &endpoint {
                if let Some(LogDataSource::RedapProxy(uri)) =
                    LogDataSource::from_uri(FileSource::Uri, endpoint, &Default::default())
                {
                    if let Some(error) = ctx.last_loading_error_for(&LogSource::MessageProxy(uri)) {
                        self.playback_window_error =
                            Some(format!("Playback stream disconnected: {error}\n{endpoint}"));
                        self.playback_window_pending = false;
                        self.playback_waiting_receipt = None;
                    }
                }
            }
        }
        let status = serde_json::json!({
            "mcap": self.playback_mcap_path,
            "clock_ready": self.indexed_source_ready(ctx),
            "source": self.playback_source,
            "recovering": self.playback_recovery.is_some(),
            "initial_seek": self.playback_initial_seek.map(|time| time.to_string()),
            "recording": self.playback_recording.as_ref().map(|store| store.recording_id().as_str()),
            "pending": self.playback_window_pending,
            "waiting_receipt": self.playback_waiting_receipt.is_some(),
            "error": self.playback_window_error,
            "proxy": endpoint.as_ref().ok(),
            "receivers": ctx.connected_receivers.sources().iter().map(|source| source.to_string()).collect::<Vec<_>>(),
            "recordings": ctx.store_bundle().recordings().map(|rec| serde_json::json!({
                "id":rec.store_id().recording_id().as_str(),
                "timelines":rec.timelines().keys().map(|t| t.as_str()).collect::<Vec<_>>(),
            })).collect::<Vec<_>>(),
        });
        ctx.egui_ctx
            .data_mut(|d| d.insert_temp(egui::Id::new("ad_playback_state"), status));
        if (self.playback_mcap_path.is_empty() && self.playback_window_error.is_none())
            || (self.playback_clock_ready
                && self.playback_initial_seek.is_none()
                && self.playback_window_error.is_none())
        {
            return;
        }
        let error = self.playback_window_error.clone();
        egui::Window::new("Playback connection")
            .id(egui::Id::new("ad_playback_connection_notice"))
            .anchor(egui::Align2::CENTER_TOP, [0.0, 48.0])
            .collapsible(false)
            .resizable(false)
            .show(ctx.egui_ctx, |ui| {
                ui.set_max_width(540.0);
                if let Some(error) = error {
                    ui.colored_label(Color32::LIGHT_RED, error);
                    let retry = ui.button("Reconnect and retry playback");
                    ctx.egui_ctx.data_mut(|d| {
                        d.insert_temp(egui::Id::new("ad_playback_retry"), retry.rect.center())
                    });
                    if retry.clicked() {
                        self.playback_window_error = None;
                        if self.playback_source.is_none() {
                            crate::web_tools::request_host_mcap_topics(
                                &self.playback_mcap_path,
                                ctx.egui_ctx.clone(),
                            );
                        } else {
                            self.request_playback_from_start(ctx, true);
                        }
                    }
                } else {
                    ui.label("Waiting for recording data from the playback stream…");
                    if let Ok(endpoint) = endpoint {
                        ui.monospace(endpoint);
                    }
                }
            });
    }

    #[cfg(target_arch = "wasm32")]
    fn ensure_playback_prefetch(&mut self, ctx: &AppContext<'_>) {
        if ctx
            .egui_ctx
            .data_mut(|d| d.remove_temp::<bool>(egui::Id::new("web_monitor_cancel_buffer_resume")))
            .unwrap_or(false)
        {
            self.playback_buffering = false;
        }
        if self.playback_mcap_path.is_empty() {
            return;
        }
        // SetTime/Pause are queued commands. Reading the controller in the same
        // frame still sees its initial Following state at the loaded range end.
        // Do not interpret that stale state as a user Play request and arm
        // buffer auto-resume for a newly opened recording.
        if let Some(seek) = self.playback_initial_seek {
            if self.playback_layout_pending.is_some() {
                ctx.egui_ctx.request_repaint();
                return;
            }
            let clock = self
                .playback_recovery
                .as_ref()
                .map_or("publish_time", |b| b.clock.as_str());
            let applied = ctx.active_time_ctrl().is_some_and(|tc| {
                tc.time_int().is_some_and(|time| time.as_i64() == seek)
                    && tc.play_state() == PlayState::Paused
                    && tc.timeline_name().as_str() == clock
            });
            if !applied {
                // Blueprint activation can replace the time controller AFTER
                // the first queued seek. Complete this transition against the
                // receipt owner, not merely after issuing commands once.
                if self
                    .playback_recording
                    .as_ref()
                    .is_some_and(|id| ctx.active_recording().is_some_and(|r| r.store_id() == id))
                {
                    ctx.send_time_commands_to_active_recording(vec![
                        TimeControlCommand::SetActiveTimeline(
                            TimelineName::try_new(clock).expect("validated playback clock"),
                        ),
                        TimeControlCommand::SetTime(TimeInt::new_temporal(seek).into()),
                        TimeControlCommand::Pause,
                    ]);
                }
                ctx.egui_ctx.request_repaint();
                return;
            }
            self.playback_initial_seek = None;
            self.playback_recovery = None;
        }
        // Until the initial seek is applied, the time controller can still
        // belong to the workspace/previous recording and report Playing.
        // It must not arm automatic resume for a newly opened, paused bag.
        if self.playback_clock_ready
            && !self.playback_cached_ranges.is_empty()
            && let Some(tc) = ctx.active_time_ctrl()
        {
            if let Some(t) = tc.time_int().map(|t| t.as_i64()) {
                let available = self
                    .playback_cached_ranges
                    .iter()
                    .find(|&&(b, e)| b <= t && t < e)
                    .map(|&(_, e)| e);
                let playing = matches!(tc.play_state(), PlayState::Playing | PlayState::Following);
                let near_end = self
                    .header_end_ns
                    .is_some_and(|end| t >= end.saturating_sub(1));
                if playing
                    && !near_end
                    && available.is_none_or(|end| {
                        end - t < 100_000_000
                            && self.header_end_ns.is_some_and(|bag_end| end < bag_end)
                    })
                {
                    self.playback_buffering = true;
                    ctx.send_time_commands_to_active_recording(vec![TimeControlCommand::Pause]);
                } else if self.playback_buffering
                    && available.is_some_and(|end| {
                        end - t >= 500_000_000
                            || self.header_end_ns.is_some_and(|bag_end| end >= bag_end)
                    })
                {
                    self.playback_buffering = false;
                    ctx.send_time_commands_to_active_recording(vec![
                        TimeControlCommand::SetPlayState(PlayState::Playing),
                    ]);
                }
            }
        }
        if self.playback_window_pending {
            if self
                .playback_request_started
                .is_some_and(|started| started.elapsed().as_secs() >= 60)
            {
                self.playback_window_pending = false;
                self.playback_waiting_receipt = None;
                self.playback_window_error = Some("Timed out waiting for playback data. Check the gRPC connection and reopen the bag.".into());
                return;
            }
            if self.playback_buffering {
                self.open_status_msg = "Buffering selected channels…".into();
            }
            ctx.egui_ctx
                .request_repaint_after(std::time::Duration::from_millis(200));
            return;
        }
        if self.playback_mcap_path.is_empty() || self.enabled_playback_topics().is_empty() {
            return;
        }
        if !self.playback_clock_ready {
            return;
        }
        let Some(bag_begin) = self.header_begin_ns else {
            return;
        };
        let Some(bag_end) = self.header_end_ns else {
            return;
        };
        let playhead = ctx
            .active_time_ctrl()
            .and_then(|tc| tc.time_int().map(|t| t.as_i64()))
            .unwrap_or(bag_begin);
        let playhead = playhead.clamp(bag_begin, bag_end.saturating_sub(1));
        if self.playback_window_error.is_some() {
            return;
        }
        let loaded_end = self
            .playback_cached_ranges
            .iter()
            .find(|&&(b, e)| b <= playhead && playhead < e)
            .map(|&(_, e)| e);
        if loaded_end
            .is_some_and(|end| end >= bag_end || playhead + PLAYBACK_PREFETCH_AHEAD_NS < end)
        {
            return;
        }
        let win_begin = loaded_end
            .unwrap_or_else(|| playhead.saturating_sub(PLAYBACK_LOOKBACK_NS).max(bag_begin));
        let win_end = (loaded_end.unwrap_or(playhead) + PLAYBACK_STEP_WINDOW_NS).min(bag_end);
        if win_end <= win_begin {
            return;
        }
        self.request_playback_window(ctx, win_begin, win_end, /*reset=*/ false);
    }

    #[cfg(target_arch = "wasm32")]
    fn ensure_mcap_topic_list(&mut self, ctx: &AppContext<'_>) {
        if !self.mcap_topic_list.is_empty() {
            return;
        }
        // Never infer the active source from an input draft or the server's
        // last-opened cache: those can belong to another tab/recording.
        if !self.playback_mcap_path.is_empty() {
            crate::web_tools::request_host_mcap_topics(
                &self.playback_mcap_path,
                ctx.egui_ctx.clone(),
            );
        }
    }

    #[cfg(target_arch = "wasm32")]
    fn poll_convert_job(&mut self, ctx: &AppContext<'_>) {
        if self.indexed_source_ready(ctx) && !self.playback_window_pending {
            crate::web_tools::acknowledge_browser_preview(&self.playback_mcap_path);
        }
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
        let message = sanitize_status_text(&json_field(body, "message").unwrap_or_default());
        if let Some(job_id) = json_field(body, "job_id") {
            self.convert_job_id = Some(job_id);
        }
        // Header times arrive with meta / ready / done — keep for Properties (no timeline calc).
        if let Some(v) = json_i64(body, "begin_ns") {
            if v > 0 {
                self.header_begin_ns = Some(v);
            }
        }
        if let Some(v) = json_i64(body, "end_ns") {
            if v > 0 {
                self.header_end_ns = Some(v);
            }
        }
        let pct = (progress * 100.0).clamp(0.0, 100.0);
        self.open_status_msg = format!("Record->MCAP {pct:.0}% - {message}");
        // Shared Source/Sim modal: always mirror convert progress (do not require a
        // prior upload — Sim replay opens a server .record path directly).
        let filename = self
            .local_bag
            .rsplit('/')
            .next()
            .filter(|s| !s.is_empty())
            .or_else(|| {
                self.open_local_path_draft
                    .rsplit('/')
                    .next()
                    .filter(|s| !s.is_empty())
            })
            .unwrap_or("bag")
            .to_owned();
        crate::web_tools::set_upload_progress(&crate::web_tools::UploadProgress {
            active: true,
            phase: "converting".into(),
            filename: filename.clone(),
            fraction: progress.clamp(0.0, 1.0) as f32,
            loaded: 0,
            total: 0,
            message: format!("Converting on server… {pct:.0}% — {message}"),
        });

        if status == "done" || status == "ready" {
            crate::web_tools::clear_upload_progress();
            if let Some(out) = json_field(body, "output_path") {
                if self.convert_opened_output.as_deref() != Some(out.as_str()) {
                    self.convert_opened_output = Some(out.clone());
                    self.convert_job_id = None;
                    self.layout_bound_store_id = None;
                    self.playback_clock_ready = false;
                    // Windowed path: topic list + Panel checkboxes; do NOT full-stream open_local.
                    self.begin_windowed_mcap_session(ctx, &out, /*fetch_topics=*/ true);
                }
            }
        } else if status == "error" {
            let missing = message.contains("Cached MCAP missing");
            let record = self.local_bag.clone();
            let is_record = {
                let lower = record.to_ascii_lowercase();
                lower.contains(".record") && !lower.ends_with(".rrd") && !lower.ends_with(".rbl")
            };
            if missing && is_record {
                self.convert_opened_output = None;
                self.convert_job_id = None;
                self.open_status_msg = format!("Cached MCAP missing — reconverting…\n{record}");
                seed_convert_progress_modal(&record, "Cached MCAP missing — reconverting…");
                crate::web_tools::request_host_convert_record_with_map(
                    &record,
                    self.open_map_path_draft.trim(),
                    ctx.egui_ctx.clone(),
                );
            } else {
                self.open_status_msg = format!("Conversion failed: {message}");
                self.convert_job_id = None;
                crate::web_tools::set_upload_progress(&crate::web_tools::UploadProgress {
                    active: true,
                    phase: "error".into(),
                    filename,
                    fraction: 0.0,
                    loaded: 0,
                    total: 0,
                    message: format!("Conversion failed: {message}"),
                });
            }
        }
    }

    fn try_open_local_path(&mut self, ctx: &AppContext<'_>, path: &str) {
        let lower = path.to_ascii_lowercase();
        let is_record =
            lower.contains(".record") && !lower.ends_with(".rrd") && !lower.ends_with(".rbl");
        // An explicit new source supersedes recovery, including while conversion
        // is pending. A layout (.rbl) is not a change of recording source.
        #[cfg(target_arch = "wasm32")]
        if is_record || lower.ends_with(".mcap") || lower.ends_with(".rrd") {
            self.playback_explicit_source = true;
            self.playback_source = None;
            self.playback_recovery = None;
            self.playback_saved_bookmark.clear();
            self.playback_mcap_path.clear();
            self.playback_recording = None;
            self.playback_window_pending = false;
            self.playback_waiting_receipt = None;
            self.playback_initial_seek = None;
            self.playback_clock_ready = false;
            self.playback_window_error = crate::web_tools::clear_playback_bookmark().err();
            self.playback_cached_ranges.clear();
        }
        if is_record {
            self.local_bag = path.to_owned();
            self.scenario_id.clear();
            self.trip_id.clear();
            self.convert_opened_output = None;
            self.header_begin_ns = None;
            self.header_end_ns = None;
            #[cfg(target_arch = "wasm32")]
            {
                self.open_status_msg = format!("Converting Apollo record → MCAP…\n{path}");
                self.convert_job_id = None;
                seed_convert_progress_modal(path, "Starting Record → MCAP conversion…");
                crate::web_tools::request_host_convert_record_with_map(
                    path,
                    self.open_map_path_draft.trim(),
                    ctx.egui_ctx.clone(),
                );
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
        if lower.ends_with(".rrd") {
            self.playback_mcap_path.clear();
            self.playback_recording = None;
            self.playback_window_pending = false;
            self.playback_waiting_receipt = None;
            self.playback_window_error = None;
            self.playback_cached_ranges.clear();
        }
        // Force layout/status refresh even when re-opening the same recording.
        self.layout_bound_store_id = None;
        self.playback_clock_ready = false;

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
            ctx.command_sender()
                .send_system(SystemCommand::LoadDataSource(LogDataSource::File {
                    file_source: FileSource::FileDialog {
                        recommended_store_id: None,
                        force_store_info: true,
                    },
                    path: p,
                }));
        }
        #[cfg(target_arch = "wasm32")]
        {
            if lower.ends_with(".mcap") {
                // Windowed playback — never full-stream MCAP into the proxy.
                self.begin_windowed_mcap_session(ctx, path, /*fetch_topics=*/ true);
                return;
            }
            // .rrd / .rbl: host full stream via gRPC proxy.
            self.open_status_msg = format!("Host streaming {path}…");
            self.mcap_topic_list.clear();
            crate::web_tools::request_host_open_local(path, ctx.egui_ctx.clone());
        }
    }

    fn open_trip_segment(&mut self, ctx: &AppContext<'_>, car_id: &str, start: &str, end: &str) {
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

    fn apply_layout(&mut self, ctx: &AppContext<'_>, kind: AdLayoutKind) {
        self.active_layout = Some(kind);
        let name = kind.file_name().unwrap_or("custom.rbl");
        let bytes = if kind == AdLayoutKind::Custom {
            self.custom_layout.clone()
        } else {
            kind.bytes().unwrap_or_default().to_vec()
        };
        if bytes.is_empty() {
            re_log::warn!(
                "No custom layout saved yet. Edit a layout, then use Panel → Save as Custom layout."
            );
            return;
        }
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
        if self.playback_initial_seek.is_some() || self.playback_recovery.is_some() {
            self.playback_layout_pending = self.current_blueprint_id.clone();
        }
        if self.playback_clock_ready
            && let Some(old) = self.current_blueprint_id.clone()
            && let Some(tc) = ctx.active_time_ctrl()
            && let Some(time) = tc.time_int()
        {
            self.pending_layout_clock =
                Some((old, *tc.timeline_name(), time.as_i64(), tc.play_state()));
        }
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
            std::borrow::Cow::Owned(bytes),
            &tx,
        ) {
            re_log::error!("Failed to import layout {name}: {err}");
            let _ = tx.quit(Some(Box::new(err)));
            return;
        }
        // import_from_file_contents already quits the sender when done.
        self.layout_applied_application = Some(target_store_id.application_id().clone());
        ctx.command_sender()
            .send_system(SystemCommand::AddReceiver(rx));
    }
}

/// Seed / refresh the shared Source progress modal for Record→MCAP conversion
/// (upload flow and Sim replay both use this).
#[cfg(target_arch = "wasm32")]
fn seed_convert_progress_modal(path: &str, message: &str) {
    let filename = path
        .rsplit('/')
        .next()
        .filter(|s| !s.is_empty())
        .unwrap_or(path)
        .to_owned();
    crate::web_tools::set_upload_progress(&crate::web_tools::UploadProgress {
        active: true,
        phase: "converting".into(),
        filename,
        fraction: 0.0,
        loaded: 0,
        total: 0,
        message: message.to_owned(),
    });
}

fn format_upload_bytes(n: u64) -> String {
    const KIB: f64 = 1024.0;
    const MIB: f64 = 1024.0 * 1024.0;
    const GIB: f64 = 1024.0 * 1024.0 * 1024.0;
    let n = n as f64;
    if n >= GIB {
        format!("{:.2} GiB", n / GIB)
    } else if n >= MIB {
        format!("{:.1} MiB", n / MIB)
    } else if n >= KIB {
        format!("{:.0} KiB", n / KIB)
    } else {
        format!("{n:.0} B")
    }
}

/// Fix UTF-8 mojibake from byte-wise JSON parsers (e.g. "…" → "Ã¢Â€Â¦" / "â€¦").
fn sanitize_status_text(s: &str) -> String {
    let mut out = s
        .replace("Ã¢Â€Â¦", "...")
        .replace("Ã¢Â\u{80}Â¦", "...")
        .replace("â€¦", "...")
        .replace("\u{2026}", "...") // …
        .replace('\u{2014}', "-") // —
        .replace('\u{2013}', "-") // –
        .replace('\u{2192}', "->"); // →
    // Collapse the raw byte-wise decode of U+2026: U+00E2 U+0080 U+00A6
    out = out.replace("\u{00e2}\u{0080}\u{00a6}", "...");
    // Drop leftover C1 controls that fonts render as tofu/boxes.
    out.chars()
        .filter(|c| {
            let u = *c as u32;
            !(0x80..=0x9F).contains(&u)
        })
        .collect()
}

fn menu_row(ui: &mut Ui, label: &str) -> egui::Response {
    ui.add_sized(
        [ui.available_width(), 28.0],
        egui::Button::new(RichText::new(label).size(12.0).color(theme::TEXT))
            .fill(Color32::TRANSPARENT)
            .corner_radius(4.0),
    )
}

fn layout_section_label(ui: &mut Ui, title: &str) {
    ui.label(
        RichText::new(title)
            .size(13.0)
            .strong()
            .color(theme::ACCENT),
    );
}

fn layout_drawer_frame() -> egui::Frame {
    egui::Frame::new()
        .fill(theme::RAIL_BG)
        .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.25)))
        .inner_margin(egui::Margin::same(14))
}

fn layout_control(ui: &Ui, name: &str, rect: Rect) {
    let rect = rect.intersect(ui.clip_rect());
    if !rect.is_positive() {
        return;
    }
    ui.ctx().data_mut(|d| {
        let key = egui::Id::new("ad_layout_controls");
        let mut controls = d
            .get_temp::<serde_json::Value>(key)
            .unwrap_or_else(|| serde_json::json!({}));
        controls[name] = serde_json::json!([rect.center().x, rect.center().y]);
        d.insert_temp(key, controls);
    });
}

fn drawer_heading(ui: &mut Ui, title: &str, open: &mut bool) {
    ui.horizontal(|ui| {
        ui.label(RichText::new(title).size(18.0).strong().color(theme::TEXT));
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            let close = ui.add(egui::Button::new("‹").frame(false));
            layout_control(ui, "close_drawer", close.rect);
            if close.on_hover_text("收起侧栏").clicked() {
                *open = false;
            }
        });
    });
}

fn layout_action(ui: &mut Ui, title: &str, primary: bool) -> egui::Response {
    ui.add_sized(
        [ui.available_width(), 40.0],
        egui::Button::new(RichText::new(title).size(14.0).color(theme::TEXT))
            .fill(if primary {
                theme::ACCENT_STRONG
            } else {
                theme::RAIL_BG
            })
            .stroke(Stroke::new(
                1.0,
                theme::ACCENT.gamma_multiply(if primary { 0.7 } else { 0.9 }),
            ))
            .corner_radius(6.0),
    )
}

fn library_group(
    ui: &mut Ui,
    title: &str,
    count: usize,
    default_open: bool,
    searching: bool,
    contents: impl FnOnce(&mut Ui),
) {
    egui::Frame::new()
        .fill(theme::CARD_BG.gamma_multiply(0.3))
        .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.3)))
        .corner_radius(6.0)
        .inner_margin(egui::Margin::symmetric(8, 4))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            let mut header = egui::CollapsingHeader::new(
                RichText::new(title).size(13.0).strong().color(theme::TEXT),
            )
            .id_salt(("panel_library", title, searching))
            .default_open(default_open);
            if searching {
                header = header.open(Some(true));
            }
            let response = header.show(ui, |ui| {
                ui.spacing_mut().item_spacing.y = 0.0;
                contents(ui);
            });
            layout_control(ui, &format!("group_{title}"), response.header_response.rect);
            let center = Pos2::new(
                ui.max_rect().right() - 9.0,
                response.header_response.rect.center().y,
            );
            ui.painter()
                .circle_filled(center, 9.0, theme::ACCENT.gamma_multiply(0.16));
            ui.painter().text(
                center,
                egui::Align2::CENTER_CENTER,
                count.to_string(),
                egui::FontId::proportional(11.0),
                theme::TEXT_DIM,
            );
        });
}

fn library_row(ui: &mut Ui, label: &str) -> egui::Response {
    let (rect, response) =
        ui.allocate_exact_size(Vec2::new(ui.available_width(), 32.0), Sense::click());
    if response.hovered() {
        ui.painter().rect_filled(rect, 4.0, theme::CARD_BG_HOVER);
    }
    ui.painter().text(
        rect.left_center() + Vec2::new(4.0, 0.0),
        egui::Align2::LEFT_CENTER,
        label,
        egui::FontId::proportional(13.0),
        theme::TEXT,
    );
    ui.painter().text(
        rect.right_center() - Vec2::new(12.0, 0.0),
        egui::Align2::CENTER_CENTER,
        "+",
        egui::FontId::proportional(19.0),
        theme::ACCENT,
    );
    ui.painter().hline(
        rect.x_range(),
        rect.bottom(),
        Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.12)),
    );
    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

fn paint_grip(painter: &egui::Painter, center: Pos2) {
    for x in [-3.0, 3.0] {
        for y in [-6.0, 0.0, 6.0] {
            painter.circle_filled(
                center + Vec2::new(x, y),
                1.4,
                theme::TEXT_DIM.gamma_multiply(0.8),
            );
        }
    }
}

fn panel_display_name(name: &str) -> &str {
    match name {
        "Planning 3D" | "Perception 3D" | "Control 3D" | "3D scene" => "三维场景",
        "Planning profile" => "规划曲线",
        "Trajectory XY" => "XY 轨迹",
        "Planning message" => "规划消息",
        "Speed tracking" => "速度跟踪",
        "Steering feedback" => "转向反馈",
        "Tracking errors" => "跟踪误差",
        "Pedals" => "踏板",
        "Topic inspector" => "消息检查器",
        "Signal plot" => "信号曲线",
        "Value watch" => "值监视",
        "State transitions" => "状态变化",
        "Topic health" => "话题健康",
        other => other,
    }
}

fn prop_row(ui: &mut Ui, label: &str, value: &str) {
    let (rect, _) = ui.allocate_exact_size(Vec2::new(ui.available_width(), 28.0), Sense::hover());
    let offset = 154.0_f32.min(rect.width() * 0.4);
    ui.painter().text(
        rect.left_center(),
        egui::Align2::LEFT_CENTER,
        label,
        egui::FontId::proportional(14.0),
        SOURCE_MUTED,
    );
    let value_rect = Rect::from_min_max(rect.min + Vec2::new(offset, 0.0), rect.max);
    let mut child = ui.new_child(
        egui::UiBuilder::new()
            .max_rect(value_rect)
            .layout(egui::Layout::left_to_right(egui::Align::Center)),
    );
    let text = RichText::new(value).size(14.0).color(theme::TEXT);
    let text = if label.ends_with("时间") {
        text.monospace()
    } else {
        text
    };
    child
        .add(egui::Label::new(text).truncate())
        .on_hover_text(value);
    ui.ctx().data_mut(|d| {
        let key=egui::Id::new("ad_source_ui");
        if let Some(mut state)=d.get_temp::<serde_json::Value>(key) {
            state["properties"].as_array_mut().expect("source properties").push(
                serde_json::json!({"label":label,"value":value,"rect":[rect.left(),rect.top(),rect.right(),rect.bottom()],"value_x":value_rect.left()}));
            d.insert_temp(key,state);
        }
    });
}

fn format_source_time(ns: i64, tz: TimestampFormat) -> String {
    let time = re_log_types::Timestamp::from_nanos_since_epoch(ns).to_jiff_zoned(tz);
    format!(
        "{}.{:03}",
        time.strftime("%Y-%m-%d %H:%M:%S"),
        ns.rem_euclid(1_000_000_000) / 1_000_000
    )
}

fn paint_layer_arrow(ui: &Ui, center: Pos2, open: bool) {
    let points = if open {
        vec![
            center + Vec2::new(-3.5, -2.0),
            center + Vec2::new(3.5, -2.0),
            center + Vec2::new(0.0, 3.0),
        ]
    } else {
        vec![
            center + Vec2::new(-2.0, -3.5),
            center + Vec2::new(-2.0, 3.5),
            center + Vec2::new(3.0, 0.0),
        ]
    };
    ui.painter().add(egui::Shape::convex_polygon(
        points,
        theme::TEXT,
        Stroke::NONE,
    ));
}

fn layer_search_field(ui: &mut Ui, rect: Rect, text: &mut String) -> egui::Response {
    ui.painter().rect(
        rect,
        5.0,
        SOURCE_INPUT,
        Stroke::new(1.0, SOURCE_BORDER),
        StrokeKind::Inside,
    );
    let c = rect.left_center() + Vec2::new(16.0, -1.0);
    ui.painter()
        .circle_stroke(c, 5.5, Stroke::new(1.2, SOURCE_MUTED));
    ui.painter().line_segment(
        [c + Vec2::splat(4.0), c + Vec2::splat(8.0)],
        Stroke::new(1.2, SOURCE_MUTED),
    );
    let mut child = ui.new_child(egui::UiBuilder::new().max_rect(Rect::from_min_max(
        rect.min + Vec2::new(32.0, 9.0),
        rect.max - Vec2::new(7.0, 9.0),
    )));
    child.add_sized(
        [rect.width() - 39.0, 20.0],
        egui::TextEdit::singleline(text)
            .hint_text("搜索图层…")
            .font(egui::FontId::proportional(14.0))
            .frame(egui::Frame::NONE)
            .margin(Vec2::ZERO),
    )
}

fn source_style(ui: &mut Ui) {
    ui.visuals_mut().override_text_color = Some(theme::TEXT);
    ui.visuals_mut().widgets.inactive.weak_bg_fill = SOURCE_INPUT;
    ui.visuals_mut().widgets.inactive.bg_fill = SOURCE_INPUT;
    ui.visuals_mut().widgets.inactive.bg_stroke = Stroke::new(1.0, SOURCE_BORDER);
    ui.visuals_mut().widgets.hovered.weak_bg_fill = theme::CARD_BG;
    ui.visuals_mut().widgets.hovered.fg_stroke = Stroke::new(1.0, theme::TEXT);
    ui.visuals_mut().text_edit_bg_color = Some(SOURCE_INPUT);
    ui.visuals_mut().selection.bg_fill = theme::ACCENT_STRONG;
}

fn source_label(ui: &mut Ui, label: &str, size: f32, strong: bool, extent: Vec2) {
    let (rect, _) = ui.allocate_exact_size(extent, Sense::hover());
    let text = RichText::new(label).size(size).color(theme::TEXT);
    let text = if strong { text.strong() } else { text };
    let response = ui
        .new_child(
            egui::UiBuilder::new()
                .max_rect(rect)
                .layout(egui::Layout::left_to_right(egui::Align::Center)),
        )
        .add(egui::Label::new(text).truncate());
    source_control_rect(ui, &format!("label_{label}"), response.rect);
}

fn source_field_label(ui: &mut Ui, label: &str) {
    source_label(
        ui,
        label,
        14.0,
        false,
        Vec2::new(ui.available_width(), 20.0),
    );
    ui.add_space(6.0);
}

fn source_control_rect(ui: &Ui, name: &str, rect: Rect) {
    ui.ctx().data_mut(|d| {
        let key = egui::Id::new("ad_source_ui");
        if let Some(mut state) = d.get_temp::<serde_json::Value>(key) {
            state["controls"][name] =
                serde_json::json!([rect.left(), rect.top(), rect.right(), rect.bottom()]);
            d.insert_temp(key, state);
        }
    });
}

fn source_combo(ui: &mut Ui, label: &str) -> egui::Response {
    let (rect, response) =
        ui.allocate_exact_size(Vec2::new(ui.available_width(), 38.0), Sense::click());
    ui.painter().rect(
        rect,
        5.0,
        if response.hovered() {
            theme::CARD_BG
        } else {
            SOURCE_INPUT
        },
        Stroke::new(1.0, SOURCE_BORDER),
        StrokeKind::Inside,
    );
    clipped_source_text(
        ui,
        Rect::from_min_max(
            rect.min + Vec2::new(13.0, 0.0),
            rect.max - Vec2::new(30.0, 0.0),
        ),
        label,
        theme::TEXT,
    );
    let center = rect.right_center() - Vec2::new(17.0, 0.0);
    ui.painter().add(egui::Shape::line(
        vec![
            center + Vec2::new(-4.0, -2.0),
            center + Vec2::new(0.0, 2.0),
            center + Vec2::new(4.0, -2.0),
        ],
        Stroke::new(1.2, SOURCE_MUTED),
    ));
    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

fn clipped_source_text(ui: &mut Ui, rect: Rect, label: &str, color: Color32) {
    ui.new_child(
        egui::UiBuilder::new()
            .max_rect(rect)
            .layout(egui::Layout::left_to_right(egui::Align::Center)),
    )
    .add(egui::Label::new(RichText::new(label).size(14.0).color(color)).truncate());
}

fn source_mode_picker(ui: &mut Ui, selected: &mut SourceOpenMode) {
    let response = source_combo(ui, selected.label());
    source_control_rect(ui, "Mode", response.rect);
    egui::Popup::menu(&response)
        .id(egui::Id::new("source_mode_picker"))
        .align(egui::RectAlign::BOTTOM_END)
        .gap(4.0)
        .show(|ui| {
            source_style(ui);
            ui.set_width((response.rect.width() - 16.0).max(100.0));
            for mode in SourceOpenMode::all() {
                if ui
                    .add_sized(
                        [ui.available_width(), 30.0],
                        egui::Button::new(mode.label()),
                    )
                    .clicked()
                {
                    *selected = mode;
                    ui.close();
                }
            }
        });
}

fn source_choose_trigger(ui: &mut Ui, label: &str) -> egui::Response {
    let (rect, response) =
        ui.allocate_exact_size(Vec2::new(ui.available_width(), 38.0), Sense::click());
    ui.painter().rect(
        rect,
        5.0,
        if response.hovered() {
            theme::CARD_BG
        } else {
            SOURCE_INPUT
        },
        Stroke::new(1.0, SOURCE_BORDER),
        StrokeKind::Inside,
    );
    let split = rect.right() - 86.0;
    ui.painter()
        .vline(split, rect.y_range(), Stroke::new(1.0, SOURCE_BORDER));
    clipped_source_text(
        ui,
        Rect::from_min_max(
            rect.min + Vec2::new(13.0, 0.0),
            Pos2::new(split - 10.0, rect.bottom()),
        ),
        label,
        theme::TEXT,
    );
    ui.painter().text(
        Pos2::new(split + 43.0, rect.center().y),
        egui::Align2::CENTER_CENTER,
        "更换文件",
        egui::FontId::proportional(14.0),
        SOURCE_MUTED,
    );
    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

fn source_path_picker(
    ui: &mut Ui,
    id: &str,
    selected: &mut String,
    values: &serde_json::Value,
    optional: bool,
    empty_label: &str,
) {
    let short = if selected.is_empty() {
        empty_label
    } else {
        selected.rsplit('/').next().unwrap_or(selected)
    };
    let response =
        source_combo(ui, short).on_hover_text("地图用于下一次录包转换；MCAP 保留内嵌地图");
    source_control_rect(ui, "Map", response.rect);
    egui::Popup::menu(&response)
        .id(egui::Id::new(("source_path_picker", id)))
        .align(egui::RectAlign::BOTTOM_END)
        .gap(4.0)
        .show(|ui| {
            source_style(ui);
            ui.set_width((response.rect.width() - 16.0).max(220.0));
            ui.set_max_height(300.0);
            egui::ScrollArea::vertical()
                .max_height(230.0)
                .show(ui, |ui| {
                    if optional {
                        let r = ui.add_sized(
                            [ui.available_width(), 30.0],
                            egui::Button::new(empty_label),
                        );
                        source_control_rect(ui, "map_none", r.rect);
                        if r.clicked() {
                            selected.clear();
                            ui.close();
                        }
                    }
                    if let Some(values) = values.as_array() {
                        for path in values.iter().filter_map(serde_json::Value::as_str) {
                            let r = ui
                                .add_sized(
                                    [ui.available_width(), 30.0],
                                    egui::Button::new(path.rsplit('/').next().unwrap_or(path)),
                                )
                                .on_hover_text(path);
                            source_control_rect(ui, &format!("map_{path}"), r.rect);
                            if r.clicked() {
                                *selected = path.into();
                                ui.close();
                            }
                        }
                        if values.is_empty() {
                            ui.label("暂无可选地图");
                        }
                    } else {
                        ui.label("正在加载地图…");
                    }
                });
            ui.separator();
            ui.label(
                RichText::new("自定义地图路径")
                    .size(12.0)
                    .color(SOURCE_MUTED),
            );
            let r = themed_text_edit(ui, selected, "/apollo_workspace/data/map_data/…");
            source_control_rect(ui, "map_path", r.rect);
        });
}

fn themed_text_edit(ui: &mut Ui, text: &mut String, hint: &str) -> egui::Response {
    dark_framed_text_edit(ui, text, hint, ui.available_width())
}

/// Dark CARD_BG slab + TEXT — never use Frame::NONE (skips TextEdit background_color).
fn dark_framed_text_edit(ui: &mut Ui, text: &mut String, hint: &str, width: f32) -> egui::Response {
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

fn paint_nav_icon(painter: &egui::Painter, id: AdNavId, center: Pos2, color: Color32) {
    match id {
        AdNavId::Source => {
            // stacked cylinders
            for dy in [-6.0_f32, 0.0, 6.0] {
                let r = Rect::from_center_size(center + Vec2::new(0.0, dy), Vec2::new(16.0, 5.0));
                painter.rect_stroke(
                    r,
                    CornerRadius::same(2),
                    Stroke::new(1.4, color),
                    StrokeKind::Inside,
                );
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
                    StrokeKind::Inside,
                );
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
                StrokeKind::Inside,
            );
            painter.rect_stroke(
                Rect::from_center_size(center - Vec2::new(0.0, 4.0), Vec2::new(12.0, 6.0)),
                CornerRadius::same(2),
                Stroke::new(1.4, color),
                StrokeKind::Inside,
            );
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
    if !rest.starts_with('"') {
        return None;
    }
    // Char-wise parse so multi-byte UTF-8 (e.g. "…") is not split into mojibake.
    let mut out = String::new();
    let mut chars = rest[1..].chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => return Some(out),
            '\\' => {
                let esc = chars.next()?;
                match esc {
                    'n' => out.push('\n'),
                    'r' => out.push('\r'),
                    't' => out.push('\t'),
                    '"' => out.push('"'),
                    '\\' => out.push('\\'),
                    '/' => out.push('/'),
                    'u' => {
                        let hex: String = chars.by_ref().take(4).collect();
                        if hex.len() != 4 {
                            return None;
                        }
                        let cp = u32::from_str_radix(&hex, 16).ok()?;
                        out.push(char::from_u32(cp)?);
                    }
                    other => out.push(other),
                }
            }
            c => out.push(c),
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

#[cfg(target_arch = "wasm32")]
fn json_i64(text: &str, key: &str) -> Option<i64> {
    let pat = format!("\"{key}\":");
    let idx = text.find(&pat)?;
    let rest = text[idx + pat.len()..].trim_start();
    if rest.starts_with("null") {
        return None;
    }
    let end = rest
        .find(|c: char| c == ',' || c == '}' || c.is_whitespace())
        .unwrap_or(rest.len());
    rest[..end].parse().ok()
}
