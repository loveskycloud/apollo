//! Compact semantic scene emitted by the Apollo converter.
use crate::parsers::{MessageParser, ParserContext};
use prost_reflect::{DynamicMessage, MessageDescriptor, Value};
use re_chunk::{Chunk, RowId};
use re_sdk_types::archetypes::LineStrips3D;

/// Fit the original Y-forward ego glyph to Apollo's configured edge distances.
/// Keep the recorded pose untouched: its origin is the rear axle, not box center.
pub(super) fn vehicle_asset(geometry: &str) -> anyhow::Result<Vec<u8>> {
    let geometry: serde_json::Value = serde_json::from_str(geometry)?;
    let number = |key: &str| -> anyhow::Result<f64> {
        let value = geometry[key]
            .as_f64()
            .ok_or_else(|| anyhow::anyhow!("Missing vehicle {key}"))?;
        anyhow::ensure!(value.is_finite() && value > 0.0, "Invalid vehicle {key}");
        Ok(value)
    };
    let left = number("left_edge_to_center")?;
    let right = number("right_edge_to_center")?;
    let front = number("front_edge_to_center")?;
    let back = number("back_edge_to_center")?;
    let size = [number("width")?, number("length")?, number("height")?];
    anyhow::ensure!(
        (size[0] - left - right).abs() < 1e-5 && (size[1] - front - back).abs() < 1e-5,
        "Vehicle dimensions disagree with edge distances"
    );
    let original = include_bytes!("../../assets/ego_vehicle.glb");
    let json_len = u32::from_le_bytes(original[12..16].try_into()?) as usize;
    let mut doc: serde_json::Value = serde_json::from_slice(&original[20..20 + json_len])?;
    let mut low = [f64::INFINITY; 3];
    let mut high = [f64::NEG_INFINITY; 3];
    for accessor in doc["accessors"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("Missing model accessors"))?
    {
        if accessor["type"] == "VEC3" {
            for i in 0..3 {
                low[i] = low[i].min(
                    accessor["min"][i]
                        .as_f64()
                        .ok_or_else(|| anyhow::anyhow!("Missing model bounds"))?,
                );
                high[i] = high[i].max(
                    accessor["max"][i]
                        .as_f64()
                        .ok_or_else(|| anyhow::anyhow!("Missing model bounds"))?,
                );
            }
        }
    }
    let target_min = [-left, -back, 0.0];
    let mut scale = [0.0; 3];
    let mut translation = [0.0; 3];
    for i in 0..3 {
        anyhow::ensure!(high[i] > low[i], "Invalid ego model bounds");
        scale[i] = size[i] / (high[i] - low[i]);
        translation[i] = target_min[i] - low[i] * scale[i];
    }
    let children = doc["scenes"][0]["nodes"].clone();
    let nodes = doc["nodes"]
        .as_array_mut()
        .ok_or_else(|| anyhow::anyhow!("Missing model nodes"))?;
    let root = nodes.len();
    nodes.push(
        serde_json::json!({"children": children, "scale": scale, "translation": translation}),
    );
    doc["scenes"][0]["nodes"] = serde_json::json!([root]);
    let mut json = serde_json::to_vec(&doc)?;
    while json.len() % 4 != 0 {
        json.push(b' ');
    }
    let binary = &original[20 + json_len..];
    let mut result = original[..12].to_vec();
    result[8..12].copy_from_slice(&u32::try_from(20 + json.len() + binary.len())?.to_le_bytes());
    result.extend(u32::try_from(json.len())?.to_le_bytes());
    result.extend_from_slice(b"JSON");
    result.extend(json);
    result.extend_from_slice(binary);
    Ok(result)
}

/// Immutable HD map meshes share the recording's wm_map_local frame.
pub(super) struct MapMeshParser {
    descriptor: MessageDescriptor,
    chunks: Vec<Chunk>,
}

impl MapMeshParser {
    pub fn new(descriptor: MessageDescriptor) -> Self {
        Self {
            descriptor,
            chunks: Vec::new(),
        }
    }
}

