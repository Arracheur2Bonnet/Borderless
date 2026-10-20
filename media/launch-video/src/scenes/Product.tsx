import React from 'react';
import {AbsoluteFill} from 'remotion';
import D from '../data.json';
import {C, INTER, TNUM} from '../theme';
import {Camera, Key, PKey, Pointer, lerp, p, rise, typed} from '../anim';
import {ActionCard, ActionData, CARD_GEOM, COL, Composer, DocLine, Dots, Hash, Header, Mark2, Notice, PAYEE_GEOM, PayeeCard, Pill, Sidebar, TAB_X, UserBubble, Window} from '../ui';

// Beats 2 and 3 (segments 04 to 08) as one continuous take of the app, like the reference film.
// Every string comes from data.json (the real orchestrator) or from ui/app.py. Frames are 30 fps units.

const WIN = {x: 160, y: 96, w: 1600, h: 920};
const OX = WIN.x;                       // window content origin, layer coordinates
const OY = WIN.y + 44;
const VIEW = {y: 148, h: 636};          // chat viewport, content coordinates
const COMPOSER_Y = 796;

// chat content, in scroll coordinates
const ACTIONS = D.actions as ActionData[];
const H = ACTIONS.map((a) => CARD_GEOM.height(a.facts.length, a.verdict === 'step_up'));
const Y = {b1: 0, doc: 94, r0: 130, notice: 200, r1: 313, r2: 375, r3: 409, r4: 499, r5: 533, c1: 585, c2: 0, c3: 0, b2: 0, vop: 0, c4: 0};
Y.c2 = Y.c1 + H[0] + 16;
Y.c3 = Y.c2 + H[1] + 16;
Y.b2 = Y.c3 + H[2] + 32;
Y.vop = Y.b2 + 94;
Y.c4 = Y.vop + 124;
const SC = {c1: Y.c1 - 12, c2: Y.c2 - 12, c3: Y.c3 - 12, vop: Y.b2 - 12};

// layer coordinates of what the pointer clicks
const ax = (x: number) => OX + COL.x + x;
const ay = (y: number, scroll: number) => OY + VIEW.y + y - scroll;
const SEND = {x: OX + COL.x + COL.w - 12 - 18, y: OY + COMPOSER_Y + 30};
const A1 = {x: ax(CARD_GEOM.pad + 120), y: ay(Y.c1 + CARD_GEOM.button(3, false), SC.c1)};
const A2 = {x: ax(CARD_GEOM.pad + 120), y: ay(Y.c2 + CARD_GEOM.button(2, false), SC.c2)};
const OTP = {x: ax(CARD_GEOM.pad + 150), y: ay(Y.c3 + CARD_GEOM.otp(2), SC.c3)};
const VER = {x: ax(CARD_GEOM.pad + 120), y: ay(Y.c3 + CARD_GEOM.button(2, true), SC.c3)};
const CANCEL = {x: ax(COL.w - 26 - 60), y: ay(Y.c4 + PAYEE_GEOM.button, SC.vop)};
const TAB = {x: OX + COL.x + TAB_X[1] + 34, y: OY + 105};
const PANEL = {lede: 0, copy: 94, table: 134, buttons: 328, result: 386};
const COLS = [34, 590, 130, 92, 94];
const ROW_H = 46;
const CHECK = {x: OX + COL.x + 202 + 135, y: OY + VIEW.y + PANEL.buttons + 21};

const PLACEHOLDER = 'Ask BorderlessGuard… or correct: "actually make it 40", "cancel that" (max 2,000 characters)';

/** Render `text` with one phrase marked (plain text if the phrase is not there). */
function marked(text: string, phrase: string, t: number, color: string, bg = false): React.ReactNode {
  const i = text.indexOf(phrase);
  if (i < 0) return text;
  return (<>{text.slice(0, i)}<Mark2 t={t} color={color} bg={bg}>{phrase}</Mark2>{text.slice(i + phrase.length)}</>);
}

