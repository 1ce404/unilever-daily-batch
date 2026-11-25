/** [INPUT] Remotion frame; palette, shape and caption width. [OUTPUT] Transparent brand stickers or a single-row caption backplate. [POS] Bottom/side safe zones only; no extra text. */
import React from 'react';
import {AbsoluteFill,useCurrentFrame} from 'remotion';
export const Composition=(props:any={})=>{
 const p={accent:'#A890BA',pale:'#FAF4FF',kind:'sparkle',durationFrames:36,chars:8,...props,...(props.params||{})};
 const f=useCurrentFrame(),d=Math.max(2,p.durationFrames),u=Math.max(0,Math.min(1,f/(d-1))),fade=Math.min(1,f/4,(d-1-f)/4),pop=1+.06*Math.sin(Math.min(1,u*3)*Math.PI);
 if(f<0||f>=d)return null;
 const w=Math.min(860,Math.max(230,p.chars*51+72)),y=1550;
 if(p.kind==='mat')return <AbsoluteFill style={{pointerEvents:'none'}}><svg viewBox="0 0 1080 1920" width="100%" height="100%" opacity={Math.max(0,fade)}><g transform={`translate(540 ${y}) scale(${pop})`}>
 {p.shape==='brackets'?<><path d={`M${-w/2+18} -36h-26v72h26M${w/2-18} -36h26v72h-26`} fill="none" stroke={p.accent} strokeWidth="4" strokeLinecap="round"/><path d={`M${-w/2+35} 39H${w/2-35}`} stroke={p.accent} strokeWidth="3" strokeDasharray="8 9" opacity=".8"/></>:p.shape==='brush'?<><path d={`M${-w/2-12} -27L${w/2+7} -35L${w/2-3} 31L${-w/2-5} 39Z`} fill={p.pale} opacity=".96"/><path d={`M${-w/2+4} 38L${w/2-10} 30`} stroke={p.accent} strokeWidth="5" strokeLinecap="round"/><path d={`M${-w/2-18} -8l9-17M${w/2+15} 17l-8 17`} stroke={p.accent} strokeWidth="3"/></>:<><rect x={-w/2+4} y="-33" width={w} height="78" rx={p.shape==='tab'?8:34} fill={p.accent} opacity=".18"/><rect x={-w/2} y="-39" width={w} height="78" rx={p.shape==='tab'?8:34} fill={p.pale} stroke={p.accent} strokeWidth="2.5"/><path d={`M${-w/2+20} 29H${w/2-20}`} stroke={p.accent} strokeWidth="3" strokeLinecap="round" opacity=".65"/></>}
 <path d={`M${-w/2-22} -9v18m-9-9h18M${w/2+20} -6v12m-6-6h12`} stroke={p.accent} strokeWidth="3" strokeLinecap="round"/>
 </g></svg></AbsoluteFill>;
 const x=p.side==='left'?125:955,yy=1300+Math.sin(u*Math.PI)*-14;
 return <AbsoluteFill style={{pointerEvents:'none'}}><svg width="100%" height="100%" viewBox="0 0 1080 1920"><g transform={`translate(${x} ${yy}) scale(${pop})`} opacity={Math.max(0,fade)} fill="none" stroke={p.accent} strokeWidth="5" strokeLinecap="round" strokeLinejoin="round">
 <circle r="72" fill={p.pale} stroke="none" opacity=".88"/>
 {p.kind==='lens'?<><circle cx="-8" cy="-10" r="29"/><path d="M15 13l26 26M-24-18l8-6"/></>:p.kind==='petal'?<>{[0,60,120,180,240,300].map(a=><ellipse key={a} cx="0" cy="-24" rx="12" ry="23" transform={`rotate(${a})`}/>)}<circle r="8" fill={p.accent}/></>:p.kind==='flow'?<><path d="M-40-24c28-24 50 24 78 0M-40 0c28-24 50 24 78 0M-40 24c28-24 50 24 78 0"/></>:p.kind==='mist'?<><path d="M-32 24v-32h20v32zM-26-8v-12h16M-10-20h14"/>{[0,1,2,3,4].map(i=><circle key={i} cx={16+i*6} cy={-36+i*8} r={2+i%2} fill={p.accent} opacity={.45+.45*Math.sin((u+i*.12)*Math.PI)}/>)}</>:<><path d="M0-42C4-10 10-4 40 0 10 4 4 10 0 42-4 10-10 4-40 0-10-4-4-10 0-42z" fill={p.accent} opacity=".8"/><path d="M40-40v16m-8-8h16M-38 30v12m-6-6h12" strokeWidth="3"/></>}
 </g></svg></AbsoluteFill>;
};
