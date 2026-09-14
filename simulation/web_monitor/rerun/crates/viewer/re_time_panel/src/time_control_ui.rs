use egui::{NumExt as _, Popup, RectAlign};
use re_entity_db::EntityDb;
use re_log_types::TimeType;
use re_sdk_types::blueprint::components::{LoopMode, PlayState};
use re_ui::menu::menu_style;
use re_ui::{
    ComboItem, ReButton, RecordingCommandKind, Size, UiExt as _, Variant, icons, list_item,
};
use re_viewer_context::{TimeControl, TimeControlCommand};

fn default_step_ms() -> u32 {
    10
}

#[derive(serde::Deserialize, serde::Serialize)]
pub struct TimeControlUi {
    /// Paused transport step: 1 / 10 / 100 / 1000 ms.
    #[serde(default = "default_step_ms")]
    step_ms: u32,
    /// Do not replace an in-progress timestamp edit with advancing playback time.
    #[serde(skip)]
    clock_edit: Option<String>,
    #[serde(skip)]
    clock_edit_dirty: bool,
    #[serde(skip)]
    clock_edit_error: Option<String>,
    #[serde(skip)]
    clock_was_focused: bool,
    #[serde(skip)]
    clock_edit_source: Option<(String, String)>,
}

impl Default for TimeControlUi {
    fn default() -> Self {
        Self {
            step_ms: default_step_ms(),
            clock_edit: None,
            clock_edit_dirty: false,
            clock_edit_error: None,
            clock_was_focused: false,
            clock_edit_source: None,
        }
    }
}

const TIME_CONTROL_ROW_SIZE: Size = Size::custom(22.0);

impl TimeControlUi {
    #[expect(clippy::unused_self)]
    pub fn timeline_selector_ui(
        &self,
        time_ctrl: &TimeControl,
        entity_db: &EntityDb,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        let response = ui
            .add(
                ReButton::dropdown(time_ctrl.timeline_name().as_str())
                    .size(TIME_CONTROL_ROW_SIZE)
                    .ghost(),
            )
            .on_hover_ui(|ui| {
                list_item::list_item_scope(ui, "tooltip", |ui| {
                    ui.markdown_ui(
                        r"
Select timeline.

Each piece of logged data is associated with one or more timelines.

The logging SDK can create two timelines for you automatically:
* `log_time` - a temporal timeline with the time of the log call (opt-out)
* `log_tick` - a sequence timeline with the sequence number of the log call (opt-in)

You can also define your own timelines, e.g. for sensor time or camera frame number.
"
                        .trim(),
                    );

                    ui.re_hyperlink(
                        "Full documentation",
                        "https://rerun.io/docs/concepts/logging-and-ingestion/timelines",
                        // Always open in a new tab
                        true,
                    );
                });
            });
        Popup::menu(&response).style(menu_style()).show(|ui| {
            let timelines = entity_db.timelines();

            if timelines.is_empty() {
                ui.weak("The recording has no timelines");
                return;
            }

            for timeline in timelines.values() {
                let num_rows = entity_db.num_temporal_rows_on_timeline(timeline.name());
                if ui
                    .add(
                        ComboItem::new(timeline.name().as_str())
                            .value(format!("{} rows", re_format::format_uint(num_rows)))
                            .selected(timeline.name() == time_ctrl.timeline_name()),
                    )
                    .clicked()
                {
                    time_commands.push(TimeControlCommand::SetActiveTimeline(*timeline.name()));
                }
            }
        });
        // Sort of an inline of the `egui::Response::context_menu` function.
        // This is required to assign an id to the context menu, which would
        // otherwise conflict with the popup of this `ComboBox`'s popup menu.
        egui::Popup::menu(&response)
            .id(egui::Id::new("timeline select context menu"))
            .open_memory(if response.secondary_clicked() {
                Some(egui::SetOpenCommand::Bool(true))
            } else if response.clicked() {
                // Explicitly close the menu if the widget was clicked
                // Without this, the context menu would stay open if the user clicks the widget
                Some(egui::SetOpenCommand::Bool(false))
            } else {
                None
            })
            .at_pointer_fixed()
            .show(|ui| {
                if ui.button("Copy timeline name").clicked() {
                    let timeline = format!("{}", time_ctrl.timeline_name());
                    re_log::info!("Copied timeline: {}", timeline);
                    ui.copy_text(timeline);
                }
            });
    }

