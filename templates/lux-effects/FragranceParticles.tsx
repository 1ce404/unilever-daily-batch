/**
 * [INPUT]: React、Remotion 帧时钟；颜色、密度、种子与局部时长参数。
 * [OUTPUT]: Composition，确定性透明侧边香氛光点。
 * [POS]: lux-effects 的结果镜头叠层；中心人物、字幕与角标区留空。
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import React from "react";
import {AbsoluteFill, useCurrentFrame} from "remotion";

const defaults = {accent: "#E7C78B", intensity: 0.65, count: 16, seed: 7, durationFrames: 42};
type Params = Partial<typeof defaults>;
type Props = Params & {params?: Params};
const finite = (v: unknown, fallback: number, low: number, high: number) => Number.isFinite(Number(v)) ? Math.min(high, Math.max(low, Number(v))) : fallback;
const fract = (x: number) => x - Math.floor(x);
const random = (seed: number) => fract(Math.sin(seed * 127.1 + 311.7) * 43758.5453);

export const Composition = (props: Props = {}) => {
  const p = {...defaults, ...props, ...(props.params || {})};
  const f = useCurrentFrame();
  const duration = finite(p.durationFrames, 42, 2, 90);
  if (f < 0 || f >= duration) return null;
  const phase = f / Math.max(1, duration - 1);
  const accent = /^#[0-9a-f]{6}$/i.test(p.accent) ? p.accent : defaults.accent;
  const seed = finite(p.seed, 7, 0, 10000);
  const count = Math.round(finite(p.count, 16, 0, 24));
  return <AbsoluteFill data-effect="fragrance-particles" style={{pointerEvents: "none", overflow: "hidden", background: "transparent"}}>
    <svg width="100%" height="100%" viewBox="0 0 1080 1920" aria-hidden="true">
      {Array.from({length: count}, (_, i) => {
        const r = random(seed + i * 9 + 1);
        const x = i % 2 ? 895 + r * 130 : 55 + r * 130;
        const y = 250 + random(seed + i * 9 + 2) * 960 - phase * (45 + r * 80);
        const radius = 3 + random(seed + i * 9 + 3) * 8;
        const pulse = Math.sin(Math.PI * phase) * (.6 + .4 * Math.sin((phase + r) * Math.PI));
        return <g key={i} opacity={pulse * finite(p.intensity, .65, 0, .85)}>
          <circle cx={x} cy={y} r={radius * 2.6} fill={accent} opacity=".1"/>
          <circle cx={x} cy={y} r={radius} fill={accent}/>
          {i % 4 === 0 && <path d={`M${x-radius*2} ${y}H${x+radius*2}M${x} ${y-radius*2}V${y+radius*2}`} stroke="#FFF6E8" strokeWidth="1.2"/>}
        </g>;
      })}
    </svg>
  </AbsoluteFill>;
};
