import type {Timeline} from './types';

// Shown in Remotion Studio when no --props file is given. Uses no media files.
const words = (text: string, start = 0.2, perWord = 0.4) =>
  text.split(' ').map((t, i) => ({text: t, start: start + i * perWord, end: start + i * perWord + perWord * 0.9}));

export const sampleTimeline: Timeline = {
  version: 1,
  projectId: 'sample',
  fps: 30,
  width: 1080,
  height: 1920,
  durationInFrames: 240,
  captionStyle: 'bold-pop',
  showCaptions: true,
  voiceVolume: 1,
  music: null,
  scenes: [
    {
      id: 's1', startFrame: 0, durationInFrames: 120, mood: 'excited',
      onScreenText: 'ReelForge preview',
      visual: {kind: 'image', src: '', durationSeconds: null, motion: 'zoom-in'},
      voice: {src: '', durationSeconds: 3.6},
      words: words('This is a sample scene with animated captions'),
    },
    {
      id: 's2', startFrame: 120, durationInFrames: 120, mood: 'calm',
      onScreenText: 'Run the pipeline',
      visual: {kind: 'image', src: '', durationSeconds: null, motion: 'pan-left'},
      voice: {src: '', durationSeconds: 3.6},
      words: words('Build a project to see real voice and visuals'),
    },
  ],
};