    #[expect(clippy::unused_self)]
    pub fn fps_ui(
        &self,
        time_ctrl: &TimeControl,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        if time_ctrl.time_type() == Some(TimeType::Sequence)
            && let Some(mut fps) = time_ctrl.fps()
        {
            let old_fps = fps;
            ReButton::wrap_widget(ui, Variant::Ghost, TIME_CONTROL_ROW_SIZE, false, |ui| {
                ui.add(
                    egui::DragValue::new(&mut fps)
                        .suffix(" FPS")
                        .speed(1)
                        .range(0.0..=f32::INFINITY),
                )
                .on_hover_text("Frames per second");
            });
            if old_fps != fps {
                time_commands.push(TimeControlCommand::SetFps(fps));
            }
        }
    }

    pub fn play_pause_ui(
        &self,
        time_ctrl: &TimeControl,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 5.0; // from figma
            self.play_pause_button_ui(time_ctrl, ui, time_commands);
            self.playhead_nav_ui(ui, time_commands);
            self.loop_button_ui(time_ctrl, ui, time_commands);
        });
    }

    #[expect(clippy::unused_self)]
    fn play_pause_button_ui(
        &self,
        time_ctrl: &TimeControl,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        let is_paused = time_ctrl.play_state() == PlayState::Paused;
        if ui
            .add(
                ReButton::icon(if is_paused { icons::PLAY } else { icons::PAUSE })
                    .selected(!is_paused)
                    .size(TIME_CONTROL_ROW_SIZE)
                    .secondary(),
            )
            .on_hover_ui(|ui| RecordingCommandKind::PlaybackTogglePlayPause.tooltip_ui(ui))
            .clicked()
        {
            time_commands.push(TimeControlCommand::TogglePlayPause);
        }
    }

    #[expect(clippy::unused_self)]
    fn playhead_nav_ui(&self, ui: &mut egui::Ui, time_commands: &mut Vec<TimeControlCommand>) {
        let commands = [
            [
                RecordingCommandKind::PlaybackForward,
                RecordingCommandKind::PlaybackBack,
            ],
            [
                RecordingCommandKind::PlaybackForwardFast,
                RecordingCommandKind::PlaybackBackFast,
            ],
            [
                RecordingCommandKind::PlaybackStepForward,
                RecordingCommandKind::PlaybackStepBack,
            ],
            [
                RecordingCommandKind::PlaybackEndAndFollow,
                RecordingCommandKind::PlaybackBeginning,
            ],
        ];

        let tokens = ui.tokens();

        // Keep the button looking hovered while its menu popup is open.
        let popup_id = ui.id().with("playhead_nav_menu");
        let popup_open = egui::Popup::is_id_open(ui.ctx(), popup_id);

        // Match the height of a `large_button`.
        let button = ui
            .scope(|ui| {
                ui.spacing_mut().interact_size.y = tokens.large_button_size.y;

                ui.add(
                    re_ui::ReButton::new((
                        re_ui::icons::PLAYHEAD_NAV,
                        re_ui::icons::DROPDOWN_ARROW,
                    ))
                    .secondary()
                    .size(TIME_CONTROL_ROW_SIZE)
                    .highlighted(popup_open),
                )
            })
            .inner;

        egui::Popup::menu(&button)
            .style(menu_style())
            .id(popup_id)
            .align(RectAlign::TOP_START)
            .show(|ui| {
                for (idx, group) in commands.into_iter().enumerate() {
                    if idx > 0 {
                        ui.separator();
                    }

                    for command in group {
                        let button = command.menu_button(ui.ctx());
                        let button = ui.add(button).on_hover_ui(|ui| command.tooltip_ui(ui));

                        if button.clicked()
                            && let Some(time_command) =
                                TimeControlCommand::from_recording_command(command)
                        {
                            time_commands.push(time_command);
                        }
                    }
                }
            });
    }

    #[expect(clippy::unused_self)]
    fn loop_button_ui(
        &self,
        time_ctrl: &TimeControl,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        let button = ReButton::icon(re_ui::icons::LOOP)
            .size(TIME_CONTROL_ROW_SIZE)
            .secondary();

        // `selected` also switches the variant to `Variant::Selected`, which takes its fill
        // straight from the theme tokens and would ignore the loop colors set below. Put the
        // variant back with `secondary`, so the fill keeps coming from `visuals.selection`.
        let selected_button = |button: ReButton<'static>| button.selected(true).secondary();

        ui.scope(|ui| {
            // Loop-button cycles between states:
            match time_ctrl.loop_mode() {
                LoopMode::Off => {
                    if ui.add(button).on_hover_text("Looping is off").clicked() {
                        time_commands.push(TimeControlCommand::SetLoopMode(LoopMode::All));
                    }
                }
                LoopMode::All => {
                    ui.visuals_mut().selection.bg_fill = ui.tokens().loop_everything_color;
                    if ui
                        .add(selected_button(button))
                        .on_hover_text("Looping is off")
                        .clicked()
                    {
                        // Only go to the selection time selection mode if there's already a selection.
                        // (otherwise, we'd create a selection as a fail-safe, but that's rather confusing!)
                        if time_ctrl.time_selection().is_some() {
                            time_commands
                                .push(TimeControlCommand::SetLoopMode(LoopMode::Selection));
                        } else {
                            time_commands.push(TimeControlCommand::SetLoopMode(LoopMode::Off));
                        }
                    }
                }
                LoopMode::Selection => {
                    // No need for this - the selection color is already same as the loop color.
                    // ui.visuals_mut().selection.bg_fill = ui.tokens().loop_selection_color.to_opaque();

                    if ui
                        .add(selected_button(button))
                        .on_hover_text("Looping is off")
                        .clicked()
                    {
                        time_commands.push(TimeControlCommand::SetLoopMode(LoopMode::Off));
                    }
                }
            }
        });
    }

    #[expect(clippy::unused_self)]
    pub fn playback_speed_ui(
        &self,
        time_ctrl: &TimeControl,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        let mut speed = time_ctrl.speed();
        let drag_speed = (speed * 0.02).at_least(0.01);

        ReButton::wrap_widget(ui, Variant::Ghost, TIME_CONTROL_ROW_SIZE, false, |ui| {
            ui.add(
                egui::DragValue::new(&mut speed)
                    .speed(drag_speed)
                    .suffix("x"),
            )
            .on_hover_text("Playback speed");
        });

        if speed != time_ctrl.speed() {
            time_commands.push(TimeControlCommand::SetSpeed(speed));
        }
    }
}

