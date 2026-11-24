"use client";

import { useEffect, useRef } from "react";

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const BAR_WIDTH = 3;
const BAR_GAP = 3;
const MIN_BAR = 3;
/** Cap the bar count so wide layouts stay a calm, centred pill instead of a busy full-width strip. */
const MAX_BARS = 56;
/** Speech energy sits in the lower part of the spectrum; ignore the near-silent top bins. */
const SPEECH_BAND = 0.45;
const SILENCE = 0.04;

/** Average of the frequency bins that feed bar `i` of `bars`, normalised to 0..1 and lifted for perception. */
function bandLevel(buf: Uint8Array<ArrayBuffer>, i: number, bars: number): number {
  const usable = Math.max(bars, Math.floor(buf.length * SPEECH_BAND));
  const start = Math.floor((i / bars) * usable);
  const end = Math.max(start + 1, Math.floor(((i + 1) / bars) * usable));
  let sum = 0;
  for (let k = start; k < end; k++) sum += buf[k];
  return Math.sqrt(sum / (end - start) / 255);
}

/**
 * Compact voice visualiser: a mirrored row of rounded bars. Bars take the colour of whoever is
 * speaking (caller = --wave-mic, Nova = --wave-agent) and rest as a quiet dotted line in silence.
 * Levels are smoothed (fast attack, slow release) and tapered toward the edges for a calm shape.
 */
export function Waveform({
  mic,
  agent,
  height = 44,
  label = "Live audio level",
}: {
  mic: AnalyserNode | null;
  agent: AnalyserNode | null;
  height?: number;
  label?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const micColor = cssVar("--wave-mic", "#2f7fe0");
    const agentColor = cssVar("--wave-agent", "#f59e4b");
    const restColor = cssVar("--border-strong", "#b5cfec");
    const micBuf = mic ? new Uint8Array(mic.frequencyBinCount) : null;
    const agentBuf = agent ? new Uint8Array(agent.frequencyBinCount) : null;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let levels: number[] = [];
    let raf = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const { width } = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const draw = () => {
      const w = canvas.getBoundingClientRect().width;
      const bars = Math.min(MAX_BARS, Math.max(8, Math.floor((w + BAR_GAP) / (BAR_WIDTH + BAR_GAP))));
      if (levels.length !== bars) levels = new Array<number>(bars).fill(0);
      if (micBuf && mic) mic.getByteFrequencyData(micBuf);
      if (agentBuf && agent) agent.getByteFrequencyData(agentBuf);

      let micEnergy = 0;
      let agentEnergy = 0;
      const offset = (w - (bars * (BAR_WIDTH + BAR_GAP) - BAR_GAP)) / 2;
      ctx.clearRect(0, 0, w, height);

      const targets: number[] = [];
      for (let i = 0; i < bars; i++) {
        // Mirror around the centre so low frequencies sit in the middle of the pill.
        const half = Math.ceil(bars / 2);
        const j = i < half ? half - 1 - i : i - half;
        const m = micBuf ? bandLevel(micBuf, j, half) : 0;
        const a = agentBuf ? bandLevel(agentBuf, j, half) : 0;
        micEnergy += m;
        agentEnergy += a;
        const taper = 0.35 + 0.65 * Math.sin((Math.PI * (i + 0.5)) / bars);
        targets.push(Math.max(m, a) * taper);
      }

      const speaking = Math.max(micEnergy, agentEnergy) / bars > SILENCE;
      ctx.fillStyle = !speaking ? restColor : agentEnergy > micEnergy ? agentColor : micColor;
      for (let i = 0; i < bars; i++) {
        const target = speaking ? targets[i] : 0;
        const prev = levels[i];
        levels[i] = reduceMotion ? target : prev + (target - prev) * (target > prev ? 0.55 : 0.12);
        const h = Math.max(MIN_BAR, Math.min(height - 4, levels[i] * height * 1.15));
        const x = offset + i * (BAR_WIDTH + BAR_GAP);
        ctx.beginPath();
        ctx.roundRect(x, (height - h) / 2, BAR_WIDTH, h, BAR_WIDTH / 2);
        ctx.fill();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [mic, agent, height]);

  return <canvas ref={canvasRef} role="img" aria-label={label} style={{ height }} className="block w-full" />;
}
