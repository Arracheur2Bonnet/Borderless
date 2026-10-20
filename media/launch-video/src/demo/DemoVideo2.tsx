import React from 'react';
import {AbsoluteFill, Audio, Freeze, OffthreadVideo, Sequence, spring, staticFile, useCurrentFrame} from 'remotion';
import {C, INTER, MONO, TNUM} from '../theme';
import {Camera, Key, p} from '../anim';
import {Mark, Wordmark} from '../ui';
import {Hook} from '../scenes/Hook';
import {Measured} from '../scenes/Measured';
import D from '../data.json';
import PLAN from './plan.json';

// Demo video, v2 motion design. Same edit plan, same voice and subtitles as v1 (src/demo/plan.json); new visual layer:
// dark hook and lockup with an iris into the light product, kinetic headlines revealed on the words actually said,
// spring camera with a spotlight on the part of the screen being discussed, stamps on verified results, animated
// side panels for the decision flow, the risk tiers, the receipt chain and the attack test, and original UI sounds.

type Line = {text: string; accent: string[]; tone: Tone; at: number};
type Scene = {
  id: string; kind: 'rec' | 'hook' | 'brand' | 'measured' | 'terminal' | 'payoff'; from: number; dur: number; chapter: string;
  rec?: {start: number; end: number}; zoom?: Key[]; a?: number; team_from?: number; warp?: number; principle_from?: number;
  eyebrow?: string; headline?: Line[]; panel?: {kind: 'flow' | 'tiers' | 'chain' | 'shields'; active: string | null; at: number};
  stamp?: {text: string; tone: Tone; box: number[]; at: number}; close_lines?: number[];
  sfx?: {src: string; from: number; vol: number}[];
};
type Tone = 'ok' | 'warn' | 'deny' | 'info';
type Plan = {total: number; rec: string; scenes: Scene[]; voice: {id: string; from: number; src: string}[];
  subs: {from: number; to: number; text: string}[]; terminal: string[]};
const P = PLAN as unknown as Plan;
const FPS = 30;
const DARK = new Set(['hook', 'brand', 'terminal', 'payoff']);
const INK_D = '#0c0d11';
const TONE: Record<Tone, string> = {ok: '#1e9e5f', warn: '#d97706', deny: '#e0412f', info: C.accent};
const TONE_DARK: Record<Tone, string> = {ok: '#4ade80', warn: '#fbbf24', deny: '#ff6b5a', info: '#8ea2ff'};
const sp = (f: number, damping = 16, stiffness = 180) => spring({frame: f, fps: FPS, config: {damping, stiffness, mass: 0.8}});

// ------------------------------------------------------------------ backgrounds
const LightBG: React.FC = () => (
  <AbsoluteFill style={{background: `radial-gradient(1200px 700px at 50% 38%, #ffffff 0%, ${C.paper} 60%, #ece9e2 100%)`}}>
    <AbsoluteFill style={{backgroundImage: 'radial-gradient(rgba(22,23,26,0.07) 1.2px, transparent 1.3px)', backgroundSize: '28px 28px', opacity: 0.55}} />
  </AbsoluteFill>
);
const DarkBG: React.FC<{f: number; glow?: string}> = ({f, glow = 'rgba(39,65,212,0.30)'}) => (
  <AbsoluteFill style={{background: INK_D}}>
    <AbsoluteFill style={{background: `radial-gradient(900px 600px at 50% 45%, ${glow} 0%, transparent 70%)`}} />
    <AbsoluteFill style={{backgroundImage: 'linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.045) 1px, transparent 1px)',
      backgroundSize: '64px 64px', backgroundPosition: `0px ${(f * 0.35) % 64}px`, maskImage: 'radial-gradient(circle at 50% 50%, black 30%, transparent 75%)',
      WebkitMaskImage: 'radial-gradient(circle at 50% 50%, black 30%, transparent 75%)'}} />
  </AbsoluteFill>
);