// ---------------------------------------------------------------------------
// Scene-editor-style media bar; existing command-based playback and receipt cache.
// ---------------------------------------------------------------------------

mod ad_theme {
    use egui::Color32;
    // Carolanne product palette, matching the AD shell.
    pub const MUTED: Color32 = Color32::from_rgb(0xC4, 0xB5, 0xFD);
    pub const TEXT: Color32 = Color32::from_rgb(0xF3, 0xEE, 0xFF);
    pub const ACCENT: Color32 = Color32::from_rgb(0x9F, 0x7A, 0xEA);
    pub const INPUT: Color32 = Color32::from_rgb(0x3A, 0x31, 0x50);
    pub const BAR_BG: Color32 = Color32::from_rgb(0x1E, 0x1A, 0x28);
    pub const BORDER: Color32 = Color32::from_rgb(0x3A, 0x31, 0x50);
    pub const HOVER: Color32 = Color32::from_rgb(0x4A, 0x3F, 0x66);
    pub const CACHED: Color32 = Color32::from_rgb(0x59, 0x48, 0x78);
}

fn tick_font() -> egui::FontId {
    egui::FontId::proportional(12.0)
}

/// Read-only widget rectangles, also consumed by browser acceptance tests.
fn record_rect(ui: &egui::Ui, name: &str, rect: egui::Rect) {
    ui.ctx().data_mut(|d| {
        let key = egui::Id::new("ad_media_bar_rects");
        let mut rects = d
            .get_temp::<std::collections::BTreeMap<String, [f32; 4]>>(key)
            .unwrap_or_default();
        rects.insert(
            name.into(),
            [rect.left(), rect.top(), rect.right(), rect.bottom()],
        );
        d.insert_temp(key, rects);
    });
}

