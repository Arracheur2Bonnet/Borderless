// Writes English subtitles (SRT) for the film from the narration in src/timeline.json, timed with the same
// schedule as the video (src/voiceover.json included). Output: out/borderlessguard-launch.en.srt
//
// A cue starts 0.15 s into its segment and lasts as long as the measured clip when there is one; without a
// recording it is spread over the segment at a speaking pace. Long lines are split at sentence or comma breaks.
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const T = JSON.parse(readFileSync(join(root, 'src', 'timeline.json'), 'utf8'));
const V = JSON.parse(readFileSync(join(root, 'src', 'voiceover.json'), 'utf8'));
const voice = V.durations ?? {};
const MAX = 84;            // characters per cue (two lines of 42)

function split(text) {
  if (text.length <= MAX) return [text];
  const parts = text.split(/(?<=[.?!:]["']?)\s+/).filter(Boolean);   // split after a sentence, never inside 11.96
  const out = [];
  for (const part of parts) {
    if (part.length <= MAX) { out.push(part); continue; }
    let cur = '';
    for (const chunk of part.split(/(?<=,)\s+/)) {
      if (cur && (cur + ' ' + chunk).length > MAX) { out.push(cur); cur = chunk; } else cur = cur ? `${cur} ${chunk}` : chunk;
    }
    if (cur) out.push(cur);
  }
  // merge very short neighbours
  const merged = [];
  for (const s of out) {
    const last = merged[merged.length - 1];
    if (last && (last + ' ' + s).length <= MAX) merged[merged.length - 1] = `${last} ${s}`; else merged.push(s);
  }
  return merged;
}

function wrap2(s) {
  if (s.length <= 42) return s;
  const mid = s.length / 2;
  let best = -1;
  for (let i = 0; i < s.length; i++) if (s[i] === ' ' && (best < 0 || Math.abs(i - mid) < Math.abs(best - mid))) best = i;
  return best < 0 ? s : `${s.slice(0, best)}\n${s.slice(best + 1)}`;
}

const ts = (t) => {
  const ms = Math.round(t * 1000);
  const h = String(Math.floor(ms / 3600000)).padStart(2, '0');
  const m = String(Math.floor((ms % 3600000) / 60000)).padStart(2, '0');
  const s = String(Math.floor((ms % 60000) / 1000)).padStart(2, '0');
  return `${h}:${m}:${s},${String(ms % 1000).padStart(3, '0')}`;
};

let t = 0;
let n = 0;
const cues = [];
for (const seg of T.segments) {
  const clip = voice[seg.id];
  const len = Math.max(seg.seconds, clip ? clip + T.voicePaddingSec : 0);
  const speech = clip ?? Math.min(len - 0.4, (seg.vo.split(/\s+/).length / 150) * 60 + 0.4);
  const parts = split(seg.vo);
  const chars = parts.reduce((a, s) => a + s.length, 0);
  let start = t + 0.15;
  for (const part of parts) {
    const d = (speech * part.length) / chars;
    cues.push(`${++n}\n${ts(start)} --> ${ts(Math.min(start + d, t + len - 0.05))}\n${wrap2(part)}\n`);
    start += d;
  }
  t += len;
}
mkdirSync(join(root, 'out'), {recursive: true});
const file = join(root, 'out', 'borderlessguard-launch.en.srt');
writeFileSync(file, cues.join('\n'));
console.log(`wrote ${file}: ${n} cues, film length ${t.toFixed(2)} s`);