impl MessageParser for MapMeshParser {
    fn append(&mut self, ctx: &mut ParserContext, msg: &mcap::Message<'_>) -> anyhow::Result<()> {
        anyhow::ensure!(
            msg.channel.topic.starts_with("/hdmap/"),
            "MapMesh must use /hdmap/ namespace"
        );
        let message = DynamicMessage::decode(self.descriptor.clone(), msg.data.as_ref())?;
        let xyz = message
            .get_field_by_name("xyz")
            .ok_or_else(|| anyhow::anyhow!("MapMesh missing xyz"))?;
        let Value::List(xyz) = xyz.as_ref() else {
            anyhow::bail!("MapMesh xyz must be a list");
        };
        anyhow::ensure!(
            !xyz.is_empty() && xyz.len() % 3 == 0,
            "Invalid MapMesh coordinates"
        );
        let mut vertices = Vec::with_capacity(xyz.len() / 3);
        for p in xyz.chunks_exact(3) {
            let mut vertex = [0.0_f32; 3];
            for (i, value) in p.iter().enumerate() {
                let Value::F64(value) = value else {
                    anyhow::bail!("MapMesh coordinates must be doubles");
                };
                anyhow::ensure!(
                    value.is_finite() && (*value as f32).is_finite(),
                    "Non-finite MapMesh coordinate"
                );
                vertex[i] = *value as f32;
            }
            vertices.push(vertex);
        }
        let indices = message
            .get_field_by_name("triangle_indices")
            .ok_or_else(|| anyhow::anyhow!("MapMesh missing indices"))?;
        let Value::List(indices) = indices.as_ref() else {
            anyhow::bail!("MapMesh indices must be a list");
        };
        anyhow::ensure!(
            !indices.is_empty() && indices.len() % 3 == 0,
            "Invalid MapMesh triangles"
        );
        let mut triangles = Vec::with_capacity(indices.len() / 3);
        for t in indices.chunks_exact(3) {
            let mut triangle = [0_u32; 3];
            for (i, value) in t.iter().enumerate() {
                let Value::U32(value) = value else {
                    anyhow::bail!("MapMesh indices must be uint32");
                };
                anyhow::ensure!(
                    (*value as usize) < vertices.len(),
                    "MapMesh index out of bounds"
                );
                triangle[i] = *value;
            }
            triangles.push(triangle);
        }
        let rgba = message
            .get_field_by_name("rgba")
            .ok_or_else(|| anyhow::anyhow!("MapMesh missing color"))?;
        let Value::U32(rgba) = rgba.as_ref() else {
            anyhow::bail!("MapMesh color must be uint32");
        };
        let mesh = re_sdk_types::archetypes::Mesh3D::new(vertices)
            .with_triangle_indices(triangles)
            .with_albedo_factor(*rgba);
        self.chunks.push(
            Chunk::builder(ctx.entity_path().clone())
                .with_archetype(
                    RowId::new(),
                    re_log_types::TimePoint::STATIC,
                    &re_sdk_types::archetypes::CoordinateFrame::new("wm_map_local"),
                )
                .with_archetype(RowId::new(), re_log_types::TimePoint::STATIC, &mesh)
                .build()?,
        );
        Ok(())
    }

    fn finalize(self: Box<Self>, _: ParserContext) -> anyhow::Result<Vec<Chunk>> {
        Ok(self.chunks)
    }
}

