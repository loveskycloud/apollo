//! Playback protocol shared by the AD shell and its regression tests.

pub(super) const SOURCE_ENTITY: &str = "/__web_monitor_session/source";

/// Immutable source identity, carried in the recording rather than inferred
/// from a global last-opened-file cache or an application name.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(super) struct RecordingSource {
    pub version: u32,
    pub path: String,
    pub size: String,
    pub modified_ns: String,
    pub topics: Vec<String>,
}

impl RecordingSource {
    pub fn validate(&self) -> Result<(), String> {
        if self.version != 1
            || !self.path.ends_with(".mcap")
            || self.size.parse::<u64>().is_err()
            || self.modified_ns.parse::<u128>().is_err()
        {
            return Err("Invalid recording source descriptor".into());
        }
        Ok(())
    }

    pub fn same_file(&self, other: &Self) -> bool {
        self.path == other.path && self.size == other.size && self.modified_ns == other.modified_ns
    }
}

/// Per-tab user intent. Cache coverage and transient request state are never
/// persisted: recovery must receive a fresh, ordered stream receipt.
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub(super) struct PlaybackBookmark {
    pub source: RecordingSource,
    pub clock: String,
    pub time_ns: i64,
    pub layers: std::collections::HashMap<String, bool>,
}

impl PlaybackBookmark {
    pub fn validate(&self) -> Result<(), String> {
        self.source.validate()?;
        if !matches!(self.clock.as_str(), "publish_time" | "message_time") || self.time_ns < 0 {
            return Err("Invalid saved playback clock or cursor".into());
        }
        Ok(())
    }
}

#[derive(Debug, serde::Deserialize)]
pub(super) struct WindowReceipt {
    pub receipt: String,
    pub begin_ns: i64,
    pub end_ns: i64,
    pub seek_ns: i64,
    pub ready_ns: i64,
    pub reset: bool,
}

impl WindowReceipt {
    pub fn parse(body: &str) -> Result<Self, String> {
        let value: Self = serde_json::from_str(body).map_err(|err| err.to_string())?;
        if value.end_ns <= value.begin_ns || !value.receipt.starts_with("/__web_monitor_buffer/") {
            return Err("Invalid playback interval or receipt entity".into());
        }
        Ok(value)
    }

    pub fn cached_range(&self) -> (i64, i64) {
        // Initial camera bootstrap skips data before the random-access point.
        // Ordinary seek windows still contain valid lidar/pose data, even
        // before the first camera keyframe. Do not block those channels.
        let begin = if self.reset {
            self.begin_ns.max(self.ready_ns)
        } else {
            self.begin_ns
        };
        (begin, self.end_ns)
    }
}

#[cfg(test)]
mod tests {
    use super::{PlaybackBookmark, RecordingSource, WindowReceipt};

    fn source() -> RecordingSource {
        RecordingSource {
            version: 1,
            path: "/data/run.mcap".into(),
            size: "120".into(),
            modified_ns: "1789290000000000000".into(),
            topics: vec!["/apollo/localization/pose".into()],
        }
    }

    #[test]
    fn source_identity_rejects_replaced_files_not_layer_changes() {
        let original = source();
        assert!(original.validate().is_ok());
        let mut changed = original.clone();
        changed.topics.clear();
        assert!(original.same_file(&changed));
        changed.modified_ns.push('1');
        assert!(!original.same_file(&changed));
        changed = original.clone();
        changed.path = "/data/other.mcap".into();
        assert!(!original.same_file(&changed));
        changed = original.clone();
        changed.size = "121".into();
        assert!(!original.same_file(&changed));
    }

    #[test]
    fn bookmark_roundtrip_preserves_exact_clock_cursor_and_hidden_layers() {
        let mut bookmark = PlaybackBookmark {
            source: source(),
            clock: "message_time".into(),
            time_ns: 1789290000000000123,
            layers: [("/apollo/localization/pose".into(), false)].into(),
        };
        let decoded: PlaybackBookmark =
            serde_json::from_str(&serde_json::to_string(&bookmark).unwrap()).unwrap();
        assert_eq!(decoded.time_ns, bookmark.time_ns);
        assert_eq!(decoded.layers, bookmark.layers);
        assert!(decoded.validate().is_ok());
        for invalid in ["log_time", "message_publish_time", ""] {
            bookmark.clock = invalid.into();
            assert!(bookmark.validate().is_err());
        }
        bookmark.clock = "publish_time".into();
        bookmark.source.version = 2;
        assert!(bookmark.validate().is_err());
    }

    fn response(reset: &str) -> String {
        format!(
            r#"{{"status":"ok","receipt":"/__web_monitor_buffer/test","begin_ns":100,"end_ns":1000,"seek_ns":800,"ready_ns":800,"reset":{reset}}}"#
        )
    }

    #[test]
    fn real_json_boolean_initializes_paused_playhead() {
        let receipt = WindowReceipt::parse(&response("true")).unwrap();
        assert!(receipt.reset);
        assert_eq!(receipt.seek_ns, 800);
        assert_eq!(receipt.cached_range(), (800, 1000));
    }

    #[test]
    fn seek_before_camera_keyframe_keeps_lidar_window_available() {
        let receipt = WindowReceipt::parse(&response("false")).unwrap();
        assert!(!receipt.reset);
        assert_eq!(receipt.cached_range(), (100, 1000));
    }

    #[test]
    fn protocol_errors_are_not_silently_treated_as_merge_success() {
        assert!(WindowReceipt::parse(&response("\"true\"")).is_err());
        assert!(WindowReceipt::parse(&response("null")).is_err());
        assert!(WindowReceipt::parse(r#"{"status":"ok"}"#).is_err());
    }
}
