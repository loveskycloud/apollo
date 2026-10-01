"""Generate an explicit synthetic Apollo HDMap and matching WorldSim scenarios."""
import json
from pathlib import Path

from google.protobuf import text_format
from modules.common_msgs.map_msgs.map_pb2 import Map
from modules.routing.proto.topo_graph_pb2 import Graph


def create(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    map_dir = directory / "map"
    map_dir.mkdir(exist_ok=True)
    hdmap = Map()
    hdmap.header.version = b"ml-planning-demo-v1"
    hdmap.header.district = b"synthetic-training-corridor"
    lane = hdmap.lane.add()
    lane.id.id = "ml_lane"
    lane.length, lane.speed_limit = 80, 3
    lane.type, lane.turn, lane.direction = lane.CITY_DRIVING, lane.NO_TURN, lane.FORWARD

    def curve(target, y):
        segment = target.segment.add()
        segment.s, segment.length, segment.heading = 0, 80, 0
        segment.start_position.x, segment.start_position.y = 1000, y
        for x in range(1000, 1081, 2):
            point = segment.line_segment.point.add()
            point.x, point.y, point.z = x, y, 0

    curve(lane.central_curve, 2000)
    for boundary, y in ((lane.left_boundary, 2004), (lane.right_boundary, 1996)):
        curve(boundary.curve, y)
        boundary.length = 80
        boundary.boundary_type.add(s=0).types.append(4)
    for samples in (lane.left_sample, lane.right_sample, lane.left_road_sample, lane.right_road_sample):
        for s in (0, 80):
            samples.add(s=s, width=4)
    road = hdmap.road.add()
    road.id.id = "ml_road"
    road.type = road.CITY_ROAD
    section = road.section.add()
    section.id.id = "ml_section"
    section.lane_id.add().id = "ml_lane"
    for name in ("base_map", "sim_map"):
        (map_dir / (name + ".bin")).write_bytes(hdmap.SerializeToString())
        (map_dir / (name + ".txt")).write_text(text_format.MessageToString(hdmap))
    graph = Graph(hdmap_version="ml-planning-demo-v1", hdmap_district="synthetic-training-corridor")
    node = graph.node.add(lane_id="ml_lane", road_id="ml_road", length=80, cost=0, is_virtual=False)
    node.central_curve.CopyFrom(lane.central_curve)
    (map_dir / "routing_map.bin").write_bytes(graph.SerializeToString())
    (map_dir / "routing_map.txt").write_text(text_format.MessageToString(graph))
    vehicle = directory / "vehicle.pb.txt"
    vehicle.write_text('''vehicle_param {
  brand: NEOLIX
  front_edge_to_center: 1.0
  back_edge_to_center: 1.0
  left_edge_to_center: 0.6
  right_edge_to_center: 0.6
  length: 2.0
  width: 1.2
  height: 1.5
  min_turn_radius: 4.0
  max_acceleration: 1.0
  max_deceleration: -2.0
  max_steer_angle: 0.6
  max_steer_angle_rate: 1.0
  steer_ratio: 1.0
  wheel_base: 1.2
  wheel_rolling_radius: 0.2
  max_abs_speed_when_stopped: 0.1
}
''')
    return map_dir, vehicle


def scenario(kind, obstacle_offset=0):
    return {"id": "ml-" + kind, "name": "PPO " + kind + " / synthetic HDMap",
            "duration": 30, "mapId": "ml-corridor", "ego": {
                "id": "ego", "position": {"x": 1010, "y": 2000}, "heading": 0,
                "activeRouteId": "route", "routes": [{"id": "route", "waypoints": [
                    {"position": {"x": 1065, "y": 2000}, "heading": 0}]}]},
            "agents": [] if kind == "straight" else [{"id": "box", "type": "AGENT_TYPE_STATIC",
                "position": {"x": 1035, "y": 2000 + obstacle_offset}, "heading": 0,
                "size": {"x": 1, "y": 1, "z": 1.5}}]}


if __name__ == "__main__":
    base = Path(__file__).resolve().parent / "examples"
    create(base)
    for kind in ("straight", "avoid"):
        (base / (kind + ".worldsim.scenario.json")).write_text(json.dumps(scenario(kind), indent=2))
