/**
 * [INPUT]: React、Remotion 帧时钟；颜色、强度与局部时长参数。
 * [OUTPUT]: Composition，透明香槟金扫光，不绘制文字。
 * [POS]: lux-effects 的产品亮相叠层；素材与字幕由原轨道提供。
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import React from "react";
import {AbsoluteFill, useCurrentFrame, interpolate} from "remotion";

const defaults = {accent: "#E7C78B", intensity: 0.32, durationFrames: 42};
type Params = Partial<typeof defaults>;
type Props = Params & {params?: Params};
const finite = (v: unknown, fallback: number, low: number, high: number) => Number.isFinite(Number(v)) ? Math.min(high, Math.max(low, Number(v))) : fallback;
const color = (v: unknown) => typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v) ? v : defaults.accent;

export const Composition = (props: Props = {}) => {
  const p = {...defaults, ...props, ...(props.params || {})};
  const f = useCurrentFrame();
  const duration = finite(p.durationFrames, 42, 2, 90);
  if (f < 0 || f >= duration) return null;
  const phase = f / Math.max(1, duration - 1);
  const envelope = Math.sin(Math.PI * phase);
  const x = interpolate(phase, [0, 1], [-420, 1480]);
  const accent = color(p.accent);
  return <AbsoluteFill data-effect="champagne-sweep" style={{pointerEvents: "none", overflow: "hidden", background: "transparent"}}>
    <svg width="100%" height="100%" viewBox="0 0 1080 1920" aria-hidden="true">
      <defs>
        <linearGradient id="lux-sweep-band"><stop stopColor={accent} stopOpacity="0"/><stop offset=".48" stopColor={accent} stopOpacity=".35"/><stop offset=".52" stopColor="#FFF6E8" stopOpacity=".85"/><stop offset="1" stopColor={accent} stopOpacity="0"/></linearGradient>
        <linearGradient id="lux-sweep-safe" x2="0" y2="1"><stop stopColor="white"/><stop offset=".68" stopColor="white"/><stop offset=".79" stopColor="black"/></linearGradient>
        <mask id="lux-sweep-mask"><rect width="1080" height="1920" fill="url(#lux-sweep-safe)"/></mask>
      </defs>
      <g mask="url(#lux-sweep-mask)" opacity={envelope * finite(p.intensity, .32, 0, .45)}>
        <path d={`M${x-110} 0L${x+250} 0L${x-250} 1530L${x-610} 1530Z`} fill="url(#lux-sweep-band)"/>
        <path d={`M${x+65} 120L${x-355} 1390`} stroke={accent} strokeWidth="2" opacity=".6"/>
      </g>
    </svg>
  </AbsoluteFill>;
};