impl TimeControlUi {
    /// Flat scene-editor-style controls, with no settings or reset actions.
    pub fn media_bar_ui(
        &mut self,
        time_ctrl: &TimeControl,
        entity_db: &EntityDb,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
        timeline_rect_out: &mut Option<egui::Rect>,
        time_origin_ns: i64,
        playback_end: Option<re_log_types::TimeInt>,
    ) {
        ui.ctx().data_mut(|d| {
            d.insert_temp(
                egui::Id::new("ad_media_bar_rects"),
                std::collections::BTreeMap::<String, [f32; 4]>::new(),
            )
        });
        record_rect(ui, "bar", ui.max_rect());
        let source = (
            entity_db.store_id().to_string(),
            time_ctrl.timeline_name().to_string(),
        );
        if self.clock_edit_source.as_ref() != Some(&source) {
            self.clock_edit_source = Some(source);
            self.cancel_timestamp_edit();
        }
        ui.scope(|ui| {
            ui.spacing_mut().item_spacing = egui::vec2(6.0, 8.0);
            ui.spacing_mut().interact_size.y = 24.0;
            ui.spacing_mut().button_padding = egui::vec2(7.0, 3.0);
            ui.visuals_mut().override_text_color = Some(ad_theme::TEXT);
            ui.visuals_mut().selection.bg_fill = ad_theme::ACCENT;
            ui.visuals_mut().window_fill = ad_theme::BAR_BG;
            ui.visuals_mut().window_stroke = egui::Stroke::new(1.0, ad_theme::BORDER);
            ui.visuals_mut().extreme_bg_color = ad_theme::INPUT;
            let visuals = &mut ui.visuals_mut().widgets;
            for widget in [
                &mut visuals.inactive,
                &mut visuals.hovered,
                &mut visuals.active,
                &mut visuals.open,
            ] {
                widget.corner_radius = egui::CornerRadius::same(4);
                widget.bg_stroke = egui::Stroke::new(1.0, ad_theme::BORDER);
                widget.bg_fill = ad_theme::BAR_BG;
                widget.weak_bg_fill = ad_theme::BAR_BG;
                widget.fg_stroke.color = ad_theme::TEXT;
            }
            visuals.hovered.bg_fill = ad_theme::HOVER;
            visuals.hovered.weak_bg_fill = ad_theme::HOVER;
            if media_bar_is_compact(ui.available_width()) {
                ui.vertical(|ui| {
                    ui.horizontal(|ui| {
                        self.inline_controls(
                            ui,
                            time_ctrl,
                            entity_db,
                            time_origin_ns,
                            playback_end,
                            time_commands,
                        )
                    });
                    ui.horizontal(|ui| {
                        self.slider_and_timestamp(
                            ui,
                            time_ctrl,
                            time_origin_ns,
                            playback_end,
                            time_commands,
                            timeline_rect_out,
                        )
                    });
                });
            } else {
                self.inline_controls(
                    ui,
                    time_ctrl,
                    entity_db,
                    time_origin_ns,
                    playback_end,
                    time_commands,
                );
                ui.add_space(6.0);
                self.slider_and_timestamp(
                    ui,
                    time_ctrl,
                    time_origin_ns,
                    playback_end,
                    time_commands,
                    timeline_rect_out,
                );
            }
        });
    }

    fn inline_controls(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        entity_db: &EntityDb,
        begin: i64,
        end: Option<re_log_types::TimeInt>,
        commands: &mut Vec<TimeControlCommand>,
    ) {
        self.transport_controls(ui, time_ctrl, begin, end, commands);
        self.speed_dropdown(ui, time_ctrl, commands);
        self.timeline_mode_pill(ui, time_ctrl, entity_db, commands);
        let step = egui::ComboBox::from_id_salt("ad_media_step")
            .selected_text(egui::RichText::new(format!("{} ms", self.step_ms)).font(tick_font()))
            .width(76.0)
            .show_ui(ui, |ui| {
                for ms in [1_u32, 10, 100, 1000] {
                    let row = ui.selectable_value(&mut self.step_ms, ms, format!("{ms} ms"));
                    record_rect(ui, &format!("step_{ms}"), row.rect);
                }
            })
            .response
            .on_hover_text("Step backward / forward");
        record_rect(ui, "step", step.rect);
    }

