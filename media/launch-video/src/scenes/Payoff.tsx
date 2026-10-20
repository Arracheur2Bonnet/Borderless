import React from 'react';
import {AbsoluteFill} from 'remotion';
import D from '../data.json';
import {C, INTER} from '../theme';
import {p} from '../anim';
import {Mark, Wordmark} from '../ui';

// Beat 4 (segment 10): the principle in three short lines, then the lockup and the hand-off to the live demo.

const LINES: [string, string][] = [['The AI ', 'reads.'], ['The rules ', 'decide.'], ['She ', 'approves.']];

export const Payoff: React.FC<{f: number; dur: number}> = ({f, dur}) => {
  const linesOut = p(f, 128, 146);
  const lock = f - 146;
  return (
    <AbsoluteFill style={{background: C.paper, fontFamily: INTER, opacity: p(f, 0, 10)}}>
      <div style={{position: 'absolute', left: 0, right: 0, top: 330, opacity: 1 - linesOut, transform: `translateY(${-linesOut * 16}px)`}}>
        {LINES.map(([a, b], i) => {
          const t = p(f, 8 + i * 38, 26 + i * 38);
          return (
            <div key={b} style={{textAlign: 'center', fontSize: 92, fontWeight: 600, letterSpacing: '-0.03em', lineHeight: '128px',
              color: C.ink, opacity: t, transform: `translateY(${(1 - t) * 14}px)`}}>
              {a}<span style={{color: i === 2 ? C.accent : C.ink}}>{b}</span>
            </div>
          );
        })}
      </div>

      {lock > 0 && (
        <>
          <div style={{position: 'absolute', left: 0, right: 0, top: 400, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 30}}>
            <Mark size={120} dots={p(lock, 0, 18)} route={p(lock, 4, 28)} box={p(lock, 16, 28)} check={p(lock, 24, 36)} />
            <div style={{opacity: p(lock, 14, 32), clipPath: `inset(0 ${(1 - p(lock, 14, 34)) * 100}% 0 0)`}}>
              <Wordmark size={88} />
            </div>
          </div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 580, textAlign: 'center', fontSize: 26, color: C.ink2, opacity: p(lock, 36, 52)}}>
            {D.team}
          </div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 636, textAlign: 'center', fontSize: 18, fontWeight: 600, letterSpacing: '0.12em',
            color: C.accent, opacity: p(lock, 50, 66)}}>
            NEXT: THE LIVE DEMO
          </div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 1004, textAlign: 'center', fontSize: 15, color: C.ink3, opacity: p(lock, 50, 66)}}>
            Sandbox prototype. No real money moves.
          </div>
        </>
      )}
    </AbsoluteFill>
  );
};
