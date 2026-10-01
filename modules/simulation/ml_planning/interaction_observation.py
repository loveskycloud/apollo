"""Versioned perception-only actor features, shared by training/evaluation.

The first 16 fields retain V2 semantics. Four risk-ranked bodies add
ds/10, dl, present, length/2, width/2, vs, vl, sin(relative heading).
No actor routes, future samples or scenario labels enter these features.
"""
import numpy as np

INTERACTION_OBS_DIM = 48


def append_interactions(legacy, x, y, speed, bodies, visible):
    dx = bodies["ox"] - x[:, None]
    closing = np.where(dx >= 0, speed[:, None]-bodies["ovs"],
                       bodies["ovs"]-speed[:, None])
    risk = np.where(visible, abs(dx)/np.maximum(.2, closing), np.inf)
    order = np.argsort(risk, axis=1, kind="stable")[:, :4]
    take = lambda array: np.take_along_axis(array, order, axis=1)
    present = take(visible)
    heading = bodies.get("yaw", np.arctan2(bodies["ovl"], bodies["ovs"]))
    fields = [np.clip(take(dx)/10, -1, 1), take(bodies["oy"])-y[:, None],
              present, take(bodies["length"])/2, take(bodies["width"])/2,
              take(bodies["ovs"]), take(bodies["ovl"]), np.sin(take(heading))]
    slots = np.stack(fields, axis=-1) * present[..., None]
    return np.concatenate((legacy, slots.reshape(len(x), -1)), axis=-1).astype(np.float32)


def lateral_indices(dim):
    return [0, 1, 4, 15] + ([i+j for i in range(16, dim, 8) for j in (1, 6, 7)]
                              if dim == INTERACTION_OBS_DIM else [])
