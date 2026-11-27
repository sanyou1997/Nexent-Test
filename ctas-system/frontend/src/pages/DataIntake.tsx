import { useEffect, useState } from 'react'
import {
  Card, Row, Col, Statistic, Progress, Tag, Alert, Descriptions, Spin,
  Typography, Space, Tooltip,
} from 'antd'
import {
  DatabaseOutlined, FileImageOutlined, AudioOutlined, BankOutlined,
  ExperimentOutlined, WarningOutlined, CheckCircleOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'

const { Title, Paragraph, Text } = Typography

interface Channel {
  key: string
  name: string
  persisted: boolean
  count: number
  latest_at: string | null
  completeness: Record<string, any>
  note: string
}
interface Overview {
  generated_at: string
  channels: Channel[]
  summary: { persisted_total: number; low_completeness_batches: number }
}

const ICONS: Record<string, JSX.Element> = {
  bank: <BankOutlined />, archive: <DatabaseOutlined />, transcript: <AudioOutlined />,
  dataset: <ExperimentOutlined />, ocr: <FileImageOutlined />, asr: <AudioOutlined />,
}

function LevelTag({ level, n }: { level: string; n: number }) {
  const map: Record<string, { c: string; t: string }> = {
    高: { c: 'green', t: '高' }, 中: { c: 'gold', t: '中' }, 低: { c: 'red', t: '低' },
  }
  const m = map[level] || { c: 'default', t: level }
  return <Tag color={m.c}>{m.t} {n}</Tag>
}

function CompletenessView({ ch }: { ch: Channel }) {
  const c = ch.completeness || {}
  if (ch.key === 'bank') {
    const score = c.avg_score
    const pct = typeof score === 'number' ? Math.round(score) : 0
    const color = pct >= 80 ? '#15803d' : pct >= 60 ? '#b45309' : '#b91c1c'
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Progress percent={pct} strokeColor={color} />
        <Space wrap>
          <Text type="secondary">按完整度：</Text>
          {Object.entries(c.by_level || {}).map(([lv, n]) => (
            <LevelTag key={lv} level={lv} n={n as number} />
          ))}
        </Space>
        {c.low_count > 0 && (
          <Alert type="warning" showIcon style={{ padding: '4px 8px', fontSize: 12 }}
            message={`${c.low_count} 个批次完整度偏低，建议复核映射后重导`} />
        )}
      </Space>
    )
  }
  if (ch.key === 'archive') {
    return (
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="OCR 已落地">{c.ocr_done}/{ch.count}</Descriptions.Item>
        <Descriptions.Item label="未识别文本">
          {c.ocr_missing > 0 ? <Tag color="red">{c.ocr_missing} 份</Tag> : <Tag color="green">无</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="总页数">{c.total_pages}</Descriptions.Item>
        <Descriptions.Item label="总字数">{c.total_chars}</Descriptions.Item>
      </Descriptions>
    )
  }
  if (ch.key === 'transcript') {
    return (
      <Space wrap>
        {Object.entries(c.by_status || {}).map(([s, n]) => (
          <Tag key={s} color={s === '失败' ? 'red' : s === '转录中' ? 'gold' : 'green'}>
            {s} {String(n)}
          </Tag>
        ))}
      </Space>
    )
  }
  if (ch.key === 'dataset') {
    return <Statistic title="总行数" value={c.total_rows} valueStyle={{ fontSize: 20 }} />
  }
  // ocr / asr 实时
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Statistic title="今日调用" value={ch.count} valueStyle={{ fontSize: 20 }} />
      <Text type="secondary" style={{ fontSize: 12 }}>{c.scope}</Text>
    </Space>
  )
}

export default function DataIntake() {
  const [data, setData] = useState<Overview | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/data-intake/overview').then((r) => setData(r.data))
      .catch((e) => setErr(e?.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>

  return (
    <div style={{ padding: 24 }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        <DatabaseOutlined /> 数据接入完整度
      </Title>
      <Paragraph type="secondary" style={{ marginTop: 0 }}>
        让「数据是否全了」全局可见——分析之前先评估接入质量。银行流水自带完整性质检；
        档案/转录/数据集按落库量化；OCR/语音为实时调用，借审计链反查当日次数。
      </Paragraph>

      {err && <Alert type="error" showIcon message={err} style={{ marginBottom: 16 }} />}

      {data && (
        <>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Card><Statistic title="已落库接入总量" value={data.summary.persisted_total} suffix="条/批" /></Card>
            </Col>
            <Col span={8}>
              <Card>
                <Statistic title="低完整度待复核" value={data.summary.low_completeness_batches}
                  valueStyle={{ color: data.summary.low_completeness_batches > 0 ? '#b91c1c' : '#15803d' }} />
              </Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="数据更新于" value={data.generated_at} valueStyle={{ fontSize: 14 }} /></Card></Col>
          </Row>

          <Row gutter={[16, 16]}>
            {data.channels.map((ch) => (
              <Col xs={24} sm={12} lg={8} key={ch.key}>
                <Card
                  title={<Space>{ICONS[ch.key]}{ch.name}
                    {ch.persisted ? <Tag color="blue">落库</Tag> : <Tag>实时</Tag>}
                  </Space>}
                  extra={<Text type="secondary" style={{ fontSize: 12 }}>{ch.count} 项</Text>}
                >
                  <CompletenessView ch={ch} />
                  <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0, marginTop: 8 }}>
                    {ch.note}
                  </Paragraph>
                  {ch.latest_at && (
                    <Text type="secondary" style={{ fontSize: 12 }}>最近：{ch.latest_at}</Text>
                  )}
                </Card>
              </Col>
            ))}
          </Row>

          <Alert style={{ marginTop: 16 }} type="info" showIcon
            message="说明"
            description="本页只做「接入质量可见」，不改动任何数据。完整度偏低的批次请回到对应模块复核映射或重导；OCR/语音如需留存请走「档案数字化 / 转录」。"
            icon={<CheckCircleOutlined />} />
        </>
      )}
    </div>
  )
}
