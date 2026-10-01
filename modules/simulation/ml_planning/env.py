"""Unified PPO curriculum in a vectorized curved-road surrogate, not WorldSim.

One actor handles empty roads, bends and obstacles; stage only controls training.
Runtime never receives a scenario ID or a straight/avoid selector.
"""
import numpy as np
from interaction_observation import append_interactions

DT, OBS_DIM, ACT_DIM = .1, 16, 2
MAX_SPEED = 1.0
NEAR_MISS_MARGIN = .25
STATIC_CLEARANCE = .05
PASS_REAR_MARGIN = .5


def body_clearance(x, y, yaw, ox, oy, length, width, obstacle_yaw=0):
    """Conservative Frenet bounding boxes; asymmetric Ranger pose reference."""
    ec, es = np.cos(yaw), np.sin(yaw)
    oc, os = np.cos(obstacle_yaw), np.sin(obstacle_yaw)
    ex, ey = x+.26*ec, y+.26*es
    ehx, ehy = .36*abs(ec)+.25*abs(es), .36*abs(es)+.25*abs(ec)
    ohx, ohy = length/2*abs(oc)+width/2*abs(os), length/2*abs(os)+width/2*abs(oc)
    gx, gy = abs(ox-ex)-ehx-ohx, abs(oy-ey)-ehy-ohy
    gap = np.hypot(np.maximum(0,gx),np.maximum(0,gy))
    return gap, (gx <= 0) & (gy <= 0), ex-ehx-(ox+ohx)


def dynamics(x, y, yaw, speed, action, remaining, half_width, curvature):
    target_y = np.maximum(0, half_width - .27) * np.tanh(action[..., 0])
    limit = np.minimum(MAX_SPEED, np.sqrt(.35 / np.maximum(abs(curvature), .01)))
    target_v = np.minimum(.5 * (1 + np.tanh(action[..., 1])) * limit,
                          .25 * np.maximum(0, remaining - .12))
    accel = np.clip(target_v - speed, -1, .6)
    next_speed = np.maximum(0, speed + accel * DT)
    v = (speed + next_speed) / 2
    # Curvature feed-forward tracks the map; actor chooses lateral/speed targets.
    correction = np.clip(2.0 * (target_y - y) - 2.5 * yaw, -.8, .8)
    next_yaw = yaw + v * correction * DT
    mid = (yaw + next_yaw) / 2
    return (x + v * np.cos(mid) / np.maximum(.3, 1 - curvature * y) * DT,
            y + v * np.sin(mid) * DT, next_yaw, next_speed)


def observation(x, y, yaw, speed, ox, oy, mask, goal, half_width, curvature,
                length, width, ovs=0, ovl=0, k2=None, k5=None):
    ones = np.ones_like(x)
    return np.stack([y, yaw, speed,
        np.where(mask, np.clip((ox-x)/10, -1, 1), 1),
        np.where(mask, oy-y, 0), mask.astype(float), half_width/2, half_width/2,
        np.clip((goal-x)/30, 0, 1), curvature,
        curvature if k2 is None else k2, curvature if k5 is None else k5,
        np.where(mask, length/2, 0), np.where(mask, width/2, 0),
        ones*ovs, ones*ovl], -1).astype(np.float32)


