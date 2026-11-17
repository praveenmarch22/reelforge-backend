// The timeline JSON written by the Python pipeline (see docs/PIPELINE_STRATEGY.md).

export type Word = {text: string; start: number; end: number};

export type Motion = 'zoom-in' | 'zoom-out' | 'pan-left' | 'pan-right';

export type TimelineScene = {
  id: string;
  startFrame: number;
  durationInFrames: number;
  mood: string;
  onScreenText: string;
  visual: {
    kind: 'image' | 'video';
    src: string;
    durationSeconds: number | null;
    motion: Motion;
  };
  voice: {src: string; durationSeconds: number};
  words: Word[]; // seconds from the start of this scene
};

export type CaptionStyle = 'bold-pop' | 'clean' | 'neon';

export type Timeline = {
  version: number;
  projectId: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  captionStyle: CaptionStyle;
  showCaptions: boolean;
  voiceVolume: number;
  music: null | {
    src: string;
    volume: number;
    duckedVolume: number;
    fadeInSeconds: number;
    fadeOutSeconds: number;
  };
  scenes: TimelineScene[];
};
