//! Per-topic import coverage for an append-only playback recording.
//!
//! Re-importing a camera's preceding GOP gives the same access units new row
//! IDs. The resulting duplicate timestamps break video decoding and waste RAM.

#[derive(Default)]
pub(super) struct PlaybackCoverage {
    ranges: std::collections::HashMap<String, Vec<(u64, u64)>>,
}

impl PlaybackCoverage {
    pub fn clear(&mut self) {
        self.ranges.clear();
    }

    pub fn missing(&self, topic: &str, begin: u64, end: u64) -> Vec<(u64, u64)> {
        let mut cursor = begin;
        let mut missing = Vec::new();
        if let Some(ranges) = self.ranges.get(topic) {
            for &(b, e) in ranges {
                if e <= cursor {
                    continue;
                }
                if b >= end {
                    break;
                }
                if b > cursor {
                    missing.push((cursor, b.min(end)));
                }
                cursor = cursor.max(e);
            }
        }
        if cursor < end {
            missing.push((cursor, end));
        }
        missing
    }

    pub fn insert(&mut self, topic: String, begin: u64, end: u64) {
        if begin >= end {
            return;
        }
        let ranges = self.ranges.entry(topic).or_default();
        ranges.push((begin, end));
        ranges.sort_unstable();
        let mut merged: Vec<(u64, u64)> = Vec::new();
        for &(b, e) in ranges.iter() {
            if let Some(last) = merged.last_mut().filter(|last| b <= last.1) {
                last.1 = last.1.max(e);
            } else {
                merged.push((b, e));
            }
        }
        *ranges = merged;
    }
}

#[cfg(test)]
mod tests {
    use super::PlaybackCoverage;

    #[test]
    fn camera_gop_overlap_is_not_reimported() {
        let mut c = PlaybackCoverage::default();
        c.insert("camera".into(), 8, 12);
        assert_eq!(c.missing("camera", 8, 14), vec![(12, 14)]);
        assert!(c.missing("camera", 9, 11).is_empty());
        assert_eq!(c.missing("lidar", 8, 14), vec![(8, 14)]);
    }

    #[test]
    fn backwards_seek_preserves_holes_and_merges_adjacent_ranges() {
        let mut c = PlaybackCoverage::default();
        c.insert("lidar".into(), 8, 12);
        c.insert("lidar".into(), 2, 4);
        assert_eq!(c.missing("lidar", 0, 14), vec![(0, 2), (4, 8), (12, 14)]);
        c.insert("lidar".into(), 4, 8);
        assert_eq!(c.missing("lidar", 2, 12), vec![]);
        c.clear();
        assert_eq!(c.missing("lidar", 2, 12), vec![(2, 12)]);
    }
}