class VectorEnv:
    def __init__(self, count=64, stage="unified", seed=1, runtime_nudge=False):
        self.n, self.stage = count, stage
        self.runtime_nudge = runtime_nudge
        self.rng = np.random.default_rng(seed)
        for name in ("x", "y", "yaw", "speed", "ox", "oy", "goal", "half_width", "age", "returns", "curvature", "length", "width", "ovs", "ovl", "spawn_distance", "bend_end", "bend_scale", "bend_phase"):
            setattr(self, name, np.zeros(count))
        self.mask = np.zeros(count, dtype=bool)
        self.active = np.zeros(count, dtype=bool)
        self.last_lateral = np.zeros(count)
        self.last_accel = np.zeros(count)
        self.episode_kind = np.full(count, "mixed", dtype=object)
        self.motion_age = np.zeros(count)
        self.walk_velocity = np.zeros(count)
        self.wandering = np.zeros(count, dtype=bool)
        self.pausing = np.zeros(count, dtype=bool)
        for name in ("ox", "oy", "ovs", "ovl", "length", "width"):
            setattr(self, "extra_"+name, np.zeros((count, 3)))
        self.extra_mask = np.zeros((count, 3), dtype=bool)
        self.reset(np.ones(count, dtype=bool))

    def reset(self, selected):
        n = np.count_nonzero(selected)
        self.x[selected] = 0
        self.y[selected] = self.rng.uniform(-.035, .035, n)
        self.yaw[selected] = self.rng.uniform(-.03, .03, n)
        self.speed[selected] = 0
        self.ox[selected] = self.rng.uniform(5, 13, n)
        self.oy[selected] = self.rng.uniform(-.32, .32, n)
        self.goal[selected] = self.rng.uniform(22, 38, n)
        self.half_width[selected] = self.rng.uniform(.72, 1.0, n)
        self.curvature[selected] = self.rng.uniform(-.95, .95, n)
        self.bend_end[selected] = self.rng.uniform(7, 15, n)
        self.bend_scale[selected] = self.rng.uniform(1.5, 4, n)
        self.bend_phase[selected] = self.rng.uniform(-np.pi, np.pi, n)
        self.length[selected] = self.rng.uniform(.18, .4, n)
        self.width[selected] = self.rng.uniform(.12, .25, n)
        self.mask[selected] = (self.rng.random(n) < .65) if self.stage != "straight" else False
        self.active[selected] = False
        self.spawn_distance[selected] = self.rng.uniform(1.8, 12, n)
        self.last_lateral[selected] = 0
        self.last_accel[selected] = 0
        self.episode_kind[selected] = "mixed"
        self.motion_age[selected] = 0
        self.walk_velocity[selected] = 0
        self.wandering[selected] = False
        self.pausing[selected] = False
        self.ovs[selected] = 0
        self.ovl[selected] = 0
        # Static, moving lead objects and pedestrians entering from either side.
        mode = self.rng.random(self.n)
        crossing = selected & self.mask & (mode < .4)
        lead = selected & self.mask & (mode >= .4) & (mode < .55)
        side = self.rng.choice([-1., 1.], np.count_nonzero(crossing))
        self.oy[crossing] = side * self.rng.uniform(.9, 1.3, len(side))
        self.ovl[crossing] = -side * self.rng.uniform(.25, .8, len(side))
        self.ovs[crossing] = self.rng.uniform(-.3,.3,len(side))
        self.length[crossing] = .4
        self.width[crossing] = .4
        self.walk_velocity[crossing] = self.ovl[crossing]
        self.wandering[crossing] = self.rng.random(len(side)) < .35
        self.pausing[crossing] = self.rng.random(len(side)) < .25
        self.ovs[lead] = self.rng.uniform(.2, .5, np.count_nonzero(lead))
        self.length[lead] = self.rng.uniform(.6, 1.4, np.count_nonzero(lead))
        self.width[lead] = self.rng.uniform(.28, .45, np.count_nonzero(lead))
        self.oy[lead] = self.rng.choice([-1.,1.],np.count_nonzero(lead))*self.rng.uniform(.15,.45,np.count_nonzero(lead))
        # Tight bends/narrow lanes are also part of the empty-road curriculum.
        empty = selected & ~self.mask
        self.half_width[empty] = self.rng.uniform(.36, 1.0, np.count_nonzero(empty))
        self.half_width[lead] = self.rng.uniform(.45, 1.0, np.count_nonzero(lead))
        # Regress narrow roadside nudge and lead-following failures from the
        # all-map native suite. Geometry/clearance is sampled, never scene IDs.
        narrow_static = selected & self.mask & (mode >= .55) & (self.rng.random(self.n)<.5)
        self.half_width[narrow_static] = self.rng.uniform(.36,.65,np.count_nonzero(narrow_static))
        self.oy[narrow_static] = self.rng.choice([-1.,1.],np.count_nonzero(narrow_static))*(self.half_width[narrow_static]-.08)
        self.length[narrow_static] = .35
        self.width[narrow_static] = .18
        # Recovery states must be learned, not just tiny perturbations of center.
        self.y[selected] = self.rng.uniform(-1, 1, n) * np.minimum(.28, self.half_width[selected]-.36)
        self.yaw[selected] = self.rng.uniform(-.08, .08, n)
        self.age[selected] = 0
        self.returns[selected] = 0
        # Groups and successive vehicles: observe the nearest actor while every
        # physical body participates in collision/reward evaluation.
        self.extra_mask[selected] = (self.rng.random((n,3)) < .55) & self.mask[selected,None]
        self.extra_ox[selected] = self.ox[selected,None] + self.rng.uniform(1.2, 6, (n,3))
        self.extra_oy[selected] = self.rng.choice([-1.,1.], (n,3))*self.rng.uniform(.6,1.5,(n,3))
        self.extra_ovs[selected] = self.rng.uniform(-.25,.25,(n,3))
        self.extra_ovl[selected] = -np.sign(self.extra_oy[selected])*self.rng.uniform(.2,.8,(n,3))
        self.extra_length[selected] = .4
        self.extra_width[selected] = .4
        lead_group = selected & (self.ovs > .05)
        m = np.count_nonzero(lead_group)
        self.extra_oy[lead_group] = self.oy[lead_group,None]
        self.extra_ovs[lead_group] = self.rng.uniform(.2,.5,(m,3))
        self.extra_ovl[lead_group] = 0
        self.extra_length[lead_group] = .6
        self.extra_width[lead_group] = .32
        if self.stage == "roadside":
            # Fine-tuning retains mixed traffic, adding passable roadside
            # encroachment and successive stationary objects on both sides.
            roadside = selected & (self.rng.random(self.n) < .75)
            m = np.count_nonzero(roadside)
            sides = self.rng.choice([-1., 1.], m)
            self.half_width[roadside] = self.rng.uniform(.40, .65, m)
            self.width[roadside] = self.rng.uniform(.10, .28, m)
            intrusion = self.rng.uniform(.08, .20, m)
            self.oy[roadside] = sides*(self.half_width[roadside]-intrusion+self.width[roadside]/2)
            self.length[roadside] = self.rng.uniform(.2, .7, m)
            self.ox[roadside] = self.rng.uniform(1.2, 4., m)
            self.goal[roadside] = self.rng.uniform(18., 25., m)
            self.curvature[roadside] = self.rng.uniform(-.25, .25, m)
            self.y[roadside] = 0
            self.yaw[roadside] = 0
            self.ovs[roadside] = self.ovl[roadside] = 0
            self.walk_velocity[roadside] = 0
            self.wandering[roadside] = self.pausing[roadside] = False
            self.mask[roadside] = self.active[roadside] = True
            self.extra_mask[roadside] = self.rng.random((m, 3)) < .8
            self.extra_ox[roadside] = self.ox[roadside,None] + np.arange(1,4)*4
            self.extra_oy[roadside] = self.oy[roadside,None]*self.rng.choice([-1.,1.],(m,3))
            self.extra_ovs[roadside] = self.extra_ovl[roadside] = 0
            self.extra_width[roadside] = self.width[roadside,None]
            self.extra_length[roadside] = self.length[roadside,None]
        if self.stage == "interaction":
            # 25% original mixed traffic prevents forgetting. The new episodes
            # are sampled from geometry, never from the held-out native suite.
            curriculum = self.rng.random(self.n)
            nudge = selected & (curriculum >= .25) & (curriculum < .50)
            meeting = selected & (curriculum >= .50) & (curriculum < .80)
            combined = selected & (curriculum >= .80)
            specialized = nudge | meeting | combined
            m = np.count_nonzero(specialized)
            self.half_width[specialized] = self.rng.uniform(.8, 1.15, m)
            self.goal[specialized] = self.rng.uniform(18, 26, m)
            self.curvature[specialized] = self.rng.uniform(-.12, .12, m)
            self.mask[specialized] = self.active[specialized] = True
            self.wandering[specialized] = self.pausing[specialized] = False
            self.walk_velocity[specialized] = self.ovl[specialized] = 0
            self.extra_mask[specialized] = False
            self.episode_kind[nudge] = "nudge"
            self.episode_kind[meeting] = "meeting"
            self.episode_kind[combined] = "meeting_nudge"
            parked = nudge | combined
            m = np.count_nonzero(parked)
            self.ox[parked] = self.rng.uniform(5, 9, m)
            self.oy[parked] = self.rng.choice([-1., 1.], m)*self.rng.uniform(.28, .48, m)
            self.length[parked] = self.rng.uniform(.3, .9, m)
            self.width[parked] = self.rng.uniform(.18, .35, m)
            self.ovs[parked] = 0
            m = np.count_nonzero(meeting)
            self.ox[meeting] = self.rng.uniform(7, 12, m)
            self.oy[meeting] = self.rng.uniform(.2, .5, m)
            self.ovs[meeting] = -self.rng.uniform(.25, .65, m)
            self.length[meeting] = self.rng.uniform(.6, 1.2, m)
            self.width[meeting] = self.rng.uniform(.3, .45, m)
            m = np.count_nonzero(combined)
            self.extra_mask[combined, 0] = True
            self.extra_ox[combined, 0] = self.rng.uniform(10, 15, m)
            self.extra_oy[combined, 0] = self.rng.uniform(.25, .5, m)
            self.extra_ovs[combined, 0] = -self.rng.uniform(.3, .6, m)
            self.extra_ovl[combined, 0] = 0
            self.extra_length[combined, 0] = self.rng.uniform(.6, 1.2, m)
            self.extra_width[combined, 0] = self.rng.uniform(.3, .45, m)
        return self.obs()

    def road_curvature(self, x):
        # Alternating bends followed by a straight, with actual preview features.
        return self.curvature * np.sin(x/self.bend_scale+self.bend_phase) * .5*(1-np.tanh(x-self.bend_end))

    def obs(self):
        bodies = {name:np.column_stack((getattr(self,name),getattr(self,"extra_"+name)))
                  for name in ("ox","oy","ovs","ovl","length","width")}
        mask = np.column_stack((self.mask, self.extra_mask)) & self.active[:,None]
        dx = bodies["ox"]-self.x[:,None]
        heading = np.arctan2(bodies["ovl"], bodies["ovs"])
        extent = bodies["length"]/2*abs(np.cos(heading)) + bodies["width"]/2*abs(np.sin(heading))
        visible = mask & (dx >= np.where(bodies["ovs"]>.05,-8.,-(.1+extent+PASS_REAR_MARGIN)))
        visible &= ~((abs(bodies["oy"])>self.half_width[:,None]+bodies["width"]) & (bodies["oy"]*bodies["ovl"]>=0))
        nearest = np.argmin(np.where(visible,abs(dx),np.inf),axis=1)
        values = {name:array[np.arange(self.n),nearest] for name,array in bodies.items()}
        present = visible.any(axis=1)
        legacy = observation(self.x, self.y, self.yaw, self.speed, values["ox"], values["oy"],
                           present, self.goal, self.half_width, self.road_curvature(self.x), values["length"], values["width"],
                           np.where(present,values["ovs"],0),np.where(present,values["ovl"],0),
                           self.road_curvature(self.x+2),self.road_curvature(self.x+5))
        if self.stage == "interaction":
            return append_interactions(legacy, self.x, self.y, self.speed, bodies, visible)
        return legacy

    def step(self, actions):
        old_x = self.x.copy()
        old_y = self.y.copy()
        old_speed = self.speed.copy()
        curvature = self.road_curvature(self.x)
        executed_actions = actions.copy()
        if self.runtime_nudge:
            # Match the existing C++ static close-pass speed cap. Without it,
            # an actor can learn to race through a gap that closes when the
            # deployed pipeline slows to 0.20 m/s. No future script is used.
            ox=np.column_stack((self.ox,self.extra_ox))
            oy=np.column_stack((self.oy,self.extra_oy))
            ovs=np.column_stack((self.ovs,self.extra_ovs))
            ovl=np.column_stack((self.ovl,self.extra_ovl))
            length=np.column_stack((self.length,self.extra_length))
            width=np.column_stack((self.width,self.extra_width))
            present=np.column_stack((self.mask,self.extra_mask)) & self.active[:,None]
            near_static=present & (np.hypot(ovs,ovl)<.01) & (self.x[:,None]-.1-ox-length/2<.20)
            near_static &= (ox-length/2-self.x[:,None]-.62<1.5) & (abs(oy)<self.half_width[:,None]+width/2)
            slow=near_static.any(axis=1)
            executed_actions[slow,1]=np.minimum(executed_actions[slow,1],np.arctanh(-.6))
        self.x, self.y, self.yaw, self.speed = dynamics(self.x, self.y, self.yaw,
            self.speed, executed_actions, self.goal-self.x, self.half_width, curvature)
        self.age += 1
        self.active |= self.ox-self.x <= self.spawn_distance
        self.motion_age += self.active*DT
        # Unannounced reversals and stop/restart motions are observed only via
        # current position/velocity, as in the real perception interface.
        reverse = self.wandering & (self.motion_age > 2) & (self.motion_age < 4)
        self.ovl = np.where(self.walk_velocity != 0,
                            self.walk_velocity*np.where(reverse, -1, 1), self.ovl)
        pause = self.pausing & (self.motion_age > 1.5) & (self.motion_age < 4.5)
        self.ovl = np.where(pause, 0, self.ovl)
        self.ox += np.where(self.active,self.ovs*DT,0)
        self.oy += np.where(self.active,self.ovl*DT,0)
        self.extra_ox += self.active[:,None]*self.extra_ovs*DT
        self.extra_oy += self.active[:,None]*self.extra_ovl*DT
        dx, dy = self.ox-self.x, self.oy-self.y
        long_extent = .62*np.abs(np.cos(self.yaw)) + .25*np.abs(np.sin(self.yaw))
        lat_extent = .62*np.abs(np.sin(self.yaw)) + .25*np.abs(np.cos(self.yaw))
        obstacle_yaw = np.where(abs(self.ovl)>.05, np.arctan2(self.ovl,self.ovs), 0)
        gap, overlap, rear_gap = body_clearance(self.x,self.y,self.yaw,self.ox,self.oy,self.length,self.width,obstacle_yaw)
        present = self.mask & self.active
        collision = present & overlap
        extra_present = self.extra_mask & self.active[:,None]
        extra_gap, extra_hit, _ = body_clearance(self.x[:,None],self.y[:,None],self.yaw[:,None],
            self.extra_ox,self.extra_oy,self.extra_length,self.extra_width,np.arctan2(self.extra_ovl,self.extra_ovs))
        collision |= (extra_present & extra_hit).any(axis=1)
        static = np.hypot(self.ovs,self.ovl) < .01
        margin = np.where(static, STATIC_CLEARANCE, NEAR_MISS_MARGIN)
        extra_margin = np.where(np.hypot(self.extra_ovs,self.extra_ovl)<.01, STATIC_CLEARANCE, NEAR_MISS_MARGIN)
        near_miss = present & (gap < margin) & ~collision
        future_gap, _, _ = body_clearance(self.x+self.speed*np.cos(self.yaw),self.y+self.speed*np.sin(self.yaw),self.yaw,
            self.ox+self.ovs,self.oy+self.ovl,self.length,self.width,obstacle_yaw)
        offroad = abs(self.y)+lat_extent+.03 > self.half_width
        # Finishing early must not erase a following vehicle that will hit
        # the parked ego. Check closest approach over 40 seconds for all actors.
        moving_x=np.column_stack((self.ox,self.extra_ox))
        moving_y=np.column_stack((self.oy,self.extra_oy))
        moving_vx=np.column_stack((self.ovs,self.extra_ovs))
        moving_vy=np.column_stack((self.ovl,self.extra_ovl))
        moving_mask=np.column_stack((present,extra_present))
        projection=((self.x[:,None]+.26-moving_x)*moving_vx+(self.y[:,None]-moving_y)*moving_vy)
        closest_t=np.clip(projection/np.maximum(.001,moving_vx**2+moving_vy**2),0,40)
        _, terminal_hits, _=body_clearance(self.x[:,None],self.y[:,None],self.yaw[:,None],
            moving_x+moving_vx*closest_t,moving_y+moving_vy*closest_t,
            np.column_stack((self.length,self.extra_length)),np.column_stack((self.width,self.extra_width)),
            np.arctan2(moving_vy,moving_vx))
        terminal_unsafe=(moving_mask & terminal_hits).any(axis=1)
        success = (self.x > self.goal-.3) & (abs(self.y)<.15) & ~collision & ~offroad & ~terminal_unsafe
        # Frenet progress alone rewards cutting inside curves. Use physical
        # forward distance and explicitly reward recovery when the road is clear.
        # Distant actors must not pull the car off its reference line. Relax
        # centering only within 1.5 m of the FRONT body edge (not pose origin).
        threat = present & (rear_gap < PASS_REAR_MARGIN+2*np.maximum(0,self.ovs-self.speed)) & (dx-long_extent-self.length/2 < 1.5) & (abs(self.oy) < self.half_width+self.width)
        threat |= (extra_present & (extra_gap<1.5) & (self.extra_ox>self.x[:,None]-.5)).any(axis=1)
        if self.stage == "interaction":
            # Opposing traffic needs lateral preparation before the old 1.5 m
            # nudge window. This is a reward condition, not a runtime rule.
            closing_time = (moving_x-self.x[:, None]) / np.maximum(.2, self.speed[:, None]-moving_vx)
            threat |= (moving_mask & (moving_vx < -.05) &
                       (closing_time > 0) & (closing_time < 6)).any(axis=1)
        center_weight = np.where(threat, .3, 12.)
        lateral = np.tanh(actions[:, 0])
        reward = 5*(self.x-old_x)*np.maximum(.3,1-curvature*old_y) - center_weight*self.y**2 - 2*self.yaw**2 -.005
        reward -= .15*(lateral-self.last_lateral)**2
        acceleration = (self.speed-old_speed)/DT
        if self.stage == "interaction":
            # Bounded soft costs: safety penalties still dominate emergency braking.
            jerk = (acceleration-self.last_accel)/DT
            reward -= .02*np.minimum(jerk**2, 25) + .03*acceleration**2
            reward -= .25*(lateral-self.last_lateral)**2
        self.last_accel[:] = acceleration
        reward -= .4*(~threat)*lateral**2
        self.last_lateral[:] = lateral
        # A crossing actor occupies the corridor: waiting is useful, pushing
        # ahead or changing sides in response to each observation is not.
        crossing = threat & (abs(self.ovl) > .05) & (abs(self.oy) < .65)
        reward += crossing * .08 * (self.speed < .15)
        reward -= crossing * .5 * self.speed**2
        # Continuous distance cost catches close passes even without collision.
        reward -= present * 4*np.clip((margin-gap)/margin,0,1)**2
        reward -= (extra_present*8*np.clip((extra_margin-extra_gap)/extra_margin,0,1)**2).sum(axis=1)
        reward -= present * 2*np.clip((margin-future_gap)/margin,0,1)**2
        if self.runtime_nudge:
            # Dense multi-body closest-approach shaping improves credit
            # assignment before entering an oncoming/static bottleneck.
            rvx=moving_vx-self.speed[:,None]*np.cos(self.yaw[:,None])
            rvy=moving_vy-self.speed[:,None]*np.sin(self.yaw[:,None])
            dxp=moving_x-self.x[:,None]-.26*np.cos(self.yaw[:,None])
            dyp=moving_y-self.y[:,None]-.26*np.sin(self.yaw[:,None])
            closest=np.clip(-(dxp*rvx+dyp*rvy)/np.maximum(.001,rvx**2+rvy**2),0,6)
            future,_,_=body_clearance(
                self.x[:,None]+self.speed[:,None]*np.cos(self.yaw[:,None])*closest,
                self.y[:,None]+self.speed[:,None]*np.sin(self.yaw[:,None])*closest,self.yaw[:,None],
                moving_x+moving_vx*closest,moving_y+moving_vy*closest,
                np.column_stack((self.length,self.extra_length)),np.column_stack((self.width,self.extra_width)),
                np.arctan2(moving_vy,moving_vx))
            oncoming=moving_mask & (moving_vx<-.05)
            reward -= (oncoming * .5*np.clip((.25-future)/.25,0,1)**2/(1+closest)).sum(axis=1)
        nudge = present & static & (rear_gap < .05) & (dx-long_extent-self.length/2 < 1.5)
        nudge |= (extra_present & (np.hypot(self.extra_ovs,self.extra_ovl)<.01) &
                  (self.extra_ox+self.extra_length/2>self.x[:,None]-.15) &
                  (self.extra_ox-self.extra_length/2<self.x[:,None]+2.12)).any(axis=1)
        reward -= nudge * 4*np.maximum(0,self.speed-.25)**2
        # Do not trade a completed pass for an early centering reward. The ego
        # REAR must clear the other vehicle's FRONT, including a safety margin.
        alongside = present & (self.ovs>.05) & (abs(self.ovl)<.1) & (dx<2) & (rear_gap<PASS_REAR_MARGIN)
        reward -= 200*alongside*np.maximum(0,abs(old_y)-abs(self.y))
        reward -= .001*(actions**2).sum(-1)
        reward -= 3*(self.goal-self.x<8)*terminal_unsafe
        narrow_lead=present & (self.ovs>.05) & (abs(self.ovl)<.1) & (dx>0) & (self.half_width*2<.5+self.width+.34)
        reward -= narrow_lead*3*self.y**2
        reward += 30*success - 800*collision - 100*offroad
        done = collision|offroad|success|(self.age>=1100)
        self.returns += reward
        info = [{"return":float(self.returns[i]),"success":bool(success[i]),
                 "index":int(i), "kind":str(self.episode_kind[i]),
                 "collision":bool(collision[i]),"offroad":bool(offroad[i]),
                 "near_miss":bool(near_miss[i])} for i in np.flatnonzero(done)]
        self.reset(done)
        return self.obs(), reward.astype(np.float32), done, info
