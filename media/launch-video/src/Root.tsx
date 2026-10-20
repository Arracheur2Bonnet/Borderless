import React from 'react';
import {Composition} from 'remotion';
import {LaunchVideo} from './Video';
import {totalFrames} from './timeline';
import {DemoVideo, DEMO_TOTAL} from './demo/DemoVideo';

// Two renders of the same film: 30 fps (default, smaller file) and 60 fps (smoother pointer and camera).
export const Root: React.FC = () => (
  <>
    <Composition id="LaunchVideo" component={LaunchVideo} durationInFrames={totalFrames(30)} fps={30} width={1920} height={1080} />
    <Composition id="DemoVideo" component={DemoVideo} durationInFrames={DEMO_TOTAL} fps={30} width={1920} height={1080} />
    <Composition id="LaunchVideo60" component={LaunchVideo} durationInFrames={totalFrames(60)} fps={60} width={1920} height={1080} />
  </>
);