pub(super) struct PlannedPathParser {
    descriptor: MessageDescriptor,
    chunks: Vec<Chunk>,
}
impl PlannedPathParser {
    pub fn new(descriptor: MessageDescriptor) -> Self {
        Self {
            descriptor,
            chunks: Vec::new(),
        }
    }
}
impl MessageParser for PlannedPathParser {
    fn append(&mut self, ctx: &mut ParserContext, msg: &mcap::Message<'_>) -> anyhow::Result<()> {
        let message = DynamicMessage::decode(self.descriptor.clone(), msg.data.as_ref())?;
        let values = message
            .get_field_by_name("xyz")
            .ok_or_else(|| anyhow::anyhow!("PlannedPath missing xyz"))?;
        let Value::List(values) = values.as_ref() else {
            anyhow::bail!("PlannedPath xyz must be repeated double");
        };
        anyhow::ensure!(
            values.len() % 3 == 0,
            "PlannedPath xyz length is not divisible by 3"
        );
        let mut points = Vec::with_capacity(values.len() / 3);
        for xyz in values.chunks_exact(3) {
            let mut p = [0.0_f32; 3];
            for (i, value) in xyz.iter().enumerate() {
                let Value::F64(value) = value else {
                    anyhow::bail!("PlannedPath coordinate must be double");
                };
                anyhow::ensure!(value.is_finite(), "Non-finite PlannedPath coordinate");
                p[i] = *value as f32;
            }
            points.push(p);
        }
        if msg.channel.topic == "/planning/trajectory" {
            let vertices = message.get_field_by_name("ribbon_xyz").ok_or_else(|| {
                anyhow::anyhow!("Planning ribbon missing vehicle geometry; reconvert this record")
            })?;
            let Value::List(vertices) = vertices.as_ref() else {
                anyhow::bail!("Invalid ribbon vertices");
            };
            anyhow::ensure!(
                vertices.len() % 6 == 0,
                "Ribbon requires left/right vertex pairs"
            );
            let mut positions = Vec::with_capacity(vertices.len() / 3);
            for vertex in vertices.chunks_exact(3) {
                let mut xyz = [0.0_f32; 3];
                for (i, value) in vertex.iter().enumerate() {
                    let Value::F64(value) = value else {
                        anyhow::bail!("Invalid ribbon coordinate");
                    };
                    anyhow::ensure!(
                        value.is_finite() && (*value as f32).is_finite(),
                        "Non-finite ribbon coordinate"
                    );
                    xyz[i] = *value as f32;
                }
                positions.push(xyz);
            }
            let colors = message
                .get_field_by_name("vertex_rgba")
                .ok_or_else(|| anyhow::anyhow!("Missing planning ribbon colors"))?;
            let Value::List(colors) = colors.as_ref() else {
                anyhow::bail!("Invalid ribbon colors");
            };
            anyhow::ensure!(
                colors.len() == positions.len(),
                "Ribbon vertex/color count mismatch"
            );
            let colors = colors
                .iter()
                .map(|value| {
                    let Value::U32(value) = value else {
                        anyhow::bail!("Invalid ribbon color");
                    };
                    Ok(*value)
                })
                .collect::<anyhow::Result<Vec<_>>>()?;
            let mut triangles = Vec::new();
            for pair in 0..(positions.len() / 2).saturating_sub(1) {
                let i = u32::try_from(pair * 2)?;
                triangles.extend([[i, i + 1, i + 2], [i + 1, i + 3, i + 2]]);
            }
            let mesh = re_sdk_types::archetypes::Mesh3D::new(positions)
                .with_triangle_indices(triangles)
                .with_vertex_colors(colors);
            let time = crate::util::log_and_publish_timepoint_from_msg(msg, ctx.time_type());
            self.chunks.push(
                Chunk::builder(ctx.entity_path().clone())
                    .with_archetype(
                        RowId::new(),
                        time.clone(),
                        &re_sdk_types::archetypes::CoordinateFrame::new("wm_map_local"),
                    )
                    .with_archetype(RowId::new(), time, &mesh)
                    .build()?,
            );
            return Ok(());
        }
        // Optional strip lengths preserve distinct obstacles/hypotheses. Older
        // v11 schemas have only xyz and remain compatible as one planning line.
        let mut lengths = Vec::new();
        if let Some(value) = message.get_field_by_name("strip_lengths") {
            let Value::List(values) = value.as_ref() else {
                anyhow::bail!("strip_lengths must be a list");
            };
            for value in values {
                let Value::U32(n) = value else {
                    anyhow::bail!("Invalid strip length");
                };
                lengths.push(*n as usize);
            }
        }
        let mut strips = Vec::new();
        if lengths.is_empty() {
            if !points.is_empty() {
                strips.push(points);
            }
        } else {
            anyhow::ensure!(
                lengths.iter().sum::<usize>() == points.len(),
                "Path strip lengths do not match coordinates"
            );
            let mut offset = 0;
            for length in lengths {
                strips.push(points[offset..offset + length].to_vec());
                offset += length;
            }
        }
        let planning = msg.channel.topic == "/planning/trajectory";
        let color = if planning {
            0xFF_B0_30_FF_u32
        } else if msg.channel.topic.starts_with("/perception/") {
            0x40_E0_D0_FF
        } else {
            0x90_9C_FF_FF
        };
        let mut labels = Vec::new();
        if let Some(value) = message.get_field_by_name("labels") {
            let Value::List(values) = value.as_ref() else {
                anyhow::bail!("labels must be a list");
            };
            for value in values {
                let Value::String(label) = value else {
                    anyhow::bail!("Invalid path label");
                };
                labels.push(label.clone());
            }
        }
        if planning {
            labels = vec!["Planning".to_owned()];
        }
        let curve = LineStrips3D::new(strips)
            .with_colors([color])
            .with_radii([re_sdk_types::components::Radius::new_ui_points(
                if planning { 2.5 } else { 1.5 },
            )])
            .with_labels(labels)
            .with_show_labels(true);
        self.chunks.push(
            Chunk::builder(ctx.entity_path().clone())
                .with_archetype(
                    RowId::new(),
                    crate::util::log_and_publish_timepoint_from_msg(msg, ctx.time_type()),
                    &re_sdk_types::archetypes::CoordinateFrame::new("wm_map_local"),
                )
                .with_archetype(
                    RowId::new(),
                    crate::util::log_and_publish_timepoint_from_msg(msg, ctx.time_type()),
                    &curve,
                )
                .build()?,
        );
        Ok(())
    }
    fn finalize(self: Box<Self>, _: ParserContext) -> anyhow::Result<Vec<Chunk>> {
        Ok(self.chunks)
    }
}

