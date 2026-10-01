"""Render audited map geometry and kinematic witnesses, not planner screenshots."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'modules/simulation/simulator'))
from quality_metrics import body


def main():
    source = ROOT/'modules/simulation/scene_editor/examples/parking_lab'
    cases = json.loads((source/'manifest.json').read_text())['cases']
    fig, axes = plt.subplots(6, 6, figsize=(22, 16), constrained_layout=True)
    for ax, case in zip(axes.flat, cases):
        p = case['parking']
        path = json.loads((source/case['file'].replace('.worldsim.scenario.json', '.witness.json')).read_text())
        ox, oy = path[0][:2]
        local = lambda points: [(x-ox, y-oy) for x, y in points]
        ax.add_patch(Polygon(local(p['area']), facecolor='#edf1f5', edgecolor='#64748b'))
        ax.add_patch(Polygon(local(p['slot']), facecolor='#d4f0df', edgecolor='#16834a'))
        ax.add_patch(Polygon(local(body(*p['goal'], .62, .1, .25).exterior.coords),
                             facecolor='#16834a', alpha=.6))
        segments = [[(a[0]-ox,a[1]-oy),(b[0]-ox,b[1]-oy)] for a,b in zip(path,path[1:])]
        ax.add_collection(LineCollection(segments, colors=['#2869cf' if b[3]>0 else '#cc6228'
                                                          for b in path[1:]], linewidth=1.2))
        ax.plot(0,0,'o',color='#1e293b',markersize=3)
        ax.set_title(f"{p['style']} / {p['entry']}\nwidth {p['width_m']:.2f} m | aisle {p['aisle_width_m']:.1f} m",fontsize=9)
        ax.set_aspect('equal');ax.autoscale_view();ax.tick_params(labelsize=7)
        ax.grid(alpha=.15)
    fig.suptitle('Ranger Mini V3 parking laboratory — 36 audited combinations\n'
                 'Blue: forward | orange: reverse | green: bay + final full vehicle body | axes: metres relative to start\n'
                 'Geometric reachability witnesses; native closed-loop acceptance is reported separately', fontsize=15)
    fig.savefig(source/'parking-layout.png',dpi=110)
    plt.close(fig)


if __name__ == '__main__':
    main()
