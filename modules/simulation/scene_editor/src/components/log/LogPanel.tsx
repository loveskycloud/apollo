import { useEffect, useRef } from 'react';
import { useApolloStore } from '../../apollo/apolloStore';

export function LogPanel() {
  const logs = useApolloStore((s) => s.log);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs.length]);

  return (
    <div className="log-panel">
      {logs.length === 0 ? (
        <div className="log-line info">等待连接 Apollo Sim Bridge…</div>
      ) : (
        logs.map((line, i) => (
          <div key={`${i}-${line}`} className={`log-line ${logLevel(line)}`}>
            {line}
          </div>
        ))
      )}
      <div ref={bottomRef} />
    </div>
  );
}

function logLevel(line: string): 'info' | 'ok' | 'warn' {
  if (line.includes('[错误]') || /失败|ERROR|FAIL/i.test(line)) return 'warn';
  if (line.includes('[警告]')) return 'warn';
  if (/已连接|RUNNING|成功|ready|已下发/i.test(line)) return 'ok';
  return 'info';
}
