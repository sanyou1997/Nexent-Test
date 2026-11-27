import { useEffect, useState } from 'react'
import { Alert, Button, Col, Descriptions, Input, Radio, Row, Segmented, Space, Tag, message } from 'antd'
import {
  ScanOutlined, AudioOutlined, FilePdfOutlined, ThunderboltOutlined, SaveOutlined, SyncOutlined,
  CheckCircleFilled, CloseCircleFilled, ApiOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'

interface OcrCfg { source: string; provider: string; pdf_mode: string; urls: Record<string, string>; from?: string }
interface AsrCfg { source: string; provider: string; urls: Record<string, string>; qwen_model: string; from?: string }
interface NexentCfg {
  ok: boolean; error: string; tenant: string; fetched_at: string
  raw?: { pdf_provider: string; audio_provider: string }
  ocr?: Omit<OcrCfg, 'source'>; asr?: Omit<AsrCfg, 'source'>
}
interface Settings {
  ocr: OcrCfg
  asr: AsrCfg
  nexent: NexentCfg
  effective: { ocr: OcrCfg; asr: AsrCfg }
  dsn_configured: boolean
  choices: { ocr: Record<string, string>; pdf_mode: Record<string, string>; asr: Record<string, string> }
  labels: { ocr: string; asr: string }
}

const OCR_DESC: Record<string, string> = {
  umi_ocr: '与 Nexent「文件解析」共用的 Umi-OCR，返回逐行文字与坐标，支持原文对照高亮。',
  paddle_ocr: 'Nexent 接入的 PaddleOCR（PaddleX /ocr 接口），返回逐行文字与坐标。',
  paddle_layout: 'Nexent 接入的 PP-StructureV3 版面解析，只返回正文，无逐行坐标。',
}
const ASR_DESC: Record<string, string> = {
  paraformer: 'Nexent 默认的本地普通话转写（Paraformer-Large + VAD + 标点），适合谈话录音；非普通话自动改用 Qwen3-ASR。',
  qwen_asr: 'Nexent 接入的 Qwen3-ASR（OpenAI 兼容 /v1/audio/transcriptions），支持多语种。',
}
const NX_PDF: Record<string, string> = { mineu: 'MinerU', umi_ocr: 'Umi-OCR', paddle_ocr: 'PaddleOCR', paddle_layout: 'PP-StructureV3' }
const NX_AUDIO: Record<string, string> = { paraformer: 'Paraformer 本地转写', qwen_asr: 'Qwen3-ASR' }

type TestResult = { ok: boolean; message: string; latency_ms?: number } | null

/** 系统管理 → 识别服务（仅管理员）：材料 OCR / 扫描 PDF / 谈话录音转写，跟随 Nexent 配置或手动配置一份。 */
export default function RecognitionSettings() {
  const [s, setS] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [testing, setTesting] = useState('')
  const [results, setResults] = useState<Record<string, TestResult>>({})

  const load = () => api.get('/recognition/settings').then(({ data }) => setS(data)).catch((e) => message.error(errMsg(e)))
  useEffect(() => { load() }, [])
  if (!s) return null

  const setOcr = (patch: Partial<OcrCfg>) => setS({ ...s, ocr: { ...s.ocr, ...patch } })
  const setAsr = (patch: Partial<AsrCfg>) => setS({ ...s, asr: { ...s.asr, ...patch } })

  const sync = async () => {
    setSyncing(true)
    try {
      const { data } = await api.post('/recognition/sync')
      setS((cur) => cur ? { ...data, ocr: cur.ocr, asr: cur.asr } : data)
      if (data.nexent?.ok) message.success('已同步 Nexent 当前配置'); else message.warning(data.nexent?.error || '同步失败')
    } catch (e) { message.error(errMsg(e)) } finally { setSyncing(false) }
  }

  const runTest = async (key: string, kind: string, provider: string, url = '', model = '') => {
    setTesting(key)
    try {
      const { data } = await api.post('/recognition/test', { kind, provider, url, model }, { timeout: 180000 })
      setResults((r) => ({ ...r, [key]: data }))
    } catch (e) { setResults((r) => ({ ...r, [key]: { ok: false, message: errMsg(e) } })) } finally { setTesting('') }
  }

  const save = async () => {
    setSaving(true)
    try {
      const { data } = await api.put('/recognition/settings', {
        ocr: { source: s.ocr.source, provider: s.ocr.provider, pdf_mode: s.ocr.pdf_mode, urls: s.ocr.urls },
        asr: { source: s.asr.source, provider: s.asr.provider, urls: s.asr.urls, qwen_model: s.asr.qwen_model },
      })
      setS(data)
      message.success('已保存，新的识别请求即时生效')
    } catch (e) { message.error(errMsg(e)) } finally { setSaving(false) }
  }

  const Result = ({ k }: { k: string }) => {
    const r = results[k]
    if (!r) return null
    return (
      <div className={`cx-test-result ${r.ok ? 'ok' : 'bad'}`}>
        {r.ok ? <CheckCircleFilled /> : <CloseCircleFilled />}
        <span>{r.message}{r.latency_ms ? ` · ${r.latency_ms} ms` : ''}</span>
      </div>
    )
  }

  const nx = s.nexent
  const eff = s.effective
  const fromTag = (from?: string) => from === 'nexent' ? <Tag color="green" bordered={false}>来自 Nexent</Tag>
    : from === 'manual_fallback' ? <Tag color="orange" bordered={false}>Nexent 读取失败，暂用手动配置</Tag>
      : <Tag bordered={false}>手动配置</Tag>

  const optionCard = (group: 'ocr' | 'asr', key: string, label: string, desc: string) => {
    const active = group === 'ocr' ? s.ocr.provider === key : s.asr.provider === key
    return (
      <label key={key} className={`cx-opt${active ? ' active' : ''}`}>
        <Radio value={key} />
        <div className="cx-opt-body">
          <div className="cx-opt-title">{label}</div>
          <div className="cx-opt-desc">{desc}</div>
        </div>
      </label>
    )
  }

  return (
    <div className="cx-page">
      <div className="cx-page-head">
        <div>
          <div className="cx-page-title"><ScanOutlined /> 识别服务</div>
          <div className="cx-page-sub">材料 OCR、扫描版 PDF、谈话录音转写全部复用 Nexent 的识别服务：默认跟随 Nexent 当前配置，也可以在这里手动配置一份。</div>
        </div>
        <div className="cx-page-actions">
          <Button icon={<SyncOutlined spin={syncing} />} onClick={sync} disabled={syncing}>同步 Nexent 配置</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>保存设置</Button>
        </div>
      </div>

      <div className="cx-card" style={{ padding: 16 }}>
        <div className="cx-section-title"><ApiOutlined /> Nexent 当前配置</div>
        {nx.ok ? (
          <Descriptions size="small" column={{ xs: 1, lg: 3 }}>
            <Descriptions.Item label="租户">{nx.tenant}</Descriptions.Item>
            <Descriptions.Item label="PDF / 文档解析">{NX_PDF[nx.raw?.pdf_provider || ''] || nx.raw?.pdf_provider}</Descriptions.Item>
            <Descriptions.Item label="录音转写">{NX_AUDIO[nx.raw?.audio_provider || ''] || nx.raw?.audio_provider}</Descriptions.Item>
            <Descriptions.Item label="读取时间">{nx.fetched_at}（每 30 秒自动刷新）</Descriptions.Item>
          </Descriptions>
        ) : (
          <Alert type="warning" showIcon message={nx.error}
            description={!s.dsn_configured ? 'Nexent 的识别配置接口只接受网页登录，CTAS 需要一个只读数据库连接才能读取。由 Nexent 管理员执行 docker/nexent-readonly-role.sql 并在 .env 中配置 NEXENT_DB_DSN 后即可跟随；在此之前按下方手动配置运行（默认值与 Nexent 默认一致）。' : undefined} />
        )}
      </div>

      <Row gutter={16}>
        <Col xs={24} xl={12}>
          <div className="cx-card" style={{ padding: 20 }}>
            <div className="cx-section-head">
              <div className="cx-section-title" style={{ marginBottom: 0 }}><ScanOutlined /> 文字识别 · 扫描版 PDF</div>
              <Segmented size="small" value={s.ocr.source} onChange={(v) => setOcr({ source: String(v) })}
                options={[{ label: '跟随 Nexent', value: 'nexent' }, { label: '手动配置', value: 'manual' }]} />
            </div>
            <div className="cx-eff">当前生效：{s.labels.ocr} · {eff.ocr.pdf_mode === 'mineru' ? '扫描 PDF 交 MinerU' : '扫描 PDF 逐页 OCR'} {fromTag(eff.ocr.from)}</div>

            {s.ocr.source === 'nexent' ? (
              <div className="cx-muted" style={{ fontSize: 13, lineHeight: 1.8, marginTop: 10 }}>
                每次识别前读取 Nexent「文件解析」的选择：Nexent 选 MinerU 时，扫描版 PDF 整份交 MinerU、图片用 Umi-OCR；选 Umi-OCR / PaddleOCR 时，图片和扫描页都用该服务。读取失败时使用「手动配置」中的设置。
                <Space style={{ marginTop: 8 }} wrap>
                  <Button size="small" icon={<ThunderboltOutlined />} loading={testing === 'ocr'}
                    onClick={() => runTest('ocr', 'ocr', eff.ocr.provider, eff.ocr.urls[eff.ocr.provider])}>测试图片识别</Button>
                  {eff.ocr.pdf_mode === 'mineru' && <Button size="small" icon={<ThunderboltOutlined />} loading={testing === 'pdf'}
                    onClick={() => runTest('pdf', 'pdf', 'mineru', eff.ocr.urls.mineru)}>测试 MinerU</Button>}
                </Space>
                <Result k="ocr" /><Result k="pdf" />
              </div>
            ) : (
              <>
                <Radio.Group value={s.ocr.provider} onChange={(e) => setOcr({ provider: e.target.value })} style={{ width: '100%', marginTop: 12 }}>
                  <div className="cx-opt-list">
                    {Object.entries(s.choices.ocr).map(([k, v]) => optionCard('ocr', k, v, OCR_DESC[k] || ''))}
                  </div>
                </Radio.Group>
                <div className="cx-field">
                  <div className="cx-field-label">服务地址</div>
                  <Space.Compact style={{ width: '100%' }}>
                    <Input value={s.ocr.urls[s.ocr.provider]} onChange={(e) => setOcr({ urls: { ...s.ocr.urls, [s.ocr.provider]: e.target.value } })} />
                    <Button icon={<ThunderboltOutlined />} loading={testing === 'ocr'}
                      onClick={() => runTest('ocr', 'ocr', s.ocr.provider, s.ocr.urls[s.ocr.provider])}>识别测试</Button>
                  </Space.Compact>
                  <Result k="ocr" />
                </div>
                <div className="cx-section-title" style={{ marginTop: 18 }}><FilePdfOutlined /> 扫描版 PDF</div>
                <Radio.Group value={s.ocr.pdf_mode} onChange={(e) => setOcr({ pdf_mode: e.target.value })}>
                  <Space direction="vertical">
                    {Object.entries(s.choices.pdf_mode).map(([k, v]) => <Radio key={k} value={k}>{v}</Radio>)}
                  </Space>
                </Radio.Group>
                {s.ocr.pdf_mode === 'mineru' && (
                  <div className="cx-field">
                    <div className="cx-field-label">MinerU 地址（数字文本页仍走本机文本抽取；MinerU 结果不含逐行坐标）</div>
                    <Space.Compact style={{ width: '100%' }}>
                      <Input value={s.ocr.urls.mineru} onChange={(e) => setOcr({ urls: { ...s.ocr.urls, mineru: e.target.value } })} />
                      <Button icon={<ThunderboltOutlined />} loading={testing === 'pdf'} onClick={() => runTest('pdf', 'pdf', 'mineru', s.ocr.urls.mineru)}>连接测试</Button>
                    </Space.Compact>
                    <Result k="pdf" />
                  </div>
                )}
              </>
            )}
          </div>
        </Col>

        <Col xs={24} xl={12}>
          <div className="cx-card" style={{ padding: 20 }}>
            <div className="cx-section-head">
              <div className="cx-section-title" style={{ marginBottom: 0 }}><AudioOutlined /> 谈话录音转写</div>
              <Segmented size="small" value={s.asr.source} onChange={(v) => setAsr({ source: String(v) })}
                options={[{ label: '跟随 Nexent', value: 'nexent' }, { label: '手动配置', value: 'manual' }]} />
            </div>
            <div className="cx-eff">当前生效：{s.labels.asr} {fromTag(eff.asr.from)}</div>

            {s.asr.source === 'nexent' ? (
              <div className="cx-muted" style={{ fontSize: 13, lineHeight: 1.8, marginTop: 10 }}>
                每次转写前读取 Nexent「文件解析 → 录音」的选择与服务地址。读取失败时使用「手动配置」中的设置。
                <div style={{ marginTop: 8 }}>
                  <Button size="small" icon={<ThunderboltOutlined />} loading={testing === 'asr'}
                    onClick={() => runTest('asr', 'asr', eff.asr.provider, eff.asr.urls[eff.asr.provider], eff.asr.qwen_model)}>连接测试</Button>
                </div>
                <Result k="asr" />
              </div>
            ) : (
              <>
                <Radio.Group value={s.asr.provider} onChange={(e) => setAsr({ provider: e.target.value })} style={{ width: '100%', marginTop: 12 }}>
                  <div className="cx-opt-list">
                    {Object.entries(s.choices.asr).map(([k, v]) => optionCard('asr', k, v, ASR_DESC[k] || ''))}
                  </div>
                </Radio.Group>
                <div className="cx-field">
                  <div className="cx-field-label">服务地址</div>
                  <Space.Compact style={{ width: '100%' }}>
                    <Input value={s.asr.urls[s.asr.provider]} onChange={(e) => setAsr({ urls: { ...s.asr.urls, [s.asr.provider]: e.target.value } })} />
                    <Button icon={<ThunderboltOutlined />} loading={testing === 'asr'}
                      onClick={() => runTest('asr', 'asr', s.asr.provider, s.asr.urls[s.asr.provider], s.asr.qwen_model)}>连接测试</Button>
                  </Space.Compact>
                  {s.asr.provider === 'qwen_asr' && (
                    <>
                      <div className="cx-field-label" style={{ marginTop: 10 }}>模型名</div>
                      <Input value={s.asr.qwen_model} onChange={(e) => setAsr({ qwen_model: e.target.value })} />
                    </>
                  )}
                  <Result k="asr" />
                </div>
              </>
            )}
          </div>
        </Col>
      </Row>
    </div>
  )
}
