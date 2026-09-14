import { Button, Checkbox, Input, Select } from 'antd';
import {
  CaretRightOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useApolloStore } from '../../apollo/apolloStore';
import type { ApolloModuleKey } from '../../apollo/protocol';

const MODULE_LABELS: Array<{ key: ApolloModuleKey; label: string; dag: string }> = [
  { key: 'routing', label: 'Routing', dag: 'routing.dag + external_command' },
  { key: 'planning', label: 'Planning', dag: 'planning.dag' },
  {
    key: 'prediction',
    label: 'Prediction',
    dag: 'modules/fake_prediction/dag/fake_prediction.dag',
  },
  { key: 'control', label: 'Control', dag: 'control.dag (10ms)' },
];

function StatusLine() {
  const status = useApolloStore((s) => s.status);
  const simRunning = useApolloStore((s) => s.simRunning);
  const serviceId = useApolloStore((s) => s.serviceId);
  const mode = useApolloStore((s) => s.mode);

  const tone =
    status === 'connected'
      ? 'ok'
      : status === 'connecting'
        ? 'busy'
        : status === 'error'
          ? 'err'
          : 'idle';
  const label =
    status === 'connected'
      ? `已连接${serviceId ? ` · ${serviceId}` : ''}`
      : status === 'connecting'
        ? '连接中'
        : status === 'error'
          ? '连接错误'
          : '未连接';

  return (
    <div className="sim-status">
      <span className={`sim-status-dot ${tone}`} />
      <span className="sim-status-text">{label}</span>
      {simRunning && <span className="sim-status-badge">仿真中</span>}
      {status === 'connected' && mode === 'mock' && (
        <span className="sim-status-badge muted">Mock</span>
      )}
    </div>
  );
}

export function SimConfigPanel() {
  const addressInput = useApolloStore((s) => s.addressInput);
  const setAddressInput = useApolloStore((s) => s.setAddressInput);
  const status = useApolloStore((s) => s.status);
  const connect = useApolloStore((s) => s.connect);

  const modules = useApolloStore((s) => s.modules);
  const runningModules = useApolloStore((s) => s.runningModules);
  const setModule = useApolloStore((s) => s.setModule);

  const currentVehicle = useApolloStore((s) => s.currentVehicle);
  const vehicles = useApolloStore((s) => s.vehicles);
  const setVehicle = useApolloStore((s) => s.setVehicle);
  const vehicleParam = useApolloStore((s) => s.vehicleParam);

  const simRunning = useApolloStore((s) => s.simRunning);
  const simControl = useApolloStore((s) => s.simControl);
  const egoSpeed = useApolloStore((s) => s.egoSpeed);
  const obstacleCount = useApolloStore((s) => s.obstacleCount);
  const mode = useApolloStore((s) => s.mode);

  return (
    <div className="sim-config">
      <div className="attr-block">
        <h4>连接</h4>
        <StatusLine />
        <div className="sim-address-row">
          <Input
            size="small"
            value={addressInput}
            onChange={(e) => setAddressInput(e.target.value)}
            onPressEnter={() => connect()}
            placeholder="服务标识，空=本机"
            allowClear
          />
          <Button
            size="small"
            type="primary"
            loading={status === 'connecting'}
            onClick={() => connect()}
          >
            连接
          </Button>
        </div>
        {status === 'connected' && mode === 'mock' ? (
          <div className="sim-hint">当前为 Mock 动画，非真实算法输出。</div>
        ) : (
          <div className="sim-hint">断线将自动重连。</div>
        )}
      </div>

      <div className="attr-block">
        <h4>算法模块</h4>
        <div className="sim-modules">
          {MODULE_LABELS.map(({ key, label, dag }) => (
            <div key={key} className="sim-module-row" title={dag}>
              <Checkbox checked={modules[key]} onChange={(e) => setModule(key, e.target.checked)}>
                {label}
              </Checkbox>
              <span className={`sim-module-state ${runningModules[key] ? 'on' : ''}`}>
                {runningModules[key] ? '运行中' : '未运行'}
              </span>
            </div>
          ))}
        </div>
        <div className="sim-hint">勾选随场景保存，启动仿真时拉起。</div>
      </div>

      <div className="attr-block">
        <h4>车辆配置</h4>
        <div className="field-row">
          <label>车型</label>
          <Select
            size="small"
            style={{ width: '100%' }}
            placeholder="选择车型"
            value={currentVehicle ?? undefined}
            options={vehicles.map((v) => ({
              value: v,
              label: v === currentVehicle ? `${v}（当前）` : v,
            }))}
            onChange={(v) => setVehicle(v)}
          />
        </div>
        {vehicleParam && (
          <div className="field-row">
            <label>尺寸</label>
            <div className="sim-param">
              {`长 ${vehicleParam.length ?? '-'} m · 宽 ${vehicleParam.width ?? '-'} m · 轴距 ${vehicleParam.wheelBase ?? vehicleParam.wheel_base ?? '-'}`}
            </div>
          </div>
        )}
        <div className="sim-hint">地图在「项目」中绑定，仿真中不可更换。</div>
      </div>

      <div className="attr-block">
        <h4>仿真控制</h4>
        <div className="field-actions">
          {simRunning ? (
            <Button size="small" icon={<PauseCircleOutlined />} onClick={() => simControl('STOP')}>
              停止仿真
            </Button>
          ) : (
            <Button
              size="small"
              type="primary"
              icon={<CaretRightOutlined />}
              onClick={() => simControl('START')}
            >
              启动仿真
            </Button>
          )}
          <Button size="small" icon={<ReloadOutlined />} onClick={() => simControl('RESET')}>
            重置起点
          </Button>
        </div>
        <div className="field-row" style={{ marginTop: 8, marginBottom: 0 }}>
          <label>状态</label>
          <div className="sim-param">
            {`速度 ${egoSpeed.toFixed(1)} m/s`}
            {modules.prediction ? ` · 障碍 ${obstacleCount}` : ''}
          </div>
        </div>
      </div>
    </div>
  );
}
