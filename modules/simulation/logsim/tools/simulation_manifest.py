"""Load the simulation recording contract and select module topic lists."""

import json
from pathlib import Path


MANIFEST_PATH = (Path(__file__).resolve().parents[2] /
                 "simulator/conf/simulation.manifest.json")


def load_manifest(path=MANIFEST_PATH):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"modules", "common"}:
        raise ValueError("Simulation manifest requires modules and common")
    if not isinstance(manifest["modules"], list):
        raise ValueError("Simulation manifest modules must be a list")

    def topics(values):
        if (not isinstance(values, list) or
                any(not isinstance(topic, str) or not topic.startswith("/")
                    or any(character.isspace() for character in topic)
                    for topic in values)):
            raise ValueError("Simulation manifest topics must be absolute topic lists")
        if len(set(values)) != len(values):
            raise ValueError("Duplicate topic in simulation manifest list")

    topics(manifest["common"])
    names = set()
    for module in manifest["modules"]:
        if (not isinstance(module, dict) or
                set(module) != {"name", "inputs", "outputs"}):
            raise ValueError("Simulation manifest module requires name, inputs and outputs")
        name = module["name"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Invalid or duplicate simulation manifest module name")
        names.add(name)
        topics(module["inputs"])
        topics(module["outputs"])
    return manifest


def record_topics(selected_modules, manifest, include_bag=True):
    modules = {module["name"]: module for module in manifest["modules"]}
    topics = list(manifest["common"])
    for name in selected_modules:
        if name not in modules:
            raise ValueError(f"Module missing from simulation manifest: {name}")
        topics.extend(modules[name]["inputs"])
        topics.extend(modules[name]["outputs"])
    return list(dict.fromkeys(topic for topic in topics
                              if include_bag or not topic.startswith("/bag/")))


def bag_topic_mappings(selected_modules, manifest):
    return {topic[4:]: topic for topic in record_topics(selected_modules, manifest)
            if topic.startswith("/bag/")}


def channel_policy(selected_modules, manifest, input_kind="BAG"):
    """Keep regenerated module outputs out of the live bag input namespace."""
    if input_kind not in ("BAG", "WORLD"):
        raise ValueError("Unknown simulation input kind")
    entries = {module["name"]: module for module in manifest["modules"]}
    record = record_topics(selected_modules, manifest, include_bag=input_kind == "BAG")
    inject = [topic for topic in manifest["common"] if not topic.startswith("/bag/")]
    suppress = []
    for name in selected_modules:
        inject.extend(topic for topic in entries[name]["inputs"]
                      if not topic.startswith("/bag/"))
        suppress.extend(topic for topic in entries[name]["outputs"]
                        if not topic.startswith("/bag/"))
    if input_kind == "BAG" and "/apollo/planning/command" in inject:
        inject.append("/apollo/planning_command_history")
    if input_kind == "WORLD":
        if {"LOCALIZATION", "PERCEPTION"}.intersection(selected_modules):
            raise ValueError("Sensor modules require BAG sensor inputs")
        inject.extend(["/apollo/perception/obstacles", "/apollo/raw_routing_request",
                       "/apollo/planning/command"])
    suppress = list(dict.fromkeys(suppress))
    inject = list(dict.fromkeys(topic for topic in inject if topic not in suppress))
    mappings = bag_topic_mappings(selected_modules, manifest) if input_kind == "BAG" else {}
    if input_kind == "BAG" and "/apollo/planning_command_history" in inject:
        mappings["/apollo/planning_command_history"] = "/bag/apollo/planning_command_history"
        record.append("/bag/apollo/planning_command_history")
    return {"inject_channels": inject, "suppress_channels": suppress,
            "record_channels": list(dict.fromkeys(record + inject)),
            "bag_topic_mappings": mappings}
