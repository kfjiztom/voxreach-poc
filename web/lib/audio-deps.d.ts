/* Minimal type stubs for the dynamically-imported audio packages.
 * Real types ship with the packages — these stubs let local typecheck
 * pass before `npm install` populates node_modules.
 *
 * If the upstream packages add proper types we should remove this file.
 */

declare module "opus-recorder" {
  interface RecorderOptions {
    encoderPath: string;
    streamPages?: boolean;
    encoderApplication?: number;
    encoderFrameSize?: number;
    encoderSampleRate?: number;
    numberOfChannels?: number;
    maxFramesPerPage?: number;
    resampleQuality?: number;
    mediaStream?: MediaStream;
    mediaTrackConstraints?: boolean | MediaTrackConstraints;
    sourceNode?: unknown;
  }

  export default class Recorder {
    constructor(options: RecorderOptions);
    start: () => Promise<void>;
    stop: () => Promise<void>;
    ondataavailable: (chunk: Uint8Array) => void;
    onstop?: () => void;
    onerror?: (err: Error) => void;
  }
}

declare module "@wasm-audio-decoders/ogg-opus" {
  export class OggOpusDecoderWebWorker {
    constructor();
    ready: Promise<void>;
    decode: (oggBytes: Uint8Array) => Promise<{
      channelData: Float32Array[];
      samplesDecoded: number;
      sampleRate: number;
    }>;
    free: () => void;
  }
}
