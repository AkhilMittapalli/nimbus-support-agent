# Nimbus Support - frontend

Next.js 16 (App Router, TypeScript strict, Tailwind 4) console for the support agent: streaming chat,
live voice (Gemini Live realtime engine + browser STT/TTS pipeline fallback), and an observability drawer.

```bash
cp .env.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev                  # http://localhost:3000
npm run build && npm run lint
```

Deploy on Vercel with **Root Directory = `frontend`** and `NEXT_PUBLIC_API_URL` set to the backend's public
URL (the WebSocket URL is derived from it: `https` -> `wss`). Add the Vercel domain to the backend's
`CORS_ORIGINS`.

## Layout

| Path | Purpose |
|------|---------|
| `lib/protocol.ts` | Event protocol types (mirror of `backend/app/agent/events.py`) |
| `lib/sse.ts` | POST + `text/event-stream` parser (EventSource can't POST) |
| `lib/conversation.ts` | Pure reducer: one timeline for chat and voice events |
| `lib/audio.ts`, `public/pcm-capture-worklet.js` | Mic capture -> 16 kHz PCM16, gapless 24 kHz playback with barge-in flush |
| `lib/speech.ts` | Web Speech helpers for the pipeline engine (TTS queue, sentence chunking, echo filter) |
| `hooks/useConversation.ts` | Session lifecycle, chat turns, retry/stop/interrupt |
| `hooks/useRealtimeEngine.ts` | Gemini Live engine over the backend WebSocket |
| `hooks/usePipelineEngine.ts` | STT -> `/api/chat` (mode `voice`) -> TTS engine |
| `hooks/useVoiceSession.ts` | Engine facade: Auto / Realtime / Pipeline with automatic fallback |
| `components/chat/*` | Timeline, bubbles, step timeline, guardrail badge, tickets, composer |
| `components/voice/*` | Voice panel, waveform |
| `components/observability/*` | Traces, span waterfall, session totals, chaos controls |
