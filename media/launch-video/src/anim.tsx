import React from 'react';
import {AbsoluteFill, interpolate} from 'remotion';
import {C, EASE, INTER} from './theme';

export const CL = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
/** Eased 0..1 progress between frames a and b. */
export const p = (f: number, a: number, b: number): number => (b <= a ? (f >= b ? 1 : 0) : interpolate(f, [a, b], [0, 1], {...CL, easing: EASE}));
export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

export const rise = (f: number, start: number, dur = 16, dist = 14): React.CSSProperties => {
  const t = p(f, start, start + dur);
  return {opacity: t, transform: `translateY(${(1 - t) * dist}px)`};
};

/** Scene envelope: fade in over the first frames, out over the last ones (scenes dip through paper). */
export const envelope = (f: number, dur: number, edge = 10): number => Math.min(p(f, 0, edge), 1 - p(f, dur - edge, dur));

export const fmt = (n: number): string => n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});

/** Camera: keyframes give a zoom and the layer point (1920x1080 space) to bring to the centre of the frame. */
export type Key = {f: number; s: number; x: number; y: number};
export const Camera: React.FC<{frame: number; keys: Key[]; children: React.ReactNode}> = ({frame, keys, children}) => {
  let {s, x, y} = keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (frame >= a.f) {
      const t = p(frame, a.f, b.f);
      s = lerp(a.s, b.s, t);
      x = lerp(a.x, b.x, t);
      y = lerp(a.y, b.y, t);
    }
  }
  const tx = -s * (x - 960);
  const ty = -s * (y - 540);
  return (
    <AbsoluteFill style={{transform: `translate(${tx}px, ${ty}px) scale(${s})`, transformOrigin: '960px 540px'}}>
      {children}
    </AbsoluteFill>
  );
};

/** One short line, top centre, revealed word by word (the reference's caption grammar). */
export const Caption: React.FC<{text: string; frame: number; dur: number}> = ({text, frame, dur}) => {
  if (!text) return null;
  const out = 1 - p(frame, dur - 12, dur - 2);
  const chip = p(frame, 2, 10);
  return (
    <div style={{position: 'absolute', top: 40, left: 0, right: 0, display: 'flex', justifyContent: 'center', opacity: out}}>
      {/* a paper chip keeps the line legible when the camera is close on the product */}
      <div style={{fontFamily: INTER, fontSize: 32, fontWeight: 500, color: C.ink, letterSpacing: '-0.012em', padding: '10px 24px 11px 24px',
        borderRadius: 14, background: `rgba(246, 245, 241, ${0.94 * chip})`, whiteSpace: 'nowrap'}}>
        {text.split(' ').map((w, i, all) => {
          const t = p(frame, 6 + i * 2.4, 6 + i * 2.4 + 12);
          return (
            <span key={i} style={{display: 'inline-block', opacity: t, transform: `translateY(${(1 - t) * 8}px)`, marginRight: i < all.length - 1 ? '0.27em' : 0}}>
              {w}
            </span>
          );
        })}
      </div>
    </div>
  );
};

/** A pointer that moves like a hand (eased between points) and shows a soft ring on each click. */
export type PKey = {f: number; x: number; y: number};
export const Pointer: React.FC<{frame: number; path: PKey[]; clicks: number[]; hideAfter?: number}> = ({frame, path, clicks, hideAfter}) => {
  if (!path.length || frame < path[0].f - 8) return null;
  let {x, y} = path[0];
  for (let i = 0; i < path.length - 1; i++) {
    const a = path[i];
    const b = path[i + 1];
    if (frame >= a.f) {
      const t = p(frame, a.f, b.f);
      x = lerp(a.x, b.x, t);
      // a slight arc, like a wrist
      y = lerp(a.y, b.y, t) - Math.sin(Math.PI * t) * Math.min(40, Math.abs(b.x - a.x) * 0.06);
    }
  }
  let opacity = p(frame, path[0].f - 8, path[0].f);
  if (hideAfter !== undefined) opacity *= 1 - p(frame, hideAfter, hideAfter + 8);
  let press = 1;
  const rings: React.ReactNode[] = [];
  clicks.forEach((c, i) => {
    if (frame >= c - 3 && frame < c + 3) press = 0.86;
    if (frame >= c && frame < c + 16) {
      const t = (frame - c) / 16;
      rings.push(<div key={i} style={{position: 'absolute', left: x - 22, top: y - 22, width: 44, height: 44, borderRadius: 22,
        border: `2px solid ${C.accent}`, opacity: 0.45 * (1 - t), transform: `scale(${0.5 + t})`}} />);
    }
  });
  return (
    <div style={{position: 'absolute', inset: 0, pointerEvents: 'none', opacity}}>
      {rings}
      <svg width={30} height={38} viewBox="0 0 20 26" style={{position: 'absolute', left: x - 2, top: y - 2, transform: `scale(${press})`, transformOrigin: '2px 2px'}}>
        <path d="M1.5 1.5 L1.5 20.5 L6.4 15.8 L9.7 23.4 L13 22 L9.8 14.6 L16.6 14.6 Z" fill={C.ink} stroke="#ffffff" strokeWidth="1.4" strokeLinejoin="round" />
      </svg>
    </div>
  );
};

/** Characters typed so far at `cps` characters per second, starting at frame `start`. */
export const typed = (text: string, frame: number, start: number, fps: number, cps = 22): string =>
  text.slice(0, Math.max(0, Math.min(text.length, Math.floor(((frame - start) / fps) * cps))));
