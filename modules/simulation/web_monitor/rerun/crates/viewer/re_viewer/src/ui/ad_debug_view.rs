//! Docked algorithm tools. Configuration belongs to the blueprint, not a floating window.
use super::ad_debug_panels::{Kind, Panel};
use super::ad_shell::theme;
use egui::{Color32, RichText, Stroke, StrokeKind};
use re_viewer_context::{BlueprintContext as _, ViewClass, ViewState, ViewStateExt as _};

#[derive(Clone, Default)]
pub(crate) struct Source {
    pub topics: Vec<String>,
    pub mcap: String,
    pub origin: Option<i64>,
    pub notice: String,
}

#[derive(Default)]
struct State {
    panel: Option<Panel>,
    saved: String,
}
impl ViewState for State {
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
        self
    }
    fn heap_size_bytes(&self) -> u64 {
        self.saved.len() as u64
    }
}

#[derive(Default)]
pub(crate) struct AdDebugView;
impl ViewClass for AdDebugView {
    fn identifier() -> re_sdk_types::ViewClassIdentifier {
        "AdDebug".into()
    }
    fn display_name(&self) -> &'static str {
        "AD algorithm debug"
    }
    fn icon(&self) -> &'static re_ui::Icon {
        &re_ui::icons::VIEW_TEXT
    }
    fn help(&self, _: egui::os::OperatingSystem) -> re_ui::Help {
        re_ui::Help::new("Algorithm debug panel").markdown("Read-only indexed bag queries, synchronized with the playback cursor. Drag the panel title to dock; use Panel to add or remove windows.")
    }
    fn new_state(&self) -> Box<dyn ViewState> {
        Box::<State>::default()
    }
    fn on_register(
        &self,
        _: &mut re_viewer_context::ViewSystemRegistrator<'_>,
    ) -> Result<(), re_viewer_context::ViewClassRegistryError> {
        Ok(())
    }
    fn layout_priority(&self) -> re_viewer_context::ViewClassLayoutPriority {
        re_viewer_context::ViewClassLayoutPriority::Low
    }
    fn spawn_heuristics(
        &self,
        _: &re_viewer_context::ViewerContext<'_>,
        _: &dyn Fn(&re_log_types::EntityPath) -> bool,
    ) -> re_viewer_context::ViewSpawnHeuristics {
        re_viewer_context::ViewSpawnHeuristics::empty()
    }
    fn ui(
        &self,
        ctx: &re_viewer_context::ViewerContext<'_>,
        _: &re_viewer_context::MissingChunkReporter,
        ui: &mut egui::Ui,
        state: &mut dyn ViewState,
        query: &re_viewer_context::ViewQuery<'_>,
        _: re_viewer_context::SystemExecutionOutput,
    ) -> Result<re_viewer_context::ViewClassUiOutput, re_viewer_context::ViewSystemExecutionError>
    {
        let state = state.downcast_mut::<State>()?;
        let path = query.view_id.as_entity_path().join(&"ad_config".into());
        if state.panel.is_none() {
            let saved = ctx
                .store_context
                .blueprint
                .latest_at_component::<re_sdk_types::components::Text>(
                    &path,
                    ctx.blueprint_query,
                    re_sdk_types::archetypes::TextDocument::descriptor_text().component,
                )
                .map(|(_, text)| text.to_string());
            if let Some(saved) = saved {
                match serde_json::from_str(&saved) {
                    Ok(panel) => {
                        state.panel = Some(panel);
                        state.saved = saved;
                    }
                    Err(err) => {
                        ui.label(
                            RichText::new(format!("Invalid saved panel: {err}"))
                                .size(12.0)
                                .color(Color32::from_rgb(0xFE, 0xCA, 0xCA)),
                        );
                        return Ok(Default::default());
                    }
                }
            } else {
                let name = query.space_origin.to_string();
                state.panel = Some(Panel::preset(
                    name.rsplit('/').next().unwrap_or("inspector"),
                    egui::Id::new(query.view_id).value(),
                ));
            }
        }
        let source = ctx
            .egui_ctx()
            .data(|d| d.get_temp::<Source>(egui::Id::new("ad_debug_source")))
            .unwrap_or_default();
        let panel = state.panel.as_mut().expect("initialized above");

        ui.visuals_mut().override_text_color = Some(theme::TEXT);
        ui.visuals_mut().extreme_bg_color = theme::CARD_BG;

        let current = panel.kind_title();
        let trigger = tool_change_trigger(ui, current);
        egui::Popup::menu(&trigger)
            .id(egui::Id::new(("debug_tool_kind", query.view_id)))
            .align(egui::RectAlign::BOTTOM_START)
            .gap(4.0)
            .show(|ui| {
                egui::Frame::new()
                    .fill(theme::PANEL_BG)
                    .stroke(Stroke::new(1.0, theme::ACCENT.gamma_multiply(0.4)))
                    .corner_radius(8.0)
                    .inner_margin(egui::Margin::symmetric(8, 8))
                    .show(ui, |ui| {
                        ui.set_min_width(trigger.rect.width().max(220.0));
                        ui.visuals_mut().override_text_color = Some(theme::TEXT);
                        ui.visuals_mut().widgets.hovered.weak_bg_fill = theme::CARD_BG_HOVER;
                        for kind in Kind::ALL {
                            let on = panel.kind_title() == kind.title();
                            let row = ui.add_sized(
                                [ui.available_width(), 28.0],
                                egui::Button::new(
                                    RichText::new(kind.title())
                                        .size(12.0)
                                        .color(if on {
                                            Color32::WHITE
                                        } else {
                                            theme::TEXT
                                        }),
                                )
                                .fill(if on {
                                    theme::ACCENT_STRONG.gamma_multiply(0.7)
                                } else {
                                    Color32::TRANSPARENT
                                })
                                .corner_radius(4.0),
                            );
                            if row.clicked() {
                                *panel = Panel::new(kind, egui::Id::new(query.view_id).value());
                                ui.close();
                            }
                        }
                    });
            });
        ui.add_space(6.0);

        panel.show(
            &ctx.app_ctx,
            ui,
            &source.topics,
            &source.mcap,
            source.origin,
            &source.notice,
        );
        if let Ok(config) = serde_json::to_string(panel)
            && config != state.saved
        {
            ctx.save_blueprint_archetype(
                path,
                &re_sdk_types::archetypes::TextDocument::new(config.clone()),
            );
            state.saved = config;
        }
        Ok(Default::default())
    }
}

fn tool_change_trigger(ui: &mut egui::Ui, current: &str) -> egui::Response {
    let height = 30.0;
    let width = ui.available_width().min(280.0);
    let (rect, response) =
        ui.allocate_exact_size(egui::vec2(width, height), egui::Sense::click());
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
        current,
        egui::FontId::proportional(12.0),
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
