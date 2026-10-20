import T from './timeline.json';
import V from './voiceover.json';

export type Seg = {id: string; beat: string; seconds: number; caption: string; vo: string; captionSeconds?: number};
export type Slot = Seg & {from: number; dur: number; hasVoice: boolean};

export const SEGMENTS = T.segments as Seg[];
const VOICE = ((V as {durations?: Record<string, number>}).durations ?? {}) as Record<string, number>;
export const HAS_MUSIC = Boolean((V as {music?: boolean}).music);

/** Frame schedule for a given fps: segments play back to back; a voice-over clip can only lengthen a segment. */
export function schedule(fps: number): Slot[] {
  let t = 0;
  return SEGMENTS.map((s) => {
    const v = VOICE[s.id];
    const sec = Math.max(s.seconds, v ? v + T.voicePaddingSec : 0);
    const out = {...s, from: Math.round(t * fps), dur: Math.round(sec * fps), hasVoice: Boolean(v)};
    t += sec;
    return out;
  });
}

export function totalFrames(fps: number): number {
  const s = schedule(fps);
  const last = s[s.length - 1];
  return last.from + last.dur;
}

export function slot(sch: Slot[], id: string): Slot {
  const s = sch.find((x) => x.id === id);
  if (!s) throw new Error(`unknown segment ${id}`);
  return s;
}