    fn slider_and_timestamp(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        begin: i64,
        end: Option<re_log_types::TimeInt>,
        commands: &mut Vec<TimeControlCommand>,
        timeline_rect_out: &mut Option<egui::Rect>,
    ) {
        let text = match (time_ctrl.time_int(), end) {
            (Some(t), Some(end)) => format!(
                "{:.2} / {:.0} s",
                t.as_i64().saturating_sub(begin).max(0) as f64 / 1e9,
                end.as_i64().saturating_sub(begin).max(0) as f64 / 1e9
            ),
            _ => "— / — s".into(),
        };
        let width = ui
            .painter()
            .layout_no_wrap(text.clone(), tick_font(), ad_theme::MUTED)
            .size()
            .x
            .max(100.0);
        // Size from the recording bounds, not the changing playhead or edit draft,
        // so playback cannot make the field (and slider) jump horizontally.
        let input_width = [begin, end.map_or(begin, |end| end.as_i64())]
            .into_iter()
            .map(|ns| {
                let sample: String = format_timestamp_seconds(ns)
                    .chars()
                    .map(|c| if c.is_ascii_digit() { '8' } else { c })
                    .collect();
                ui.painter()
                    .layout_no_wrap(sample, tick_font(), ad_theme::TEXT)
                    .size()
                    .x
                    + 16.0
            })
            .fold(112.0_f32, f32::max);
        let slider_width =
            (ui.available_width() - width - input_width - 2.0 * ui.spacing().item_spacing.x)
                .max(24.0);
        let (slider, _) =
            ui.allocate_exact_size(egui::vec2(slider_width, 24.0), egui::Sense::hover());
        *timeline_rect_out = Some(slider);
        record_rect(ui, "slider", slider);
        let label = ui.add_sized(
            [width, 24.0],
            egui::Label::new(
                egui::RichText::new(text)
                    .font(tick_font())
                    .color(ad_theme::MUTED),
            )
            .halign(egui::Align::RIGHT),
        );
        record_rect(ui, "time", label.rect);
        self.timestamp_input(ui, time_ctrl, begin, end, input_width, commands);
    }

    fn cancel_timestamp_edit(&mut self) {
        self.clock_edit = None;
        self.clock_edit_dirty = false;
        self.clock_edit_error = None;
        self.clock_was_focused = false;
    }

    fn timestamp_input(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        begin: i64,
        end: Option<re_log_types::TimeInt>,
        width: f32,
        commands: &mut Vec<TimeControlCommand>,
    ) {
        let live = time_ctrl
            .time_int()
            .map(|t| format_timestamp_seconds(t.as_i64()))
            .unwrap_or_default();
        if !self.clock_was_focused && !self.clock_edit_dirty {
            self.clock_edit = Some(live.clone());
        }
        let draft = self.clock_edit.get_or_insert(live);
        // TextEdit may consume Escape and surrender focus while handling it.
        let (escape_pressed, enter) = ui.input(|i| {
            (
                i.key_pressed(egui::Key::Escape),
                i.key_pressed(egui::Key::Enter),
            )
        });
        let input = ui.add_sized(
            [width, 24.0],
            egui::TextEdit::singleline(draft)
                .font(tick_font())
                .desired_width(width)
                .hint_text("Timestamp (seconds)")
                .id_salt("ad_timestamp_input"),
        );
        record_rect(ui, "seek_input", input.rect);
        if input.changed() {
            self.clock_edit_dirty = true;
            self.clock_edit_error = None;
        }
        let escape =
            (self.clock_was_focused || input.has_focus() || input.lost_focus()) && escape_pressed;
        if escape {
            self.cancel_timestamp_edit();
            input.surrender_focus();
        } else if self.clock_edit_dirty && (input.lost_focus() || (input.has_focus() && enter)) {
            match parse_timestamp_seconds(draft) {
                Some(target)
                    if target >= begin && end.is_some_and(|end| target <= end.as_i64()) =>
                {
                    paused_seek(ui, target, commands);
                    self.cancel_timestamp_edit();
                    input.surrender_focus();
                }
                Some(_) => {
                    self.clock_edit_error =
                        Some("Timestamp is outside this bag's time range.".into())
                }
                None => {
                    self.clock_edit_error =
                        Some("Invalid timestamp. Enter seconds with up to 9 decimal places.".into())
                }
            }
        }
        self.clock_was_focused = input.has_focus() && !escape && !enter;
        let hint = self.clock_edit_error.as_deref().unwrap_or(
            "Current clock timestamp in seconds (nanosecond precision). Enter or leave the field to seek; Esc cancels. Not elapsed time from bag start.");
        input.clone().on_hover_text(hint);
        if self.clock_edit_error.is_some() {
            ui.painter().rect_stroke(
                input.rect,
                4.0,
                egui::Stroke::new(1.0, egui::Color32::LIGHT_RED),
                egui::StrokeKind::Inside,
            );
        }
        ui.ctx().data_mut(|d| {
            d.insert_temp(
                egui::Id::new("ad_media_timestamp"),
                (
                    self.clock_edit.clone().unwrap_or_default(),
                    self.clock_edit_error.clone().unwrap_or_default(),
                    self.clock_was_focused,
                ),
            )
        });
    }

