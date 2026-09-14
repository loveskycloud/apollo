//! Compact semantic scene emitted by the Apollo converter.
use crate::parsers::{MessageParser, ParserContext};
use prost_reflect::{DynamicMessage, MessageDescriptor, Value};
use re_chunk::{Chunk, RowId};
use re_sdk_types::archetypes::LineStrips3D;

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
