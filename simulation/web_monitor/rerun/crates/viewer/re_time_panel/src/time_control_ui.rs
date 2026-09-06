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
    /// Default step when pressing step-forward: 1 / 10 / 100 ms.
    #[serde(default = "default_step_ms")]
    step_ms: u32,
    /// While the clock field is focused, hold the raw edit string.
    #[serde(skip)]
    clock_edit: Option<String>,
}

impl Default for TimeControlUi {
    fn default() -> Self {
        Self {
            step_ms: default_step_ms(),
            clock_edit: None,
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
// AD collapsed media bar (ported from 0.19.1 custom patch; command-based API)
// ---------------------------------------------------------------------------

mod ad_theme {
    use egui::Color32;
    /// Muted lavender for secondary icons / tick labels / clock.
    pub const MUTED: Color32 = Color32::from_rgb(0xA8, 0x9B, 0xC8);
    /// Brighter accent for Play + playhead.
    pub const ACCENT: Color32 = Color32::from_rgb(0xB8, 0x94, 0xF6);
    pub const ACCENT_STRONG: Color32 = Color32::from_rgb(0xC4, 0xA1, 0xFF);
    pub const PILL_BG: Color32 = Color32::from_rgb(0x2A, 0x23, 0x3A);
    pub const BAR_BG: Color32 = Color32::from_rgb(0x1A, 0x16, 0x24);
    pub const AXIS: Color32 = Color32::from_rgb(0xB8, 0x94, 0xF6);
}

/// Same typeface as the ruler tick labels.
fn tick_font() -> egui::FontId {
    egui::FontId::proportional(10.0)
}

impl TimeControlUi {
    /// transport · speed · Publish/Message time · thin timeline · step · editable clock
    pub fn media_bar_ui(
        &mut self,
        time_ctrl: &TimeControl,
        entity_db: &EntityDb,
        ui: &mut egui::Ui,
        time_commands: &mut Vec<TimeControlCommand>,
        timeline_rect_out: &mut Option<egui::Rect>,
        clock_text: &str,
        time_origin_ns: i64,
    ) {
        use egui::{Sense, Vec2};

        ui.with_layout(egui::Layout::left_to_right(egui::Align::Center), |ui| {
            ui.spacing_mut().item_spacing.x = 12.0;
            ui.set_min_height(ui.available_height());

            self.transport_controls(ui, time_ctrl, entity_db, time_commands);
            self.speed_dropdown(ui, time_ctrl, time_commands);
            self.timeline_mode_pill(ui, time_ctrl, entity_db, time_commands);

            let clock_w = 92.0;
            let step_w = 40.0;
            let after_scrub = 8.0;
            let scrub_w = (ui.available_width() - clock_w - step_w - after_scrub - 4.0).max(40.0);
            let row_h = ui.available_height().max(28.0);

            let (scrub, _) = ui.allocate_exact_size(Vec2::new(scrub_w, row_h), Sense::hover());
            *timeline_rect_out = Some(scrub);

            ui.add_space(after_scrub);
            let prev_spacing = ui.spacing().item_spacing.x;
            ui.spacing_mut().item_spacing.x = 2.0;
            self.step_dropdown(ui);
            self.editable_clock(ui, time_ctrl, clock_text, clock_w, time_origin_ns, time_commands);
            ui.spacing_mut().item_spacing.x = prev_spacing;
        });
    }

    fn transport_controls(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        entity_db: &EntityDb,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        ui.spacing_mut().item_spacing.x = 8.0;

        if icon_btn(ui, IconKind::SkipStart, ad_theme::MUTED, "Skip to start").clicked() {
            time_commands.push(TimeControlCommand::MoveBeginning);
        }

        let playing = matches!(
            time_ctrl.play_state(),
            PlayState::Playing | PlayState::Following
        );
        if playing {
            if icon_btn(ui, IconKind::Pause, ad_theme::MUTED, "Pause").clicked() {
                time_commands.push(TimeControlCommand::Pause);
            }
        } else if icon_btn(ui, IconKind::Play, ad_theme::ACCENT, "Play").clicked() {
            time_commands.push(TimeControlCommand::SetPlayState(PlayState::Playing));
        }

        if icon_btn(ui, IconKind::StepFwd, ad_theme::MUTED, "Step forward").clicked() {
            self.step_forward(time_ctrl, time_commands);
        }

        if icon_btn(ui, IconKind::SkipEnd, ad_theme::MUTED, "Skip to end").clicked() {
            if let Some(range) = entity_db.time_range_for(time_ctrl.timeline_name()) {
                time_commands.push(TimeControlCommand::Pause);
                time_commands.push(TimeControlCommand::SetTimeClamped(range.max().into()));
            }
        }
    }

    fn step_forward(&self, time_ctrl: &TimeControl, time_commands: &mut Vec<TimeControlCommand>) {
        time_commands.push(TimeControlCommand::Pause);
        let delta_ns = i64::from(self.step_ms) * 1_000_000;
        let cur = time_ctrl.time_int().unwrap_or(re_log_types::TimeInt::ZERO);
        let next = re_log_types::TimeInt::new_temporal(cur.as_i64().saturating_add(delta_ns));
        time_commands.push(TimeControlCommand::SetTimeClamped(next.into()));
    }

    fn apply_combo_visuals(ui: &mut egui::Ui) {
        let v = &mut ui.visuals_mut().widgets;
        v.inactive.weak_bg_fill = ad_theme::PILL_BG;
        v.inactive.bg_fill = ad_theme::PILL_BG;
        v.hovered.weak_bg_fill = ad_theme::PILL_BG;
        v.hovered.bg_fill = ad_theme::PILL_BG;
        v.active.weak_bg_fill = ad_theme::PILL_BG;
        v.active.bg_fill = ad_theme::PILL_BG;
        v.open.weak_bg_fill = ad_theme::PILL_BG;
        v.open.bg_fill = ad_theme::PILL_BG;
    }

    fn speed_dropdown(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        let mut speed = time_ctrl.speed();
        let speeds = [0.1_f32, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0];
        Self::apply_combo_visuals(ui);
        egui::ComboBox::from_id_salt("ad_media_speed")
            .selected_text(
                egui::RichText::new(format!("{speed:.1}x"))
                    .font(tick_font())
                    .color(ad_theme::MUTED),
            )
            .width(52.0)
            .show_ui(ui, |ui| {
                for &s in &speeds {
                    let label = format!("{s:.1}x");
                    if ui
                        .selectable_label(
                            (speed - s).abs() < 0.001,
                            egui::RichText::new(label).font(tick_font()),
                        )
                        .clicked()
                    {
                        speed = s;
                    }
                }
            });
        if (speed - time_ctrl.speed()).abs() > f32::EPSILON {
            time_commands.push(TimeControlCommand::SetSpeed(speed));
        }
    }

    fn step_dropdown(&mut self, ui: &mut egui::Ui) {
        let steps = [1_u32, 10, 100];
        Self::apply_combo_visuals(ui);
        egui::ComboBox::from_id_salt("ad_media_step_ms")
            .selected_text(
                egui::RichText::new(format!("{}ms", self.step_ms))
                    .font(tick_font())
                    .color(ad_theme::MUTED),
            )
            .width(40.0)
            .show_ui(ui, |ui| {
                for &s in &steps {
                    if ui
                        .selectable_label(
                            self.step_ms == s,
                            egui::RichText::new(format!("{s}ms")).font(tick_font()),
                        )
                        .clicked()
                    {
                        self.step_ms = s;
                    }
                }
            });
    }

    /// Apollo bags expose two sync axes via MCAP:
    /// - `message_publish_time` ← Cyber publish time (default)
    /// - `message_log_time` ← measurement / message time
    ///
    /// Replaces the old Live (follow-latest) pill so operators pick which clock
    /// drives LatestAt for lidar + camera together.
    fn timeline_mode_pill(
        &mut self,
        ui: &mut egui::Ui,
        time_ctrl: &TimeControl,
        entity_db: &EntityDb,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        use egui::RichText;

        const PUBLISH: &str = "message_publish_time";
        const MESSAGE: &str = "message_log_time";

        let has_publish = entity_db.timelines().contains_key(&re_log_types::TimelineName::from(PUBLISH));
        let has_message = entity_db.timelines().contains_key(&re_log_types::TimelineName::from(MESSAGE));
        if !has_publish && !has_message {
            // Non-MCAP recordings: keep a compact timeline name readout.
            ui.label(
                RichText::new(time_ctrl.timeline_name().as_str())
                    .font(tick_font())
                    .color(ad_theme::MUTED),
            );
            return;
        }

        let current = time_ctrl.timeline_name().as_str();
        let label = if current == PUBLISH {
            "Publish time"
        } else if current == MESSAGE {
            "Message time"
        } else if has_publish {
            "Publish time" // pending / other — show intended default
        } else {
            "Message time"
        };

        Self::apply_combo_visuals(ui);
        egui::ComboBox::from_id_salt("ad_timeline_mode")
            .selected_text(
                RichText::new(label)
                    .font(tick_font())
                    .color(ad_theme::ACCENT),
            )
            .width(110.0)
            .show_ui(ui, |ui| {
                if has_publish {
                    let selected = current == PUBLISH;
                    if ui
                        .selectable_label(
                            selected,
                            RichText::new("Publish time").font(tick_font()),
                        )
                        .on_hover_text(
                            "Align all topics by Cyber publish time (MCAP publish_time).",
                        )
                        .clicked()
                    {
                        time_commands.push(TimeControlCommand::Pause);
                        time_commands.push(TimeControlCommand::SetActiveTimeline(
                            re_log_types::TimelineName::from(PUBLISH),
                        ));
                    }
                }
                if has_message {
                    let selected = current == MESSAGE;
                    if ui
                        .selectable_label(
                            selected,
                            RichText::new("Message time").font(tick_font()),
                        )
                        .on_hover_text(
                            "Align all topics by measurement/message time (MCAP log_time).",
                        )
                        .clicked()
                    {
                        time_commands.push(TimeControlCommand::Pause);
                        time_commands.push(TimeControlCommand::SetActiveTimeline(
                            re_log_types::TimelineName::from(MESSAGE),
                        ));
                    }
                }
            })
            .response
            .on_hover_text(
                "Playback clock: Publish time (default) or Message time. Both lidar and camera LatestAt use this timeline.",
            );
    }

    fn editable_clock(
        &mut self,
        ui: &mut egui::Ui,
        _time_ctrl: &TimeControl,
        clock_text: &str,
        width: f32,
        // Timeline origin so relative HH:MM:SS.mmm maps back to absolute TimeInt.
        time_origin_ns: i64,
        time_commands: &mut Vec<TimeControlCommand>,
    ) {
        use egui::{FontId, Key, Vec2};

        let edit = self.clock_edit.get_or_insert_with(|| clock_text.to_owned());
        if !ui.memory(|m| m.has_focus(ui.id().with("ad_clock_edit"))) {
            *edit = clock_text.to_owned();
        }

        let te = egui::TextEdit::singleline(edit)
            .font(FontId::proportional(10.0))
            .text_color(ad_theme::MUTED)
            .desired_width(width)
            .margin(egui::Margin::symmetric(4, 2))
            .frame(egui::Frame::NONE)
            .id_salt("ad_clock_edit");

        let resp = ui.add_sized(Vec2::new(width, 22.0), te);
        if resp.lost_focus() || (resp.has_focus() && ui.input(|i| i.key_pressed(Key::Enter))) {
            if let Some(ns) = parse_clock_hmsm(edit) {
                let abs = ns.saturating_add(time_origin_ns);
                time_commands.push(TimeControlCommand::Pause);
                time_commands.push(TimeControlCommand::SetTimeClamped(
                    re_log_types::TimeInt::new_temporal(abs).into(),
                ));
            }
            self.clock_edit = None;
        }
    }
}

#[derive(Clone, Copy)]
enum IconKind {
    SkipStart,
    Play,
    Pause,
    StepFwd,
    SkipEnd,
}

fn icon_btn(ui: &mut egui::Ui, kind: IconKind, color: egui::Color32, tip: &str) -> egui::Response {
    use egui::{Sense, Vec2};
    let (rect, response) = ui.allocate_exact_size(Vec2::splat(24.0), Sense::click());
    let color = if response.hovered() {
        egui::Color32::WHITE
    } else {
        color
    };
    paint_icon(ui.painter(), kind, rect.center(), color);
    response.on_hover_text(tip)
}

fn paint_icon(painter: &egui::Painter, kind: IconKind, c: egui::Pos2, color: egui::Color32) {
    use egui::{Pos2, Shape, Stroke, Vec2};
    match kind {
        IconKind::SkipStart => {
            let h = 7.0;
            painter.line_segment(
                [c + Vec2::new(-5.0, -h), c + Vec2::new(-5.0, h)],
                Stroke::new(1.6, color),
            );
            painter.add(Shape::convex_polygon(
                vec![
                    c + Vec2::new(6.0, -h),
                    c + Vec2::new(-3.0, 0.0),
                    c + Vec2::new(6.0, h),
                ],
                color,
                Stroke::NONE,
            ));
        }
        IconKind::Play => {
            let h = 7.0;
            painter.add(Shape::convex_polygon(
                vec![
                    c + Vec2::new(-4.0, -h),
                    c + Vec2::new(6.0, 0.0),
                    c + Vec2::new(-4.0, h),
                ],
                color,
                Stroke::NONE,
            ));
        }
        IconKind::Pause => {
            let h = 7.0;
            let w = 2.2;
            painter.rect_filled(
                egui::Rect::from_center_size(c + Vec2::new(-3.2, 0.0), Vec2::new(w, h * 2.0)),
                0.0,
                color,
            );
            painter.rect_filled(
                egui::Rect::from_center_size(c + Vec2::new(3.2, 0.0), Vec2::new(w, h * 2.0)),
                0.0,
                color,
            );
        }
        IconKind::StepFwd => {
            let h = 6.5;
            painter.add(Shape::convex_polygon(
                vec![
                    c + Vec2::new(-6.0, -h),
                    c + Vec2::new(1.0, 0.0),
                    c + Vec2::new(-6.0, h),
                ],
                color,
                Stroke::NONE,
            ));
            painter.line_segment(
                [c + Vec2::new(4.5, -h), c + Vec2::new(4.5, h)],
                Stroke::new(1.6, color),
            );
        }
        IconKind::SkipEnd => {
            let h = 7.0;
            painter.add(Shape::convex_polygon(
                vec![
                    c + Vec2::new(-6.0, -h),
                    c + Vec2::new(3.0, 0.0),
                    c + Vec2::new(-6.0, h),
                ],
                color,
                Stroke::NONE,
            ));
            painter.line_segment(
                [c + Vec2::new(5.0, -h), c + Vec2::new(5.0, h)],
                Stroke::new(1.6, color),
            );
            let _ = Pos2::ZERO;
        }
    }
}

/// Thin tick ruler used by the collapsed media bar.
///
/// Visual match to the AD prototype: 1px axis, ticks upward, `MM:SS` labels below,
/// playhead = thin vertical + circular knob on top.
/// `duration_secs` is the active timeline span (bag length) — dynamic, not fixed.
pub fn paint_prototype_ruler(
    painter: &egui::Painter,
    rect: egui::Rect,
    playhead_t: f32,
    duration_secs: f64,
    is_sequence: bool,
) {
    use egui::{Align2, Pos2, Stroke};

    let (left, right) = prototype_ruler_x_range(rect);
    if right - left < 20.0 {
        return;
    }

    // Axis slightly above center so labels fit below (matches 0.19 prototype).
    let y = rect.center().y - 4.0;

    painter.line_segment(
        [Pos2::new(left, y), Pos2::new(right, y)],
        Stroke::new(1.0, ad_theme::AXIS.gamma_multiply(0.55)),
    );

    let duration = duration_secs.max(0.0);
    if duration > 0.0 {
        let (major_secs, minor_secs) = nice_ruler_tick_interval(duration);
        let x_at = |sec: f64| -> f32 {
            let u = (sec / duration).clamp(0.0, 1.0) as f32;
            left + u * (right - left)
        };

        // Collect tick times: minors from 0..duration, plus exact end.
        let mut secs: Vec<f64> = Vec::new();
        let mut t = 0.0;
        while t < duration - minor_secs * 0.25 {
            secs.push(t);
            t += minor_secs;
        }
        secs.push(duration);

        for &sec in &secs {
            let major = is_major_tick(sec, major_secs, duration);
            let h = if major { 7.0 } else { 3.5 };
            let x = x_at(sec);
            painter.line_segment(
                [Pos2::new(x, y), Pos2::new(x, y - h)],
                Stroke::new(
                    1.0,
                    ad_theme::AXIS.gamma_multiply(if major { 0.7 } else { 0.35 }),
                ),
            );
            if major {
                let align = if sec <= 1e-9 {
                    Align2::LEFT_TOP
                } else if (duration - sec).abs() <= 1e-6 {
                    Align2::RIGHT_TOP
                } else {
                    Align2::CENTER_TOP
                };
                painter.text(
                    Pos2::new(x, y + 3.0),
                    align,
                    format_ruler_tick_label(sec, is_sequence),
                    tick_font(),
                    ad_theme::MUTED,
                );
            }
        }
    }

    // Playhead: circle knob on TOP of the vertical line.
    let t = playhead_t.clamp(0.0, 1.0);
    let px = left + t * (right - left);
    let top = y - 9.0;
    painter.line_segment(
        [Pos2::new(px, top), Pos2::new(px, y + 1.0)],
        Stroke::new(1.0, ad_theme::ACCENT_STRONG),
    );
    painter.circle_filled(Pos2::new(px, top), 3.0, ad_theme::ACCENT_STRONG);
}

/// Choose major/minor seconds so ~6–10 major ticks span the bag (e.g. 60s → 10s / 2s).
fn nice_ruler_tick_interval(duration_secs: f64) -> (f64, f64) {
    const MAJORS: &[f64] = &[
        0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 900.0,
        1800.0, 3600.0,
    ];
    let target = (duration_secs / 8.0).max(0.05);
    let major = MAJORS
        .iter()
        .copied()
        .find(|&c| c >= target)
        .unwrap_or_else(|| {
            let hours = (target / 3600.0).ceil().max(1.0);
            hours * 3600.0
        });
    (major, major / 5.0)
}

fn is_major_tick(sec: f64, major_secs: f64, duration: f64) -> bool {
    if (duration - sec).abs() <= 1e-6 {
        return true;
    }
    if major_secs <= 0.0 {
        return false;
    }
    let q = sec / major_secs;
    (q - q.round()).abs() < 1e-6
}

fn format_ruler_tick_label(secs: f64, is_sequence: bool) -> String {
    if is_sequence {
        return format!("{}", secs.round() as i64);
    }
    let total = secs.round().max(0.0) as u64;
    let h = total / 3600;
    let m = (total % 3600) / 60;
    let s = total % 60;
    if h > 0 {
        format!("{h:02}:{m:02}:{s:02}")
    } else {
        format!("{m:02}:{s:02}")
    }
}

pub fn prototype_ruler_x_range(rect: egui::Rect) -> (f32, f32) {
    let pad = 22.0;
    (rect.left() + pad, rect.right() - pad)
}

pub fn format_clock_hmsm(time: Option<re_log_types::TimeInt>) -> String {
    match time {
        Some(t) if t.is_static() => "static".into(),
        Some(t) => format_ns_hmsm(t.as_i64().max(0) as u64),
        None => "00:00:00.000".into(),
    }
}

fn format_ns_hmsm(mut ns: u64) -> String {
    let ms = (ns / 1_000_000) % 1000;
    ns /= 1_000_000_000;
    let s = ns % 60;
    let m = (ns / 60) % 60;
    let h = ns / 3600;
    format!("{h:02}:{m:02}:{s:02}.{ms:03}")
}

pub fn parse_clock_hmsm(input: &str) -> Option<i64> {
    let s = input.trim();
    if s.is_empty() {
        return None;
    }
    // Accept HH:MM:SS.mmm or MM:SS.mmm or SS.mmm
    let parts: Vec<&str> = s.split(':').collect();
    let (h, m, rest) = match parts.as_slice() {
        [h, m, rest] => (h.parse::<u64>().ok()?, m.parse::<u64>().ok()?, *rest),
        [m, rest] => (0, m.parse::<u64>().ok()?, *rest),
        [rest] => (0, 0, *rest),
        _ => return None,
    };
    let (sec, ms) = if let Some((sec, ms)) = rest.split_once('.') {
        (sec.parse::<u64>().ok()?, ms.parse::<u64>().ok().unwrap_or(0).min(999))
    } else {
        (rest.parse::<u64>().ok()?, 0)
    };
    let total_ms = (((h * 60 + m) * 60) + sec) * 1000 + ms;
    Some((total_ms as i64).saturating_mul(1_000_000))
}

pub fn panel_fill() -> egui::Color32 {
    ad_theme::BAR_BG
}

pub fn panel_stroke() -> egui::Stroke {
    egui::Stroke::new(1.0, ad_theme::ACCENT.gamma_multiply(0.2))
}
