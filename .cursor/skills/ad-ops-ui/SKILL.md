---
name: ad-ops-ui
description: >-
  Design and critique Apollo AD ops panels in Rerun/egui (Simulation Config,
  Source, Layers, dark production tools). Use when redesigning or reviewing
  web_monitor / ad_sim / ad_shell UI, when the user says the panel looks ugly,
  unreadable, or not production-ready, or when dropdown/text contrast fails.
---

# AD Ops UI (egui / Rerun)

## Subject

Apollo simulation & playback **ops console** used by engineers daily. Goal: calm,
readable, production dark UI — not a marketing page, not a default egui form.

## Palette (must use)

| Token | Hex | Role |
|-------|-----|------|
| APP_BG | `#1E1A28` | App chrome |
| RAIL_BG | `#161320` | Section inset |
| PANEL_BG | `#2A233A` | Side panel / popup fill |
| CARD_BG | `#3A3150` | Controls |
| CARD_BG_HOVER | `#4A3F66` | Hover |
| ACCENT | `#B894F6` | Focus / active underline |
| ACCENT_STRONG | `#9F7AEA` | Primary CTA / selected chip |
| TEXT | `#F3EEFF` | Primary labels & menu items |
| TEXT_DIM | `#C4B5FD` | Hints only (never sole menu text) |

Never put `TEXT` / light grey on white or near-white. Never put white/light
`TextEdit` surfaces in this panel.

## Hard rules (fail = reject)

1. **Popup readability**: Every open menu/list item must be `TEXT` on `PANEL_BG` or
   `CARD_BG`. Screenshot the open dropdown before calling the work done.
2. **No default egui chrome**: Do not rely on unstyled `ComboBox` / `TextEdit` /
   `selectable_value` alone — wrap with explicit `Frame` fill + `RichText::color`.
3. **Scoped visuals do not theme popups**: `ui.scope` + `visuals_mut` on the
   trigger does **not** reliably style the popup window. Prefer
   `Popup::menu(&response).show(|ui| { Frame::new().fill(PANEL_BG)... })` or an
   `Area` with an explicit dark `Frame`.
4. **Contrast**: Labels ≥ `TEXT`; hints may use `TEXT_DIM`. Checkbox/chip labels
   must not be dim-on-dim.
5. **Density**: Ops forms are compact. Prefer one row per field; hide advanced
   path paste behind a collapse. Avoid stacked duplicate “Choose…” + empty white box.
6. **CTA**: One primary button, white text on `ACCENT_STRONG`, full width.
7. **Stay in AD chrome**: Reuse `ad_shell::theme` tokens; do not invent a second
   purple system.

## Layout pattern for config panels

```
[ Tab | Tab ]
Title + one-line subtitle
┌ Section (RAIL_BG card) ─────────
│ Label
│ [ Dark trigger ▾ ]   ← opens dark Popup list
│ › Paste path (optional)
└───────────────────────────────
[ Primary CTA ]
```

## Critique checklist (run after every visual change)

Answer yes/no from a **screenshot with menus open**:

- [ ] Open dropdown items are clearly readable (light text on dark fill)
- [ ] Closed fields are dark, not white slabs
- [ ] Section cards share one visual language
- [ ] Module toggles readable on/off
- [ ] Primary CTA readable and obvious
- [ ] No stacked empty path boxes fighting the dropdown
- [ ] Fits a ~400px side panel without looking sparse or noisy

If any item is **no**, fix before shipping. Do not claim “production-ready” until
the open-menu screenshot passes.

## Implementation notes

- Files: `re_viewer/src/ui/ad_sim.rs`, `ad_shell.rs` (`theme` / `apply_theme`)
- After UI edits: rebuild web viewer (`simulation/web_monitor/scripts/build_viewer.sh`)
- Prefer `Popup::menu` (egui 0.36) over raw `ComboBox` for catalog pickers
