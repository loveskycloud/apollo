"""Versioned task documents used by generators and execution backends."""
from dataclasses import dataclass
import json
from pathlib import Path

from .binary import Binary, binary_from_dict, sha256


PROTOCOL = 'apollo.simulation.task'
VERSION = 1
PNC_MODULES = ['PREDICTION', 'PLANNING', 'CONTROL', 'ROUTING']
ML_MODULES = ['ML_PLANNING', 'ROUTING']


@dataclass(frozen=True)
class SimulationTask:
    binary: Binary
    config: dict
    input_sha256: str
    input_id: str | None = None

    def to_dict(self):
        return dict(protocol=PROTOCOL, version=VERSION, binary=self.binary.to_dict(),
                    input=dict(id=self.input_id, sha256=self.input_sha256), config=self.config)

    @classmethod
    def from_dict(cls, value):
        if value.get('protocol') != PROTOCOL or value.get('version') != VERSION:
            raise ValueError('Unsupported simulation task protocol/version')
        binary = binary_from_dict(value['binary'])
        task = cls(binary, value['config'], value['input']['sha256'], value['input'].get('id'))
        task.verify_input()
        return task

    def verify_input(self):
        if sha256(self.config['source']) != self.input_sha256:
            raise RuntimeError('Scenario/input changed after task generation')


def get_scenario(*, input_file=None, input_id=None):
    """The future scenario-ID provider plugs in here, before task generation."""
    if input_id is not None:
        raise ValueError('Scenario ID service (-i) is not connected yet; use -f PATH')
    if input_file is None:
        raise ValueError('A scenario/input file is required')
    path = Path(input_file).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('Scenario/input must be a file: ' + str(path))
    return path


def _map_ready(path):
    return path.is_dir() and all(any((path / (name + suffix)).is_file()
                                    for suffix in ('.bin', '.txt')) for name in ('base_map', 'sim_map'))


def _named_resource(identity, binary_root, workspace, kind):
    if not isinstance(identity, str) or not identity or Path(identity).name != identity:
        raise ValueError(f'Scenario has no simple {kind} identity; specify --map-dir / --profile')
    # Data carried by the selected binary is authoritative; a workspace is an
    # explicitly documented second data source, never a source of its libraries.
    for root in dict.fromkeys((binary_root, Path(workspace))):
        candidates = ([root / 'data/map_data' / identity, root / 'modules/map/data' / identity]
                      if kind == 'map' else [root / 'profiles' / identity])
        matches = {path.resolve() for path in candidates if
                   (_map_ready(path) if kind == 'map' else
                    (path / 'modules/common/data/vehicle_param.pb.txt').is_file())}
        if len(matches) > 1:
            raise ValueError(f'Ambiguous {kind} {identity!r}; select an explicit path')
        if matches:
            return matches.pop()
    raise ValueError(f'Scenario {kind} {identity!r} is unavailable; include it in the package or specify its path')


def generate_task(binary, source, *, workspace, kind='world', planner='pnc',
                  map_dir=None, profile=None, modules=None, model=None,
                  repeat=1, seed=1, step_ms=10, timeout_s=600):
    source = Path(source).resolve(strict=True)
    if kind not in ('world', 'bag') or planner not in ('pnc', 'ml'):
        raise ValueError('Unsupported input kind / planner')
    if kind == 'world':
        if source.suffix != '.json':
            raise ValueError('WORLD input must be a scenario JSON')
        document = json.loads(source.read_text())
        if not isinstance(document.get('ego'), dict):
            raise ValueError('WORLD scenario must contain ego')
        map_dir = map_dir or _named_resource(document.get('mapId', document.get('map_id')),
                                             binary.path, workspace, 'map')
        profile = profile or _named_resource(document['ego'].get('vehicleProfile',
                                            document['ego'].get('vehicle_profile')),
                                            binary.path, workspace, 'vehicle')
    if map_dir is None or profile is None:
        raise ValueError('BAG input requires --map-dir and --profile')
    map_dir = Path(map_dir).expanduser().resolve(strict=True)
    profile = Path(profile).expanduser().resolve(strict=True)
    if not _map_ready(map_dir):
        raise ValueError('Selected map requires base_map and sim_map resources')
    vehicle = profile / 'modules/common/data/vehicle_param.pb.txt'
    if not vehicle.is_file():
        raise ValueError('Selected profile has no vehicle parameters: ' + str(vehicle))
    selected = list(modules if modules is not None else (ML_MODULES if planner == 'ml' else PNC_MODULES))
    if not selected or len(set(selected)) != len(selected):
        raise ValueError('Select distinct algorithm modules')
    if 'ML_PLANNING' in selected and 'PLANNING' in selected:
        raise ValueError('PLANNING and ML_PLANNING are mutually exclusive')
    model = model or ('perfect_planning' if 'ML_PLANNING' in selected else 'kinematic_control')
    if repeat not in (1, 2, 3) or step_ms not in (1, 2, 5, 10) or not 0 <= seed <= 2**32 - 1:
        raise ValueError('Invalid repeat, step_ms or seed')
    if not 10 <= timeout_s <= 7200:
        raise ValueError('timeout_s must be 10..7200 seconds')
    config = dict(kind=kind, source=str(source), map=str(map_dir), profile=str(profile),
                  vehicle=str(vehicle), modules=selected, model=model,
                  repeat=repeat, seed=seed, step_ms=step_ms, timeout_s=timeout_s)
    return SimulationTask(binary, config, sha256(source))
