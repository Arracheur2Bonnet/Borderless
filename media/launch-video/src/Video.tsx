import React from 'react';
import {AbsoluteFill, Audio, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {C} from './theme';
import {Caption} from './anim';
import {HAS_MUSIC, schedule, slot} from './timeline';
import {Hook} from './scenes/Hook';
import {Brand} from './scenes/Brand';
import {Product} from './scenes/Product';
import {Measured} from './scenes/Measured';
import {Payoff} from './scenes/Payoff';

/** Gives a scene its local frame in 30 fps units, so the 30 and 60 fps renders share one choreography. */
const At: React.FC<{render: (f: number) => React.ReactNode}> = ({render}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return <>{render((frame * 30) / fps)}</>;
};

export const LaunchVideo: React.FC = () => {
  const {fps, durationInFrames} = useVideoConfig();
  const sch = schedule(fps);
  const k = 30 / fps;
  const s = (id: string) => slot(sch, id);
  const [s1, s2, s3, s9, s10] = ['01-invoice', '02-hidden-line', '03-brand', '09-measured', '10-payoff'].map(s);
  const prod = ['04-ask', '05-plan', '06-approve', '07-payee-check', '08-receipts'].map(s);
  const pStart = prod[0].from;
  const pDur = prod[4].from + prod[4].dur - pStart;

  return (
    <AbsoluteFill style={{background: C.paper}}>
      <Sequence from={s1.from} durationInFrames={s1.dur + s2.dur} name="Hook: the invoice">
        <At render={(f) => <Hook f={f} a={s1.dur * k} total={(s1.dur + s2.dur) * k} />} />
      </Sequence>
      <Sequence from={s3.from} durationInFrames={s3.dur} name="Reveal: brand">
        <At render={(f) => <Brand f={f} dur={s3.dur * k} />} />
      </Sequence>
      <Sequence from={pStart} durationInFrames={pDur} name="Reveal and proof: the app">
        <At render={(f) => <Product f={f} starts={prod.map((x) => (x.from - pStart) * k)} durs={prod.map((x) => x.dur * k)} />} />
      </Sequence>
      <Sequence from={s9.from} durationInFrames={s9.dur} name="Proof: measured blind">
        <At render={(f) => <Measured f={f} dur={s9.dur * k} />} />
      </Sequence>
      <Sequence from={s10.from} durationInFrames={s10.dur} name="Payoff">
        <At render={(f) => <Payoff f={f} dur={s10.dur * k} />} />
      </Sequence>

      {sch.filter((x) => x.caption).map((x) => (
        <Sequence key={`cap-${x.id}`} from={x.from} durationInFrames={x.dur} name={`Caption ${x.id}`}>
          <At render={(f) => <Caption text={x.caption} frame={f} dur={Math.min(x.dur * k, (x.captionSeconds ?? 1e9) * 30)} />} />
        </Sequence>
      ))}

      {sch.filter((x) => x.hasVoice).map((x) => (
        <Sequence key={`vo-${x.id}`} from={x.from} durationInFrames={x.dur} name={`Voice ${x.id}`}>
          <Audio src={staticFile(`voiceover/${x.id}.mp3`)} />
        </Sequence>
      ))}
      {HAS_MUSIC && (
        <Audio src={staticFile('music.mp3')}
          volume={(fr) => interpolate(fr, [0, fps, durationInFrames - 2 * fps, durationInFrames], [0, 0.16, 0.16, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})} />
      )}
    </AbsoluteFill>
  );
};