    fn transport_controls(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        begin: i64,
        end: Option<re_log_types::TimeInt>,
        commands: &mut Vec<TimeControlCommand>,
    ) {
        let current = time_ctrl.time_int().map_or(begin, |t| t.as_i64());
        let end = end.map_or(current.max(begin), |t| t.as_i64());
        if media_button(
            ui,
            IconKind::Backward,
            "backward",
            &format!("Step backward ({} ms)", self.step_ms),
        )
        .clicked()
        {
            paused_seek(
                ui,
                stepped_time(current, -i64::from(self.step_ms), begin, end),
                commands,
            );
        }
        let playing = matches!(
            time_ctrl.play_state(),
            PlayState::Playing | PlayState::Following
        );
        if media_button(
            ui,
            if playing {
                IconKind::Pause
            } else {
                IconKind::Play
            },
            "play",
            if playing { "Pause" } else { "Play" },
        )
        .clicked()
        {
            if playing {
                cancel_buffer_resume(ui);
                commands.push(TimeControlCommand::Pause);
            } else {
                commands.push(TimeControlCommand::SetPlayState(PlayState::Playing));
            }
        }
        if media_button(
            ui,
            IconKind::Forward,
            "forward",
            &format!("Step forward ({} ms)", self.step_ms),
        )
        .clicked()
        {
            paused_seek(
                ui,
                stepped_time(current, i64::from(self.step_ms), begin, end),
                commands,
            );
        }
    }

    fn speed_dropdown(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        commands: &mut Vec<TimeControlCommand>,
    ) {
        let mut speed = time_ctrl.speed();
        let combo = egui::ComboBox::from_id_salt("ad_media_speed")
            .selected_text(egui::RichText::new(format!("{speed}x")).font(tick_font()))
            .width(72.0)
            .show_ui(ui, |ui| {
                for s in [0.1_f32, 0.25, 0.5, 1.0, 2.0, 4.0, 5.0, 10.0] {
                    let row = ui.selectable_value(&mut speed, s, format!("{s}x"));
                    record_rect(ui, &format!("speed_{s}"), row.rect);
                }
            });
        record_rect(ui, "speed", combo.response.rect);
        if speed != time_ctrl.speed() {
            commands.push(TimeControlCommand::SetSpeed(speed));
        }
    }

    fn timeline_mode_pill(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        entity_db: &EntityDb,
        commands: &mut Vec<TimeControlCommand>,
    ) {
        const CLOCKS: [&str; 2] = ["publish_time", "message_time"];
        let timelines = entity_db.timelines();
        let available: Vec<_> = CLOCKS
            .into_iter()
            .filter(|name| timelines.contains_key(&re_log_types::TimelineName::from(*name)))
            .collect();
        if available.is_empty() {
            ui.weak("Waiting for data");
            return;
        }
        let current = time_ctrl.timeline_name().as_str();
        let combo = egui::ComboBox::from_id_salt("ad_media_clock")
            .selected_text(egui::RichText::new(current).font(tick_font()))
            .width(130.0)
            .show_ui(ui, |ui| {
                for name in available {
                    let row = ui.selectable_label(current == name, name);
                    record_rect(ui, name, row.rect);
                    if row.clicked() && current != name {
                        let timeline = re_log_types::TimelineName::from(name);
                        cancel_buffer_resume(ui);
                        commands.push(TimeControlCommand::Pause);
                        commands.push(TimeControlCommand::SetActiveTimeline(timeline));
                        // Compare both clocks at the same instant. Do not reset to
                        // the new clock's first loaded sample or clamp to its cache.
                        if let Some(time) = time_ctrl.time_int() {
                            commands.push(TimeControlCommand::SetTime(time.into()));
                        }
                    }
                }
            });
        record_rect(ui, "clock", combo.response.rect);
    }
}

