import React from 'react';
import {C, INTER, MONO, TNUM} from './theme';
import {fmt, p} from './anim';

// The app, rebuilt for motion. It mirrors the light redesign of ui/ (components.css, theme.py, app.py): quiet status
// marks (icon + word, no fill), inset user bubble, plain agent text, ruled fact lists, one primary button per decision.
// Heights are fixed on purpose: the pointer and the camera aim at known coordinates (see the *_GEOM exports).

type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';
const FG: Record<Tone, string> = {success: C.successText, warning: C.warningText, danger: C.dangerText, info: C.accent, neutral: C.ink2};

type IconName = 'check' | 'key' | 'clock' | 'warn' | 'alert' | 'block' | 'clip' | 'arrow' | 'x' | 'chev' | 'flask';
export const Icon: React.FC<{name: IconName; color: string; size?: number}> = ({name, color, size = 16}) => {
  const s = {fill: 'none', stroke: color, strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const};
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{flex: 'none'}}>
      {name === 'check' && <path d="M5 12.5l4.2 4.2L19 7" {...s} />}
      {name === 'key' && (<><circle cx="8" cy="15" r="4" {...s} /><path d="M11 12l7-7M15 6l2 2" {...s} /></>)}
      {name === 'clock' && (<><circle cx="12" cy="12" r="8.5" {...s} /><path d="M12 7.5V12l3 2" {...s} /></>)}
      {name === 'warn' && (<><path d="M12 4l9 16H3L12 4z" {...s} /><path d="M12 10v4M12 17v.5" {...s} /></>)}
      {name === 'alert' && (<><circle cx="12" cy="12" r="8.5" {...s} /><path d="M12 7.5v5M12 16v.5" {...s} /></>)}
      {name === 'block' && (<><circle cx="12" cy="12" r="8.5" {...s} /><path d="M6 18L18 6" {...s} /></>)}
      {name === 'clip' && <path d="M20 11.5l-7.8 7.8a5 5 0 01-7.1-7.1l8.1-8.1a3.3 3.3 0 014.7 4.7l-8.1 8.1a1.7 1.7 0 01-2.4-2.4l7.4-7.4" {...s} />}
      {name === 'arrow' && <path d="M12 19V5M6 11l6-6 6 6" {...s} strokeWidth={2.2} />}
      {name === 'x' && <path d="M6 6l12 12M18 6L6 18" {...s} />}
      {name === 'chev' && <path d="M9 6l6 6-6 6" {...s} />}
      {name === 'flask' && <path d="M9 3h6M10 3v6L5 19a1.5 1.5 0 001.3 2h11.4A1.5 1.5 0 0019 19l-5-10V3" {...s} />}
    </svg>
  );
};

/** Status mark, as ui/theme.py pill(): icon + word in the meaning colour, no filled shape. */
export const Pill: React.FC<{tone: Tone; icon?: IconName; children: React.ReactNode; size?: number; style?: React.CSSProperties}> = ({tone, icon, children, size = 15, style}) => (
  <span style={{display: 'inline-flex', alignItems: 'center', gap: 5, color: FG[tone], fontSize: size, fontWeight: 600, whiteSpace: 'nowrap', lineHeight: 1, ...style}}>
    {icon && <Icon name={icon} color={FG[tone]} size={size} />}
    {children}
  </span>
);

/** design/brand/mark.svg (or the small variant), with optional draw-on progress values 0..1. */
export const Mark: React.FC<{size: number; small?: boolean; dots?: number; route?: number; box?: number; check?: number}> = ({size, small, dots = 1, route = 1, box = 1, check = 1}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" style={{flex: 'none', overflow: 'visible'}}>
    {!small && Array.from({length: 8}).map((_, i) => (
      <circle key={i} cx="32" cy={4 + i * 8} r="1.6" fill={C.ink} opacity={i === 3 || i === 4 ? 0 : p(dots * 8, i, i + 1)} />
    ))}
    <path d={small ? 'M5 52 C 20 52, 22 32, 32 32 C 42 32, 44 12, 59 12' : 'M6 50 C 20 50, 22 32, 32 32 C 42 32, 44 14, 58 14'}
      fill="none" stroke={C.accent} strokeWidth={small ? 8 : 5.5} strokeLinecap="round" pathLength={1}
      strokeDasharray={1} strokeDashoffset={1 - route} opacity={route > 0 ? 1 : 0} />
    <g style={{transform: `scale(${box})`, transformOrigin: '32px 32px'}}>
      {small ? <rect x="19" y="19" width="26" height="26" rx="7" fill={C.ink} /> : <rect x="23" y="23" width="18" height="18" rx="5" fill={C.ink} />}
      <path d={small ? 'M25 32.5 L30 37.2 L39.5 27' : 'M27.5 32.2 L30.6 35.2 L36.6 28.8'} fill="none" stroke="#fff"
        strokeWidth={small ? 4.2 : 2.6} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={1}
        strokeDashoffset={1 - check} opacity={check > 0 ? 1 : 0} />
    </g>
  </svg>
);

