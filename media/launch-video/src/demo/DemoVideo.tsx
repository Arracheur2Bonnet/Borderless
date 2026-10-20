import React from 'react';
import {AbsoluteFill, Audio, Freeze, OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {C, INTER, MONO} from '../theme';
import {Camera, Key, p} from '../anim';
import {Hook} from '../scenes/Hook';
import {Brand} from '../scenes/Brand';
import {Measured} from '../scenes/Measured';
import {Payoff} from '../scenes/Payoff';
import PLAN from './plan.json';

// The official demo video (at most 5 minutes). Everything is placed by src/demo/plan.json, written by
// scripts/demo_video.py from the narration (deliverables/pitch/voiceover_script.txt), the recording markers
// (public/rec/markers.json) and, once it exists, the measured voice-over (public/vo/NN.wav).

type Zoom = {f: number; s: number; x: number; y: number};
type Scene = {
  id: string; kind: 'rec' | 'hook' | 'brand' | 'measured' | 'terminal' | 'payoff'; from: number; dur: number;
  chapter: string; rec?: {start: number; end: number}; zoom?: Zoom[]; a?: number; team_from?: number; warp?: number;
  principle_from?: number;
  callout?: {text: string; from: number; dur: number; tone?: 'ok' | 'warn' | 'deny' | 'info'};
};
type Plan = {
  fps: number; total: number; stage: {x: number; y: number; w: number; h: number};
  rec: string; scenes: Scene[]; voice: {id: string; from: number; src: string}[];
  subs: {from: number; to: number; text: string}[]; terminal: string[]; team: string; topic: string;
};
const P = PLAN as unknown as Plan;
const FADE = 6;

const RecScene: React.FC<{s: Scene}> = ({s}) => {
  const f = useCurrentFrame();
  const clip = Math.max(1, (s.rec!.end - s.rec!.start));
  const keys: Key[] = (s.zoom && s.zoom.length ? s.zoom : [{f: 0, s: 1, x: 960, y: 540}]) as Key[];
  const video = <OffthreadVideo src={staticFile(P.rec)} startFrom={s.rec!.start} endAt={s.rec!.end + 1} muted />;
  return (
    <Camera frame={f} keys={keys}>
      {f < clip ? video : <Freeze frame={clip - 1}>{video}</Freeze>}
    </Camera>
  );
};

const Terminal: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const lines = P.terminal;
  return (
    <AbsoluteFill style={{background: C.paper, fontFamily: INTER, opacity: p(f, 0, 10)}}>
      <div style={{position: 'absolute', left: 150, top: 120, fontSize: 30, fontWeight: 600, color: C.ink, letterSpacing: '-0.01em'}}>
        Execution evidence, rerun offline
      </div>
      <div style={{position: 'absolute', left: 150, top: 172, fontSize: 22, color: C.ink2}}>
        github.com/Arracheur2Bonnet/Borderless · no API key, no network, sandbox ledger
      </div>
      <div style={{position: 'absolute', left: 150, right: 150, top: 240, bottom: 120, background: '#16171a', borderRadius: 18,
        padding: '34px 40px', boxShadow: '0 18px 50px rgba(0,0,0,0.18)'}}>
        {lines.map((ln, i) => {
          const start = 8 + i * 9;
          const t = p(f, start, start + 6);
          const isCmd = ln.startsWith('$');
          const pass = ln.startsWith('PASS');
          const col = isCmd ? '#9db4ff' : pass ? '#7fd3a3' : ln.startsWith('ALL') ? '#ffffff' : '#c9c7c0';
          return (
            <div key={i} style={{fontFamily: MONO, fontSize: 23, lineHeight: '42px', whiteSpace: 'pre', color: col, opacity: t,
              fontWeight: ln.startsWith('ALL') ? 700 : 400}}>{ln}</div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

/** The honest limit, said before the principle: a prototype on a simulated bank. */
const Limits: React.FC<{f: number; dur: number}> = ({f, dur}) => (
  <AbsoluteFill style={{background: C.paper, fontFamily: INTER, opacity: Math.min(p(f, 0, 10), 1 - p(f, dur - 8, dur))}}>
    <div style={{position: 'absolute', left: 0, right: 0, top: 410, textAlign: 'center', fontSize: 62, fontWeight: 600, color: C.ink,
      letterSpacing: '-0.025em'}}>A prototype on a simulated bank.</div>
    <div style={{position: 'absolute', left: 0, right: 0, top: 500, textAlign: 'center', fontSize: 36, color: C.ink2,
      opacity: p(f, 18, 34)}}>Not a production system. No real money moves.</div>
  </AbsoluteFill>
);

const Stage: React.FC<{s: Scene}> = ({s}) => {
  const f = useCurrentFrame();
  const a = s.a ?? Math.round(s.dur / 2);
  let body: React.ReactNode = null;
  if (s.kind === 'rec') body = <RecScene s={s} />;
  else if (s.kind === 'hook') body = <Hook f={f} a={a} total={s.dur} />;
  else if (s.kind === 'brand') body = <Brand f={f} dur={s.dur} team={s.team_from !== undefined ? {from: s.team_from, name: P.team, track: 'International AI Track · Topic C: Cross-border and Student Finance'} : undefined} />;
  else if (s.kind === 'measured') body = <Measured f={f * (s.warp ?? 1)} dur={s.dur * (s.warp ?? 1)} />;
  else if (s.kind === 'terminal') body = <Terminal dur={s.dur} />;
  else if (s.kind === 'payoff') {
    const d = Math.max(0, (s.principle_from ?? 0) - 8);
    body = f < d ? <Limits f={f} dur={d} /> : <Payoff f={f - d} dur={s.dur - d} closing={"International AI Track · Topic C: Cross-border and Student Finance"} />;
  }
  const st = P.stage;
  const framed = s.kind === 'rec';
  return (
    <AbsoluteFill style={{opacity: p(f, 0, FADE)}}>
      <div style={{position: 'absolute', left: st.x, top: st.y, width: st.w, height: st.h, overflow: 'hidden', borderRadius: framed ? 16 : 0,
        boxShadow: framed ? '0 2px 4px rgba(22,23,26,0.06), 0 18px 48px rgba(22,23,26,0.14)' : 'none',
        outline: framed ? '1px solid rgba(28,27,24,0.10)' : 'none', background: C.paper}}>
        <div style={{width: 1920, height: 1080, transform: `scale(${st.w / 1920})`, transformOrigin: '0 0'}}>
          {body}
        </div>
      </div>
      {s.callout && f >= s.callout.from && f < s.callout.from + s.callout.dur && (
        <Callout text={s.callout.text} tone={s.callout.tone ?? 'info'} f={f - s.callout.from} dur={s.callout.dur} />
      )}
    </AbsoluteFill>
  );
};

const TONES = {ok: [C.successText, C.successTint], warn: [C.warningText, C.warningTint], deny: [C.dangerText, C.dangerTint],
  info: [C.accent, C.accentTint]} as const;

const Callout: React.FC<{text: string; tone: keyof typeof TONES; f: number; dur: number}> = ({text, tone, f, dur}) => {
  const t = Math.min(p(f, 0, 10), 1 - p(f, dur - 10, dur));
  const st = P.stage;
  const [fg, bg] = TONES[tone];
  return (
    <div style={{position: 'absolute', right: 1920 - (st.x + st.w) + 26, top: st.y + 22, opacity: t, transform: `translateY(${(1 - t) * -8}px)`,
      fontFamily: INTER, fontSize: 27, fontWeight: 600, color: fg, background: '#ffffff', padding: '12px 22px', borderRadius: 14,
      boxShadow: `0 0 0 2px ${bg}, 0 10px 30px rgba(22,23,26,0.16)`}}>
      {text}
    </div>
  );
};

const Chrome: React.FC = () => {
  const f = useCurrentFrame();
  const s = P.scenes.find((x) => f >= x.from && f < x.from + x.dur);
  const st = P.stage;
  return (
    <>
      <div style={{position: 'absolute', left: st.x + 4, top: 18, fontFamily: INTER, fontSize: 22, fontWeight: 600, color: C.ink2,
        letterSpacing: '0.01em'}}>{s?.chapter ?? ''}</div>
      <div style={{position: 'absolute', right: 1920 - (st.x + st.w) + 4, top: 18, fontFamily: INTER, fontSize: 20, color: C.ink3}}>
        {P.team} · {P.topic} · <span style={{color: C.warningText, fontWeight: 600}}>sandbox, no real money</span>
      </div>
    </>
  );
};

const Subtitles: React.FC = () => {
  const f = useCurrentFrame();
  const sub = P.subs.find((x) => f >= x.from && f < x.to);
  if (!sub) return null;
  const st = P.stage;
  const t = Math.min(p(f, sub.from, sub.from + 4), 1 - p(f, sub.to - 3, sub.to));
  return (
    <div style={{position: 'absolute', left: st.x, width: st.w, top: st.y + st.h + 12, display: 'flex', justifyContent: 'center', opacity: t}}>
      <div style={{fontFamily: INTER, fontSize: 31, lineHeight: '42px', fontWeight: 500, color: C.ink, textAlign: 'center', maxWidth: st.w - 120,
        letterSpacing: '-0.005em'}}>{sub.text}</div>
    </div>
  );
};

export const DemoVideo: React.FC = () => (
  <AbsoluteFill style={{background: C.paper}}>
    {P.scenes.map((s) => (
      <Sequence key={s.id} from={s.from} durationInFrames={s.dur} name={`${s.id} (${s.kind})`}>
        <Stage s={s} />
      </Sequence>
    ))}
    <Chrome />
    <Subtitles />
    {P.voice.map((v) => (
      <Sequence key={`vo-${v.id}`} from={v.from} name={`voice ${v.id}`}>
        <Audio src={staticFile(v.src)} />
      </Sequence>
    ))}
  </AbsoluteFill>
);

export const DEMO_TOTAL = P.total;
