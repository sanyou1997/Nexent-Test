import { useEffect, useMemo, useState } from 'react'
import {
  Card, Table, Button, Drawer, Tag, Space, message, Statistic, Row, Col,
  Typography, Popconfirm, Upload, InputNumber, Alert, Descriptions, Empty, Modal, Select, Progress, Spin,
} from 'antd'
import {
  UploadOutlined, DeleteOutlined, WarningOutlined, DollarOutlined,
  SwapOutlined, TeamOutlined, DownloadOutlined, BankOutlined, FileSearchOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import PageHeader from '../components/PageHeader'

interface Row_ {
  date: string; time: string; summary: string; counterparty: string
  account: string; amount: number; in: number; out: number; balance?: string
}
interface Anomaly extends Row_ { type: string; reason: string }
interface Analysis {
  threshold: number; total_in: number; total_out: number; net: number
  n_rows: number; n_anomalies: number; anomalies: Anomaly[]
  risky_counterparties: { counterparty: string; count: number; total: number }[]
  fund_return_counterparties?: string[]
  monthly: { month: string; in: number; out: number; n: number }[]
  disclaimer: string; analyzed_at: string
}
interface MissField { present: number; missing: number; rate: number }
interface Stmt {
  id: number; filename: string; row_count: number; dup_count: number
  total_in: number; total_out: number; created_by: string; created_at: string
  completeness_level: string; completeness_score: number; template: string; header_row: number
  missing_report?: Record<string, MissField>; mapping?: Record<string, string>
  source_rows?: number; parsed_rows?: number; skipped_rows?: number
  cleaned?: Row_[]; analysis?: Analysis
}
interface ParseResult {
  filename: string; encoding: string; header_row: number; template: string
  columns: string[]; sample: Record<string, string>[]; suggested: Record<string, string>
  pending: string[]; qc: any; preview_rows: number
}

const ANOMALY_COLOR: Record<string, string> = {
  大额交易: 'red', 整数大额: 'volcano', 夜间交易: 'orange', 高频对手方: 'purple', 敏感摘要: 'magenta',
  资金回流: 'geekblue', 余额不平: 'red', 相近重复: 'gold',
}
const STD_OPTIONS = [
  { value: 'date', label: '交易日期' }, { value: 'time', label: '交易时间' },
  { value: 'amount', label: '金额(单边)' }, { value: 'in_amt', label: '收入金额' },
  { value: 'out_amt', label: '支出金额' }, { value: 'flag', label: '收支/借贷标志' },
  { value: 'summary', label: '摘要' }, { value: 'counterparty', label: '对方户名' },
  { value: 'account', label: '对方账号' }, { value: 'balance', label: '余额' },
]
const LEVEL_COLOR: Record<string, string> = { 高: 'green', 中: 'orange', 低: 'red' }

export default function Bank() {
  const [rows, setRows] = useState<Stmt[]>([])
  const [detail, setDetail] = useState<Stmt | null>(null)
  const [threshold, setThreshold] = useState<number | null>(50000)
  const [parseR, setParseR] = useState<ParseResult | null>(null)
  const [colToStd, setColToStd] = useState<Record<string, string>>({})
  const [mapOpen, setMapOpen] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [quality, setQuality] = useState<any>(null)
  const [qualityOpen, setQualityOpen] = useState(false)
  const [qualityLoading, setQualityLoading] = useState(false)

  const load = async () => { setRows((await api.get('/bank')).data) }
  useEffect(() => { load() }, [])

  const doParse = async (file: File) => {
    setParsing(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const { data } = await api.post('/bank/parse', fd, { timeout: 120000 })
      setParseR(data)
      const rev: Record<string, string> = {}
      Object.entries(data.suggested as Record<string, string>).forEach(([std, col]) => { rev[col] = std })
      setColToStd(rev)
      setMapOpen(true)
    } catch (e) { message.error(errMsg(e)) } finally { setParsing(false) }
    return false
  }

  const doConfirm = async () => {
    if (!parseR) return
    // 反推 std->col 映射（剔除空）
    const mapping: Record<string, string> = {}
    Object.entries(colToStd).forEach(([col, std]) => { if (std) mapping[std] = col })
    if (!mapping['date'] && !(mapping['in_amt'] && mapping['out_amt'])) {
      message.warning('请至少映射「交易日期」和「金额/收入+支出」列')
      return
    }
    if (parseR.qc.level === '低') {
      Modal.confirm({
        title: '数据完整度偏低', content: parseR.qc.guide || '该批次完整度评分为低，仍要导入并分析吗？',
        okText: '仍要导入', cancelText: '返回调整',
        onOk: () => submitConfirm(mapping),
      })
    } else {
      submitConfirm(mapping)
    }
  }
  const submitConfirm = async (mapping: Record<string, string>) => {
    if (!parseR) return
    setConfirming(true)
    try {
      const fd = new FormData()
      fd.append('file', (window as any).__bankFile)
      fd.append('mapping', JSON.stringify(mapping))
      fd.append('header_row', String(parseR.header_row))
      fd.append('threshold', String(threshold ?? 50000))
      const { data } = await api.post('/bank/confirm', fd, { timeout: 120000 })
      message.success(`导入完成：有效 ${data.row_count} 笔，完整度【${data.completeness.level}】${data.completeness.guide ? '；' + data.completeness.guide : ''}`)
      setMapOpen(false); setParseR(null)
      await load()
      const fresh = (await api.get(`/bank/${data.id}`)).data
      setDetail(fresh)
    } catch (e) { message.error(errMsg(e)) } finally { setConfirming(false) }
  }

  const openDetail = async (r: Stmt) => { setDetail((await api.get(`/bank/${r.id}`)).data) }
  const exportCsv = () => {
    if (!detail?.analysis) return
    const a = detail.analysis.anomalies
    const head = '类型,日期,时间,对方户名,金额(元),收支,摘要,提示理由\n'
    const body = a.map((x) =>
      `${x.type},${x.date} ${x.time},${x.counterparty},${x.amount},${x.in > 0 ? '收入' : '支出'},${x.summary},${x.reason}`
        .replace(/"/g, '""')).map((l) => `"${l}"`).join('\n')
    const url = URL.createObjectURL(new Blob(['\ufeff' + head + body], { type: 'text/csv;charset=utf-8' }))
    const el = document.createElement('a'); el.href = url
    el.download = `异常筛查-${detail.filename || '流水'}.csv`; el.click(); URL.revokeObjectURL(url)
  }

  // 数据缺口补录：打开单批次完整度诊断
  const openQuality = async (r: Stmt) => {
    setQualityOpen(true); setQuality(null); setQualityLoading(true)
    try { setQuality((await api.get(`/bank/${r.id}/quality`)).data) }
    catch (e) { message.error(errMsg(e)) } finally { setQualityLoading(false) }
  }
  // 导出缺失补录清单（CSV，供发回相关方补齐后再导入）
  const exportGap = () => {
    const g = quality?.gap_records || []
    if (!g.length) { message.info('该批次无缺失字段记录'); return }
    const head = '日期,时间,对方户名,收入,支出,金额,摘要,对方账号,余额,缺失字段\n'
    const body = g.map((x: any) =>
      `${x.date} ${x.time},${x.counterparty},${x.in > 0 ? x.in : ''},${x.out > 0 ? x.out : ''},${x.amount},${x.summary},${x.account},${x.balance},${(x.missing_fields || []).join('/')}`
        .replace(/"/g, '""')).map((l: string) => `"${l}"`).join('\n')
    const url = URL.createObjectURL(new Blob(['\ufeff' + head + body], { type: 'text/csv;charset=utf-8' }))
    const el = document.createElement('a'); el.href = url
    el.download = `缺失补录清单-${quality.filename || '流水'}.csv`; el.click(); URL.revokeObjectURL(url)
  }

  const cols = [
    { title: '批次', dataIndex: 'filename', ellipsis: true },
    { title: '完整度', dataIndex: 'completeness_level', width: 90,
      render: (l: string) => l ? <Tag color={LEVEL_COLOR[l] || 'default'}>{l}</Tag>
        : <Tag>—</Tag> },
    { title: '有效笔数', dataIndex: 'row_count', width: 90 },
    { title: '剔除重复', dataIndex: 'dup_count', width: 90 },
    { title: '总收入（元）', dataIndex: 'total_in', width: 120, render: (v: number) => v?.toLocaleString() },
    { title: '总支出（元）', dataIndex: 'total_out', width: 120, render: (v: number) => v?.toLocaleString() },
    { title: '模板', dataIndex: 'template', width: 100, render: (t: string) => t || '自定义' },
    { title: '导入人', dataIndex: 'created_by', width: 90 },
    { title: '导入时间', dataIndex: 'created_at', width: 140 },
    { title: '操作', width: 90, render: (_: any, r: Stmt) => (
      <Space>
        <Button size="small" onClick={() => openDetail(r)}>查看</Button>
        <Popconfirm title="删除该批次？" onConfirm={async () => { await api.delete(`/bank/${r.id}`); load(); message.success('已删除') }}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>) },
  ]
  const anomalyCols = [
    { title: '类型', dataIndex: 'type', width: 100, render: (t: string) => <Tag color={ANOMALY_COLOR[t] || 'red'}>{t}</Tag> },
    { title: '日期', dataIndex: 'date', width: 100, render: (t: string, r: Anomaly) => `${t} ${r.time}` },
    { title: '对方户名', dataIndex: 'counterparty', width: 130, render: (t: string) => t || '-' },
    { title: '金额', dataIndex: 'amount', width: 110, render: (v: number, r: Anomaly) => (
      <span style={{ color: r.in > 0 ? '#15803d' : '#b91c1c', fontWeight: 600 }}>
        {r.in > 0 ? '+' : '-'}{v.toLocaleString()}</span>) },
    { title: '摘要', dataIndex: 'summary', ellipsis: true },
    { title: '提示理由', dataIndex: 'reason', ellipsis: true, render: (t: string) => <Typography.Text type="warning">{t}</Typography.Text> },
  ]
  const cleanCols = [
    { title: '日期', dataIndex: 'date', width: 100, render: (t: string, r: Row_) => `${t} ${r.time}` },
    { title: '摘要', dataIndex: 'summary', ellipsis: true },
    { title: '对方户名', dataIndex: 'counterparty', ellipsis: true },
    { title: '收入', dataIndex: 'in', width: 100, render: (v: number) => v ? `+${v.toLocaleString()}` : '-' },
    { title: '支出', dataIndex: 'out', width: 100, render: (v: number) => v ? `-${v.toLocaleString()}` : '-' },
  ]

  const missingReport = detail?.missing_report
  const qc = parseR?.qc
  const sampleCols = useMemo(() => parseR ? parseR.columns : [], [parseR])

  // 数据缺口补录：完整度非「高」的批次
  const lowBatches = rows.filter((r) => r.completeness_level && r.completeness_level !== '高')
  const topMissing = (mr?: Record<string, MissField>) =>
    Object.entries(mr || {})
      .map(([f, v]) => ({ label: STD_OPTIONS.find((o) => o.value === f)?.label || f, rate: v.rate }))
      .filter((x) => x.rate >= 0.15).sort((a, b) => b.rate - a.rate).slice(0, 3)
      .map((x) => `${x.label} 缺${Math.round(x.rate * 100)}%`)
  const gapCols = [
    { title: '日期', dataIndex: 'date', width: 130, render: (t: string, r: any) => `${t} ${r.time}` },
    { title: '对方户名', dataIndex: 'counterparty', render: (t: string) => t || <Typography.Text type="danger">缺</Typography.Text> },
    { title: '摘要', dataIndex: 'summary', ellipsis: true, render: (t: string) => t || <Typography.Text type="danger">缺</Typography.Text> },
    { title: '金额', dataIndex: 'amount', width: 110, render: (v: number, r: any) => `${r.in > 0 ? '+' : '-'}${(v || 0).toLocaleString()}` },
    { title: '缺失字段', dataIndex: 'missing_fields', width: 200, render: (m: string[]) => (m || []).map((x) => <Tag key={x} color="red">{x}</Tag>) },
  ]

  return (
    <>
      <PageHeader icon={<BankOutlined />} title="银行流水清洗与分析"
        subtitle="解析 → 列映射（三级回退）→ 完整性质检 → 标准化 → 规则筛查 → 人工研判"
        extra={
          <Space>
            <span>大额阈值（元）：</span>
            <InputNumber min={1000} step={10000} value={threshold} onChange={setThreshold} style={{ width: 110 }} />
            <Upload accept=".csv,.xlsx,.xls" showUploadList={false} beforeUpload={(f) => { (window as any).__bankFile = f; return doParse(f) }}>
              <Button type="primary" icon={<UploadOutlined />} loading={parsing}>导入流水（CSV/Excel）</Button>
            </Upload>
          </Space>
        } />
      <Card>
        <Alert type="info" showIcon style={{ marginBottom: 12 }} icon={<BankOutlined />}
          message="支持各种银行导出格式：自动识别编码与表头行，列名智能映射（关键词→大模型推断→人工），并给出『数据完整性』量化门禁——数据不全会先提示你，再决定是否分析。"
          description="筛查规则：大额交易、万元整数、夜间交易、高频对手方、敏感摘要关键词——结果仅为线索提示，最终由办案人员人工研判（演示红线：请勿导入真实案件数据）。" />
        <Table rowKey="id" size="middle" columns={cols} dataSource={rows}
          pagination={{ pageSize: 10 }} locale={{ emptyText: <Empty description="暂无导入批次，请上传银行流水文件" /> }} />
      </Card>

      {/* 数据缺口补录工作台：完整度偏低批次 → 诊断 → 导出补录清单 */}
      {lowBatches.length > 0 && (
        <Card size="small" style={{ marginBottom: 16, borderColor: '#f5c9c9' }}
          title={<><ToolOutlined style={{ color: '#b45309' }} /> 数据缺口补录（{lowBatches.length} 个批次完整度偏低）</>}>
          <Alert type="warning" showIcon style={{ marginBottom: 12 }}
            message="这些批次原料不全，直接分析会失真。点「诊断」查看缺哪个字段、缺哪些记录，可导出补录清单发回相关方补齐后重新导入。"
            description="数据全了，分析才有意义。" />
          <Table rowKey="id" size="small" pagination={false} dataSource={lowBatches}
            columns={[
              { title: '批次', dataIndex: 'filename', ellipsis: true },
              { title: '完整度', dataIndex: 'completeness_level', width: 80,
                render: (l: string) => <Tag color={LEVEL_COLOR[l] || 'default'}>{l}</Tag> },
              { title: '评分', dataIndex: 'completeness_score', width: 70, render: (v: number) => v ?? '—' },
              { title: '主要缺失字段', key: 'miss', render: (_: any, r: Stmt) => (
                topMissing(r.missing_report).length
                  ? topMissing(r.missing_report).map((t) => <Tag key={t} color="orange" style={{ marginBottom: 2 }}>{t}</Tag>)
                  : <Typography.Text type="secondary">—</Typography.Text>) },
              { title: '操作', width: 90, render: (_: any, r: Stmt) => (
                <Button size="small" type="primary" ghost icon={<FileSearchOutlined />} onClick={() => openQuality(r)}>诊断</Button>) },
            ]} />
        </Card>
      )}

      {/* 完整度诊断抽屉 */}
      <Drawer title={quality ? `完整度诊断 · ${quality.filename}` : '完整度诊断'} open={qualityOpen} width={720}
        onClose={() => setQualityOpen(false)}
        extra={quality?.gap_records?.length > 0 && (
          <Button type="primary" icon={<DownloadOutlined />} onClick={exportGap}>导出补录清单 CSV</Button>
        )}>
        {qualityLoading && <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>}
        {quality && (
          <>
            <Row gutter={12} style={{ marginBottom: 12 }}>
              <Col span={6}><Card size="small"><Statistic title="完整度" value={quality.level || '—'} valueStyle={{ color: LEVEL_COLOR[quality.level] || '#1e293b' }} /></Card></Col>
              <Col span={6}><Card size="small"><Statistic title="评分" value={quality.score || 0} suffix="/100" /></Card></Col>
              <Col span={6}><Card size="small"><Statistic title="原始/有效" value={`${quality.accounting?.source_rows}/${quality.accounting?.parsed_rows}`} /></Card></Col>
              <Col span={6}><Card size="small"><Statistic title="跳过/重复" value={`${quality.accounting?.skipped_rows}/${quality.accounting?.dup_rows}`} valueStyle={{ color: (`${quality.accounting?.skipped_rows}/${quality.accounting?.dup_rows}`) ? '#b91c1c' : undefined }} /></Card></Col>
            </Row>
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              message={`模板：${quality.template || '自定义'}　日期区间：${quality.date_range?.[0] || '-'} ~ ${quality.date_range?.[1] || '-'}`} />
            <Card size="small" title={<><ToolOutlined /> 修复建议</>} style={{ marginBottom: 12 }}>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {quality.suggestions.map((s: string, i: number) => <li key={i} style={{ marginBottom: 4 }}>{s}</li>)}
              </ul>
            </Card>
            <Card size="small" title={<><FileSearchOutlined /> 字段缺失率（按严重度排序）</>} style={{ marginBottom: 12 }}>
              {quality.fields.map((f: any) => (
                <div key={f.std} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0', fontSize: 12 }}>
                  <span style={{ width: 110 }}>{f.label}</span>
                  <Progress percent={Math.round((1 - f.rate) * 100)} size="small" style={{ flex: 1 }}
                    strokeColor={f.severity === 'high' ? '#b91c1c' : f.severity === 'mid' ? '#b45309' : '#15803d'} />
                  <span style={{ width: 130, textAlign: 'right', color: f.severity === 'high' ? '#b91c1c' : '#475569' }}>
                    缺失 {Math.round(f.rate * 100)}%（缺 {f.missing}）
                  </span>
                </div>
              ))}
            </Card>
            {quality.gap_records?.length > 0 && (
              <Card size="small" title={`缺失记录示例（${quality.gap_records.length} 条，可导出补齐）`}>
                <Table rowKey={(r: any, i?: number) => `${r.date}${r.amount}${i ?? 0}`} size="small" columns={gapCols}
                  dataSource={quality.gap_records} pagination={{ pageSize: 8 }} />
              </Card>
            )}
          </>
        )}
      </Drawer>

      {/* 解析预览 + 映射 + 完整性质检 */}
      <Modal title="导入预览 · 列映射与完整性质检" open={mapOpen} width={960} footer={null}
        onCancel={() => { setMapOpen(false); setParseR(null) }} destroyOnClose>
        {parseR && (
          <div>
            <Alert type={qc.level === '高' ? 'success' : qc.level === '中' ? 'warning' : 'error'} showIcon
              message={`数据完整度【${qc.level}】　评分 ${qc.score}　解析 ${qc.parsed_rows}/${qc.source_rows} 行（跳过 ${qc.skipped_rows} 行）`}
              description={qc.guide || '数据完整，可直接分析。'} style={{ marginBottom: 12 }} />
            <Descriptions size="small" bordered column={3} style={{ marginBottom: 12 }}>
              <Descriptions.Item label="文件名">{parseR.filename}</Descriptions.Item>
              <Descriptions.Item label="编码">{parseR.encoding}</Descriptions.Item>
              <Descriptions.Item label="模板">{parseR.template || '自定义'}</Descriptions.Item>
              <Descriptions.Item label="表头行">第 {parseR.header_row + 1} 行</Descriptions.Item>
              <Descriptions.Item label="日期区间">{qc.date_range?.[0] || '-'} ~ {qc.date_range?.[1] || '-'}</Descriptions.Item>
              <Descriptions.Item label="重复行">{qc.dup_rows}</Descriptions.Item>
            </Descriptions>

            {parseR.pending.length > 0 && (
              <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                message="以下关键字段未能自动识别，请在下表手动映射后再导入："
                description={parseR.pending.join('、')} />
            )}

            <Typography.Title level={5}><FileSearchOutlined /> 字段缺失率（完整性质检）</Typography.Title>
            <div style={{ marginBottom: 12 }}>
              {Object.entries(qc.missing_report as Record<string, MissField>).map(([f, v]) => (
                <div key={f} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0', fontSize: 12 }}>
                  <span style={{ width: 120 }}>{STD_OPTIONS.find((o) => o.value === f)?.label || f}</span>
                  <Progress percent={Math.round((1 - v.rate) * 100)} size="small" style={{ flex: 1 }}
                    strokeColor={v.rate >= 0.4 ? '#b91c1c' : v.rate >= 0.15 ? '#b45309' : '#15803d'} />
                  <span style={{ width: 130, textAlign: 'right', color: v.rate >= 0.4 ? '#b91c1c' : '#475569' }}>
                    缺失 {Math.round(v.rate * 100)}%（缺 {v.missing}）
                  </span>
                </div>
              ))}
            </div>

            <Typography.Title level={5}><FileSearchOutlined /> 列映射（左：源列 → 右：标准字段）</Typography.Title>
            <Table rowKey={(r: any) => r.col} size="small" pagination={false} style={{ marginBottom: 12 }}
              columns={[
                { title: '源列名', dataIndex: 'col', width: 240,
                  render: (c: string) => <b>{c}</b> },
                { title: '映射为标准字段', dataIndex: 'std',
                  render: (_: any, r: any) => (
                    <Select value={colToStd[r.col] || ''} style={{ width: 200 }}
                      onChange={(v) => setColToStd((m) => ({ ...m, [r.col]: v }))}
                      options={[{ value: '', label: '（不映射）' }, ...STD_OPTIONS]} />
                  ) },
              ]}
              dataSource={parseR.columns.map((c) => ({ col: c }))} />

            <Typography.Title level={5}>数据预览（前 {parseR.sample.length} 行）</Typography.Title>
            <Table rowKey={(_: any, i?: number) => String(i ?? 0)} size="small" pagination={false} scroll={{ y: 200 }}
              columns={sampleCols.map((c) => ({ title: c, dataIndex: c, ellipsis: true }))}
              dataSource={parseR.sample} />

            <div style={{ marginTop: 16, textAlign: 'right' }}>
              <Space>
                <Button onClick={() => { setMapOpen(false); setParseR(null) }}>取消</Button>
                <Button type="primary" loading={confirming} onClick={doConfirm}>确认导入并分析</Button>
              </Space>
            </div>
          </div>
        )}
      </Modal>

      {/* 详情：先展示完整性质检，再分析 */}
      <Drawer title={detail ? `流水分析 · ${detail.filename}` : ''} open={!!detail} width={960}
        onClose={() => setDetail(null)}
        extra={detail?.analysis && detail.analysis.anomalies.length > 0 && (
          <Button icon={<DownloadOutlined />} onClick={exportCsv}>导出异常清单</Button>
        )}>
        {detail && (
          <>
            <Card size="small" style={{ marginBottom: 12 }}
              title={<><FileSearchOutlined /> 完整性质检（导入门禁）</>}>
              <Row gutter={12}>
                <Col span={6}><Statistic title="完整度" value={detail.completeness_level || '—'} valueStyle={{ color: LEVEL_COLOR[detail.completeness_level] || '#1e293b' }} /></Col>
                <Col span={6}><Statistic title="完整度评分" value={detail.completeness_score || 0} suffix="/100" /></Col>
                <Col span={6}><Statistic title="解析/总行" value={`${detail.parsed_rows ?? detail.row_count}/${detail.source_rows ?? detail.row_count}`} /></Col>
                <Col span={6}><Statistic title="跳过行" value={detail.skipped_rows ?? 0} valueStyle={{ color: (detail.skipped_rows ?? 0) ? '#b91c1c' : undefined }} /></Col>
              </Row>
              {missingReport && (
                <div style={{ marginTop: 8 }}>
                  {Object.entries(missingReport).map(([f, v]) => (
                    <div key={f} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '1px 0', fontSize: 12 }}>
                      <span style={{ width: 110 }}>{STD_OPTIONS.find((o) => o.value === f)?.label || f}</span>
                      <Progress percent={Math.round((1 - v.rate) * 100)} size="small" style={{ flex: 1 }}
                        strokeColor={v.rate >= 0.4 ? '#b91c1c' : v.rate >= 0.15 ? '#b45309' : '#15803d'} />
                      <span style={{ width: 120, textAlign: 'right', color: v.rate >= 0.4 ? '#b91c1c' : '#475569' }}>缺失 {Math.round(v.rate * 100)}%</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
            {(() => {
              const a = detail.analysis
              if (!a) return null
              return (
                <>
                  <Row gutter={12} style={{ marginBottom: 12 }}>
                    <Col span={6}><Card size="small"><Statistic title="有效交易" value={a.n_rows} suffix="笔" prefix={<SwapOutlined />} /></Card></Col>
                    <Col span={6}><Card size="small"><Statistic title="总收入" value={a.total_in} precision={0} prefix={<DollarOutlined />} valueStyle={{ color: '#3f8600' }} suffix="元" /></Card></Col>
                    <Col span={6}><Card size="small"><Statistic title="总支出" value={a.total_out} precision={0} prefix={<DollarOutlined />} valueStyle={{ color: '#b91c1c' }} suffix="元" /></Card></Col>
                    <Col span={6}><Card size="small"><Statistic title="异常提示" value={a.n_anomalies} suffix="条" prefix={<WarningOutlined />} valueStyle={{ color: '#b45309' }} /></Card></Col>
                  </Row>
                  {a.risky_counterparties.length > 0 && (
                    <Card size="small" title={<><TeamOutlined /> 高频对手方（≥5 笔或累计 ≥ 阈值×5）</>} style={{ marginBottom: 12 }}>
                      {a.risky_counterparties.map((c) => (
                        <div key={c.counterparty} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0' }}>
                          <span>{c.counterparty}</span>
                          <span><Tag color="purple">{c.count} 笔</Tag><Tag color="red">累计 {c.total.toLocaleString()} 元</Tag></span>
                        </div>
                      ))}
                    </Card>
                  )}
                  {a.fund_return_counterparties && a.fund_return_counterparties.length > 0 && (
                    <Card size="small" title={<><SwapOutlined /> 资金回流 / 闭环对手（同时有收有付，须穿透）</>} style={{ marginBottom: 12 }}>
                      {a.fund_return_counterparties.map((cp: string) => (
                        <div key={cp} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0' }}>
                          <span>{cp}</span>
                          <Tag color="geekblue">同批次既收又付</Tag>
                        </div>
                      ))}
                    </Card>
                  )}
                  <Card size="small" title={<><WarningOutlined style={{ color: '#b45309' }} /> 异常线索清单（{a.anomalies.length} 条 · 规则筛查）</>} style={{ marginBottom: 12 }}>
                    <Table rowKey={(r: Anomaly) => `${r.date}${r.time}${r.amount}${r.counterparty}${r.type}`}
                      size="small" columns={anomalyCols} dataSource={a.anomalies} pagination={{ pageSize: 8 }} />
                  </Card>
                  <Card size="small" title={`清洗后明细（${detail.row_count} 笔）`} style={{ marginBottom: 12 }}>
                    <Table rowKey={(_: any, i?: number) => String(i)} size="small" columns={cleanCols}
                      dataSource={detail.cleaned} pagination={{ pageSize: 8 }} />
                  </Card>
                  <Alert type="warning" showIcon message={a.disclaimer} />
                </>
              )
            })()}
          </>
        )}
      </Drawer>
    </>
  )
}