fn cancel_buffer_resume(ui: &egui::Ui) {
    ui.ctx()
        .data_mut(|d| d.insert_temp(egui::Id::new("web_monitor_cancel_buffer_resume"), true));
}

fn paused_seek(ui: &egui::Ui, time: i64, commands: &mut Vec<TimeControlCommand>) {
    cancel_buffer_resume(ui);
    commands.push(TimeControlCommand::Pause);
    // Header bounds, not the currently loaded chunk range, constrain user seeks.
    commands.push(TimeControlCommand::SetTime(
        re_log_types::TimeInt::new_temporal(time).into(),
    ));
}

fn stepped_time(current: i64, step_ms: i64, begin: i64, end: i64) -> i64 {
    current
        .saturating_add(step_ms.saturating_mul(1_000_000))
        .clamp(begin, end)
}

#[derive(Clone, Copy)]
enum IconKind {
    Backward,
    Play,
    Pause,
    Forward,
}

fn media_button(ui: &mut egui::Ui, kind: IconKind, name: &str, tip: &str) -> egui::Response {
    use egui::{Color32, Sense, Shape, Stroke, StrokeKind, Vec2};
    let (rect, response) = ui.allocate_exact_size(Vec2::splat(24.0), Sense::click());
    record_rect(ui, name, rect);
    let primary = matches!(kind, IconKind::Play | IconKind::Pause);
    let bg = if primary {
        ad_theme::ACCENT
    } else if response.hovered() {
        ad_theme::HOVER
    } else {
        ad_theme::BAR_BG
    };
    let color = if primary {
        Color32::WHITE
    } else {
        ad_theme::TEXT
    };
    let p = ui.painter();
    p.rect(
        rect,
        4.0,
        bg,
        Stroke::new(
            1.0,
            if primary {
                ad_theme::ACCENT
            } else {
                ad_theme::BORDER
            },
        ),
        StrokeKind::Inside,
    );
    let c = rect.center();
    let stroke = Stroke::new(1.2, color);
    match kind {
        IconKind::Play | IconKind::Pause => {
            p.circle_stroke(c, 6.5, stroke);
            if matches!(kind, IconKind::Play) {
                p.add(Shape::convex_polygon(
                    vec![
                        c + Vec2::new(-1.5, -3.2),
                        c + Vec2::new(3.2, 0.0),
                        c + Vec2::new(-1.5, 3.2),
                    ],
                    color,
                    Stroke::NONE,
                ));
            } else {
                for x in [-2.0, 2.0] {
                    p.line_segment([c + Vec2::new(x, -3.0), c + Vec2::new(x, 3.0)], stroke);
                }
            }
        }
        IconKind::Backward | IconKind::Forward => {
            let direction: f32 = if matches!(kind, IconKind::Backward) {
                -1.0
            } else {
                1.0
            };
            let point = |x: f32, y: f32| c + Vec2::new(x * direction, y);
            p.add(Shape::convex_polygon(
                vec![point(-4.0, -5.0), point(2.0, 0.0), point(-4.0, 5.0)],
                color,
                Stroke::NONE,
            ));
            p.line_segment([point(4.0, -5.0), point(4.0, 5.0)], stroke);
        }
    }
    response.on_hover_text(tip)
}