export const Wordmark: React.FC<{size: number; color?: string}> = ({size, color = C.ink}) => (
  <span style={{fontFamily: INTER, fontSize: size, fontWeight: 600, letterSpacing: '-0.025em', color, lineHeight: 1}}>BorderlessGuard</span>
);

/** The app window: neutral chrome, hairline ring, one soft shadow (it floats). No title text: captions sit above it. */
export const Window: React.FC<{x: number; y: number; w: number; h: number; children: React.ReactNode; style?: React.CSSProperties}> = ({x, y, w, h, children, style}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, height: h, background: C.paper, borderRadius: 16, overflow: 'hidden',
    boxShadow: '0 0 0 1px rgba(28,27,24,0.10), 0 30px 80px rgba(28,27,24,0.10)', fontFamily: INTER, ...style}}>
    <div style={{height: 44, background: C.white, borderBottom: `1px solid ${C.sep}`, display: 'flex', alignItems: 'center', padding: '0 18px', gap: 8}}>
      {[0, 1, 2].map((i) => <div key={i} style={{width: 11, height: 11, borderRadius: 6, background: '#dedcd6'}} />)}
    </div>
    <div style={{position: 'absolute', top: 44, left: 0, right: 0, bottom: 0}}>{children}</div>
  </div>
);

const SideLabel: React.FC<{children: React.ReactNode; top: number}> = ({children, top}) => (
  <div style={{position: 'absolute', left: 30, top, fontSize: 12, fontWeight: 500, letterSpacing: '0.08em', color: C.ink2}}>{children}</div>
);

/** Sidebar, as ui/app.py render_sidebar: account, balances, reserve, saved payees and bills due. */
export const SIDEBAR_W = 300;
export const Sidebar: React.FC<{eur: number; usd: number; hl?: number}> = ({eur, usd, hl = 0}) => {
  const ok = eur >= 600 - 1e-9;
  const row = (top: number, k: string, v: React.ReactNode, big: boolean, bg = 'transparent') => (
    <div style={{position: 'absolute', left: 22, right: 22, top, height: big ? 48 : 42, padding: '0 8px', background: bg, borderRadius: 6,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: `1px solid ${C.sep}`}}>
      <span style={{fontSize: 14, color: C.ink2}}>{k}</span>
      <span style={{fontSize: big ? 21 : 14, fontWeight: big ? 500 : 600, color: C.ink, ...TNUM}}>{v}</span>
    </div>
  );
  const payees: [string, string, boolean][] = [['Université Paris-Saclay', '0129', false], ['Jean Dupont', '0143', true], ['Samira Haddad', '4921', false]];
  return (
    <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: SIDEBAR_W, background: C.sidebar, borderRight: `1px solid ${C.sep}`}}>
      <SideLabel top={30}>ACCOUNT</SideLabel>
      <div style={{position: 'absolute', left: 30, top: 50, fontSize: 18, fontWeight: 500, color: C.ink}}>Lina</div>
      <div style={{position: 'absolute', left: 30, top: 75, fontSize: 13, color: C.ink2}}>International student in France</div>
      <SideLabel top={114}>BALANCES</SideLabel>
      {row(134, 'EUR balance', fmt(eur), true, `rgba(39, 65, 212, ${0.09 * hl})`)}
      {row(182, 'USD balance', fmt(usd), true)}
      {row(230, 'Reserve to keep', '600.00 EUR', false)}
      {row(272, 'Reserve status', ok ? <Pill tone="success" icon="check" size={13}>kept</Pill> : <Pill tone="danger" icon="warn" size={13}>below</Pill>, false)}
      <div style={{position: 'absolute', left: 22, right: 22, top: 314, height: 44, borderTop: `1px solid ${C.sep}`, display: 'flex', alignItems: 'center', gap: 8,
        fontSize: 14, fontWeight: 500, color: C.ink2, paddingLeft: 8}}>
        <span style={{transform: 'rotate(90deg)', display: 'inline-flex'}}><Icon name="chev" color={C.ink2} size={13} /></span>Saved payees and bills due
      </div>
      {payees.map(([n, e, isNew], i) => (
        <div key={n} style={{position: 'absolute', left: 30, right: 22, top: 366 + i * 54, borderTop: i ? `1px solid ${C.sep}` : undefined, paddingTop: i ? 8 : 0}}>
          <div style={{fontSize: 14, fontWeight: 600, color: C.ink}}>{n}</div>
          <div style={{display: 'flex', alignItems: 'center', gap: 8, marginTop: 3, fontSize: 13, color: C.ink2}}>
            <span>ending <span style={{fontFamily: MONO}}>{e}</span></span>
            {isNew && <Pill tone="warning" icon="clock" size={12}>new · added 20 h ago</Pill>}
          </div>
        </div>
      ))}
      {[['Tuition', '1,200.00 EUR', 'due 05 Nov 2026'], ['Rent', '850.00 EUR', 'due 01 Nov 2026']].map(([n, a, d], i) => (
        <div key={n} style={{position: 'absolute', left: 30, right: 30, top: 534 + i * 54, borderTop: `1px solid ${C.sep}`, paddingTop: 8}}>
          <div style={{display: 'flex', justifyContent: 'space-between', fontSize: 14, fontWeight: 600, color: C.ink, ...TNUM}}><span>{n}</span><span>{a}</span></div>
          <div style={{fontSize: 13, color: C.ink2, marginTop: 3}}>{d}</div>
        </div>
      ))}
    </div>
  );
};

