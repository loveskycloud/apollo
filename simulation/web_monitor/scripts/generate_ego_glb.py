#!/usr/bin/env python3
"""Generate an original, illustrative 1.2 x 0.8 m ego glyph (not collision geometry).
Apollo localization convention: +Y forward, +X right, +Z up. Origin at glyph center.
No downloaded meshes or third-party licensing dependency.
"""
import json
import struct
from pathlib import Path


def generate(path):
    positions, indices, meshes, views, accessors, nodes = [], [], [], [], [], []
    blob = bytearray()
    boxes = [((0, 0, .3), (.72, 1.15, .35), 0), ((0, -.08, .57), (.6, .62, .25), 1),
             ((-.23, .585, .35), (.16, .02, .09), 2), ((.23, .585, .35), (.16, .02, .09), 2)]
    boxes += [((x, y, .17), (.12, .28, .3), 3) for x in (-.34, .34) for y in (-.38, .38)]
    for center, size, material in boxes:
        verts = [[center[j] + size[j]*(bits[j]-.5) for j in range(3)] for bits in
                 [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)]]
        idx = [0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,1,2,6,1,6,5,2,3,7,2,7,6,3,0,4,3,4,7]
        offset = len(blob)
        blob.extend(struct.pack("<24f", *[v for pt in verts for v in pt]))
        views.append({"buffer":0,"byteOffset":offset,"byteLength":96,"target":34962})
        accessors.append({"bufferView":len(views)-1,"componentType":5126,"count":8,"type":"VEC3",
                          "min":[min(v[j] for v in verts) for j in range(3)],"max":[max(v[j] for v in verts) for j in range(3)]})
        position = len(accessors)-1
        offset = len(blob)
        blob.extend(struct.pack("<36H", *idx))
        views.append({"buffer":0,"byteOffset":offset,"byteLength":72,"target":34963})
        accessors.append({"bufferView":len(views)-1,"componentType":5123,"count":36,"type":"SCALAR"})
        meshes.append({"primitives":[{"attributes":{"POSITION":position},"indices":len(accessors)-1,"material":material}]})
        nodes.append({"mesh":len(meshes)-1})
    doc = {"asset":{"version":"2.0","generator":"Apollo web_monitor original ego glyph"},"scene":0,
           "scenes":[{"nodes":list(range(len(nodes)))}],"nodes":nodes,"meshes":meshes,"bufferViews":views,"accessors":accessors,
           "buffers":[{"byteLength":len(blob)}],"materials":[{"pbrMetallicRoughness":{"baseColorFactor":c,"metallicFactor":0,"roughnessFactor":.7},"doubleSided":True}
           for c in [[.08,.65,.95,1],[.03,.12,.2,1],[1,.95,.65,1],[.035,.035,.035,1]]]}
    text = json.dumps(doc,separators=(",",":")).encode()
    text += b" "*((-len(text))%4)
    blob += b"\0"*((-len(blob))%4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<III",0x46546c67,2,28+len(text)+len(blob))+struct.pack("<II",len(text),0x4e4f534a)+text+struct.pack("<II",len(blob),0x004e4942)+blob)
    print(path)


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1]/"rerun/crates/store/re_mcap/assets/ego_vehicle.glb")
