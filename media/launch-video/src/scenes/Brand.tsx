import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C, INTER} from '../theme';
import {p} from '../anim';
import {Mark, Wordmark} from '../ui';

// Beat 2 opens (segment 03): the mark draws itself, the name and the promise follow. No caption: the lockup is the text.

export const Brand: React.FC<{f: number; dur: number}> = ({f, dur}) => {
  const out = 1 - p(f, dur - 12, dur);
  const word = p(f, 40, 62);
  const line = p(f, 70, 92);
  return (
    <AbsoluteFill style={{background: C.paper, opacity: Math.min(p(f, 0, 8), out), fontFamily: INTER,
      transform: `scale(${1 - 0.02 * (1 - out)})`}}>
      <div style={{position: 'absolute', left: 0, right: 0, top: 380, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 34}}>
        <Mark size={136} dots={p(f, 4, 26)} route={p(f, 12, 42)} box={p(f, 30, 44)} check={p(f, 40, 54)} />
        <div style={{overflow: 'hidden', paddingBottom: 12, marginBottom: -12}}>
          <div style={{opacity: word, transform: `translateX(${(1 - word) * -24}px)`, clipPath: `inset(0 ${(1 - word) * 100}% 0 0)`}}>
            <Wordmark size={96} />
          </div>
        </div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 590, textAlign: 'center', fontSize: 34, color: C.ink2, fontWeight: 500, letterSpacing: '-0.01em',
        opacity: line, transform: `translateY(${(1 - line) * 10}px)`}}>
        An AI that can pay her bills, and cannot be talked into paying the wrong person.
      </div>
    </AbsoluteFill>
  );
};