/** Main column geometry (window content coordinates). */
export const COL = {x: 480, w: 940};
export const TAB_X = [0, 98, 183];
const TAB_W = [76, 68, 86];

/** Header (mark + wordmark + sandbox tag) and the three tabs, in the main column. */
export const Header: React.FC<{u: number}> = ({u}) => {
  const labels = ['Assistant', 'Receipts', 'Attack test'];
  const ux = TAB_X[0] + (TAB_X[1] - TAB_X[0]) * u;
  const uw = TAB_W[0] + (TAB_W[1] - TAB_W[0]) * u;
  return (
    <div style={{position: 'absolute', left: COL.x, top: 0, width: COL.w, height: 132}}>
      <div style={{position: 'absolute', left: 0, top: 26, display: 'flex', alignItems: 'center', gap: 10}}>
        <Mark size={28} small />
        <Wordmark size={22} />
      </div>
      <div style={{position: 'absolute', right: 0, top: 24, height: 30, padding: '0 13px 0 10px', borderRadius: 999, boxShadow: `0 0 0 1px ${C.sepStrong}`,
        display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600, color: C.ink, letterSpacing: '0.02em'}}>
        <Icon name="flask" color={C.ink} size={13} />SANDBOX · <span style={{fontWeight: 500}}>no real money</span>
      </div>
      {labels.map((l, i) => {
        const on = i === 0 ? 1 - u : i === 1 ? u : 0;
        return <span key={l} style={{position: 'absolute', left: TAB_X[i], top: 96, fontSize: 15, fontWeight: 500, color: on > 0.5 ? C.ink : C.ink2}}>{l}</span>;
      })}
      <div style={{position: 'absolute', left: 0, right: 0, top: 131, height: 1, background: C.sep}} />
      <div style={{position: 'absolute', left: ux, width: uw, top: 130, height: 2, background: C.ink}} />
    </div>
  );
};

export const UserBubble: React.FC<{text: string; style?: React.CSSProperties}> = ({text, style}) => (
  <div style={{display: 'flex', justifyContent: 'flex-end', ...style}}>
    <div style={{maxWidth: 600, background: C.inset, color: C.ink, borderRadius: 14, padding: '12px 18px', fontSize: 18, lineHeight: '27px',
      boxShadow: `0 0 0 1px ${C.sep}`}}>{text}</div>
  </div>
);

export const DocLine: React.FC<{name: string; style?: React.CSSProperties}> = ({name, style}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 500, color: C.ink2, height: 22, ...style}}>
    <Icon name="chev" color={C.ink2} size={13} />{name} (untrusted: shown as plain text)
  </div>
);

export const Dots: React.FC<{f: number}> = ({f}) => (
  <div style={{display: 'flex', gap: 6, height: 24, alignItems: 'center'}}>
    {[0, 1, 2].map((i) => <div key={i} style={{width: 7, height: 7, borderRadius: 4, background: C.ink3, opacity: 0.3 + 0.7 * Math.max(0, Math.sin((f / 30) * Math.PI * 2 - i * 0.9))}} />)}
  </div>
);

