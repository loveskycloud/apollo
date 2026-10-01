"""Cross-language contract and curriculum regressions for phase one."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import numpy as np
import torch
from env import VectorEnv
from train import Agent
from verify_policy import actor

HERE = Path(__file__).resolve().parent


class InteractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        source = cls.root/"parity.cc"
        source.write_text('''#include "modules/simulation/ml_planning/planner.h"
#include <iostream>
#include <iomanip>
using namespace apollo::simulation::ml;
int main(int argc,char** argv) {
  Policy policy; policy.Load(argv[1]);
  State p; double goal,half,k0,k2,k5; int n;
  std::cout << std::setprecision(17);
  while(std::cin>>p.s>>p.l>>p.yaw>>p.v>>goal>>half>>k0>>k2>>k5>>n) {
    std::vector<Obstacle> bodies(n);
    for(auto& o:bodies) std::cin>>o.s>>o.l>>o.vs>>o.vl>>o.length>>o.width>>o.yaw;
    const auto obs=Observe(p,bodies,goal,half,half,k0,k2,k5);
    for(double x:obs) std::cout<<x<<' ';
    const auto a=policy.Infer(obs);std::cout<<a[0]<<' '<<a[1]<<'\\n';
  }
}''')
        subprocess.run(["g++","-std=c++17","-O2","-I",str(HERE.parents[2]),
                        str(source),"-o",str(cls.root/"parity")],check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def native(self, env, weights):
        lines=[]
        for i in range(env.n):
            bodies=[]
            for j in range(4):
                if not env.active[i] or not (env.mask[i] if j==0 else env.extra_mask[i,j-1]):
                    continue
                get=lambda name: getattr(env,name)[i] if j==0 else getattr(env,"extra_"+name)[i,j-1]
                values=[get(name) for name in ("ox","oy","ovs","ovl","length","width")]
                bodies.append(values+[np.arctan2(values[3],values[2])])
            values=[env.x[i],env.y[i],env.yaw[i],env.speed[i],env.goal[i],env.half_width[i],
                    env.road_curvature(env.x)[i],env.road_curvature(env.x+2)[i],
                    env.road_curvature(env.x+5)[i],len(bodies)]
            lines.append(" ".join(map(str,values+[v for b in bodies for v in b])))
        out=subprocess.check_output([str(self.root/"parity"),str(weights)],input="\n".join(lines),text=True)
        return np.array([[float(v) for v in line.split()] for line in out.splitlines()])

    def test_cpp_python_observation_and_inference_parity(self):
        torch.manual_seed(42)
        agent=Agent(48)
        weights=self.root/"random.weights";agent.export(weights)
        env=VectorEnv(128,"interaction",99)
        env.active[:]=True
        native=self.native(env,weights)
        np.testing.assert_allclose(native[:,:48],env.obs(),atol=1e-6,rtol=1e-6)
        np.testing.assert_allclose(native[:,48:],actor(weights,native[:,:48]),atol=1e-10)
        with torch.no_grad():
            actual=agent.distribution(torch.from_numpy(env.obs())).mean.numpy()
        np.testing.assert_allclose(actual,actor(weights,env.obs()),atol=1e-6)

    def test_warm_start_preserves_legacy_actor(self):
        old=HERE/"models/v4/unified.weights"
        agent=Agent(48);agent.warm_start_actor(old)
        new=self.root/"warm.weights";agent.export(new)
        env=VectorEnv(64,"interaction",15)
        np.testing.assert_allclose(actor(old,env.obs()),actor(new,env.obs()),atol=2e-6,rtol=2e-6)
        native=self.native(env,old)
        np.testing.assert_allclose(native[:,48:],actor(old,native[:,:48]),atol=1e-10)

    def test_masked_actors_cannot_leak_future_positions(self):
        env=VectorEnv(32,"interaction",11)
        env.active[:]=False
        before=env.obs().copy()
        env.ox[:]=100;env.extra_ox[:]=-100;env.extra_ovs[:]=30
        np.testing.assert_array_equal(before,env.obs())
        self.assertTrue((before[:,16:]==0).all())

    def test_oncoming_ranked_before_nearer_parked_actor(self):
        env=VectorEnv(1,"interaction",1)
        env.x[:]=env.y[:]=0;env.speed[:]=.8;env.active[:]=env.mask[:]=True
        env.ox[:]=4;env.oy[:]=.4;env.ovs[:]=env.ovl[:]=0
        env.extra_mask[:]=False;env.extra_mask[:,0]=True
        env.extra_ox[:,0]=5;env.extra_oy[:,0]=.3
        env.extra_ovs[:,0]=-.8;env.extra_ovl[:,0]=0
        obs=env.obs()[0]
        self.assertAlmostEqual(obs[3],.4)  # Legacy nearest remains unchanged.
        self.assertAlmostEqual(obs[16],.5)
        self.assertAlmostEqual(obs[21],-.8)
        self.assertEqual(obs[26],1)

    def test_curriculum_has_all_categories_and_repeatable_seed(self):
        a=VectorEnv(256,"interaction",74);b=VectorEnv(256,"interaction",74)
        self.assertEqual(set(a.episode_kind),{"mixed","nudge","meeting","meeting_nudge"})
        np.testing.assert_array_equal(a.obs(),b.obs())
        self.assertTrue((a.ovs[a.episode_kind=="meeting"]<0).all())
        actions=np.zeros((256,2))
        for _ in range(10):
            ao,ar,ad,_=a.step(actions);bo,br,bd,_=b.step(actions)
            np.testing.assert_array_equal(ao,bo)
            np.testing.assert_array_equal(ar,br)
            np.testing.assert_array_equal(ad,bd)

    def test_optional_nudge_cap_matches_deployed_target(self):
        env=VectorEnv(1,"interaction",3,runtime_nudge=True)
        env.x[:]=env.y[:]=env.yaw[:]=env.curvature[:]=env.speed[:]=0
        env.ox[:]=1.5;env.oy[:]=.6;env.length[:]=.4;env.width[:]=.2
        env.ovs[:]=env.ovl[:]=env.walk_velocity[:]=0
        env.mask[:]=env.active[:]=True;env.extra_mask[:]=False
        env.half_width[:]=1;env.goal[:]=30
        _,_,done,_=env.step(np.array([[0.,8.]]))
        self.assertFalse(done[0])
        self.assertAlmostEqual(env.speed[0],.02)


if __name__=="__main__":
    unittest.main()
