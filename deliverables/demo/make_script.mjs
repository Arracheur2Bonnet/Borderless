// Exports the narration of the demo presentation from index.html (the single source of truth):
//   NARRATION.md     the script for the narrator: chapter, on-screen caption, what to say, about how long
//   demo.en.srt      English subtitles timed like the autoplay mode (press A), to use when you record with autoplay
//
//   node deliverables/demo/make_script.mjs
import {readFileSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, 'index.html'), 'utf8');
const a = html.indexOf('const CH = [');
const b = html.indexOf('// ---------------------------------------------------------------------------------------------- engine');
if (a < 0 || b < 0) throw new Error('could not find the STEPS block in index.html');
const {CH, STEPS} = new Function(`${html.slice(a, b)}; return {CH, STEPS};`)();

// same timing rule as the page: about 150 words a minute, plus a beat for the camera
const dur = (st) => st.dur || Math.max(4, st.say.split(/\s+/).length / 2.5 + 1.2);
const ts = (t) => {
  const ms = Math.round(t * 1000);
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${p(Math.floor(ms / 3600000))}:${p(Math.floor(ms / 60000) % 60)}:${p(Math.floor(ms / 1000) % 60)},${p(ms % 1000, 3)}`;
};
const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`;

let t = 0;
let md = `# Demo narration\n\nGenerated from \`index.html\` by \`make_script.mjs\`: edit the steps there, then run this again.\n\n`;
md += `${STEPS.length} steps. In autoplay (press A) they last about ${mmss(STEPS.reduce((s, x) => s + dur(x), 0))} at 150 words a minute.\n\n`;
md += `| # | Starts | Chapter | On screen | Say |\n|---|---|---|---|---|\n`;
const cues = [];
STEPS.forEach((st, i) => {
  const d = dur(st);
  md += `| ${i + 1} | ${mmss(t)} | ${CH[st.ch]} | ${st.cap || '(title screen)'} | ${st.say} |\n`;
  // split long lines into two cues so each stays readable (at most about 84 characters)
  const parts = st.say.length > 84 ? st.say.split(/(?<=[.?!:])\s+/).filter(Boolean) : [st.say];   // never inside 11.96
  const merged = [];
  for (const s of parts) {
    const last = merged[merged.length - 1];
    if (last && (last + ' ' + s).length <= 84) merged[merged.length - 1] = `${last} ${s}`; else merged.push(s);
  }
  const speech = d - 1.0;
  const chars = merged.reduce((n, s) => n + s.length, 0);
  let c = t + 0.3;
  for (const s of merged) {
    const len = (speech * s.length) / chars;
    cues.push({from: c, to: c + len, text: s});
    c += len;
  }
  t += d;
});
md += `\nTotal: ${mmss(t)}.\n`;
writeFileSync(join(here, 'NARRATION.md'), md);
const wrap = (s) => {
  if (s.length <= 42) return s;
  let best = -1;
  for (let i = 0; i < s.length; i++) if (s[i] === ' ' && (best < 0 || Math.abs(i - s.length / 2) < Math.abs(best - s.length / 2))) best = i;
  return best < 0 ? s : `${s.slice(0, best)}\n${s.slice(best + 1)}`;
};
writeFileSync(join(here, 'demo.en.srt'), cues.map((c, i) => `${i + 1}\n${ts(c.from)} --> ${ts(c.to)}\n${wrap(c.text)}\n`).join('\n'));
console.log(`wrote NARRATION.md and demo.en.srt: ${STEPS.length} steps, ${cues.length} cues, ${t.toFixed(1)} s`);
