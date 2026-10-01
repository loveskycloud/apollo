//! Display layers separate transport topics from render entities.
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub(super) struct Layer {
    pub path: String,
    pub topics: Vec<String>,
    pub entities: Vec<String>,
    pub default_on: bool,
}

pub(super) fn catalog(topics: &[String]) -> Vec<Layer> {
    let mut layers = BTreeMap::<String, Layer>::new();
    for topic in topics {
        let mapped = if topic == "/vehicle" {
            Some(("localization/pose".to_owned(), topic.clone(), true))
        } else if let Some(name) = topic.strip_prefix("/hdmap/") {
            Some((format!("map/{name}"), topic.clone(), true))
        } else if topic == "/planning/trajectory" {
            Some(("planning/trajectory".to_owned(), topic.clone(), false))
        } else if topic.starts_with("/perception/") || topic.starts_with("/prediction/") {
            Some((
                topic.trim_start_matches('/').to_owned(),
                topic.clone(),
                false,
            ))
        } else if let Some(rest) = topic.strip_prefix("/lidar/") {
            rest.strip_suffix("/points").map(|sensor| {
                let sensor = match sensor {
                    "up" => "main",
                    "left" => "side_left",
                    "right" => "side_right",
                    other => other,
                };
                (format!("sensing/lidar/{sensor}"), topic.clone(), false)
            })
        } else if let Some(rest) = topic.strip_prefix("/camera/") {
            let sensor = rest.split('/').next().unwrap_or(rest);
            let name = match sensor {
                "Front120" => "camera_front/front120",
                "Front30" => "camera_front/front30",
                "FrontLeft" => "camera_left",
                "FrontRight" => "camera_right",
                "Rear" => "camera_back",
                "RearLeft" => "camera_back_left",
                "RearRight" => "camera_back_right",
                other => other,
            };
            Some((
                format!("sensing/camera/{name}"),
                format!("/camera/{sensor}"),
                true,
            ))
        } else {
            None
        };
        if let Some((path, entity, default_on)) = mapped {
            let layer = layers.entry(path.clone()).or_insert(Layer {
                path,
                topics: Vec::new(),
                entities: Vec::new(),
                default_on,
            });
            layer.topics.push(topic.clone());
            if !layer.entities.contains(&entity) {
                layer.entities.push(entity);
            }
        }
    }
    layers.into_values().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn semantic_layers_have_one_authority_and_no_invented_sensors() {
        let layers = catalog(
            &[
                "/apollo/planning",
                "/planning/trajectory",
                "/vehicle",
                "/lidar/up/points",
                "/apollo/control",
            ]
            .map(str::to_owned),
        );
        assert_eq!(layers.len(), 3);
        assert!(layers.iter().any(|l| l.path == "sensing/lidar/main"));
        assert!(!layers.iter().any(|l| l.path.contains("camera")));
        let plan = layers
            .iter()
            .find(|l| l.path == "planning/trajectory")
            .unwrap();
        assert_eq!(plan.topics, ["/planning/trajectory"]);
        assert!(!plan.default_on);
    }
    #[test]
    fn camera_components_share_switch_but_front_lenses_remain_distinct() {
        let layers = catalog(
            &[
                "/camera/Front120/video",
                "/camera/Front120/pinhole",
                "/camera/Front30/video",
                "/lidar/left/points",
            ]
            .map(str::to_owned),
        );
        assert_eq!(layers.len(), 3);
        let front = layers
            .iter()
            .find(|l| l.path.ends_with("front120"))
            .unwrap();
        assert_eq!(front.topics.len(), 2);
        assert_eq!(front.entities, ["/camera/Front120"]);
        assert!(layers.iter().any(|l| l.path == "sensing/lidar/side_left"));
    }
}