export const Product: React.FC<{f: number; starts: number[]; durs: number[]}> = ({f, starts, durs}) => {
  const [S4, S5, S6, S7, S8] = starts;
  const END = S8 + durs[4];
  const press = (c: number) => (f >= c - 3 && f < c + 4 ? 1 : 0);

  // ---- state over time
  const fx = p(f, S6 + 40, S6 + 64);
  const tu = p(f, S6 + 100, S6 + 124);
  const re = p(f, S6 + 224, S6 + 248);
  const eur = 450 + 2200 * fx - 1200 * tu - 850 * re;
  const usd = 2600 - 2403.26 * fx;
  const done = [p(f, S6 + 38, S6 + 50), p(f, S6 + 98, S6 + 110), p(f, S6 + 222, S6 + 234)];
  const otpN = f < S6 + 158 ? 0 : Math.min(6, Math.floor((f - (S6 + 158)) / 6) + 1);

  let scroll = lerp(0, SC.c1, p(f, S6, S6 + 24));
  scroll = lerp(scroll, SC.c2, p(f, S6 + 60, S6 + 84));
  scroll = lerp(scroll, SC.c3, p(f, S6 + 116, S6 + 140));
  scroll = lerp(scroll, SC.vop, p(f, S7 + 70, S7 + 94));

  const sent1 = S4 + 142;
  const sent2 = S7 + 66;
  let text = '';
  if (f >= S4 + 30 && f < sent1) text = typed(D.user_sentence, f, S4 + 30, 30, 27);
  if (f >= S7 + 6 && f < sent2) text = typed(D.vop.message, f, S7 + 6, 30, 40);
  const typing = (f >= S4 + 22 && f < sent1) || (f >= S7 + 2 && f < sent2);
  const full = f < S7 ? D.user_sentence.length : D.vop.message.length;
  const caret = typing && (text.length < full || Math.floor(f / 15) % 2 === 0);
  const chip = f < sent1 ? p(f, S4 + 12, S4 + 24) : 0;

  const tabU = p(f, S8 + 24, S8 + 38);
  const chatOp = 1 - p(f, S8 + 24, S8 + 34);
  const panelOp = p(f, S8 + 30, S8 + 44);
  const link = [p(f, S8 + 86, S8 + 106), p(f, S8 + 106, S8 + 126)];
  const result = p(f, S8 + 160, S8 + 174);

  const winIn = p(f, 0, 24);
  const out = 1 - p(f, END - 14, END);

  const W = {s: 1.1, x: 960, y: 540};
  const cam: Key[] = [
    {f: 0, s: 1.0, x: 960, y: 530},
    {f: S4 + 40, s: 1.0, x: 960, y: 530},
    {f: S4 + 80, s: 1.5, x: 1110, y: 911},
    {f: S4 + 146, s: 1.5, x: 1110, y: 911},
    {f: S4 + 186, ...W},
    {f: S5 + 16, ...W},
    {f: S5 + 50, s: 1.32, x: 1187, y: 480},
    {f: S5 + 170, s: 1.32, x: 1187, y: 480},
    {f: S5 + 205, ...W},
    {f: S6 + 236, ...W},
    {f: S6 + 268, s: 1.32, x: 560, y: 420},
    {f: S7, s: 1.32, x: 560, y: 420},
    {f: S7 + 24, ...W},
    {f: S7 + 140, ...W},
    {f: S7 + 170, s: 1.4, x: 1146, y: 700},
    {f: S7 + 212, s: 1.4, x: 1146, y: 700},
    {f: S7 + 240, ...W},
    {f: S8 + 140, ...W},
    {f: S8 + 176, s: 1.22, x: 1100, y: 520},
    {f: END, s: 1.24, x: 1100, y: 520},
  ];

  const park1 = {x: SEND.x - 60, y: SEND.y + 50};
  const park2 = {x: VER.x + 140, y: VER.y + 150};
  const park3 = {x: SEND.x - 90, y: SEND.y + 40};
  const park4 = {x: CANCEL.x + 40, y: CANCEL.y + 70};
  const path: PKey[] = [
    {f: S4 + 100, x: 1420, y: 1070},
    {f: S4 + 134, ...SEND},
    {f: S4 + 170, ...park1},
    {f: S6 + 6, ...park1},
    {f: S6 + 30, ...A1},
    {f: S6 + 66, ...A1},
    {f: S6 + 90, ...A2},
    {f: S6 + 120, ...A2},
    {f: S6 + 146, ...OTP},
    {f: S6 + 196, ...OTP},
    {f: S6 + 214, ...VER},
    {f: S6 + 246, ...park2},
    {f: S7 + 30, ...park2},
    {f: S7 + 60, ...SEND},
    {f: S7 + 96, ...park3},
    {f: S7 + 176, ...park3},
    {f: S7 + 204, ...CANCEL},
    {f: S7 + 236, ...park4},
    {f: S7 + 250, ...park4},
    {f: S8 + 18, ...TAB},
    {f: S8 + 124, ...TAB},
    {f: S8 + 150, ...CHECK},
    {f: END, x: CHECK.x + 50, y: CHECK.y + 80},
  ];
  const clicks = [sent1, S6 + 36, S6 + 96, S6 + 150, S6 + 220, sent2, S7 + 210, S8 + 24, S8 + 156];

  const para: React.CSSProperties = {position: 'absolute', left: 0, right: 0, fontSize: 18, lineHeight: '28px', color: C.ink};
  const r0 = D.reply[0];
  const cut = 'I ignored it.';
  const r0a = r0.slice(0, r0.indexOf(cut));
  const r0b = r0.slice(r0.indexOf(cut) + cut.length).trim();

  return (
    <AbsoluteFill style={{opacity: out, transform: `scale(${1 - 0.03 * (1 - out)})`}}>
      <Camera frame={f} keys={cam}>
        <Window x={WIN.x} y={WIN.y} w={WIN.w} h={WIN.h} style={{opacity: winIn, transform: `translateY(${(1 - winIn) * 36}px)`}}>
          <Sidebar eur={eur} usd={usd} hl={p(f, S6 + 250, S6 + 266) * (1 - p(f, S7 + 10, S7 + 30))} />
          <Header u={tabU} />

          {/* ---------------- Assistant tab ---------------- */}
          <div style={{position: 'absolute', left: COL.x, top: VIEW.y, width: COL.w, height: VIEW.h, overflow: 'hidden', opacity: chatOp}}>
            <div style={{position: 'absolute', left: 0, top: 0, width: COL.w, transform: `translateY(${-scroll}px)`}}>
              <UserBubble text={D.user_sentence} style={{position: 'absolute', right: 0, left: 0, top: Y.b1, ...rise(f, sent1 + 2, 16, 12)}} />
              <div style={{position: 'absolute', left: 0, top: Y.doc, opacity: p(f, S4 + 170, S4 + 178) * (1 - p(f, S5, S5 + 4))}}><Dots f={f} /></div>
              <DocLine name={D.invoice.filename} style={{position: 'absolute', left: 0, top: Y.doc, ...rise(f, S5 + 2, 14, 6)}} />
              <div style={{...para, top: Y.r0, ...rise(f, S5 + 6, 16, 8)}}>
                {r0a}<Mark2 t={p(f, S5 + 40, S5 + 58)} color={C.danger}><b style={{fontWeight: 600}}>{cut}</b></Mark2>{' '}
                <Mark2 t={p(f, S5 + 95, S5 + 125)} color={C.accent}>{r0b}</Mark2>
              </div>
              <Notice tone="danger" lead="Untrusted document, quarantined:" style={{position: 'absolute', left: 0, right: 0, top: Y.notice, ...rise(f, S5 + 30, 18, 10)}}>
                {D.invoice.filename}<br />
                It contains hidden instructions addressed to the AI. They were ignored: the document is read as data only, and any payment uses the account saved for the payee.
              </Notice>
              <div style={{...para, top: Y.r1, ...rise(f, S5 + 200, 16, 8)}}>{D.reply[1]}</div>
              <div style={{...para, top: Y.r2, ...rise(f, S5 + 250, 16, 8)}}>{D.reply[2]}</div>
              <div style={{...para, top: Y.r3, ...rise(f, S5 + 280, 16, 8)}}>{D.reply[3]}</div>
              <div style={{...para, top: Y.r4, ...rise(f, S5 + 330, 16, 8)}}>
                {marked(D.reply[4], '600.00 EUR left, exactly your reserve', p(f, S5 + 345, S5 + 372), C.accentTint2, true)}
              </div>
              <div style={{...para, top: Y.r5, ...rise(f, S5 + 352, 16, 8)}}>{D.reply[5]}</div>

              {ACTIONS.map((a, i) => (
                <ActionCard key={a.title} a={a} done={done[i]} receipt={i + 1} otp={i === 2 ? D.otp.slice(0, otpN) : ''} last={i === 2}
                  press={press([S6 + 36, S6 + 96, S6 + 220][i])}
                  style={{position: 'absolute', left: 0, right: 0, top: [Y.c1, Y.c2, Y.c3][i], ...rise(f, S6 + i * 6, 18, 14)}} />
              ))}

              <UserBubble text={D.vop.message} style={{position: 'absolute', right: 0, left: 0, top: Y.b2, ...rise(f, sent2 + 2, 16, 12)}} />
              <div style={{...para, top: Y.vop, fontSize: 17, lineHeight: '27px', ...rise(f, S7 + 86, 16, 8)}}>
                {marked(D.vop.reply, `the name "Jean Dupont Orsay" does not match the name the bank has for this account.`, p(f, S7 + 120, S7 + 140), C.danger)}
              </div>
              <PayeeCard name="Jean Dupont Orsay" ending="1332" detail={D.vop.card_line.split(' - ')[1] ?? ''} hl={p(f, S7 + 160, S7 + 176)}
                cancelled={p(f, S7 + 214, S7 + 226)} press={press(S7 + 210)}
                style={{position: 'absolute', left: 0, right: 0, top: Y.c4, ...rise(f, S7 + 98, 18, 14)}} />
            </div>
          </div>
          <div style={{position: 'absolute', left: COL.x, top: COMPOSER_Y, width: COL.w, opacity: chatOp}}>
            <Composer text={text} placeholder={PLACEHOLDER} caret={caret} chip={chip} press={press(sent1 - 2) || press(sent2 - 2)} />
          </div>

          {/* ---------------- Receipts tab ---------------- */}
          <div style={{position: 'absolute', left: COL.x, top: VIEW.y, width: COL.w, opacity: panelOp, fontFamily: INTER}}>
            <div style={{position: 'absolute', top: PANEL.lede, fontSize: 17, lineHeight: '26px', color: C.ink}}>
              Every action, done <b>or</b> refused, gets a receipt. Each receipt records what you asked, the exact payment, the simulation,
              the rules' decision and your approval, and is linked to the previous one, so history cannot be edited quietly.
            </div>
            <div style={{position: 'absolute', top: PANEL.copy, display: 'flex', alignItems: 'center', gap: 10, fontSize: 15, color: C.ink2, height: 24}}>
              Your copy of the latest receipt: <Hash v={D.proof.anchor} hl={result} />
              <Pill tone="success" icon="check" size={13}>Signed copy</Pill>
            </div>
            <div style={{position: 'absolute', top: PANEL.table, left: 0, width: COL.w}}>
              <div style={{display: 'flex', height: 36, alignItems: 'center', borderBottom: `1px solid ${C.sepStrong}`, borderTop: `1px solid ${C.sep}`,
                fontSize: 11, fontWeight: 500, letterSpacing: '0.05em', color: C.ink2, whiteSpace: 'nowrap'}}>
                {['#', 'WHAT HAPPENED', 'APPROVED BY YOU', 'FINGERPRINT', 'LINKS TO'].map((h, i) => <div key={h} style={{width: COLS[i], paddingLeft: i ? 10 : 0, boxSizing: 'border-box'}}>{h}</div>)}
              </div>
              {D.receipts.map((r, i) => (
                <div key={r.seq} style={{display: 'flex', height: ROW_H, alignItems: 'center', borderBottom: `1px solid ${C.sep}`, fontSize: 14, color: C.ink,
                  ...rise(f, S8 + 46 + i * 8, 16, 8)}}>
                  <div style={{width: COLS[0], boxSizing: 'border-box', color: C.ink2, ...TNUM}}>{r.seq}</div>
                  <div style={{width: COLS[1], paddingLeft: 10, boxSizing: 'border-box', whiteSpace: 'nowrap', overflow: 'hidden'}}>EXECUTED: {r.summary}</div>
                  <div style={{width: COLS[2], paddingLeft: 10, boxSizing: 'border-box'}}><Pill tone="success" icon="check" size={13}>Approved</Pill></div>
                  <div style={{width: COLS[3], paddingLeft: 6, boxSizing: 'border-box'}}><Hash v={r.hash} hl={i < 2 ? link[i] : 0} /></div>
                  <div style={{width: COLS[4], paddingLeft: 6, boxSizing: 'border-box'}}><Hash v={r.prev} hl={i > 0 ? link[i - 1] : 0} /></div>
                </div>
              ))}
              <svg width={COL.w} height={36 + 3 * ROW_H} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
                {[0, 1].map((i) => {
                  const y1 = 36 + i * ROW_H + ROW_H / 2;
                  const y2 = y1 + ROW_H;
                  const x1 = COLS[0] + COLS[1] + COLS[2] + 6 + 76;
                  const x2 = COLS[0] + COLS[1] + COLS[2] + COLS[3] + 4;
                  return (
                    <path key={i} d={`M ${x2} ${y2} C ${x2 - 16} ${y2}, ${x1 + 16} ${y1}, ${x1} ${y1}`} fill="none" stroke={C.accent} strokeWidth={1.8}
                      strokeLinecap="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - link[i]} opacity={link[i] > 0 ? 1 : 0} />
                  );
                })}
              </svg>
            </div>
            <div style={{position: 'absolute', top: PANEL.buttons, display: 'flex', gap: 12}}>
              <div style={{boxSizing: 'border-box', height: 42, width: 190, borderRadius: 10, background: C.white, boxShadow: `0 0 0 1px ${C.sepStrong}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, fontWeight: 500, color: C.ink}}>Check the chain</div>
              <div style={{height: 42, width: 270, borderRadius: 10, background: C.accent, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, fontWeight: 600, color: '#fff',
                transform: `scale(${1 - 0.04 * press(S8 + 156)})`}}>Check against my copy</div>
            </div>
            <Notice tone="success" style={{position: 'absolute', top: PANEL.result, left: 0, width: COL.w, opacity: result, transform: `translateY(${(1 - result) * 8}px)`}}>
              {D.proof.check} {D.proof.detail}
            </Notice>
          </div>
        </Window>
        <Pointer frame={f} path={path} clicks={clicks} />
      </Camera>
    </AbsoluteFill>
  );
};
