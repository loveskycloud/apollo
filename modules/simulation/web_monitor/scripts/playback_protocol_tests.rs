// Small harness for the production protocol module, without linking the viewer/GPU.
#[path = "../rerun/crates/viewer/re_viewer/src/ui/ad_playback.rs"]
mod ad_playback;

fn main() {
    let body = r#"{"status":"ok","receipt":"/__web_monitor_buffer/test","begin_ns":100,"end_ns":1000,"seek_ns":800,"ready_ns":800,"reset":true}"#;
    let initial = ad_playback::WindowReceipt::parse(body).unwrap();
    assert!(initial.reset);
    assert_eq!(initial.seek_ns, 800);
    assert_eq!(initial.cached_range(), (800, 1000));
    let seek = ad_playback::WindowReceipt::parse(&body.replace("true", "false")).unwrap();
    assert!(!seek.reset);
    assert_eq!(seek.cached_range(), (100, 1000));
    assert!(ad_playback::WindowReceipt::parse(&body.replace("true", "\"true\"")).is_err());
    assert!(ad_playback::WindowReceipt::parse("{}").is_err());
    println!("PASS: boolean reset, initial seek/cache, pre-keyframe lidar cache, invalid protocol");
}
