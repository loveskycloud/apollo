import { Button, Select, Slider } from 'antd';
import {
  PauseCircleOutlined,
  PlayCircleOutlined,
  StepBackwardOutlined,
  StepForwardOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useMemo } from 'react';
import { useScenarioStore } from '../../core/store';
import { distance2D } from '../../map/types';

export function PlaybackBar() {
  const playback = useScenarioStore((s) => s.playback);
  const play = useScenarioStore((s) => s.play);
  const pause = useScenarioStore((s) => s.pause);
  const stop = useScenarioStore((s) => s.stop);
  const seek = useScenarioStore((s) => s.seek);
  const setPlaybackSpeed = useScenarioStore((s) => s.setPlaybackSpeed);
  const runtime = useScenarioStore((s) => s.runtime);
  const scenario = useScenarioStore((s) => s.scenario);
  const backend = useScenarioStore((s) => s.rendererBackend);

  const ego = scenario.agents.find((a) => a.type === 'ego');
  const egoRt = ego ? runtime[ego.id] : undefined;
  const others = scenario.agents.filter((a) => a.type !== 'ego');

  const metrics = useMemo(() => {
    let minDist = Number.POSITIVE_INFINITY;
    if (egoRt) {
      for (const other of others) {
        const rt = runtime[other.id];
        if (!rt) continue;
        minDist = Math.min(minDist, distance2D(egoRt.position, rt.position));
      }
    }
    if (!Number.isFinite(minDist)) minDist = 99;
    const speed = egoRt?.speed ?? 0;
    const ttc = speed > 0.1 ? minDist / speed : 99;
    return {
      ttc,
      minDist,
      latErr: Math.abs((egoRt?.position.y ?? 0) % 1) * 0.4,
      collision: 0,
    };
  }, [egoRt, others, runtime]);

  const cpu = backend === 'webgpu' ? 18 : 27;
  const gpu = backend === 'webgpu' ? 41 : 33;

  return (
    <>
      <div className="kpi-row">
        <div className="kpi-status">
          <div className="line">
            <span className={`dot ${playback.playing ? 'on' : ''}`} />
            <span>{playback.playing ? '运行中' : '空闲'}</span>
          </div>
          <div className="line">场景：{scenario.name}</div>
          <div className="line">天气：粉尘 / 弱光</div>
        </div>

        <div className="kpi-cards">
          <div className="kpi-card">
            <div className="label">TTC</div>
            <div className="value">
              {metrics.ttc.toFixed(2)}
              <span className="unit">s</span>
            </div>
          </div>
          <div className="kpi-card">
            <div className="label">Min Distance</div>
            <div className="value">
              {metrics.minDist.toFixed(2)}
              <span className="unit">m</span>
            </div>
          </div>
          <div className="kpi-card">
            <div className="label">Lateral Error</div>
            <div className="value">
              {metrics.latErr.toFixed(2)}
              <span className="unit">m</span>
            </div>
          </div>
          <div className="kpi-card">
            <div className="label">Collision</div>
            <div className="value">{metrics.collision}</div>
          </div>
        </div>

        <div className="res-bars">
          <div className="res-bar">
            <span>CPU</span>
            <div className="track"><div className="fill" style={{ width: `${cpu}%` }} /></div>
            <span>{cpu}%</span>
          </div>
          <div className="res-bar">
            <span>GPU</span>
            <div className="track"><div className="fill" style={{ width: `${gpu}%` }} /></div>
            <span>{gpu}%</span>
          </div>
          <div className="res-bar">
            <span>MEM</span>
            <div className="track"><div className="fill" style={{ width: '38%' }} /></div>
            <span>38%</span>
          </div>
          <div className="res-bar">
            <span>VRAM</span>
            <div className="track"><div className="fill" style={{ width: '45%' }} /></div>
            <span>45%</span>
          </div>
        </div>
      </div>

      <div className="playback-row">
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <Button size="small" icon={<StepBackwardOutlined />} onClick={() => seek(Math.max(0, playback.time - 1))} />
          <Button
            size="small"
            type="primary"
            icon={playback.playing ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={() => (playback.playing ? pause() : play())}
          />
          <Button size="small" icon={<StopOutlined />} onClick={stop} />
          <Button size="small" icon={<StepForwardOutlined />} onClick={() => seek(Math.min(playback.duration, playback.time + 1))} />
          <Select
            size="small"
            style={{ width: 72 }}
            value={playback.speed}
            options={[0.5, 1, 2, 4].map((v) => ({ value: v, label: `${v}x` }))}
            onChange={setPlaybackSpeed}
          />
        </div>
        <div className="playback-slider">
          <Slider
            min={0}
            max={playback.duration}
            step={0.05}
            value={playback.time}
            onChange={seek}
            tooltip={{ formatter: (v) => `${Number(v).toFixed(2)}s` }}
          />
        </div>
        <div style={{ color: '#8b93a1', fontSize: 12, minWidth: 110, textAlign: 'right' }}>
          {playback.time.toFixed(2)} / {playback.duration.toFixed(0)} s
        </div>
      </div>
    </>
  );
}