/** A marker behind a phrase (bg) or an underline (no bg) that grows left to right with t. */
export const Mark2: React.FC<{t: number; color: string; bg?: boolean; children: React.ReactNode}> = ({t, color, bg, children}) => (
  <span style={{backgroundImage: `linear-gradient(${color}, ${color})`, backgroundRepeat: 'no-repeat',
    backgroundSize: bg ? `${t * 100}% 100%` : `${t * 100}% 2px`, backgroundPosition: bg ? '0 0' : '0 100%',
    boxDecorationBreak: 'clone', WebkitBoxDecorationBreak: 'clone', paddingBottom: bg ? 0 : 3, borderRadius: bg ? 4 : 0}}>
    {children}
  </span>
);

/** Notice: meaning-colour left rule, tint, icon, bold lead-in (st.error / st.success / st.warning in the app). */
export const Notice: React.FC<{tone: 'danger' | 'warning' | 'success'; lead?: string; children: React.ReactNode; style?: React.CSSProperties}> = ({tone, lead, children, style}) => {
  const col = tone === 'danger' ? C.danger : tone === 'warning' ? C.warning : C.success;
  const fg = tone === 'danger' ? C.dangerText : tone === 'warning' ? C.warningText : C.successText;
  const bg = tone === 'danger' ? C.dangerTint : tone === 'warning' ? C.warningTint : C.successTint;
  return (
    <div style={{boxSizing: 'border-box', borderLeft: `3px solid ${col}`, background: bg, borderRadius: '0 10px 10px 0', padding: '12px 18px 13px 16px',
      display: 'flex', gap: 12, ...style}}>
      <div style={{paddingTop: 3}}><Icon name={tone === 'success' ? 'check' : tone === 'danger' ? 'alert' : 'warn'} color={fg} size={18} /></div>
      <div style={{fontSize: 16, lineHeight: '24px', color: C.ink}}>{lead && <b style={{fontWeight: 600}}>{lead} </b>}{children}</div>
    </div>
  );
};

export type ActionData = {kind: string; title: string; amount: string; ccy: string; verdict: string; pill: string; reason: string; facts: string[][]};

const Btn: React.FC<{primary?: boolean; w: number; children: React.ReactNode; press?: number; dim?: boolean}> = ({primary, w, children, press = 0, dim}) => (
  <div style={{boxSizing: 'border-box', height: 42, width: w, borderRadius: 10, background: primary ? C.accent : C.white, color: primary ? '#fff' : C.ink,
    boxShadow: primary ? 'none' : `0 0 0 1px ${C.sepStrong}`, fontSize: 16, fontWeight: primary ? 600 : 500, display: 'flex', alignItems: 'center',
    justifyContent: 'center', transform: `scale(${1 - 0.04 * press})`, flex: 'none', opacity: dim ? 0.55 : 1}}>{children}</div>
);

const FactRows: React.FC<{rows: string[][]; extra?: React.ReactNode}> = ({rows, extra}) => (
  <div style={{borderBottom: `1px solid ${C.sep}`}}>
    {rows.map(([k, v]) => (
      <div key={k} style={{display: 'flex', alignItems: 'center', height: 36, borderTop: `1px solid ${C.sep}`}}>
        <div style={{width: 168, fontSize: 14, color: C.ink2, flex: 'none'}}>{k}</div>
        <div style={{fontSize: 15, fontWeight: 600, color: C.ink, whiteSpace: 'nowrap', ...TNUM}}>{v}</div>
      </div>
    ))}
    {extra}
  </div>
);

/** Decision-card geometry, relative to the card's top-left corner. */
export const CARD_GEOM = {
  pad: 29,                                   // 3 px rule + 26 px padding
  height: (rows: number, code: boolean) => 251 + 37 * rows + (code ? 80 : 0),
  button: (rows: number, code: boolean) => 210 + 37 * rows + (code ? 80 : 0),   // centre y of the button row
  otp: (rows: number) => 235 + 37 * rows,                                         // centre y of the code input
};

