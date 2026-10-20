import type React from 'react';
import {Easing} from 'remotion';
import {loadFont} from '@remotion/google-fonts/Inter';

// Values mirror design/tokens.css (light only). Inter (OFL) is embedded so the render is identical everywhere.
export const {fontFamily: INTER} = loadFont('normal', {weights: ['400', '500', '600', '700'], subsets: ['latin', 'latin-ext']});
export const MONO = '"Cascadia Mono", "SF Mono", Consolas, ui-monospace, monospace';

export const C = {
  paper: '#f6f5f1',
  white: '#ffffff',
  inset: '#f0eee9',
  sidebar: '#efede7',
  ink: '#16171a',
  ink2: '#55565c',
  ink3: '#646569',
  faint: '#c9c7c0',
  sep: 'rgba(28, 27, 24, 0.10)',
  sepStrong: 'rgba(28, 27, 24, 0.20)',
  accent: '#2741d4',
  accentTint: 'rgba(39, 65, 212, 0.08)',
  accentTint2: 'rgba(39, 65, 212, 0.16)',
  success: '#1e7b4c',
  successText: '#17633d',
  successTint: 'rgba(30, 123, 76, 0.10)',
  warning: '#b45309',
  warningText: '#8a4106',
  warningTint: 'rgba(217, 119, 6, 0.12)',
  danger: '#c42b1c',
  dangerText: '#a32317',
  dangerTint: 'rgba(196, 43, 28, 0.08)',
};

export const EASE = Easing.bezier(0.22, 1, 0.36, 1);
export const TNUM: React.CSSProperties = {fontVariantNumeric: 'tabular-nums', fontFeatureSettings: '"tnum" 1'};
