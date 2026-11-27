import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errMsg } from '../api/client'

export interface SegResult { ts: string; text: string }

/** 实时语音转录：Web Audio 采集 + VAD 静音切段 + WAV 分段上传识别。
 *  每段独立 WAV（自带头，后端可直接解码），串行提交避免并发占用模型。 */
export function useRealtimeASR(onError?: (m: string) => void) {
  const [recording, setRecording] = useState(false)
  const [segments, setSegments] = useState<SegResult[]>([])
  const [busy, setBusy] = useState(false) // 有分段正在识别
  const [micError, setMicError] = useState('')

  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const procRef = useRef<ScriptProcessorNode | null>(null)
  const bufRef = useRef<Float32Array[]>([]) // 当前段累积 PCM
  const bufLenRef = useRef(0)
  const voiceLenRef = useRef(0) // 当前段有效语音采样数
  const silenceRef = useRef(0) // 连续静音帧数
  const speakingRef = useRef(false)
  const startTimeRef = useRef(0)
  const queueRef = useRef<{ pcm: Blob; ts: string }[]>([])
  const sendingRef = useRef(false)
  const runningRef = useRef(false)
  const rmsListRef = useRef<number[]>([]) // 录音电平（UI 动画）

  const MIN_SEG_SEC = 1.2       // 段最短有效语音时长
  const MAX_SEG_SEC = 18        // 段最长时长（强切）
  const SILENCE_FRAMES = 35     // 约 700ms 静音切句（20ms/帧）
  const SILENCE_TH = 0.011

  const fmt = (ms: number) => {
    const s = Math.floor(ms / 1000)
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  }

  /** PCM Float32(单声道) → 16bit WAV Blob */
  const encodeWav = (chunks: Float32Array[], sampleRate: number) => {
    const total = chunks.reduce((n, c) => n + c.length, 0)
    const buf = new ArrayBuffer(44 + total * 2)
    const v = new DataView(buf)
    const ws = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)) }
    ws(0, 'RIFF'); v.setUint32(4, 36 + total * 2, true); ws(8, 'WAVE')
    ws(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true)
    v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true)
    v.setUint16(32, 2, true); v.setUint16(34, 16, true)
    ws(36, 'data'); v.setUint32(40, total * 2, true)
    let off = 44
    for (const c of chunks) {
      for (let i = 0; i < c.length; i++, off += 2) {
        const s = Math.max(-1, Math.min(1, c[i]))
        v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true)
      }
    }
    return new Blob([buf], { type: 'audio/wav' })
  }

  /** 串行发送队列中的分段 */
  const flushQueue = useCallback(async () => {
    if (sendingRef.current) return
    sendingRef.current = true
    setBusy(true)
    try {
      while (queueRef.current.length) {
        const item = queueRef.current.shift()!
        try {
          const fd = new FormData()
          fd.append('file', new File([item.pcm], `seg-${item.ts.replace(/:/g, '')}.wav`, { type: 'audio/wav' }))
          const { data } = await api.post('/asr', fd, { timeout: 180000 })
          if (data.text && data.text.trim()) {
            setSegments((prev) => [...prev, { ts: item.ts, text: data.text.trim() }])
          }
        } catch (e) {
          onError?.(errMsg(e))
        }
      }
    } finally {
      sendingRef.current = false
      setBusy(false)
    }
  }, [onError])

  const pushSegment = () => {
    const ctx = ctxRef.current
    if (!ctx || bufLenRef.current === 0) return
    // 段内有效语音太短（如噪声）则丢弃
    if (voiceLenRef.current / ctx.sampleRate < 0.25) {
      bufRef.current = []; bufLenRef.current = 0; voiceLenRef.current = 0
      return
    }
    const pcm = bufRef.current.slice()
    const sampleRate = ctx.sampleRate
    const elapsed = performance.now() - startTimeRef.current
    const ts = fmt(elapsed - (bufLenRef.current / sampleRate) * 1000)
    // 编码放到微任务，避免阻塞音频线程
    setTimeout(() => {
      queueRef.current.push({ pcm: encodeWav(pcm, sampleRate), ts })
      void flushQueue()
    }, 0)
    bufRef.current = []; bufLenRef.current = 0; voiceLenRef.current = 0
  }

  const start = useCallback(async () => {
    setMicError('')
    // 环境预检：非安全上下文（如局域网 IP 访问）浏览器禁止麦克风
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      const m = '当前页面非安全上下文（请用 localhost 或 127.0.0.1 访问），浏览器禁止麦克风'
      setMicError(m); onError?.(m); return
    }
    if (navigator.permissions?.query) {
      try {
        const p = await navigator.permissions.query({ name: 'microphone' as PermissionName })
        if (p.state === 'denied') {
          const m = '麦克风权限已被拒绝：请点击地址栏左侧的锁/麦克风图标，将麦克风设为"允许"后刷新页面'
          setMicError(m); onError?.(m); return
        }
      } catch { /* 浏览器不支持则忽略 */ }
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream
      const ctx = new AudioContext()
      ctxRef.current = ctx
      await ctx.resume()
      const src = ctx.createMediaStreamSource(stream)
      const proc = ctx.createScriptProcessor(4096, 1, 1)
      procRef.current = proc
      runningRef.current = true
      bufRef.current = []; bufLenRef.current = 0; voiceLenRef.current = 0
      silenceRef.current = 0; speakingRef.current = false
      startTimeRef.current = performance.now()
      rmsListRef.current = []

      proc.onaudioprocess = (e) => {
        if (!runningRef.current) return
        const input = e.inputBuffer.getChannelData(0)
        // 计算 RMS
        let sum = 0
        for (let i = 0; i < input.length; i++) sum += input[i] * input[i]
        const rms = Math.sqrt(sum / input.length)
        rmsListRef.current.push(rms)
        if (rmsListRef.current.length > 30) rmsListRef.current.shift()

        const segSec = bufLenRef.current / ctx.sampleRate
        if (rms > SILENCE_TH) {
          speakingRef.current = true
          silenceRef.current = 0
          voiceLenRef.current += input.length
        } else if (speakingRef.current) {
          silenceRef.current++
        }
        bufRef.current.push(new Float32Array(input))
        bufLenRef.current += input.length
        // 静音切句 或 达到最大段长强切
        if ((speakingRef.current && silenceRef.current >= SILENCE_FRAMES && segSec >= MIN_SEG_SEC)
          || segSec >= MAX_SEG_SEC) {
          pushSegment()
          silenceRef.current = 0; speakingRef.current = false
        }
      }
      src.connect(proc)
      proc.connect(ctx.destination)
      setSegments([])
      setRecording(true)
    } catch (e: any) {
      const m = e?.name === 'NotAllowedError'
        ? '麦克风权限被拒绝：请在浏览器地址栏允许麦克风访问（或检查 Windows 设置 → 隐私 → 麦克风）'
        : e?.name === 'NotFoundError' ? '未检测到麦克风设备' : `麦克风启动失败：${e?.message || e}`
      setMicError(m); onError?.(m)
    }
  }, [onError])

  const stop = useCallback(() => {
    runningRef.current = false
    try { pushSegment() } catch { /* ignore */ }
    procRef.current?.disconnect(); procRef.current = null
    ctxRef.current?.close().catch(() => {}); ctxRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop()); streamRef.current = null
    setRecording(false)
    void flushQueue()
  }, [flushQueue])

  const clear = useCallback(() => setSegments([]), [])

  // 页面卸载兜底清理
  useEffect(() => () => {
    runningRef.current = false
    try {
      procRef.current?.disconnect()
      ctxRef.current?.close().catch(() => {})
      streamRef.current?.getTracks().forEach((t) => t.stop())
    } catch { /* ignore */ }
  }, [])

  /** 当前电平 0~1（用于波形动画） */
  const level = rmsListRef.current.length
    ? Math.min(1, rmsListRef.current[rmsListRef.current.length - 1] * 12)
    : 0

  return { recording, segments, busy, micError, level, start, stop, clear }
}
