//! Pane-local vehicle instruments. No network I/O or playback changes from paint.
use egui::{Align2, Color32, FontId, Pos2, Rect, Stroke, Ui, Vec2};
use serde_json::{Value, json};

const MUTED: Color32 = Color32::from_rgb(155, 170, 190);
const ACCENT: Color32 = Color32::from_rgb(86, 221, 190);
const AMBER: Color32 = Color32::from_rgb(255, 205, 91);

fn text(ui: &Ui, pos: Pos2, label: impl ToString, size: f32, color: Color32) {
    ui.painter().text(
        pos,
        Align2::CENTER_CENTER,
        label.to_string(),
        FontId::proportional(size),
        color,
    );
}
fn number(value: &Value, signed: bool) -> String {
    value.as_f64().map_or_else(
        || "—".into(),
        |v| {
            if signed {
                format!("{v:+.1}")
            } else {
                format!("{v:.1}")
            }
        },
    )
}
fn mode(raw: Option<&str>) -> &str {
    match raw {
        Some("COMPLETE_MANUAL") => "MANUAL",
        Some("COMPLETE_AUTO_DRIVE") => "AUTO",
        Some("AUTO_STEER_ONLY") => "AUTO STEER",
        Some("AUTO_SPEED_ONLY") => "AUTO SPEED",
        Some("EMERGENCY_MODE") => "EMERGENCY",
        Some("REMOTE") => "REMOTE",
        Some(other) => other,
        None => "N/A",
    }
}
fn cell(ui: &Ui, rect: Rect, title: &str, value: impl ToString, unit: &str, color: Color32) {
    let x = rect.center().x;
    text(ui, Pos2::new(x, rect.top() + 10.0), title, 10.0, MUTED);
    text(ui, Pos2::new(x, rect.top() + 35.0), value, 20.0, color);
    text(ui, Pos2::new(x, rect.top() + 57.0), unit, 10.0, MUTED);
}

fn speed_text(value: Option<f64>, meters_per_second: bool) -> String {
    value.filter(|v| v.is_finite()).map_or_else(
        || "—".into(),
        |v| format!("{:.1}", v * if meters_per_second { 1.0 } else { 3.6 }),
    )
}

