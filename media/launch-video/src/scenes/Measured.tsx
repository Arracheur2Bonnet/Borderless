import React from 'react';
import {AbsoluteFill} from 'remotion';
import D from '../data.json';
import {C, INTER, TNUM} from '../theme';
import {p, rise} from '../anim';

// Beat 3 closes (segment 09): the blind results, frozen before any fix (deliverables/heldout_v2_results.md, blind_llm/).
// One dot per attack. The single miss is shown, not hidden.

const MISS = 57;                       // position of the one attack that was not blocked (order carries no meaning)
const GRID = {x: 1090, y: 250, cols: 10, size: 34, gap: 26};

export const Measured: React.FC<{f: number; dur: number}> = ({f, dur}) => {
  const R = D.blind.rules;
  const Q = D.blind.qwen;
  const out = 1 - p(f, dur - 12, dur);
  const fillStart = 46;
  const filled = Math.max(0, Math.min(R.total, Math.floor((f - fillStart) / 1.0)));
  const blockedSoFar = Array.from({length: filled}).filter((_, i) => i !== MISS).length;
  const big = filled >= R.total ? R.blocked : blockedSoFar;

  return (
    <AbsoluteFill style={{background: C.paper, fontFamily: INTER, opacity: Math.min(p(f, 0, 12), out)}}>
      {/* left: the headline number */}
      <div style={{position: 'absolute', left: 200, top: 250, ...rise(f, 6, 18, 12)}}>
        <div style={{display: 'flex', alignItems: 'baseline'}}>
          <span style={{fontSize: 210, fontWeight: 600, letterSpacing: '-0.04em', color: C.ink, lineHeight: 1, ...TNUM}}>{big}</span>
          <span style={{fontSize: 96, fontWeight: 500, color: C.ink3, letterSpacing: '-0.02em', marginLeft: 8, ...TNUM}}>/{R.total}</span>
        </div>
        <div style={{fontSize: 40, fontWeight: 600, color: C.ink, marginTop: 18, letterSpacing: '-0.015em'}}>attacks blocked</div>
        <div style={{fontSize: 23, lineHeight: '34px', color: C.ink2, marginTop: 14, maxWidth: 700, ...rise(f, 24, 18, 8)}}>
          Blind test: {R.total} attacks written by a separate AI agent, results frozen before any fix.
        </div>
      </div>

      {/* right: one dot per attack */}
      {Array.from({length: R.total}).map((_, i) => {
        const col = i % GRID.cols;
        const row = Math.floor(i / GRID.cols);
        const x = GRID.x + col * (GRID.size + GRID.gap);
        const y = GRID.y + row * (GRID.size + GRID.gap);
        const appear = p(f, 8 + i * 0.25, 20 + i * 0.25);
        const on = p(f, fillStart + i, fillStart + i + 6);
        const miss = i === MISS;
        return (
          <div key={i} style={{position: 'absolute', left: x, top: y, width: GRID.size, height: GRID.size, borderRadius: GRID.size / 2, boxSizing: 'border-box',
            opacity: appear, border: `2px solid ${miss && on > 0 ? C.warning : on > 0.5 ? C.ink : C.sepStrong}`,
            background: miss ? 'transparent' : `rgba(22, 23, 26, ${on})`, transform: `scale(${0.9 + 0.1 * on})`}} />
        );
      })}
      <div style={{position: 'absolute', left: GRID.x, top: GRID.y + 7 * (GRID.size + GRID.gap) + 6, display: 'flex', gap: 28, fontSize: 18, color: C.ink2,
        opacity: p(f, fillStart + 74, fillStart + 90)}}>
        <span style={{display: 'flex', alignItems: 'center', gap: 8}}><span style={{width: 14, height: 14, borderRadius: 7, background: C.ink}} />blocked</span>
        <span style={{display: 'flex', alignItems: 'center', gap: 8}}><span style={{width: 14, height: 14, borderRadius: 7, border: `2px solid ${C.warning}`, boxSizing: 'border-box'}} />
          the one miss still needed her code; no money moved</span>
      </div>

      {/* bottom band: no wrong payment, rules or local model */}
      <div style={{position: 'absolute', left: 200, right: 200, top: 770, height: 1, background: C.sep, transform: `scaleX(${p(f, 186, 214)})`, transformOrigin: '0 0'}} />
      <div style={{position: 'absolute', left: 200, top: 806, width: 700, ...rise(f, 196, 18, 10)}}>
        <div style={{fontSize: 76, fontWeight: 600, color: C.ink, lineHeight: 1, ...TNUM}}>{R.wrong_payments}</div>
        <div style={{fontSize: 24, color: C.ink2, marginTop: 10}}>payments to an account she never saved</div>
      </div>
      <div style={{position: 'absolute', left: 1090, top: 806, width: 640, ...rise(f, 290, 18, 10)}}>
        <div style={{display: 'flex', alignItems: 'baseline', gap: 14}}>
          <span style={{fontSize: 76, fontWeight: 600, color: C.ink, lineHeight: 1, ...TNUM}}>{Q.blocked}/{Q.total}</span>
          <span style={{fontSize: 22, color: C.ink2}}>blocked</span>
        </div>
        <div style={{fontSize: 24, color: C.ink2, marginTop: 10}}>{Q.model}, same rules: still {Q.wrong_payments} wrong payments</div>
      </div>
      <div style={{position: 'absolute', left: 200, top: 1000, fontSize: 15, color: C.ink3, opacity: p(f, 320, 340)}}>
        One blind run each, frozen in commits {R.commit} and {Q.commit}. Sandbox prototype: no real money moves.
      </div>
    </AbsoluteFill>
  );
};
