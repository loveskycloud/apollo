//! Docked algorithm tools. Configuration belongs to the blueprint, not a floating window.
use super::ad_debug_panels::{Kind, Panel};
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
                        ui.colored_label(
                            egui::Color32::LIGHT_RED,
                            format!("Invalid saved panel: {err}"),
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
        ui.horizontal_wrapped(|ui| {
            egui::ComboBox::from_id_salt("debug_tool_kind")
                .selected_text("Change tool…")
                .show_ui(ui, |ui| {
                    for kind in Kind::ALL {
                        if ui.selectable_label(false, kind.title()).clicked() {
                            *panel = Panel::new(kind, egui::Id::new(query.view_id).value());
                        }
                    }
                });
        });
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