#[cfg(test)]
mod tests {
    use super::vehicle_asset;

    #[test]
    fn vehicle_asset_matches_dimensions_and_rear_axle_origin() {
        let geometry = serde_json::json!({"width":0.7,"length":0.72,"height":0.66,
            "left_edge_to_center":0.4,"right_edge_to_center":0.3,
            "front_edge_to_center":0.62,"back_edge_to_center":0.1});
        let asset = vehicle_asset(&geometry.to_string()).unwrap();
        assert_eq!(
            u32::from_le_bytes(asset[8..12].try_into().unwrap()) as usize,
            asset.len()
        );
        let length = u32::from_le_bytes(asset[12..16].try_into().unwrap()) as usize;
        let doc: serde_json::Value = serde_json::from_slice(&asset[20..20 + length]).unwrap();
        let node = &doc["nodes"][doc["scenes"][0]["nodes"][0].as_u64().unwrap() as usize];
        let original_min = [-0.4, -0.575, 0.02];
        let original_max = [0.4, 0.595, 0.695];
        let desired_min = [-0.4, -0.1, 0.0];
        let desired_max = [0.3, 0.62, 0.66];
        for i in 0..3 {
            let scale = node["scale"][i].as_f64().unwrap();
            let translation = node["translation"][i].as_f64().unwrap();
            assert!((original_min[i] * scale + translation - desired_min[i]).abs() < 1e-6);
            assert!((original_max[i] * scale + translation - desired_max[i]).abs() < 1e-6);
        }
        let mut invalid = geometry;
        invalid["width"] = serde_json::json!(2.0);
        assert!(vehicle_asset(&invalid.to_string()).is_err());
    }
}