pub(super) fn show(ui: &mut Ui, pane: Rect, view_id: re_viewer_context::ViewId) {
    let data = ui
        .ctx()
        .data(|d| d.get_temp::<Value>(egui::Id::new("ad_dashboard_data")));
    let Some(data) = data else {
        return;
    };
    let id = egui::Id::new(("ad_hud_expanded", view_id));
    let mut expanded = ui
        .ctx()
        .data_mut(|d| d.get_persisted::<bool>(id))
        .unwrap_or(true);
    let width = (pane.width() - 24.0).clamp(120.0, 560.0);
    let narrow = width < 440.0;
    let height = if expanded {
        if narrow { 212.0 } else { 140.0 }
    } else {
        28.0
    };
    let width = if expanded {
        width
    } else {
        128.0_f32.min(width)
    };
    let rect = Rect::from_center_size(
        Pos2::new(pane.center().x, pane.bottom() - 12.0 - height / 2.0),
        Vec2::new(width, height),
    );
    let mut hud = ui.new_child(
        egui::UiBuilder::new()
            .id_salt(id)
            .max_rect(rect)
            .layout(egui::Layout::top_down(egui::Align::Center)),
    );
    hud.set_clip_rect(pane.intersect(ui.clip_rect()));
    // Register the entire card, so dragging its background never rotates the scene.
    hud.interact(rect, id.with("background"), egui::Sense::click_and_drag());
    hud.painter().rect(
        rect,
        10.0,
        Color32::from_rgba_unmultiplied(22, 28, 38, 242),
        Stroke::new(1.0, Color32::from_rgb(62, 78, 97)),
        egui::StrokeKind::Inside,
    );
    let toggle_rect = Rect::from_center_size(
        Pos2::new(rect.center().x, rect.top() + 14.0),
        Vec2::new(128.0, 24.0),
    );
    let toggle = hud.put(
        toggle_rect,
        egui::Button::new(if expanded {
            "Dashboard  −"
        } else {
            "Dashboard  +"
        })
        .frame(false),
    );
    if toggle
        .on_hover_text("Expand / collapse vehicle instruments in this 3D panel")
        .clicked()
    {
        expanded = !expanded;
        ui.ctx().data_mut(|d| d.insert_persisted(id, expanded));
    }
    let speed_unit_id = egui::Id::new("ad_dashboard_speed_mps");
    let mut meters_per_second = ui
        .ctx()
        .data_mut(|d| d.get_persisted::<bool>(speed_unit_id))
        .unwrap_or(false);
    let mut speed_diagnostic = Value::Null;
    if height > 28.0 {
        let chassis = &data["streams"]["chassis"]["sample"];
        let sample_ns = chassis["sample_ns"]
            .as_str()
            .and_then(|t| t.parse::<i64>().ok());
        let at = data["at_ns"].as_str().and_then(|t| t.parse::<i64>().ok());
        let age = sample_ns.zip(at).map(|(s, a)| (a - s) as f64 / 1e6);
        let stale = age.is_some_and(|a| a > 500.0);
        let color = if stale { AMBER } else { Color32::WHITE };
        let top = rect.top() + 30.0;
        let columns = if narrow { 3 } else { 5 };
        let cell_width = (width - 16.0) / columns as f32;
        let cells: Vec<_> = (0..5)
            .map(|i| {
                Rect::from_min_size(
                    Pos2::new(
                        rect.left() + 8.0 + (i % columns) as f32 * cell_width,
                        top + (i / columns) as f32 * 72.0,
                    ),
                    Vec2::new(cell_width, 70.0),
                )
            })
            .collect();
        let speed_response = hud
            .interact(cells[0], id.with("speed_unit"), egui::Sense::click())
            .on_hover_cursor(egui::CursorIcon::PointingHand)
            .on_hover_text("Click to switch km/h ↔ m/s. Chassis feedback only.");
        if speed_response.clicked() {
            meters_per_second = !meters_per_second;
            ui.ctx()
                .data_mut(|d| d.insert_persisted(speed_unit_id, meters_per_second));
        }
        let speed = speed_text(chassis["speed_mps"].as_f64(), meters_per_second);
        let unit = if meters_per_second { "m/s" } else { "km/h" };
        cell(&hud, cells[0], "SPEED", &speed, unit, color);
        speed_diagnostic = json!({"text":speed,"unit":unit,"raw_mps":chassis["speed_mps"],
            "rect":[cells[0].left(),cells[0].top(),cells[0].right(),cells[0].bottom()]});
        cell(
            &hud,
            cells[1],
            "STEERING",
            number(&chassis["steering_percentage"], true),
            "%   + left / − right",
            color,
        );
        // Bipolar steering track: zero remains in the middle, including at standstill.
        let track = Rect::from_min_size(
            cells[1].left_bottom() - Vec2::new(-10.0, 4.0),
            Vec2::new(cell_width - 20.0, 3.0),
        );
        hud.painter()
            .rect_filled(track, 2.0, Color32::from_gray(60));
        if let Some(v) = chassis["steering_percentage"].as_f64() {
            let x =
                track.center().x + (v.clamp(-100.0, 100.0) as f32 / 100.0) * track.width() / 2.0;
            hud.painter().line_segment(
                [
                    Pos2::new(track.center().x, track.center().y),
                    Pos2::new(x, track.center().y),
                ],
                Stroke::new(3.0, ACCENT),
            );
        }
        let driving = mode(chassis["driving_mode"].as_str());
        let mode_color = match driving {
            "AUTO" => ACCENT,
            "EMERGENCY" => Color32::LIGHT_RED,
            _ => color,
        };
        let gear = chassis["gear_location"]
            .as_str()
            .map(|s| s.trim_start_matches("GEAR_"))
            .unwrap_or("N/A");
        let x = cells[2].center().x;
        text(&hud, Pos2::new(x, top + 10.0), "DRIVE MODE", 10.0, MUTED);
        text(
            &hud,
            Pos2::new(x, top + 35.0),
            driving,
            if driving.len() > 10 { 11.0 } else { 15.0 },
            mode_color,
        );
        text(&hud, Pos2::new(x, top + 57.0), gear, 10.0, MUTED);
        let traffic = &data["streams"]["traffic"];
        let lights = traffic["sample"]["lights"].as_array();
        let traffic_stale = traffic["sample"]["sample_ns"]
            .as_str()
            .and_then(|t| t.parse::<i64>().ok())
            .zip(at)
            .is_some_and(|(s, a)| a - s > 1_000_000_000);
        let colors: Vec<_> = lights
            .map(|v| v.iter().filter_map(|l| l["color"].as_str()).collect())
            .unwrap_or_default();
        let traffic_rect = cells[3];
        text(
            &hud,
            Pos2::new(traffic_rect.center().x, traffic_rect.top() + 10.0),
            "TRAFFIC LIGHTS",
            10.0,
            MUTED,
        );
        for (i, (name, c)) in [
            ("RED", Color32::LIGHT_RED),
            ("YELLOW", AMBER),
            ("GREEN", ACCENT),
        ]
        .iter()
        .enumerate()
        {
            hud.painter().circle_filled(
                Pos2::new(
                    traffic_rect.center().x + (i as f32 - 1.0) * 21.0,
                    traffic_rect.top() + 34.0,
                ),
                7.0,
                if colors.contains(name) && !traffic_stale {
                    *c
                } else {
                    Color32::from_gray(50)
                },
            );
        }
        let traffic_status = if traffic["notice"].is_string() {
            "No topic".to_owned()
        } else if lights.is_none() {
            "N/A".into()
        } else if traffic_stale {
            "STALE > 1 s".into()
        } else if lights.is_some_and(Vec::is_empty) {
            "No detections".into()
        } else if colors.is_empty() {
            "UNKNOWN".into()
        } else if colors.len() == 1 {
            colors[0].to_owned()
        } else {
            format!("{} detections", colors.len())
        };
        text(
            &hud,
            Pos2::new(traffic_rect.center().x, traffic_rect.top() + 57.0),
            traffic_status,
            9.0,
            MUTED,
        );
        let pedals = cells[4];
        for (i, (label, field, c)) in [
            ("THROTTLE", "throttle_percentage", ACCENT),
            ("BRAKE", "brake_percentage", Color32::LIGHT_RED),
        ]
        .iter()
        .enumerate()
        {
            let y = pedals.top() + 10.0 + i as f32 * 33.0;
            text(
                &hud,
                Pos2::new(pedals.center().x, y),
                format!("{label} {}%", number(&chassis[*field], false)),
                10.0,
                color,
            );
            let bar = Rect::from_min_size(
                Pos2::new(pedals.left() + 8.0, y + 10.0),
                Vec2::new(cell_width - 16.0, 6.0),
            );
            hud.painter().rect_filled(bar, 3.0, Color32::from_gray(50));
            if let Some(v) = chassis[*field].as_f64() {
                hud.painter().rect_filled(
                    Rect::from_min_size(
                        bar.min,
                        Vec2::new(
                            bar.width() * v.clamp(0.0, 100.0) as f32 / 100.0,
                            bar.height(),
                        ),
                    ),
                    3.0,
                    *c,
                );
            }
        }
        let status = data["error"]
            .as_str()
            .or_else(|| data["notice"].as_str())
            .or_else(|| data["streams"]["chassis"]["notice"].as_str())
            .map(str::to_owned)
            .unwrap_or_else(|| {
                age.map(|a| {
                    format!(
                        "{} · sample age {a:.0} ms · feedback",
                        if stale { "STALE" } else { "CHASSIS" }
                    )
                })
                .unwrap_or("No chassis sample at or before cursor".into())
            });
        let status_rect = Rect::from_min_size(
            Pos2::new(rect.left() + 10.0, rect.bottom() - 27.0),
            Vec2::new(width - 20.0, 20.0),
        );
        hud.put(status_rect,egui::Label::new(egui::RichText::new(status).size(10.0).color(if data["error"].is_string(){Color32::LIGHT_RED}else if stale {AMBER}else{MUTED})).truncate()).on_hover_text(format!("Source: /apollo/canbus/chassis\nAll vehicle instruments are chassis feedback, never control commands.\nMissing/non-finite fields display —, not zero.\nSteering is signed percentage, not degrees.\nREMOTE requires an explicit REMOTE mode in the source; standard Apollo Chassis has no REMOTE enum.\nParking brake: {}\nTurn signal: {}\nTraffic detections (no single route light assumed): {}",chassis["parking_brake"],chassis["signal.turn_signal"],traffic));
    }
    ui.ctx().data_mut(|d|{
        let key=egui::Id::new("ad_dashboard_views");
        let mut views=d.get_temp::<Value>(key).unwrap_or(json!([]));
        let camera=d.get_temp::<Value>(egui::Id::new(("ad_camera",view_id)).with("diagnostic"));
        if let Some(items)=views.as_array_mut(){items.push(json!({"view_id":view_id.to_string(),"expanded":expanded,"pane":[pane.left(),pane.top(),pane.right(),pane.bottom()],"rect":[rect.left(),rect.top(),rect.right(),rect.bottom()],"toggle":[toggle_rect.center().x,toggle_rect.center().y],"camera":camera,"speed":speed_diagnostic}));}
        d.insert_temp(key,views);
    });
}

#[cfg(test)]
mod tests {
    #[test]
    fn speed_units_convert_feedback_without_inventing_missing_values() {
        assert_eq!(super::speed_text(Some(10.0), false), "36.0");
        assert_eq!(super::speed_text(Some(10.0), true), "10.0");
        assert_eq!(super::speed_text(None, false), "—");
        assert_eq!(super::speed_text(Some(f64::NAN), true), "—");
        assert_eq!(super::speed_text(Some(-2.0), false), "-7.2");
    }
}
