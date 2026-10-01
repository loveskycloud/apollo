import { useCallback, useEffect, useRef } from 'react';

interface HeadingDialProps {
  value: number; // radians
  onChange: (radians: number) => void;
  size?: number;
}

function normalizeDeg(deg: number) {
  let d = deg % 360;
  if (d < 0) d += 360;
  return d;
}

/** 圆形航向旋钮：按住拖拽旋转，类似旋转开关 */
export function HeadingDial({ value, onChange, size = 112 }: HeadingDialProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const deg = normalizeDeg((value * 180) / Math.PI);

  const headingFromPointer = useCallback((clientX: number, clientY: number) => {
    const el = rootRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    // 屏幕 Y 向下；世界航向 0=+X、逆时针为正 → 取 atan2(-sy, sx)
    const heading = Math.atan2(-(clientY - cy), clientX - cx);
    onChange(heading);
  }, [onChange]);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      headingFromPointer(e.clientX, e.clientY);
    };
    const onUp = () => {
      dragging.current = false;
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [headingFromPointer]);

  const ticks = Array.from({ length: 12 }, (_, i) => i * 30);

  return (
    <div className="heading-dial-wrap">
      <div
        ref={rootRef}
        className="heading-dial"
        style={{ width: size, height: size }}
        onPointerDown={(e) => {
          e.preventDefault();
          dragging.current = true;
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          headingFromPointer(e.clientX, e.clientY);
        }}
      >
        <div className="heading-dial-ring">
          {ticks.map((t) => (
            <span
              key={t}
              className={`heading-dial-tick ${t % 90 === 0 ? 'major' : ''}`}
              style={{ transform: `rotate(${t}deg)` }}
            >
              <i />
            </span>
          ))}
          <span className="heading-dial-label e">0°</span>
          <span className="heading-dial-label n">90°</span>
          <span className="heading-dial-label w">180°</span>
          <span className="heading-dial-label s">270°</span>
        </div>
        <div
          className="heading-dial-knob"
          style={{ transform: `rotate(${deg - 90}deg)` }}
        >
          <div className="heading-dial-pointer" />
        </div>
        <div className="heading-dial-center">{deg.toFixed(0)}°</div>
      </div>
    </div>
  );
}
