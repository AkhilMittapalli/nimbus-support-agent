"use client";

import { useEffect, useRef } from "react";

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/**
 * Real-time waveform for the caller's mic and the agent's voice (two overlaid traces).
 * Reads time-domain data from AnalyserNodes on every animation frame.
 */
export function Waveform({
  mic,
  agent,
  height = 96,
  label = "Live audio waveform",
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

    const micColor = cssVar("--wave-mic", "#2f5bea");
    const agentColor = cssVar("--wave-agent", "#7c4ddb");
    const baseColor = cssVar("--border-strong", "#cdd2d9");
    const micBuf = mic ? new Uint8Array(mic.fftSize) : null;
    const agentBuf = agent ? new Uint8Array(agent.fftSize) : null;
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

    const drawTrace = (analyser: AnalyserNode, buf: Uint8Array<ArrayBuffer>, color: string, w: number, gain: number) => {
      analyser.getByteTimeDomainData(buf);
      let energy = 0;
      for (let i = 0; i < buf.length; i++) energy += Math.abs(buf[i] - 128);
      const active = energy / buf.length > 1.2;
      ctx.lineWidth = active ? 2 : 1.25;
      ctx.strokeStyle = color;
      ctx.globalAlpha = active ? 1 : 0.35;
      ctx.beginPath();
      const step = w / buf.length;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        const y = height / 2 + v * (height / 2) * gain;
        if (i === 0) ctx.moveTo(0, y);
        else ctx.lineTo(i * step, y);
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    };

    const draw = () => {
      const w = canvas.getBoundingClientRect().width;
      ctx.clearRect(0, 0, w, height);
      ctx.strokeStyle = baseColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(w, height / 2);
      ctx.stroke();
      ctx.setLineDash([]);
      if (agent && agentBuf) drawTrace(agent, agentBuf, agentColor, w, 1.4);
      if (mic && micBuf) drawTrace(mic, micBuf, micColor, w, 2.2);
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