/** The decision card (ui/app.py): a white surface while pending, a flat record once done. */
export const ActionCard: React.FC<{a: ActionData; done: number; receipt: number; otp?: string; press?: number; last?: boolean; style?: React.CSSProperties}> = ({a, done, receipt, otp = '', press = 0, last, style}) => {
  const code = a.verdict === 'step_up';
  const rows = a.facts.length;
  return (
    <div style={{boxSizing: 'border-box', height: CARD_GEOM.height(rows, code), borderRadius: 14, padding: '22px 26px 0 26px',
      background: `rgba(255,255,255,${1 - 0.7 * done})`, boxShadow: `0 0 0 1px ${C.sep}`,
      borderLeft: `3px solid ${code ? `rgba(180, 83, 9, ${1 - done})` : 'transparent'}`, ...style}}>
      <div style={{display: 'flex', gap: 16, alignItems: 'center', height: 22}}>
        <Pill tone={code ? 'warning' : 'success'} icon={code ? 'key' : 'check'} size={14}>{a.pill}</Pill>
        <div style={{position: 'relative', height: 22, width: 200}}>
          <div style={{position: 'absolute', top: 4, opacity: 1 - done}}><Pill tone="neutral" icon="clock" size={14}>Waiting for you</Pill></div>
          <div style={{position: 'absolute', top: 4, opacity: done}}><Pill tone="success" icon="check" size={14}>Done · receipt #{receipt}</Pill></div>
        </div>
      </div>
      <div style={{fontSize: 18, fontWeight: 600, color: C.ink, height: 24, lineHeight: '24px', marginTop: 10, whiteSpace: 'nowrap'}}>{a.title}</div>
      <div style={{display: 'flex', alignItems: 'baseline', gap: 8, height: 48, marginTop: 4}}>
        <span style={{fontSize: 42, fontWeight: 600, letterSpacing: '-0.02em', color: C.ink, lineHeight: '48px', ...TNUM}}>{a.amount}</span>
        <span style={{fontSize: 20, color: C.ink2, fontWeight: 500}}>{a.ccy}</span>
      </div>
      <div style={{display: 'flex', alignItems: 'center', gap: 8, fontSize: 16, color: C.ink, height: 22, marginTop: 6, whiteSpace: 'nowrap'}}>
        <Icon name={code ? 'key' : 'check'} color={code ? C.warningText : C.successText} size={17} />{a.reason}
      </div>
      <div style={{marginTop: 14}}><FactRows rows={a.facts} /></div>
      <div style={{position: 'relative', marginTop: 16, height: code ? 42 + 80 : 42}}>
        <div style={{position: 'absolute', inset: 0, opacity: 1 - done}}>
          {code && (
            <div style={{height: 80, display: 'flex', gap: 24}}>
              <div>
                <div style={{fontSize: 14, fontWeight: 500, color: C.ink, height: 18}}>SMS verification code</div>
                <div style={{boxSizing: 'border-box', marginTop: 6, width: 300, height: 44, borderRadius: 10, background: C.white,
                  boxShadow: `0 0 0 ${otp.length > 0 && otp.length < 6 ? 2 : 1}px ${otp.length > 0 && otp.length < 6 ? C.accent : C.sepStrong}`,
                  display: 'flex', alignItems: 'center', padding: '0 14px', fontSize: 22, letterSpacing: '0.2em', color: C.ink}}>{'•'.repeat(otp.length)}</div>
              </div>
              <div style={{fontSize: 14, color: C.ink2, lineHeight: '20px', paddingTop: 26}}>
                Simulated SMS: your code is <b style={{color: C.ink}}>246810</b><br />Attempts remaining: <b style={{color: C.ink}}>3 of 3</b>
              </div>
            </div>
          )}
          <div style={{display: 'flex', justifyContent: 'space-between', marginTop: code ? 0 : 0}}>
            <Btn primary w={240} press={press}>{code ? 'Verify and approve' : 'Approve'}</Btn>
            <Btn w={120}>{last ? 'Cancel' : 'Reject'}</Btn>
          </div>
        </div>
      </div>
    </div>
  );
};

