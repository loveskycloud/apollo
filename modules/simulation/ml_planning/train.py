"""CPU PPO with GAE, clipped policy objective and independent vector rollouts."""
import argparse
import json
import hashlib
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn

from env import VectorEnv, OBS_DIM, ACT_DIM
from interaction_observation import INTERACTION_OBS_DIM, lateral_indices


class Agent(nn.Module):
    def __init__(self, obs_dim=OBS_DIM):
        super().__init__()
        self.obs_dim = obs_dim
        self.actor = nn.Sequential(nn.Linear(obs_dim, 64), nn.Tanh(),
                                   nn.Linear(64, 64), nn.Tanh(), nn.Linear(64, ACT_DIM))
        self.critic = nn.Sequential(nn.Linear(obs_dim, 64), nn.Tanh(),
                                    nn.Linear(64, 64), nn.Tanh(), nn.Linear(64, 1))
        self.log_std = nn.Parameter(torch.full((ACT_DIM,), -.3))
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.orthogonal_(module.weight, np.sqrt(2))
                nn.init.zeros_(module.bias)
        nn.init.orthogonal_(self.actor[-1].weight, .01)

    def distribution(self, obs):
        obs = obs.clone()
        # Bend magnitude controls speed; route geometry supplies turn direction.
        obs[..., [9, 10, 11]] = obs[..., [9, 10, 11]].abs()
        obs[..., 4] = torch.where((obs[..., 5]>.5) & (obs[..., 4].abs()<.01), .01, obs[..., 4])
        mirrored = obs.clone()
        mirrored[..., lateral_indices(self.obs_dim)] *= -1
        mirrored[..., [6, 7]] = obs[..., [7, 6]]
        direct, reflected = self.actor(obs), self.actor(mirrored)
        mean = torch.stack(((direct[..., 0]-reflected[..., 0])/2,
                            (direct[..., 1]+reflected[..., 1])/2), -1)
        return torch.distributions.Normal(mean, self.log_std.exp())

    def export(self, path):
        # Plain row-major weights: C++ inference needs no Python/libtorch.
        values = ["MLP_V3" if self.obs_dim == INTERACTION_OBS_DIM else "MLP_V2",
                  f"{self.obs_dim} 64 64 2"]
        for layer in self.actor:
            if isinstance(layer, nn.Linear):
                values.append(" ".join(format(float(x), ".9g") for x in layer.weight.detach().flatten()))
                values.append(" ".join(format(float(x), ".9g") for x in layer.bias.detach().flatten()))
        path.write_text("\n".join(values) + "\n")

    def warm_start_actor(self, path):
        """Use deployed weights even when its training checkpoint is absent.

        Extra input columns start at zero, preserving the original actor exactly.
        The critic and optimizer are fresh; this is not a resumed PPO run.
        """
        tokens = path.read_text().split()
        magic, dim, h1, h2, out = tokens[:5]
        dim = int(dim)
        if (magic, dim) not in (("MLP_V2", 16), ("MLP_V3", 48)) or [h1,h2,out] != ["64","64","2"] or dim > self.obs_dim:
            raise ValueError("Incompatible warm-start actor")
        values = np.asarray(tokens[5:], dtype=np.float64)
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite actor")
        offset = 0
        with torch.no_grad():
            for layer in self.actor:
                if not isinstance(layer, nn.Linear):
                    continue
                count = layer.out_features*dim
                layer.weight.zero_()
                layer.weight[:, :dim].copy_(torch.tensor(values[offset:offset+count].reshape(layer.out_features, dim)))
                offset += count
                layer.bias.copy_(torch.tensor(values[offset:offset+layer.out_features]))
                offset += layer.out_features
                dim = layer.out_features
        if offset != len(values):
            raise ValueError("Trailing actor weights")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["straight", "unified", "roadside", "interaction"], default="unified")
    p.add_argument("--steps", type=int, default=1000000)
    p.add_argument("--envs", type=int, default=64)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--resume", type=Path)
    p.add_argument("--init-weights", type=Path)
    p.add_argument("--anchor-weights", type=Path,
                   help="Retain a reference actor on observations without oncoming traffic")
    p.add_argument("--anchor-coefficient", type=float, default=.5)
    p.add_argument("--runtime-nudge", action="store_true", help="Match deployed static close-pass speed cap")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.steps <= 0 or args.envs <= 0 or (args.resume and args.init_weights):
        p.error("Positive steps/envs required; resume and init-weights are mutually exclusive")
    torch.set_num_threads(2)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    env = VectorEnv(args.envs, args.stage, args.seed, runtime_nudge=args.runtime_nudge)
    agent = Agent(INTERACTION_OBS_DIM if args.stage == "interaction" else OBS_DIM)
    if args.resume:
        agent.load_state_dict(torch.load(args.resume, weights_only=True))
        # Restore exploration for the harder curriculum.
        agent.log_std.data.fill_(-.7)
    if args.init_weights:
        agent.warm_start_actor(args.init_weights)
        agent.log_std.data.fill_(-.7)
    anchor = None
    if args.anchor_weights:
        if args.stage != "interaction" or args.anchor_coefficient < 0:
            p.error("Anchoring requires interaction stage and a nonnegative coefficient")
        anchor = Agent(INTERACTION_OBS_DIM)
        anchor.warm_start_actor(args.anchor_weights)
        anchor.requires_grad_(False)
    optimizer = torch.optim.Adam(agent.parameters(), lr=3e-4, eps=1e-5)
    obs = torch.from_numpy(env.obs())
    horizon = 128
    history, episodes = [], []
    started = time.monotonic()
    args.output.mkdir(parents=True, exist_ok=False)
    source_hashes = {}
    for name in ("env.py", "train.py", "interaction_observation.py"):
        source = Path(__file__).with_name(name).read_bytes()
        (args.output/(name+".snapshot")).write_bytes(source)
        source_hashes[name] = hashlib.sha256(source).hexdigest()
    updates = max(1, (args.steps + horizon * args.envs - 1) // (horizon * args.envs))
    for update in range(updates):
        batch = []
        with torch.no_grad():
            for _ in range(horizon):
                dist = agent.distribution(obs)
                action = dist.sample()
                value = agent.critic(obs).flatten()
                nxt, reward, done, info = env.step(action.numpy())
                episodes.extend(info)
                batch.append((obs, action, dist.log_prob(action).sum(-1), value,
                              torch.from_numpy(reward), torch.from_numpy(done)))
                obs = torch.from_numpy(nxt)
            tensors = [torch.stack(items) for items in zip(*batch)]
            states, actions, old_logp, values, rewards, dones = tensors
            advantages = torch.zeros_like(rewards)
            next_value = agent.critic(obs).flatten()
            gae = torch.zeros(args.envs)
            for t in reversed(range(horizon)):
                alive = (~dones[t]).float()
                delta = rewards[t] + .99 * next_value * alive - values[t]
                gae = delta + .99 * .95 * alive * gae
                advantages[t] = gae
                next_value = values[t]
            returns = advantages + values
        states, actions = states.flatten(0, 1), actions.flatten(0, 1)
        old_logp, advantages, returns = old_logp.flatten(), advantages.flatten(), returns.flatten()
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        for _ in range(4):
            for ids in torch.randperm(len(states)).split(512):
                dist = agent.distribution(states[ids])
                ratio = (dist.log_prob(actions[ids]).sum(-1) - old_logp[ids]).exp()
                loss_pi = -torch.minimum(ratio * advantages[ids], ratio.clamp(.8, 1.2) * advantages[ids]).mean()
                loss_v = ((agent.critic(states[ids]).flatten() - returns[ids])**2).mean()
                loss = loss_pi + .5 * loss_v - .001 * dist.entropy().sum(-1).mean()
                if anchor is not None:
                    states_mb = states[ids]
                    slots = states_mb[:,16:].reshape(-1,4,8)
                    retain = ~((slots[:,:,2]>.5) & (slots[:,:,5]<-.05)).any(-1)
                    with torch.no_grad():
                        reference = anchor.distribution(states_mb).mean.tanh()
                    error = ((dist.mean.tanh()-reference)**2).sum(-1)
                    loss += args.anchor_coefficient*(error*retain).sum()/retain.sum().clamp_min(1)
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(agent.parameters(), .5)
                optimizer.step()
                # Keep stochastic training close to deployed mean actions;
                # unbounded variance can mask a mean policy that never moves.
                with torch.no_grad():
                    agent.log_std.clamp_(-2., -.5)
        if update % 10 == 0 or update == updates - 1:
            recent = episodes[-100:]
            row = {"steps": (update + 1) * horizon * args.envs,
                   "return": float(np.mean([e["return"] for e in recent])) if recent else None,
                   "success_rate": float(np.mean([e["success"] for e in recent])) if recent else None,
                   "collision_rate": float(np.mean([e["collision"] for e in recent])) if recent else None,
                   "offroad_rate": float(np.mean([e["offroad"] for e in recent])) if recent else None,
                   "elapsed_s": round(time.monotonic() - started, 2)}
            history.append(row)
            print(json.dumps(row), flush=True)
    stem = args.output / args.stage
    torch.save(agent.state_dict(), stem.with_suffix(".pt"))
    agent.export(stem.with_suffix(".weights"))
    stem.with_suffix(".training.json").write_text(json.dumps({
        "algorithm": "PPO", "environment": "vectorized Frenet surrogate (not WorldSim)",
        "seed": args.seed, "envs": args.envs, "requested_steps": args.steps,
        "actual_steps": updates*horizon*args.envs,
        "observation_dim": agent.obs_dim, "stage": args.stage,
        "runtime_nudge": args.runtime_nudge,
        "init_weights": str(args.init_weights) if args.init_weights else None,
        "init_weights_sha256": hashlib.sha256(args.init_weights.read_bytes()).hexdigest() if args.init_weights else None,
        "anchor_weights": str(args.anchor_weights) if args.anchor_weights else None,
        "anchor_sha256": hashlib.sha256(args.anchor_weights.read_bytes()).hexdigest() if args.anchor_weights else None,
        "anchor_coefficient": args.anchor_coefficient if args.anchor_weights else None,
        "resume": str(args.resume) if args.resume else None,
        "resume_sha256": hashlib.sha256(args.resume.read_bytes()).hexdigest() if args.resume else None,
        "sources": source_hashes,
        "history": history}, indent=2))


if __name__ == "__main__":
    main()
