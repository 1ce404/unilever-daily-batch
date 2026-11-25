/** [INPUT] Clipo effect params. [OUTPUT] Short upper flower-text in explicit safe coordinates. [POS] Independent packaging; native subtitles remain one uniform SubtitleClip style. */
import React from 'react';
import {AbsoluteFill,useCurrentFrame} from 'remotion';
export const Composition=(props:any={})=>{
 const p={text:'发丝有点小心机',accent:'#D7B76D',pale:'#FFF9E8',variant:1,x:540,top:96,fontSize:52,durationFrames:45,...props,...(props.params||{})};
 const f=useCurrentFrame(),d=Math.max(2,p.durationFrames),u=Math.min(1,Math.max(0,f/7)),alpha=Math.max(0,Math.min(1,f/4,(d-1-f)/4));
 if(f<0||f>=d)return null;
 const v=Number(p.variant),box=v===2||v===5,tilt=v===3?-1.5:0;
 return <AbsoluteFill style={{pointerEvents:'none',background:'transparent'}}><div style={{position:'absolute',left:p.x,top:p.top,transform:`translateX(-50%) translateY(${(1-u)*-12}px) rotate(${tilt}deg)`,opacity:alpha,display:'flex',alignItems:'center',gap:14,padding:box?'10px 26px':'6px 16px',border:box?`2px solid ${p.accent}`:'none',borderRadius:v===5?50:v===2?10:0,background:box?p.pale:'transparent',boxShadow:box?'0 4px 12px rgba(30,20,10,.08)':'none',whiteSpace:'nowrap'}}>
 <span style={{position:'relative',fontFamily:'Noto Sans CJK SC,sans-serif',fontWeight:800,fontSize:p.fontSize,lineHeight:1.3,color:box?p.accent:v===4?'#FFF8F1':p.pale,WebkitTextStroke:box?'0px':`1.5px ${v===4?'#61514D':p.accent}`,textShadow:box?'none':'0 2px 3px rgba(30,20,10,.24)',letterSpacing:2}}>{p.text}{v===3&&<span style={{position:'absolute',left:0,right:0,bottom:-7,height:5,background:p.accent,borderRadius:8}}/>}</span>
 {v===1&&<svg width="24" height="30" viewBox="0 0 24 30"><path d="M12 0c0 10 2 13 12 15-10 2-12 5-12 15C12 20 10 17 0 15 10 13 12 10 12 0" fill={p.accent}/></svg>}
 {v===4&&<span style={{height:40,width:5,background:p.accent,borderRadius:3}}/>}
 </div></AbsoluteFill>;
};