/** Add-payee card with its Verification of Payee row (ui/theme.py _note_row). */
export const PAYEE_GEOM = {height: 391, button: 350, checkRow: {y: 197, h: 58}};
export const PayeeCard: React.FC<{name: string; ending: string; detail: string; hl: number; cancelled: number; press?: number; style?: React.CSSProperties}> = ({name, ending, detail, hl, cancelled, press = 0, style}) => (
  <div style={{boxSizing: 'border-box', height: PAYEE_GEOM.height, borderRadius: 14, padding: '22px 26px 0 26px',
    background: `rgba(255,255,255,${1 - 0.7 * cancelled})`, boxShadow: `0 0 0 1px ${C.sep}`, borderLeft: `3px solid rgba(180, 83, 9, ${1 - cancelled})`, ...style}}>
    <div style={{display: 'flex', gap: 16, alignItems: 'center', height: 22}}>
      <Pill tone="warning" icon="key" size={14}>Code needed · R3</Pill>
      <div style={{position: 'relative', height: 22, width: 200}}>
        <div style={{position: 'absolute', top: 4, opacity: 1 - cancelled}}><Pill tone="neutral" icon="clock" size={14}>Waiting for you</Pill></div>
        <div style={{position: 'absolute', top: 4, opacity: cancelled}}><Pill tone="neutral" icon="x" size={14}>Cancelled · nothing was sent</Pill></div>
      </div>
    </div>
    <div style={{fontSize: 18, fontWeight: 600, color: C.ink, height: 24, lineHeight: '24px', marginTop: 10}}>Add payee {name}</div>
    <div style={{display: 'flex', alignItems: 'center', gap: 8, fontSize: 16, color: C.ink, height: 22, marginTop: 8}}>
      <Icon name="key" color={C.warningText} size={17} />Higher risk: we ask for a one-time code.
    </div>
    <div style={{marginTop: 14}}>
      <FactRows rows={[['Name', name], ['Account', `ending ${ending}`]]} extra={
        <div style={{display: 'flex', alignItems: 'center', height: PAYEE_GEOM.checkRow.h, borderTop: `1px solid ${C.sep}`, margin: '0 -12px', padding: '0 12px',
          background: `rgba(196, 43, 28, ${0.07 * hl})`}}>
          <div style={{width: 168, fontSize: 14, color: C.ink2, flex: 'none'}}>Payee check</div>
          <Pill tone="danger" icon="warn" size={14} style={{marginRight: 10}}>No match</Pill>
          <span style={{fontSize: 15, lineHeight: '21px', color: C.ink, maxWidth: 520}}>{detail}</span>
        </div>
      } />
    </div>
    <Notice tone="warning" style={{marginTop: 14, padding: '9px 16px 10px 14px'}}>Adding a payee needs your one-time code.</Notice>
    <div style={{position: 'relative', marginTop: 16, height: 42, display: 'flex', justifyContent: 'space-between', opacity: 1 - cancelled}}>
      <Btn primary w={240} dim>Verify and approve</Btn>
      <Btn w={120} press={press}>Cancel</Btn>
    </div>
  </div>
);

export const Composer: React.FC<{text: string; placeholder: string; caret: boolean; chip?: number; press?: number}> = ({text, placeholder, caret, chip = 0, press = 0}) => (
  <div style={{boxSizing: 'border-box', width: '100%', height: 60, background: C.white, borderRadius: 12, boxShadow: `0 0 0 1px ${C.sepStrong}`,
    display: 'flex', alignItems: 'center', padding: '0 12px 0 20px', gap: 12}}>
    {chip > 0 && (
      <div style={{display: 'inline-flex', alignItems: 'center', gap: 6, height: 30, padding: '0 10px', borderRadius: 8, background: C.inset,
        fontSize: 14, color: C.ink2, opacity: chip, flex: 'none'}}>
        <Icon name="clip" color={C.ink2} size={14} />rent_invoice_nov_2026.pdf
      </div>
    )}
    <div style={{flex: 1, minWidth: 0, fontSize: 17, whiteSpace: 'nowrap', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'flex-end'}}>
      {!text && caret && <span style={{display: 'inline-block', width: 2, height: 22, background: C.accent, flex: 'none'}} />}
      <span style={{flex: 'none', color: text ? C.ink : C.ink3}}>{text || placeholder}</span>
      {text && caret && <span style={{display: 'inline-block', width: 2, height: 22, background: C.accent, marginLeft: 1, flex: 'none'}} />}
      <span style={{flex: 1}} />
    </div>
    <div style={{width: 36, height: 36, borderRadius: 18, background: text ? C.accent : C.inset, display: 'flex', alignItems: 'center', justifyContent: 'center',
      transform: `scale(${1 - 0.1 * press})`, flex: 'none'}}>
      <Icon name="arrow" color={text ? '#fff' : C.ink2} size={18} />
    </div>
  </div>
);

export const Hash: React.FC<{v: string; hl?: number}> = ({v, hl = 0}) => (
  <span style={{fontFamily: MONO, fontSize: 14, color: C.ink, padding: '3px 5px', borderRadius: 6, background: `rgba(39, 65, 212, ${0.12 * hl})`}}>{v}</span>
);