/// Scene-editor slider, with receipt-confirmed buffer segments underneath progress.
pub fn paint_media_slider(painter: &egui::Painter, rect: egui::Rect, playhead_t: f32) {
    use egui::{Pos2, Rect, Stroke};
    let (left, right) = media_slider_x_range(rect);
    if right <= left {
        return;
    }
    let y = rect.center().y;
    let segment = |a, b, color| {
        painter.rect_filled(
            Rect::from_min_max(Pos2::new(a, y - 2.0), Pos2::new(b, y + 2.0)),
            2.0,
            color,
        );
    };
    segment(left, right, ad_theme::BORDER);
    let cached = painter.ctx().data(|d| {
        d.get_temp::<(i64, i64)>(egui::Id::new("web_monitor_header_time_range_ns"))
            .zip(d.get_temp::<Vec<(i64, i64)>>(egui::Id::new("web_monitor_cached_ranges_ns")))
    });
    if let Some(((begin, end), ranges)) = cached
        && end > begin
    {
        for (a, b) in ranges {
            let x = |t: i64| {
                left + ((t.saturating_sub(begin)) as f64 / (end - begin) as f64).clamp(0.0, 1.0)
                    as f32
                    * (right - left)
            };
            segment(x(a), x(b), ad_theme::CACHED);
        }
    }
    let x = left + playhead_t.clamp(0.0, 1.0) * (right - left);
    segment(left, x, ad_theme::ACCENT);
    painter.circle_filled(Pos2::new(x, y), 5.0, ad_theme::INPUT);
    painter.circle_stroke(Pos2::new(x, y), 5.0, Stroke::new(2.0, ad_theme::ACCENT));
}

pub fn media_slider_x_range(rect: egui::Rect) -> (f32, f32) {
    (rect.left() + 6.0, rect.right() - 6.0)
}

pub fn media_bar_is_compact(width: f32) -> bool {
    width < 740.0
}

fn format_timestamp_seconds(ns: i64) -> String {
    let sign = if ns < 0 { "-" } else { "" };
    let value = ns.unsigned_abs();
    format!(
        "{sign}{}.{:09}",
        value / 1_000_000_000,
        value % 1_000_000_000
    )
}

/// Parse absolute seconds without passing epoch-sized timestamps through f64.
fn parse_timestamp_seconds(input: &str) -> Option<i64> {
    let input = input.trim();
    let (negative, value) = input
        .strip_prefix('-')
        .map_or((false, input), |s| (true, s));
    let (seconds, fraction) = value.split_once('.').unwrap_or((value, ""));
    if seconds.is_empty()
        || !seconds.bytes().all(|b| b.is_ascii_digit())
        || fraction.len() > 9
        || !fraction.bytes().all(|b| b.is_ascii_digit())
    {
        return None;
    }
    let seconds = seconds.parse::<u64>().ok()?;
    let nanos = if fraction.is_empty() {
        0
    } else {
        fraction
            .parse::<u64>()
            .ok()?
            .checked_mul(10_u64.pow(9 - fraction.len() as u32))?
    };
    let total = i128::from(seconds.checked_mul(1_000_000_000)?.checked_add(nanos)?);
    i64::try_from(if negative { -total } else { total }).ok()
}

pub fn panel_fill() -> egui::Color32 {
    ad_theme::BAR_BG
}
pub fn panel_stroke() -> egui::Stroke {
    egui::Stroke::new(1.0, ad_theme::BORDER)
}

#[cfg(test)]
mod media_bar_tests {
    use super::{
        format_timestamp_seconds, media_bar_is_compact, parse_timestamp_seconds, stepped_time,
    };
    #[test]
    fn timestamp_roundtrip_preserves_nanoseconds() {
        for ns in [0, 1, -1, 1_789_000_000_123_456_789, i64::MAX, i64::MIN] {
            assert_eq!(
                parse_timestamp_seconds(&format_timestamp_seconds(ns)),
                Some(ns)
            );
        }
        assert_eq!(parse_timestamp_seconds("30.05"), Some(30_050_000_000));
        assert_eq!(
            parse_timestamp_seconds("1789000000.000000001"),
            Some(1_789_000_000_000_000_001)
        );
        for invalid in [
            "",
            "NaN",
            "1:x",
            "01:02:03",
            "1.abc",
            "1.1234567890",
            "9223372036.854775808",
        ] {
            assert_eq!(parse_timestamp_seconds(invalid), None, "{invalid}");
        }
    }
    #[test]
    fn steps_use_header_bounds_not_loaded_window() {
        let begin = 1_789_000_000_000_000_000;
        let end = begin + 60_000_000_000;
        assert_eq!(
            stepped_time(begin + 5_000_000_000, 10, begin, end),
            begin + 5_010_000_000
        );
        assert_eq!(stepped_time(begin, -10, begin, end), begin);
        assert_eq!(stepped_time(end, 10, begin, end), end);
    }
    #[test]
    fn narrow_viewports_keep_controls_on_two_rows() {
        assert!(media_bar_is_compact(604.0));
        assert!(!media_bar_is_compact(824.0));
    }
}