// ------------------------------------------------------------------ kinetic headline (words revealed as they are said)
const Headline: React.FC<{s: Scene; f: number; dark: boolean}> = ({s, f, dark}) => {
  if (!s.headline && !s.eyebrow) return null;
  const out = 1 - p(f, s.dur - 8, s.dur);
  const lines = (s.headline ?? []).filter((l) => f >= l.at);
  const shown = lines.slice(-2);
  const col = dark ? '#f4f3ef' : C.ink;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: 22, textAlign: 'center', fontFamily: INTER, opacity: out,
      filter: `blur(${(1 - out) * 6}px)`}}>
      {s.eyebrow && (
        <div style={{fontSize: 15, fontWeight: 700, letterSpacing: '0.22em', color: dark ? 'rgba(244,243,239,0.55)' : C.ink3,
          opacity: p(f, 0, 10), transform: `translateY(${(1 - sp(f)) * 8}px)`}}>{s.eyebrow}</div>
      )}
      <div style={{marginTop: 8, display: 'flex', justifyContent: 'center', gap: 22, flexWrap: 'wrap', minHeight: 54}}>
        {shown.map((l, li) => {
          const words = l.text.split(' ');
          const isOld = li < shown.length - 1;
          return (
            <div key={l.text} style={{fontSize: 44, fontWeight: 650, letterSpacing: '-0.025em', color: col, lineHeight: '54px',
              opacity: isOld ? 0.38 : 1, transition: 'none', whiteSpace: 'nowrap'}}>
              {words.map((w, i) => {
                const t = sp(f - l.at - i * 2.2, 15, 190);
                const accent = l.accent.includes(w);
                return (
                  <span key={i} style={{display: 'inline-block', marginRight: i < words.length - 1 ? '0.26em' : 0, opacity: Math.min(1, t * 1.4),
                    transform: `translateY(${(1 - t) * 22}px)`, filter: `blur(${Math.max(0, (1 - t) * 8)}px)`,
                    color: accent ? (dark ? TONE_DARK[l.tone] : TONE[l.tone]) : col}}>{w}</span>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ------------------------------------------------------------------ stage geometry: full width, or split with a side panel
const FULL = {x: 211, y: 132, w: 1498, h: 843};
const SPLIT = {x: 64, y: 150, w: 1188, h: 669};
const geom = (s: Scene, prev: Scene | undefined, f: number) => {
  const has = Boolean(s.panel);
  const had = Boolean(prev?.panel) && prev?.kind === 'rec';
  const at = s.panel?.at ?? 0;
  let t: number;
  if (has && had) t = 1;
  else if (has) t = sp(f - at, 18, 140);
  else if (had) t = 1 - sp(f, 18, 140);
  else t = 0;
  const L = (a: number, b: number) => a + (b - a) * t;
  return {x: L(FULL.x, SPLIT.x), y: L(FULL.y, SPLIT.y), w: L(FULL.w, SPLIT.w), h: L(FULL.h, SPLIT.h), t};
};

// ------------------------------------------------------------------ the real app, recorded
const Spot: React.FC<{box: number[]; f: number; from: number; to: number; tone: Tone}> = ({box, f, from, to, tone}) => {
  const a = Math.min(p(f, from, from + 12), 1 - p(f, to - 10, to));
  if (a <= 0) return null;
  const [x, y, w, h] = box;
  const pulse = 0.5 + 0.5 * Math.sin((f - from) / 6);
  return (
    <div style={{position: 'absolute', left: x - 10, top: y - 10, width: w + 20, height: h + 20, borderRadius: 16, opacity: a,
      boxShadow: `0 0 0 4000px rgba(12,13,17,${0.22 * a}), 0 0 0 3px ${TONE[tone]}, 0 0 ${18 + 14 * pulse}px ${TONE[tone]}`}} />
  );
};
const Stamp: React.FC<{st: NonNullable<Scene['stamp']>; f: number}> = ({st, f}) => {
  const t = sp(f - st.at, 11, 220);
  if (f < st.at) return null;
  const [x, y, w] = st.box;
  const inside = st.text.startsWith('DENIED') || st.text.startsWith('NO MATCH');   // over the card's empty corner; else above the box
  return (
    <div style={{position: 'absolute', left: x + w - 40, top: inside ? y - 34 : y - 84, transform: `translate(-100%, 0) rotate(-5deg) scale(${2.2 - 1.2 * t})`,
      transformOrigin: 'right top', opacity: Math.min(1, t * 1.6), fontFamily: INTER, fontWeight: 800, fontSize: 34, letterSpacing: '0.08em',
      color: TONE[st.tone], padding: '8px 18px', border: `4px solid ${TONE[st.tone]}`, borderRadius: 10, background: 'rgba(255,255,255,0.92)',
      boxShadow: '0 10px 30px rgba(12,13,17,0.18)', whiteSpace: 'nowrap'}}>{st.text}</div>
  );
};
const Rec: React.FC<{s: Scene; f: number}> = ({s, f}) => {
  const clip = Math.max(1, s.rec!.end - s.rec!.start);
  const keys = (s.zoom && s.zoom.length ? s.zoom : [{f: 0, s: 1, x: 960, y: 540}]) as Key[];
  const video = <OffthreadVideo src={staticFile(P.rec)} startFrom={s.rec!.start} endAt={s.rec!.end + 1} muted
    style={{filter: 'contrast(1.04) saturate(1.06)'}} />;
  const zoomIn = keys.length > 2 ? keys[2].f : -1;
  const zoomOut = keys.length > 3 ? keys[3].f : s.dur - 10;
  const zBox = s.stamp?.box;
  return (
    <Camera frame={f} keys={keys}>
      {f < clip ? video : <Freeze frame={clip - 1}>{video}</Freeze>}
      {zBox && zoomIn > 0 && <Spot box={zBox} f={f} from={Math.max(zoomIn, (s.stamp?.at ?? 0) - 14)} to={zoomOut} tone={s.stamp!.tone} />}
      {s.stamp && <Stamp st={s.stamp} f={f} />}
    </Camera>
  );
};

// ------------------------------------------------------------------ side panels (what the code really does)
const Panel: React.FC<{children: React.ReactNode; f: number; at: number; title: string}> = ({children, f, at, title}) => {
  const t = sp(f - at, 17, 150);
  return (
    <div style={{position: 'absolute', left: 1290, top: 150, width: 566, height: 669, fontFamily: INTER, opacity: Math.min(1, t * 1.3),
      transform: `translateX(${(1 - t) * 60}px) perspective(1200px) rotateY(${(1 - t) * -8}deg)`}}>
      <div style={{fontSize: 14, fontWeight: 700, letterSpacing: '0.2em', color: C.ink3, marginBottom: 14}}>{title}</div>
      {children}
    </div>
  );
};
const Box: React.FC<{t: number; color?: string; bg?: string; children: React.ReactNode; style?: React.CSSProperties}> = ({t, color = C.sepStrong, bg = '#fff', children, style}) => (
  <div style={{borderRadius: 14, background: bg, border: `2px solid ${color}`, padding: '14px 18px', boxShadow: '0 8px 24px rgba(12,13,17,0.08)',
    opacity: Math.min(1, t * 1.4), transform: `translateY(${(1 - t) * 18}px) scale(${0.96 + 0.04 * t})`, ...style}}>{children}</div>
);
const FLOW: [string, string, Tone | null][] = [
  ['The model proposes', 'typed request only, no payment tool', 'warn'],
  ['Grounding', 'every amount and payee must be in her words', null],
  ['Simulation', 'the whole plan, before anything moves', null],
  ['14 rules, risk R0 to R4', 'fixed code decides', 'info'],
  ['She approves', 'one tap or a one-time code', 'ok'],
  ['Sandbox ledger + receipt', 'hash-linked, signed copy', null],
];
const Flow: React.FC<{f: number; at: number}> = ({f, at}) => (
  <Panel f={f} at={at} title="HOW A REQUEST IS DECIDED">
    {FLOW.map(([a, b, tone], i) => {
      const t = sp(f - at - 8 - i * 7, 16, 170);
      return (
        <React.Fragment key={a}>
          <Box t={t} color={tone ? TONE[tone] : C.sepStrong} style={{padding: '10px 16px'}}>
            <div style={{fontSize: 21, fontWeight: 650, color: C.ink}}>{a}</div>
            <div style={{fontSize: 15.5, color: C.ink2, marginTop: 2}}>{b}</div>
          </Box>
          {i < FLOW.length - 1 && <div style={{height: 14, width: 2, background: C.sepStrong, margin: '0 0 0 34px', transform: `scaleY(${t})`, transformOrigin: 'top'}} />}
        </React.Fragment>
      );
    })}
  </Panel>
);
const TIERS: [string, string, string, Tone][] = [
  ['R0', 'Read only', 'balances, overview, simulations', 'info'],
  ['R2', 'One tap', 'saved payee, up to 1,500 EUR', 'ok'],
  ['R3', 'One-time code', 'new payee, larger amounts', 'warn'],
  ['R4', 'Denied, no override', 'caps, unsaved account, sanctions', 'deny'],
];
const Tiers: React.FC<{f: number; at: number; active: string | null}> = ({f, at, active}) => (
  <Panel f={f} at={at} title="APPROVAL IN PROPORTION TO RISK">
    {TIERS.map(([r, a, b, tone], i) => {
      const t = sp(f - at - 6 - i * 5, 16, 170);
      const on = active === r || (active === 'done' && (r === 'R2' || r === 'R3'));
      const pulse = on ? 0.5 + 0.5 * Math.sin((f - at) / 5) : 0;
      return (
        <Box key={r} t={t} color={on ? TONE[tone] : C.sep} bg={on ? '#fff' : 'rgba(255,255,255,0.6)'}
          style={{marginBottom: 16, display: 'flex', alignItems: 'center', gap: 18, boxShadow: on ? `0 0 ${16 + 12 * pulse}px ${TONE[tone]}55` : undefined,
            transform: `translateX(${on ? -10 : 0}px)`}}>
          <div style={{width: 64, height: 64, borderRadius: 14, background: on ? TONE[tone] : C.inset, color: on ? '#fff' : C.ink2, fontSize: 26,
            fontWeight: 800, display: 'flex', alignItems: 'center', justifyContent: 'center', ...TNUM}}>{r}</div>
          <div>
            <div style={{fontSize: 24, fontWeight: 650, color: on ? TONE[tone] : C.ink}}>{a}</div>
            <div style={{fontSize: 16, color: C.ink2, marginTop: 2}}>{b}</div>
          </div>
          {on && active === 'done' && <div style={{marginLeft: 'auto', fontSize: 30, color: TONE.ok}}>✓</div>}
        </Box>
      );
    })}
    <div style={{fontSize: 15.5, color: C.ink2, marginTop: 6, opacity: p(f, at + 30, at + 44)}}>
      Three wrong codes in a row freeze every approval.
    </div>
  </Panel>
);
const Chain: React.FC<{f: number; at: number; active: string | null}> = ({f, at, active}) => (
  <Panel f={f} at={at} title="HASH-LINKED RECEIPTS">
    {D.receipts.map((r, i) => {
      const t = sp(f - at - 6 - i * 8, 16, 170);
      return (
        <React.Fragment key={r.seq}>
          <Box t={t} color={i === 2 && active === 'anchor' ? TONE.ok : C.sepStrong} style={{padding: '12px 16px'}}>
            <div style={{display: 'flex', justifyContent: 'space-between', fontSize: 15, color: C.ink3, fontWeight: 700, letterSpacing: '0.1em'}}>
              <span>RECEIPT #{r.seq}</span><span style={{fontFamily: MONO, letterSpacing: 0}}>{r.hash}</span>
            </div>
            <div style={{fontSize: 17.5, color: C.ink, marginTop: 6, lineHeight: '24px'}}>{r.summary}</div>
            <div style={{fontSize: 14, color: C.ink3, marginTop: 6, fontFamily: MONO}}>links to {r.prev}</div>
          </Box>
          {i < 2 && <div style={{display: 'flex', justifyContent: 'center', height: 22}}>
            <div style={{width: 3, height: 22, background: C.accent, transform: `scaleY(${t})`, transformOrigin: 'top'}} /></div>}
        </React.Fragment>
      );
    })}
    <Box t={sp(f - at - 34, 16, 170)} color={TONE.ok} style={{marginTop: 18, background: 'rgba(30,158,95,0.08)'}}>
      <div style={{fontSize: 19, fontWeight: 650, color: '#17633d'}}>Her signed copy: receipt #3</div>
      <div style={{fontSize: 15.5, color: C.ink2, marginTop: 2}}>{active === 'anchor' ? 'a deleted receipt no longer matches it' : 'the chain is checked against it'}</div>
    </Box>
  </Panel>
);
const ATTACKS = ['Injected rent invoice', 'Account swapped by a typed IBAN', "Ambiguous 'send the maximum'", 'Poisoned exchange-rate output',
  'Replay: confirm twice', 'Structuring: split into three', 'Sanctioned payee'];
const Shields: React.FC<{f: number; at: number; done: number}> = ({f, at, done}) => (
  <Panel f={f} at={at} title="SEVEN KNOWN ATTACKS, REPLAYED LIVE">
    {ATTACKS.map((a, i) => {
      const t = sp(f - at - 4 - i * 4, 16, 170);
      const hit = f >= done - 40 + i * 6;
      return (
        <Box key={a} t={t} color={hit ? TONE.ok : C.sep} style={{marginBottom: 10, padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 14}}>
          <div style={{width: 30, height: 30, borderRadius: 15, background: hit ? TONE.ok : C.inset, color: '#fff', fontSize: 18, fontWeight: 800,
            display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${hit ? 1 : 0.9})`}}>{hit ? '✓' : i + 1}</div>
          <div style={{fontSize: 19, color: C.ink, flex: 1}}>{a}</div>
          <div style={{fontSize: 14, fontWeight: 800, letterSpacing: '0.12em', color: hit ? TONE.ok : C.ink3, opacity: hit ? 1 : 0.4}}>BLOCKED</div>
        </Box>
      );
    })}
  </Panel>
);

// ------------------------------------------------------------------ dark scenes: hook, brand, evidence, close
const DarkHook: React.FC<{s: Scene; f: number}> = ({s, f}) => {
  const a = s.a ?? 200;
  const enter = sp(f, 18, 120);
  const reveal = p(f, a - 4, a + 10);
  return (
    <AbsoluteFill>
      <DarkBG f={f} glow={`rgba(${Math.round(39 + 170 * reveal)},${Math.round(65 - 20 * reveal)},${Math.round(212 - 160 * reveal)},0.32)`} />
      <div style={{position: 'absolute', left: 290, top: 150, width: 1340, height: 754, borderRadius: 22, overflow: 'hidden',
        boxShadow: `0 40px 120px rgba(0,0,0,0.55), 0 0 ${60 * reveal}px rgba(224,65,47,${0.55 * reveal})`,
        transform: `perspective(1600px) rotateX(${(1 - enter) * 14}deg) translateY(${(1 - enter) * 80}px) scale(${0.92 + 0.08 * enter})`,
        opacity: Math.min(1, enter * 1.5)}}>
        <div style={{width: 1920, height: 1080, transform: `scale(${1340 / 1920})`, transformOrigin: '0 0'}}>
          <Hook f={f} a={a} total={s.dur} />
        </div>
      </div>
    </AbsoluteFill>
  );
};
const DarkBrand: React.FC<{s: Scene; f: number}> = ({s, f}) => {
  const t = sp(f - 4, 14, 120);
  const out = 1 - p(f, s.dur - 12, s.dur);
  const rays = Array.from({length: 26});
  return (
    <AbsoluteFill style={{opacity: out}}>
      <DarkBG f={f} />
      <div style={{position: 'absolute', left: 960, top: 430, width: 0, height: 0}}>
        {rays.map((_, i) => {
          const ang = (i / rays.length) * 360 + f * 0.15;
          const len = 260 + 220 * t + (i % 3) * 40;
          return <div key={i} style={{position: 'absolute', left: 0, top: 0, width: len, height: 1.5, transformOrigin: '0 50%',
            transform: `rotate(${ang}deg) translateX(${140 + 40 * t}px)`, background: 'linear-gradient(90deg, rgba(142,162,255,0.45), transparent)',
            opacity: 0.55 * t}} />;
        })}
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 300, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 34,
        transform: `scale(${0.9 + 0.1 * t})`, opacity: Math.min(1, t * 1.4)}}>
        <div style={{width: 150, height: 150, borderRadius: 36, background: '#f6f5f1', display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 20px 60px rgba(39,65,212,0.45)'}}>
          <Mark size={104} dots={p(f, 6, 26)} route={p(f, 12, 40)} box={p(f, 28, 42)} check={p(f, 38, 52)} />
        </div>
        <div style={{clipPath: `inset(0 ${(1 - p(f, 30, 54)) * 100}% 0 0)`}}><Wordmark size={92} color="#f4f3ef" /></div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 500, textAlign: 'center', fontFamily: INTER, fontSize: 34, fontWeight: 500,
        color: 'rgba(244,243,239,0.82)', opacity: p(f, 54, 72), transform: `translateY(${(1 - p(f, 54, 72)) * 12}px)`}}>
        An AI that can pay her bills, and cannot be talked into paying the wrong person.
      </div>
      {s.team_from !== undefined && (
        <div style={{position: 'absolute', left: 0, right: 0, top: 640, textAlign: 'center', fontFamily: INTER, opacity: p(f, s.team_from, s.team_from + 14),
          transform: `translateY(${(1 - sp(f - s.team_from)) * 14}px)`}}>
          <div style={{display: 'inline-block', padding: '16px 34px', borderRadius: 18, border: '1px solid rgba(244,243,239,0.18)', background: 'rgba(255,255,255,0.05)'}}>
            <div style={{fontSize: 32, fontWeight: 650, color: '#f4f3ef'}}>Team Centrale</div>
            <div style={{fontSize: 22, color: 'rgba(244,243,239,0.7)', marginTop: 6}}>International AI Track · Topic C: Cross-border and Student Finance</div>
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};
const DarkTerminal: React.FC<{s: Scene; f: number}> = ({s, f}) => {
  const t = sp(f, 18, 140);
  return (
    <AbsoluteFill>
      <DarkBG f={f} glow="rgba(30,158,95,0.22)" />
      <div style={{position: 'absolute', left: 240, top: 210, width: 1440, height: 640, borderRadius: 20, background: '#14151b',
        border: '1px solid rgba(255,255,255,0.10)', boxShadow: '0 30px 100px rgba(0,0,0,0.6), 0 0 80px rgba(30,158,95,0.18)',
        transform: `perspective(1600px) rotateX(${(1 - t) * 10}deg) scale(${0.94 + 0.06 * t})`, opacity: Math.min(1, t * 1.5), overflow: 'hidden'}}>
        <div style={{height: 46, background: 'rgba(255,255,255,0.04)', display: 'flex', alignItems: 'center', gap: 10, padding: '0 20px',
          borderBottom: '1px solid rgba(255,255,255,0.07)'}}>
          {['#ff5f57', '#febc2e', '#28c840'].map((c) => <div key={c} style={{width: 13, height: 13, borderRadius: 7, background: c}} />)}
          <div style={{marginLeft: 16, fontFamily: MONO, fontSize: 16, color: 'rgba(244,243,239,0.55)'}}>github.com/Arracheur2Bonnet/Borderless · offline · sandbox</div>
        </div>
        <div style={{padding: '30px 40px'}}>
          {P.terminal.map((ln, i) => {
            const st = 10 + i * 10;
            const shown = ln.startsWith('$') ? ln.slice(0, Math.max(0, Math.floor((f - st) * 1.6))) : ln;
            const pass = ln.startsWith('PASS');
            const all = ln.startsWith('ALL');
            return (
              <div key={i} style={{fontFamily: MONO, fontSize: 24, lineHeight: '44px', whiteSpace: 'pre', opacity: p(f, st, st + 5),
                color: ln.startsWith('$') ? '#8ea2ff' : pass ? '#4ade80' : all ? '#ffffff' : '#c9c7c0', fontWeight: all ? 700 : 400,
                textShadow: pass || all ? '0 0 14px rgba(74,222,128,0.35)' : 'none'}}>{shown || ' '}</div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};
const DarkClose: React.FC<{s: Scene; f: number}> = ({s, f}) => {
  const L = s.close_lines ?? [200, 240, 280];
  const lines: [string, string, string][] = [['The AI ', 'reads.', '#8ea2ff'], ['The rules ', 'decide.', '#f4f3ef'], ['She ', 'approves.', '#4ade80']];
  const lock = L[2] + 70;
  const linesOut = p(f, lock - 16, lock);
  const limits = 1 - p(f, L[0] - 12, L[0] - 2);
  const cmd = '$ python scripts/verify_all.py';
  return (
    <AbsoluteFill style={{fontFamily: INTER}}>
      <DarkBG f={f} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 410, textAlign: 'center', opacity: Math.min(p(f, 0, 12), limits)}}>
        <div style={{fontSize: 58, fontWeight: 650, color: '#f4f3ef', letterSpacing: '-0.025em'}}>A prototype on a simulated bank.</div>
        <div style={{fontSize: 30, color: 'rgba(244,243,239,0.65)', marginTop: 16, opacity: p(f, 16, 30)}}>Not a production system. No real money moves.</div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 300, opacity: 1 - linesOut, transform: `translateY(${-linesOut * 20}px)`}}>
        {lines.map(([a, b, c], i) => {
          const t = sp(f - L[i] + 2, 15, 170);
          if (f < L[i] - 2) return <div key={b} style={{height: 128}} />;
          return (
            <div key={b} style={{textAlign: 'center', fontSize: 96, fontWeight: 650, letterSpacing: '-0.035em', lineHeight: '128px', color: '#f4f3ef',
              opacity: Math.min(1, t * 1.4), transform: `translateY(${(1 - t) * 26}px)`, filter: `blur(${(1 - t) * 10}px)`}}>
              {a}<span style={{color: c, textShadow: `0 0 30px ${c}55`}}>{b}</span>
            </div>
          );
        })}
      </div>
      {f >= lock && (
        <>
          <div style={{position: 'absolute', left: 0, right: 0, top: 330, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 30,
            transform: `scale(${0.92 + 0.08 * sp(f - lock)})`, opacity: p(f, lock, lock + 10)}}>
            <div style={{width: 128, height: 128, borderRadius: 30, background: '#f6f5f1', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
              <Mark size={90} dots={p(f - lock, 0, 16)} route={p(f - lock, 4, 26)} box={p(f - lock, 16, 26)} check={p(f - lock, 22, 34)} />
            </div>
            <Wordmark size={84} color="#f4f3ef" />
          </div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 500, textAlign: 'center', fontSize: 26, color: 'rgba(244,243,239,0.75)', opacity: p(f, lock + 18, lock + 30)}}>
            Team Centrale · International AI Track · Topic C: Cross-border and Student Finance
          </div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 580, display: 'flex', justifyContent: 'center', opacity: p(f, lock + 24, lock + 34)}}>
            <div style={{fontFamily: MONO, fontSize: 26, color: '#8ea2ff', padding: '12px 24px', borderRadius: 12, background: 'rgba(255,255,255,0.05)',
              border: '1px solid rgba(255,255,255,0.12)'}}>
              {cmd.slice(0, Math.max(0, Math.floor((f - lock - 26) * 1.5)))}<span style={{opacity: Math.floor(f / 15) % 2 ? 1 : 0}}>▌</span>
            </div>
          </div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 666, textAlign: 'center', fontSize: 22, letterSpacing: '0.04em', color: 'rgba(244,243,239,0.7)'}}>
            {['Offline', 'No API key', 'Sandbox, no real money', 'MIT licence'].map((w, i) => (
              <span key={w} style={{opacity: p(f, lock + 50 + i * 6, lock + 58 + i * 6), color: i === 0 ? '#4ade80' : undefined}}>{i ? '  ·  ' : ''}{w}</span>
            ))}
          </div>
        </>
      )}
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------------ one scene
const SceneView: React.FC<{s: Scene; prev?: Scene}> = ({s, prev}) => {
  const f = useCurrentFrame();
  const dark = DARK.has(s.kind);
  if (s.kind === 'hook') return <AbsoluteFill><DarkHook s={s} f={f} /><Headline s={s} f={f} dark /></AbsoluteFill>;
  if (s.kind === 'brand') return <DarkBrand s={s} f={f} />;
  if (s.kind === 'terminal') return <AbsoluteFill><DarkTerminal s={s} f={f} /><Headline s={s} f={f} dark /></AbsoluteFill>;
  if (s.kind === 'payoff') return <DarkClose s={s} f={f} />;
  const g = s.kind === 'rec' ? geom(s, prev, f) : {...FULL, t: 0};
  const settle = sp(f, 15, 170);
  const iris = prev && DARK.has(prev.kind) ? p(f, 0, 20) : 1;
  const body = s.kind === 'rec' ? <Rec s={s} f={f} /> : <Measured f={f * (s.warp ?? 1)} dur={s.dur * (s.warp ?? 1)} />;
  return (
    <AbsoluteFill style={{clipPath: iris < 1 ? `circle(${iris * 120}% at 50% 50%)` : undefined, background: dark ? INK_D : undefined}}>
      <LightBG />
      <div style={{position: 'absolute', left: g.x, top: g.y, width: g.w, height: g.h, borderRadius: 18, overflow: 'hidden', background: C.paper,
        boxShadow: s.kind === 'rec' ? '0 2px 6px rgba(12,13,17,0.06), 0 30px 80px rgba(12,13,17,0.20)' : 'none',
        outline: s.kind === 'rec' ? '1px solid rgba(28,27,24,0.10)' : 'none',
        transform: `perspective(1800px) rotateX(${(1 - settle) * 5}deg) scale(${1.035 - 0.035 * settle})`, filter: `blur(${(1 - settle) * 5}px)`}}>
        <div style={{width: 1920, height: 1080, transform: `scale(${g.w / 1920})`, transformOrigin: '0 0'}}>{body}</div>
        {s.kind === 'rec' && (
          <div style={{position: 'absolute', right: 16, top: 14, fontFamily: INTER, fontSize: 14, fontWeight: 700, letterSpacing: '0.12em', color: '#8a4106',
            background: 'rgba(255,255,255,0.92)', padding: '6px 12px', borderRadius: 999, boxShadow: '0 4px 14px rgba(12,13,17,0.10)'}}>SANDBOX · NO REAL MONEY</div>
        )}
      </div>
      {s.panel?.kind === 'flow' && <Flow f={f} at={s.panel.at} />}
      {s.panel?.kind === 'tiers' && <Tiers f={f} at={s.panel.at} active={s.panel.active} />}
      {s.panel?.kind === 'chain' && <Chain f={f} at={s.panel.at} active={s.panel.active} />}
      {s.panel?.kind === 'shields' && <Shields f={f} at={s.panel.at} done={s.headline && s.headline[1] ? s.headline[1].at : s.dur - 60} />}
      <Headline s={s} f={f} dark={false} />
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------------ subtitles, under the picture
const Subtitles: React.FC = () => {
  const f = useCurrentFrame();
  const sub = P.subs.find((x) => f >= x.from && f < x.to);
  if (!sub) return null;
  const s = P.scenes.find((x) => f >= x.from && f < x.from + x.dur);
  const dark = s ? DARK.has(s.kind) : false;
  const t = Math.min(p(f, sub.from, sub.from + 4), 1 - p(f, sub.to - 3, sub.to));
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 26, display: 'flex', justifyContent: 'center', opacity: t}}>
      <div style={{fontFamily: INTER, fontSize: 30, lineHeight: '40px', fontWeight: 500, textAlign: 'center', maxWidth: 1500, padding: '6px 20px',
        borderRadius: 12, color: dark ? '#f4f3ef' : C.ink, background: dark ? 'rgba(12,13,17,0.55)' : 'rgba(246,245,241,0.86)'}}>{sub.text}</div>
    </div>
  );
};

export const DemoVideo2: React.FC = () => {
  const sfx = P.scenes[0].sfx ?? [];
  return (
    <AbsoluteFill style={{background: INK_D}}>
      {P.scenes.map((s, i) => (
        <Sequence key={s.id} from={s.from} durationInFrames={s.dur} name={`${s.id} (${s.kind})`}>
          <SceneView s={s} prev={P.scenes[i - 1]} />
        </Sequence>
      ))}
      <Subtitles />
      {P.voice.map((v) => (
        <Sequence key={`vo-${v.id}`} from={v.from} name={`voice ${v.id}`}><Audio src={staticFile(v.src)} /></Sequence>
      ))}
      {sfx.map((x, i) => (
        <Sequence key={`sfx-${i}`} from={x.from} durationInFrames={30} name={`sfx ${x.src}`}><Audio src={staticFile(x.src)} volume={x.vol} /></Sequence>
      ))}
    </AbsoluteFill>
  );
};

export const DEMO2_TOTAL = P.total;
