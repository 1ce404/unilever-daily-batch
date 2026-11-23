/**
 * [INPUT]: React、Remotion 帧时钟；颜色、强度与局部时长参数。
 * [OUTPUT]: Composition，透明侧边柔光弧线。
 * [POS]: lux-effects 的顺滑结果叠层；不改变主画面、字幕或声音。
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import React from "react";
import {AbsoluteFill, useCurrentFrame} from "remotion";

const defaults = {accent: "#EEC5D6", intensity: 0.48, durationFrames: 42};
type Params = Partial<typeof defaults>;
type Props = Params & {params?: Params};
const finite = (v: unknown, fallback: number, low: number, high: number) => Number.isFinite(Number(v)) ? Math.min(high, Math.max(low, Number(v))) : fallback;

export const Composition = (props: Props = {}) => {
  const p = {...defaults, ...props, ...(props.params || {})};
  const f = useCurrentFrame();
  const duration = finite(p.durationFrames, 42, 2, 90);
  if (f < 0 || f >= duration) return null;
  const phase = f / Math.max(1, duration - 1);
  const accent = /^#[0-9a-f]{6}$/i.test(p.accent) ? p.accent : defaults.accent;
  return <AbsoluteFill data-effect="silk-glow" style={{pointerEvents: "none", overflow: "hidden", background: "transparent"}}>
    <svg width="100%" height="100%" viewBox="0 0 1080 1920" aria-hidden="true">
      <defs><linearGradient id="lux-silk"><stop stopColor={accent} stopOpacity="0"/><stop offset=".5" stopColor={accent}/><stop offset="1" stopColor="#FFF6E8" stopOpacity="0"/></linearGradient></defs>
      <g opacity={Math.sin(Math.PI * phase) * finite(p.intensity, .48, 0, .65)}>
        {[0,1,2].map(i => <React.Fragment key={i}>
          <path d={`M${-100+i*30} 1350C${340+i*12} 990 ${-210+i*20} 700 ${125+i*25} 210`} fill="none" stroke="url(#lux-silk)" strokeWidth={i === 1 ? 3 : 1.4} pathLength="1" strokeDasharray="1" strokeDashoffset={1-phase}/>
          <path d={`M${1180-i*30} 1320C${740-i*12} 980 ${1290-i*20} 690 ${955-i*25} 200`} fill="none" stroke="url(#lux-silk)" strokeWidth={i === 1 ? 3 : 1.4} pathLength="1" strokeDasharray="1" strokeDashoffset={1-phase}/>
        </React.Fragment>)}
      </g>
    </svg>
  </AbsoluteFill>;
};
