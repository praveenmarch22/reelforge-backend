import React from 'react';
import {Composition} from 'remotion';
import {Reel} from './Reel';
import {sampleTimeline} from './sample';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Reel"
    component={Reel}
    defaultProps={sampleTimeline}
    durationInFrames={sampleTimeline.durationInFrames}
    fps={sampleTimeline.fps}
    width={sampleTimeline.width}
    height={sampleTimeline.height}
    // The real length and size come from the timeline passed as props.
    calculateMetadata={({props}) => ({
      durationInFrames: Math.max(1, props.durationInFrames),
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
);
