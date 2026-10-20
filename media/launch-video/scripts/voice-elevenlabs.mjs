// Optional: generates the ten voice-over clips with the ElevenLabs text-to-speech API, one per segment, into
// public/voiceover/<segment id>.mp3. Each request passes the neighbouring segments as previous_text / next_text
// so the delivery stays continuous across cuts. Existing clips are kept unless --force is given.
//
//   ELEVENLABS_API_KEY=...  ELEVENLABS_VOICE_ID=...  npm run voice            (PowerShell: $env:ELEVENLABS_API_KEY="...")
//   optional: ELEVENLABS_MODEL (default eleven_multilingual_v2)
//
// Then `npm run sync-voice` and `npm run render`. The key is read from the environment only and never written.
import {existsSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const {segments} = JSON.parse(readFileSync(join(root, 'src', 'timeline.json'), 'utf8'));
const key = process.env.ELEVENLABS_API_KEY;
const voice = process.env.ELEVENLABS_VOICE_ID;
const model = process.env.ELEVENLABS_MODEL || 'eleven_multilingual_v2';
const force = process.argv.includes('--force');
if (!key || !voice) {
  console.error('Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID (see VOICEOVER.md). Nothing was generated.');
  process.exit(1);
}
const dir = join(root, 'public', 'voiceover');
mkdirSync(dir, {recursive: true});

for (const [i, seg] of segments.entries()) {
  const file = join(dir, `${seg.id}.mp3`);
  if (existsSync(file) && !force) {
    console.log(`${seg.id}: kept (use --force to regenerate)`);
    continue;
  }
  const body = {
    text: seg.vo,
    model_id: model,
    voice_settings: {stability: 0.55, similarity_boost: 0.75, style: 0.1, use_speaker_boost: true, speed: 1.0},
    previous_text: segments[i - 1]?.vo,
    next_text: segments[i + 1]?.vo,
  };
  const res = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voice)}?output_format=mp3_44100_128`, {
    method: 'POST',
    headers: {'xi-api-key': key, 'Content-Type': 'application/json', Accept: 'audio/mpeg'},
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    console.error(`${seg.id}: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);
    process.exit(1);
  }
  writeFileSync(file, Buffer.from(await res.arrayBuffer()));
  console.log(`${seg.id}: written`);
}
console.log('Done. Next: npm run sync-voice, then npm run render.');
