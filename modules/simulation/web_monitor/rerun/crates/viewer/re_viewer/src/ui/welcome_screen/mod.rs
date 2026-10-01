//! Welcome screen stub for Apollo web_monitor product UI.
//! Keep types/exports so call sites compile; do not show Rerun marketing UI.

mod intro_section;

use std::sync::Arc;

use re_log_channel::LogSource;
use re_viewer_context::AppContext;

use crate::app_state::WelcomeScreenState;

pub use intro_section::{CloudState, LoginState};

#[derive(Default)]
pub struct WelcomeScreen {}

impl WelcomeScreen {
    pub fn set_examples_manifest_url(&mut self, _egui_ctx: &egui::Context, _url: String) {}

    /// Intentionally empty: do not show Rerun marketing / example welcome UI.
    pub fn ui(
        &mut self,
        _ui: &mut egui::Ui,
        _ctx: &AppContext<'_>,
        _welcome_screen_state: &WelcomeScreenState,
        _log_sources: &[Arc<LogSource>],
        _login_state: &CloudState,
    ) {
    }
}
