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


class Agent(nn.Module):
    def __init__(self):
        super().__init__()
        self.actor = nn.Sequential(nn.Linear(OBS_DIM, 64), nn.Tanh(),
                                   nn.Linear(64, 64), nn.Tanh(), nn.Linear(64, ACT_DIM))
        self.critic = nn.Sequential(nn.Linear(OBS_DIM, 64), nn.Tanh(),
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
        mirrored[..., [0, 1, 4, 15]] *= -1
        mirrored[..., [6, 7]] = obs[..., [7, 6]]
        direct, reflected = self.actor(obs), self.actor(mirrored)
        mean = torch.stack(((direct[..., 0]-reflected[..., 0])/2,
                            (direct[..., 1]+reflected[..., 1])/2), -1)
        return torch.distributions.Normal(mean, self.log_std.exp())

    def export(self, path):
        # Plain row-major weights: C++ inference needs no Python/libtorch.
        values = ["MLP_V2", f"{OBS_DIM} 64 64 2"]
        for layer in self.actor:
            if isinstance(layer, nn.Linear):
                values.append(" ".join(format(float(x), ".9g") for x in layer.weight.detach().flatten()))
                values.append(" ".join(format(float(x), ".9g") for x in layer.bias.detach().flatten()))
        path.write_text("\n".join(values) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["straight", "unified", "roadside"], default="unified")
    p.add_argument("--steps", type=int, default=1000000)
    p.add_argument("--envs", type=int, default=64)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--resume", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    env = VectorEnv(args.envs, args.stage, args.seed)
    agent = Agent()
    if args.resume:
        agent.load_state_dict(torch.load(args.resume, weights_only=True))
        # Restore exploration for the harder curriculum.
        agent.log_std.data.fill_(-.7)
    optimizer = torch.optim.Adam(agent.parameters(), lr=3e-4, eps=1e-5)
    obs = torch.from_numpy(env.obs())
    horizon = 128
    history, episodes = [], []
    started = time.monotonic()
    args.output.mkdir(parents=True, exist_ok=True)
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
        "resume": str(args.resume) if args.resume else None,
        "resume_sha256": hashlib.sha256(args.resume.read_bytes()).hexdigest() if args.resume else None,
        "sources": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in ("env.py", "train.py")},
        "history": history}, indent=2))


if __name__ == "__main__":
    main()
