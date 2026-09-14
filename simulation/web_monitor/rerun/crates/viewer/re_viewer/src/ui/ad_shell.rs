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
}

impl Default for AdShell {
    fn default() -> Self {
        Self {
            active_nav: AdNavId::Source,
            source_open: false,
            layout_open: false,
            panel_open: false,
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
        }
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
            self.sim_open = false;
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
        ui.painter().rect_filled(rect, CornerRadius::same(6), fill);
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
        ui.painter().circle_filled(pin_rect.center(), 10.0, pin_bg);
        paint_nail(
            ui.painter(),
            pin_rect.center(),
            if pinned {
                theme::PIN_ACTIVE
            } else {
                theme::TEXT_DIM
            },
        );
        if pin_resp.on_hover_text("Pin as default layout").clicked() {
            self.default_layout = Some(kind);
            re_log::info!("Pinned default layout: {}", kind.label());
        }

        if response.clicked() {
            self.apply_layout(ctx, kind);
        }

        ui.add_space(4.0);
    }

    pub(crate) fn show_panel_secondary(
        &mut self,
        ctx: &re_viewer_context::ViewerContext<'_>,
        viewport: &re_viewport_blueprint::ViewportBlueprint,
        ui: &mut Ui,
    ) {
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

                ui.menu_button("Add panel / split / tabs…", |ui| {
                    let mut presets = vec![("3D scene".to_owned(), "3D", "/lidar/up/points".to_owned(), "+ /lidar/**\n+ /vehicle/**\n+ /planning/**\n+ /perception/**\n+ /prediction/**\n+ /hdmap/**".to_owned())];
                    for topic in self.mcap_topic_list.iter().filter(|t| t.starts_with("/camera/")) {
                        presets.push((topic.trim_start_matches("/camera/").to_owned(), "2D", topic.clone(), format!("+ {topic}/**")));
                    }
                    for (name, class, origin, filter) in presets {
                        if ui.button(format!("+ {name}")).clicked() {
                            let mut view = re_viewport_blueprint::ViewBlueprint::new(class.into(), re_viewer_context::RecommendedView { origin: origin.into(), query_filter: re_log_types::EntityPathFilter::parse_forgiving(&filter) });
                            view.display_name = Some(name);
                            viewport.add_views(std::iter::once(view), None, None);
                            viewport.mark_user_interaction(ctx);
                            ui.close();
                        }
                    }
                    if !self.mcap_topic_list.iter().any(|t|t.starts_with("/camera/")) { ui.weak("No camera channels in this bag"); }
                    for (name, preset) in [("Acceleration tracking", "acceleration"), ("Heading error", "heading"), ("Controller runtime", "runtime"), ("Full control dashboard", "control")] {
                        if ui.button(format!("+ {name}")).clicked() {
                            let mut view = re_viewport_blueprint::ViewBlueprint::new_with_root_wildcard("AdDebug".into());
                            view.space_origin = format!("/debug/{preset}").into();
                            view.display_name = Some(name.into());
                            viewport.add_views(std::iter::once(view), None, None);
                            viewport.mark_user_interaction(ctx);
                            ui.close();
                        }
                    }
                    ui.separator();
                    if ui.button("More types / split / tabs…").clicked() {
                        re_viewport_blueprint::ui::show_add_view_or_container_modal(viewport.root_container);
                        ui.close();
                    }
                });
                for (name, preset) in [("Topic inspector", "inspector"), ("Signal plot", "plot"), ("Planning profile", "profile"), ("Trajectory XY", "trajectory"), ("Speed tracking", "speed"), ("Steering feedback", "steering"), ("Tracking errors", "errors"), ("Pedals", "pedals"), ("State transitions", "states"), ("Value watch", "watch"), ("Topic health", "health")] {
                    if ui.button(format!("+ {name}")).clicked() {
                        let mut view = re_viewport_blueprint::ViewBlueprint::new_with_root_wildcard("AdDebug".into());
                        view.space_origin = format!("/debug/{preset}").into();
                        view.display_name = Some(name.into());
                        viewport.add_views(std::iter::once(view), None, None);
                        viewport.mark_user_interaction(ctx);
                        self.topics_picker_expanded = false;
                    }
                }
                ui.separator();
                ui.label("Current panels (drag their titles to dock)");
                for view in viewport.views.values() {
                    ui.horizontal(|ui| {
                        if ui.small_button("×").on_hover_text("Remove this panel").clicked() {
                            viewport.remove_contents(re_viewer_context::Contents::View(view.id));
                            viewport.mark_user_interaction(ctx);
                        }
                        ui.label(view.display_name.as_deref().unwrap_or("Unnamed panel"));
                    });
                }
                if ui.button("Save as Custom layout").clicked() {
                    match crate::saving::RrdSnapshot::blueprint(ctx.store_context.blueprint, None).and_then(crate::saving::RrdSnapshot::encode) {
                        Ok(bytes) => { self.custom_layout = bytes; self.active_layout = Some(AdLayoutKind::Custom); self.default_layout = Some(AdLayoutKind::Custom); },
                        Err(err) => re_log::error!("Failed to save custom layout: {err}"),
                    }
                }
                use re_ui::RecordingCommandSender as _;
                if ui.button("Export layout (.rbl)").clicked() {
                    ctx.command_sender().send_recording_command(re_ui::RecordingCommand { recording_id: ctx.store_context.recording.store_id().clone(), kind: re_ui::RecordingCommandKind::SaveBlueprint });
                }
                ui.separator();
                ui.weak("Drag panel titles to split or tab; drag dividers to resize. Custom is saved in this browser. Export .rbl for a portable backup (open it via Source).");
            });
        self.panel_open = panel_open_flag;
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
        egui::Area::new(egui::Id::new("ad_viewport_topics_picker"))
            .fixed_pos(Pos2::new(
                ui.available_rect_before_wrap().left() + 12.0,
                32.0,
            ))
            .order(egui::Order::Foreground)
            .show(ui.ctx(), |ui| {
                if ui
                    .button(if self.topics_picker_expanded {
                        "Layers ▾"
                    } else {
                        "Layers ▸"
                    })
                    .on_hover_text("Show or hide scene layers")
                    .clicked()
                {
                    self.topics_picker_expanded = !self.topics_picker_expanded;
                }
                if !self.topics_picker_expanded {
                    return;
                }
                egui::Frame::new()
                    .fill(theme::PANEL_BG)
                    .inner_margin(12)
                    .corner_radius(10)
                    .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.45)))
                    .show(ui, |ui| {
                        ui.set_width(300.0);
                        ui.weak("Checked = visible in all spatial panels");
                        ui.horizontal(|ui| {
                            for (label, value) in [("All", true), ("None", false)] {
                                if ui.small_button(label).clicked() {
                                    for layer in &layers {
                                        for topic in &layer.topics {
                                            self.playback_topic_enabled
                                                .insert(topic.clone(), value);
                                        }
                                    }
                                    changed = true;
                                }
                            }
                        });
                        egui::ScrollArea::vertical()
                            .max_height(440.0)
                            .show(ui, |ui| {
                                for prefix in [
                                    "map",
                                    "sensing",
                                    "planning",
                                    "localization",
                                    "perception",
                                    "prediction",
                                ] {
                                    changed |=
                                        self.draw_layer_branch(ui, prefix, &layers, &mut hitboxes);
                                }
                            });
                        if layers.is_empty() {
                            ui.weak("Open a bag with spatial data.");
                        }
                        for layer in &layers {
                            if (layer.path.starts_with("prediction/")
                                || layer.path.starts_with("perception/")
                                || layer.path == "planning/trajectory")
                                && layer.topics.iter().any(|t| {
                                    self.playback_topic_enabled.get(t).copied().unwrap_or(false)
                                })
                            {
                                let component =
                                    re_sdk_types::archetypes::LineStrips3D::descriptor_strips()
                                        .component;
                                for entity in &layer.entities {
                                    let result = ctx.store_context.recording.latest_at(
                                        &ctx.current_query(),
                                        &entity.as_str().into(),
                                        [component],
                                    );
                                    if result
                                        .component_batch_raw(component)
                                        .is_some_and(|batch| batch.is_empty())
                                    {
                                        ui.weak(format!(
                                            "{}: no geometry at current time",
                                            layer.path
                                        ));
                                    }
                                }
                            }
                        }
                        ui.separator();
                        ui.label("Lidar height (sensor Z, meters)");
                        ui.horizontal(|ui| {
                            for (color, label) in [
                                (Color32::from_rgb(255, 216, 64), "≤0"),
                                (Color32::from_rgb(255, 145, 48), "2"),
                                (Color32::from_rgb(240, 84, 114), "5"),
                                (Color32::from_rgb(175, 130, 255), "≥10"),
                            ] {
                                ui.colored_label(color, format!("■ {label}"));
                            }
                        });
                        ui.weak(
                            "Hover a layer for its source topic. Raw messages: Panel → Inspector.",
                        );
                        if let Some(error) = &self.playback_window_error {
                            ui.colored_label(Color32::LIGHT_RED, error);
                        }
                    });
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
                serde_json::json!({"layers":state,"views":rendered,"nodes":hitboxes}),
            )
        });
    }

    fn draw_layer_branch(
        &mut self,
        ui: &mut Ui,
        prefix: &str,
        layers: &[super::ad_layers::Layer],
        hitboxes: &mut std::collections::BTreeMap<String, [f32; 2]>,
    ) -> bool {
        let members = layers
            .iter()
            .filter(|l| l.path == prefix || l.path.starts_with(&format!("{prefix}/")))
            .collect::<Vec<_>>();
        if members.is_empty() {
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
        let mut on = enabled == members.len();
        let mut changed = false;
        let name = prefix.rsplit('/').next().unwrap_or(prefix);
        let mut checkbox = |ui: &mut Ui| {
            let response = ui.add(
                egui::Checkbox::new(&mut on, name)
                    .indeterminate(enabled > 0 && enabled < members.len()),
            );
            hitboxes.insert(
                prefix.to_owned(),
                [response.rect.center().x, response.rect.center().y],
            );
            if response.changed() {
                for layer in &members {
                    for topic in &layer.topics {
                        self.playback_topic_enabled.insert(topic.clone(), on);
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
        };
        if members.len() == 1 && members[0].path == prefix {
            checkbox(ui);
        } else {
            let id = ui.make_persistent_id(("display-layer", prefix));
            let children = members
                .iter()
                .filter_map(|l| l.path.strip_prefix(&format!("{prefix}/")))
                .map(|s| s.split('/').next().expect("child"))
                .collect::<std::collections::BTreeSet<_>>();
            egui::collapsing_header::CollapsingState::load_with_default_open(ui.ctx(), id, true)
                .show_header(ui, checkbox)
                .body(|ui| {
                    for child in children {
                        changed |= self.draw_layer_branch(
                            ui,
                            &format!("{prefix}/{child}"),
                            layers,
                            hitboxes,
                        );
                    }
                });
        }
        changed
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
        ui.add_space(6.0);

        let has_data = db.map(|d| d.store_info().is_some()).unwrap_or(false);
        if !has_data
            && self.local_bag.is_empty()
            && self.scenario_id.is_empty()
            && self.trip_id.is_empty()
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

        let (start_s, end_s) =
            if let (Some(b), Some(e)) = (self.header_begin_ns, self.header_end_ns) {
                // Prefer Cyber/MCAP header times — do not wait for / recompute from EntityDb timelines.
                (format_header_time_ns(b, tz), format_header_time_ns(e, tz))
            } else {
                recording_time_bounds(ctx)
            };
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
        let bag = if !self.local_bag.is_empty() && self.local_bag != "(choose a local .rrd / bag…)"
        {
            self.local_bag.clone()
        } else if db.is_some_and(|d| matches!(d.data_source.as_ref(), Some(LogSource::File { .. })))
        {
            source_s.clone()
        } else {
            "—".into()
        };

        prop_row(ui, "Start time", &start_s);
        prop_row(ui, "End time", &end_s);
        prop_row(ui, "Car ID", &car);
        prop_row(ui, "Scenario ID", &scenario);
        prop_row(ui, "Trip ID", &trip);
        prop_row(ui, "Local bag", &bag);
        prop_row(ui, "Connection", &source_s);
    }

    fn source_open_section(&mut self, ctx: &AppContext<'_>, ui: &mut Ui) {
        ui.ctx().data_mut(|d| {
            d.insert_temp(
                egui::Id::new("ad_source_ui"),
                serde_json::json!({"controls":{},"properties":[]}),
            )
        });
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

        let selector = egui::ComboBox::from_id_salt("ad_source_mode")
            .selected_text(self.source_open_mode.label())
            .width(ui.available_width())
            .show_ui(ui, |ui| {
                for mode in SourceOpenMode::all() {
                    let row = ui.selectable_value(&mut self.source_open_mode, mode, mode.label());
                    source_control_rect(ui, mode.label(), row.rect);
                }
            });
        source_control_rect(ui, "mode", selector.response.rect);

        ui.add_space(10.0);

        match self.source_open_mode {
            SourceOpenMode::Local => {
                ui.label(
                    RichText::new("Open a server path. Apollo .record converts to cached MCAP; .rrd/.mcap stream from the host.")
                        .size(11.0)
                        .color(theme::TEXT_DIM),
                );
                ui.add_space(8.0);

                field_label(ui, "Server path (.record / .rrd / .mcap)");
                let path_edit = themed_text_edit(
                    ui,
                    &mut self.open_local_path_draft,
                    "/apollo_workspace/data/bag/20260514114049.record.00000.20260514114049",
                );
                source_control_rect(ui, "path", path_edit.rect);
                ui.add_space(6.0);
                let can_path = !self.open_local_path_draft.trim().is_empty();
                field_label(ui, "HD map for .record (optional)");
                themed_text_edit(
                    ui,
                    &mut self.open_map_path_draft,
                    "Apollo map directory or base_map.txt / .bin / .json",
                );
                ui.weak("Simulation: task map is automatic. Other bags: select the matching map, then Open path. MCAP keeps its embedded map.");
                let open_button = ui.add_enabled(
                    can_path,
                    primary_button("Open Path").min_size(Vec2::new(ui.available_width(), 32.0)),
                );
                source_control_rect(ui, "Open Path", open_button.rect);
                // TextEdit drops focus on Enter before has_focus() is seen.
                let open_enter = can_path
                    && ui.input(|i| i.key_pressed(egui::Key::Enter))
                    && (path_edit.has_focus() || path_edit.lost_focus());
                if open_button.clicked() || open_enter {
                    if open_enter {
                        self.enter_handled_this_frame = true;
                    }
                    let path = self.open_local_path_draft.trim().to_owned();
                    self.try_open_local_path(ctx, &path);
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
    fn take_host_open_status(&mut self, ctx: &AppContext<'_>) {
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
            crate::web_tools::request_host_convert_record_with_map(
                &record,
                self.open_map_path_draft.trim(),
                ctx.egui_ctx.clone(),
            );
            return;
        }
        self.open_status_msg = msg;
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
        self.open_status_msg = format!("Record→MCAP {pct:.0}% — {message}");

        if status == "done" || status == "ready" {
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
                crate::web_tools::request_host_convert_record_with_map(
                    &record,
                    self.open_map_path_draft.trim(),
                    ctx.egui_ctx.clone(),
                );
            } else {
                self.open_status_msg = format!("Conversion failed: {message}");
                self.convert_job_id = None;
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

fn prop_row(ui: &mut Ui, label: &str, value: &str) {
    // Long file paths must wrap below the label, never overlap neighboring rows.
    ui.label(RichText::new(label).size(11.0).color(theme::TEXT_DIM));
    ui.add(egui::Label::new(RichText::new(value).size(12.0).color(theme::TEXT)).wrap());
    ui.ctx().data_mut(|d| {
        let key = egui::Id::new("ad_source_ui");
        if let Some(mut state) = d.get_temp::<serde_json::Value>(key) {
            state["properties"]
                .as_array_mut()
                .expect("source property list")
                .push(serde_json::json!({"label":label,"value":value}));
            d.insert_temp(key, state);
        }
    });
    ui.add_space(4.0);
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

/// Format Cyber header time (absolute ns) for Properties — no EntityDb range scan.
fn format_header_time_ns(ns: i64, tz: TimestampFormat) -> String {
    if ns <= 0 {
        return "—".into();
    }
    let timeline = Timeline::new_timestamp("header");
    format_bound(TimeInt::new_temporal(ns), &timeline, tz)
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
        StrokeKind::Inside,
    );

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
                match bytes[i + 1] {
                    b'n' => out.push('\n'),
                    b'r' => out.push('\r'),
                    b't' => out.push('\t'),
                    b'"' => out.push('"'),
                    b'\\' => out.push('\\'),
                    b'/' => out.push('/'),
                    b'u' if i + 5 < bytes.len() => {
                        let hex = std::str::from_utf8(&bytes[i + 2..i + 6]).ok()?;
                        let cp = u32::from_str_radix(hex, 16).ok()?;
                        out.push(char::from_u32(cp)?);
                        i += 6;
                        continue;
                    }
                    other => out.push(other as char),
                }
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
