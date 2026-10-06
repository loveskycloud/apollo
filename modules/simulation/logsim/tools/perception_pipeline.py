"""Freeze a configured Perception launch into one simulation DAG and topic contract."""

import copy
from pathlib import Path
import re
import xml.etree.ElementTree as ET

DEFAULT_LAUNCH = "modules/perception/launch/perception_lidar.launch"


def prepare_perception_pipeline(runtime, launch, target, manifest):
    from google.protobuf import text_format
    import sys
    sys.path.insert(0, "/opt/apollo/neo/python")
    from cyber.proto.dag_conf_pb2 import DagConfig

    runtime, launch, target = Path(runtime), Path(launch), Path(target)

    def configured_path(value):
        # Launch profiles often retain /apollo paths. Resolve those against the
        # frozen task overlay, so live profile switches cannot affect a run.
        value = value.removeprefix("/apollo/")
        path = Path(value)
        return path if path.is_absolute() else runtime / path

    if not launch.is_absolute():
        launch = configured_path(str(launch))
    if launch.suffix == ".launch":
        paths = [configured_path(node.text.strip())
                 for node in ET.parse(launch).getroot().iter("dag_conf")
                 if node.text and node.text.strip()]
    elif launch.suffix == ".dag":
        paths = [launch]
    else:
        raise ValueError("Perception configuration must be a .launch or .dag")
    if not paths:
        raise ValueError(f"Perception launch has no DAGs: {launch}")
    merged = DagConfig()
    sensors = []
    outputs = ["/apollo/perception/obstacles"]
    for path in paths:
        dag = text_format.Parse(path.read_text(), DagConfig())
        if not dag.module_config:
            raise ValueError(f"Perception DAG has no components: {path}")
        for module in dag.module_config:
            for component in list(module.components) + list(module.timer_components):
                config = component.config
                sensors.extend(reader.channel for reader in getattr(config, "readers", [])
                               if reader.channel.startswith("/apollo/sensor/"))
                if config.config_file_path:
                    content = configured_path(config.config_file_path).read_text()
                    content = re.sub(r"(?m)#.*$", "", content)
                    outputs.extend(re.findall(
                        r'\b(?:output_channel_name|output_topic|output_obstacles_channel_name)\s*:\s*"(/apollo/perception/[^"\s]+)"',
                        content))
                for field in ("config_file_path", "flag_file_path"):
                    value = getattr(config, field, "")
                    if value.startswith("/apollo/"):
                        setattr(config, field, str(configured_path(value)))
        merged.MergeFrom(dag)
    if not sensors:
        raise ValueError(f"Perception pipeline has no external sensor readers: {launch}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text_format.MessageToString(merged))
    selected = copy.deepcopy(manifest)
    entry = next(module for module in selected["modules"] if module["name"] == "PERCEPTION")
    inputs = list(dict.fromkeys(sensors + ["/apollo/localization/pose", "/tf", "/tf_static"]))
    entry["inputs"] = inputs
    entry["outputs"] = [topic for source in dict.fromkeys(outputs)
                        for topic in (source, "/bag" + source)]
    return selected

