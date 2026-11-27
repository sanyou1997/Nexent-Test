import { useEffect, useMemo, useState } from 'react'
import {
  Card, Row, Col, Table, Button, Space, Tag, Modal, Form, Input, Select,
  Upload, message, Descriptions, Alert, Empty, Spin, Tabs, Tooltip, Popconfirm, Divider,
} from 'antd'
import {
  DatabaseOutlined, UploadOutlined, SearchOutlined, BarChartOutlined,
  FileSearchOutlined, CheckCircleOutlined, EditOutlined, DeleteOutlined,
  DownloadOutlined, WarningOutlined, ThunderboltOutlined, CopyOutlined, FileTextOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api/client'
import { useAuth } from '../auth'
import PageHeader from '../components/PageHeader'
import HighlightText from '../components/HighlightText'
import { BarChart, TrendChart, DonutChart, CoverageBar, StatTile } from '../components/StatCharts'

const SEV_COLOR: Record<string, string> = { 一般: 'blue', 重要: 'orange', 重大: 'red' }
const STATUS_COLOR: Record<string, string> = { 已入库: 'green', 待补录: 'orange', 已复核: 'cyan' }
const FIELD_LABEL: Record<string, string> = {
  report_type: '报告类型', report_date: '报告日期', region: '地区',
  audited_org: '被审计单位', audit_type: '审计类型', problem_category: '问题类别',
  severity: '严重程度', amount: '涉及金额', report_no: '报告文号', subject: '被审计对象',
  findings: '问题发现', conclusion: '处理意见',
}

/** 历史报告库：批量导入历史审计/调查/巡视报告 → 自动识别建档 → 类案参考 + 多维统计。
 *  三个页签分工：① 报告库（检索/补录） ② 统计分析（多维度） ③ 数据质检（完整性门禁）。 */
export default function HistoricalReports() {
  const { user } = useAuth()
  const [tab, setTab] = useState('library')
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  // 初始筛选从 URL 参数读取（支持驾驶舱图表下钻带参进入）
  const [filters, setFilters] = useState<any>(() => {
    const sp = new URLSearchParams(window.location.search)
    const map: Record<string, string> = {
      year: 'year', region: 'region', problem_category: 'problem_category',
      report_type: 'report_type', severity: 'severity', status: 'status', q: 'q',
    }
    const f: any = { q: '', report_type: '', year: undefined, region: '', problem_category: '', status: '' }
    for (const [urlKey, field] of Object.entries(map)) {
      const v = sp.get(urlKey)
      if (v) f[field] = urlKey === 'year' ? Number(v) : v
    }
    return f
  })
  const [meta, setMeta] = useState<any>(null)
  const [dims, setDims] = useState<any>(null)
  const [quality, setQuality] = useState<any>(null)
  const [detail, setDetail] = useState<any>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editForm] = Form.useForm()
  const [similar, setSimilar] = useState<any[]>([])
  const [importOpen, setImportOpen] = useState(false)
  const [importResult, setImportResult] = useState<any>(null)
  const [importing, setImporting] = useState(false)
  const [fileList, setFileList] = useState<any[]>([])
  const [useLlm, setUseLlm] = useState(true)
  const [batches, setBatches] = useState<any[]>([])
  const [findings, setFindings] = useState<any>(null)
  const [refStat, setRefStat] = useState<any>(null)
  const [reportFindings, setReportFindings] = useState<any[]>([])
  // 批量删除：需输入账号密码二次确认
  const [selectedIds, setSelectedIds] = useState<React.Key[]>([])
  const [delOpen, setDelOpen] = useState(false)
  const [delForm] = Form.useForm()
  const [deleting, setDeleting] = useState(false)
  // 屡查屡犯 / 整改跟踪
  const [recurring, setRecurring] = useState<any>(null)
  const [recurringOrg, setRecurringOrg] = useState<any>(null)
  const [orgDrawerOpen, setOrgDrawerOpen] = useState(false)
  // 类案检索：命中片段高亮 + 引用留痕
  const [qInput, setQInput] = useState('')
  const [lastQuery, setLastQuery] = useState('')
  const [searchTerms, setSearchTerms] = useState<string[]>([])
  const [refsOpen, setRefsOpen] = useState(false)
  const [refs, setRefs] = useState<any[]>([])

  const load = () => {
    setLoading(true)
    const p = new URLSearchParams()
    Object.entries(filters).forEach(([k, v]) => { if (v !== '' && v !== undefined && v !== null) p.set(k, String(v)) })
    p.set('page', String(page)); p.set('per_page', '20')
    api.get(`/historical-reports?${p.toString()}`).then(({ data }: any) => {
      setRows(data.items || []); setTotal(data.total || 0)
    }).catch((e: any) => message.error(errMsg(e))).finally(() => setLoading(false))
  }

  const loadRecurring = () => {
    api.get('/historical-reports/recurring').then(({ data }: any) => setRecurring(data)).catch(() => setRecurring(null))
  }

  const openRecurringOrg = async (org: string) => {
    try {
      const { data } = await api.get(`/historical-reports/recurring/org?org=${encodeURIComponent(org)}`)
      setRecurringOrg(data); setOrgDrawerOpen(true)
    } catch (e: any) { message.error(errMsg(e)) }
  }

  const loadStats = () => {
    api.get('/historical-reports/stats/dimensions').then(({ data }: any) => setDims(data)).catch(() => setDims(null))
    api.get('/historical-reports/quality-report').then(({ data }: any) => setQuality(data)).catch(() => setQuality(null))
    api.get('/historical-reports/findings/analysis').then(({ data }: any) => setFindings(data)).catch(() => setFindings(null))
    api.get('/historical-reports/refs/validate').then(({ data }: any) => setRefStat(data)).catch(() => setRefStat(null))
  }

  useEffect(() => {
    api.get('/historical-reports/meta/options').then(({ data }: any) => setMeta(data)).catch(() => setMeta(null))
    api.get('/historical-reports/batches').then(({ data }: any) => setBatches(data.items || [])).catch(() => setBatches([]))
  }, [])

  useEffect(() => {
    const p = new URLSearchParams()
    Object.entries(filters).forEach(([k, v]) => { if (v !== '' && v !== undefined && v !== null) p.set(k, String(v)) })
    p.set('page', String(page)); p.set('per_page', '20')
    setLoading(true)
    api.get(`/historical-reports?${p.toString()}`).then(({ data }: any) => {
      setRows(data.items || []); setTotal(data.total || 0)
    }).catch((e: any) => message.error(errMsg(e))).finally(() => setLoading(false))
  }, [page, filters])

  useEffect(() => {
    if (tab !== 'library') {
      api.get('/historical-reports/stats/dimensions').then(({ data }: any) => setDims(data)).catch(() => setDims(null))
      api.get('/historical-reports/quality-report').then(({ data }: any) => setQuality(data)).catch(() => setQuality(null))
    }
    if (tab === 'rectify') loadRecurring()
  }, [tab])

  const openDetail = async (id: number) => {
    try {
      const { data } = await api.get(`/historical-reports/${id}`)
      setDetail(data); setDetailOpen(true)
      const { data: sim } = await api.get(`/historical-reports/similar/${id}`)
      setSimilar(sim.hits || [])
      const { data: fds } = await api.get(`/historical-reports/${id}/findings`)
      setReportFindings(fds.items || [])
    } catch (e: any) { message.error(errMsg(e)) }
  }

  const openEdit = (r: any) => {
    setDetail(r); setEditOpen(true)
  }

  // 补录表单：Modal 挂载后再填值（Form 实例未连接时 setFieldsValue 会丢值）
  useEffect(() => {
    if (!editOpen || !detail) return
    const t = setTimeout(() => {
      editForm.setFieldsValue({
        title: detail.title, report_no: detail.report_no, report_type: detail.report_type,
        report_date: detail.report_date, region: detail.region, audited_org: detail.audited_org,
        audit_type: detail.audit_type, problem_category: detail.problem_category,
        severity: detail.severity, amount: detail.amount, subject: detail.subject,
        findings: detail.findings, conclusion: detail.conclusion,
        tags: (detail.tags || []).join('、'), status: detail.status,
      })
    }, 60)
    return () => clearTimeout(t)
  }, [editOpen, detail])

  const saveEdit = async () => {
    const v = await editForm.validateFields()
    const body: any = { ...v, amount: Number(v.amount || 0), tags: v.tags ? String(v.tags).split(/[、,，;；\s]+/).filter(Boolean) : [] }
    try {
      await api.put(`/historical-reports/${detail.id}`, body)
      message.success('已保存，报告状态已更新')
      setEditOpen(false); load(); loadStats()
    } catch (e: any) { message.error(errMsg(e)) }
  }

  const doImport = async (files: any[]) => {
    if (!files.length) { message.warning('请先选择文件'); return }
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    fd.append('use_llm', String(useLlm))
    fd.append('note', '批量导入历史报告')
    setImporting(true)
    try {
      const { data } = await api.post('/historical-reports/batch-import', fd, { timeout: 900000 })
      setImportResult(data)
      setFileList([])  // 清空已选，避免再次点击时重复提交同一批
      message.success(`导入完成：成功 ${data.ok} / 重复 ${data.duplicated} / 失败 ${data.failed}`)
      load(); loadStats()
      api.get('/historical-reports/batches').then(({ data: b }: any) => setBatches(b.items || []))
    } catch (e: any) { message.error(errMsg(e)) } finally { setImporting(false) }
  }

  const doSearch = async (q: string) => {
    setLastQuery(q)
    try {
      const { data } = await api.post('/historical-reports/search', {
        q, top_k: 20,
        report_type: filters.report_type || '', year: filters.year || 0,
        region: filters.region || '', problem_category: filters.problem_category || '',
      })
      setRows(data.hits || []); setTotal(data.total || 0); setPage(1)
      setSearchTerms(data.terms || [])
      message.info(`类案检索命中 ${data.hits?.length || 0} 条（候选池 ${data.total} 条）`)
    } catch (e: any) { message.error(errMsg(e)) }
  }

  const removeReport = async (id: number) => {
    try {
      await api.delete(`/historical-reports/${id}`)
      message.success('已删除'); load(); loadStats()
    } catch (e: any) { message.error(errMsg(e)) }
  }

  /** 批量删除：必须输入当前账号 + 密码，由后端校验后才真正删除。 */
  const doBatchDelete = async () => {
    const v = await delForm.validateFields()
    setDeleting(true)
    try {
      const { data } = await api.post('/historical-reports/batch-delete', {
        ids: selectedIds.map(Number), username: (v.username || '').trim(), password: v.password,
      })
      message.success(`已删除 ${data.deleted} 份报告${data.not_found ? `（${data.not_found} 份不存在）` : ''}`)
      setDelOpen(false); setSelectedIds([]); delForm.resetFields()
      load(); loadStats()
    } catch (e: any) {
      message.error(errMsg(e))
    } finally { setDeleting(false) }
  }

  /** 引用片段：把历史报告的「问题发现 + 处理意见」拼成可粘贴的参考段，
   *  直接粘进审理文书草稿，避免手抄错漏（不跨模块写库，保持文书库干净）。 */
  const quoteToClipboard = async (r: any) => {
    const parts: string[] = []
    if (r.findings) parts.push(`【主要问题】\n${r.findings}`)
    if (r.conclusion) parts.push(`【处理意见】\n${r.conclusion}`)
    if (!parts.length) { message.warning('该报告未提取到问题发现与处理意见'); return }
    const head = `（类案参考：${r.title}｜${r.report_date || r.year || ''}${r.report_no ? '｜' + r.report_no : ''}）\n`
    const text = head + parts.join('\n\n')
    try {
      await navigator.clipboard.writeText(text)
      message.success('类案参考片段已复制，可粘贴到审理文书')
    } catch {
      message.info(text)  // 剪贴板不可用时直接展示，便于手动复制
    }
  }

  /** 引用并复制（精确到问题条目）：先落引用留痕形成审计链，
   *  再把该问题表述复制到剪贴板，可直接粘贴进审理文书草稿。 */
  const quoteSnippet = async (r: any, s: any) => {
    try {
      await api.post('/historical-reports/reference', {
        report_id: r.id, finding_id: s.finding_id || 0, query: lastQuery,
      })
    } catch (e: any) { message.error(errMsg(e)); return }
    const head = `（类案参考：${r.title}｜${r.report_date || r.year || ''}${r.report_no ? '｜' + r.report_no : ''}${s.problem_type ? '｜' + s.problem_type : ''}）\n`
    const text = head + s.content
    try {
      await navigator.clipboard.writeText(text)
      message.success('已记录引用并复制该问题表述，可粘贴到审理文书')
    } catch {
      message.info(text)  // 剪贴板不可用时直接展示，便于手动复制
    }
  }

  /** 引用并复制（报告级）：相似报告列表用，复制「问题发现 + 处理意见」整段。 */
  const citeReport = async (r: any) => {
    try {
      await api.post('/historical-reports/reference', { report_id: r.id, query: lastQuery })
    } catch (e: any) { message.error(errMsg(e)); return }
    quoteToClipboard(r)
  }

  const openRefs = async () => {
    try {
      const { data } = await api.get('/historical-reports/references?limit=50')
      setRefs(data.items || []); setRefsOpen(true)
    } catch (e: any) { message.error(errMsg(e)) }
  }

  const columns = [
    {
      title: '报告', dataIndex: 'title', ellipsis: true,
      render: (v: string, r: any) => (
        <a onClick={() => openDetail(r.id)} title={v}>{v || '（未识别标题）'}</a>
      ),
    },
    { title: '文号', dataIndex: 'report_no', width: 130, ellipsis: true },
    { title: '年度', dataIndex: 'year', width: 68 },
    { title: '地区', dataIndex: 'region', width: 120, ellipsis: true },
    { title: '被审计单位', dataIndex: 'audited_org', width: 170, ellipsis: true },
    { title: '类型', dataIndex: 'report_type', width: 108, render: (v: string) => <Tag color="blue">{v || '—'}</Tag> },
    { title: '问题类别', dataIndex: 'problem_category', width: 100, render: (v: string) => <Tag>{v || '—'}</Tag> },
    {
      title: '程度', dataIndex: 'severity', width: 74,
      render: (v: string) => (v ? <Tag color={SEV_COLOR[v]}>{v}</Tag> : <Tag>—</Tag>),
    },
    {
      title: '金额(万)', dataIndex: 'amount', width: 86,
      render: (v: number) => (v ? v.toLocaleString() : '—'),
    },
    {
      title: '识别', key: 'conf', width: 132,
      render: (_: any, r: any) => (
        <Space size={4}>
          <Tag color={r.extract_confidence >= 0.9 ? 'green' : r.extract_confidence >= 0.75 ? 'gold' : 'red'}>
            {Math.round((r.extract_confidence || 0) * 100)}%
          </Tag>
          {r.missing_fields?.length > 0 && (
            <Tooltip title={`待补录：${r.missing_fields.map((f: string) => FIELD_LABEL[f] || f).join('、')}`}>
              <Tag color="orange" icon={<WarningOutlined />}>{r.missing_fields.length}</Tag>
            </Tooltip>
          )}
          {r.near_duplicate_of > 0 && (
            <Tooltip title={`疑似重复：与 #${r.near_duplicate_of} 文号相同，请人工确认`}>
              <Tag color="volcano">疑似重复</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    { title: '状态', dataIndex: 'status', width: 82, render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    {
      title: '操作', key: 'op', width: 150, fixed: 'right' as const,
      render: (_: any, r: any) => (
        <Space size={2}>
          <Tooltip title="复制问题发现+处理意见（粘到审理文书）">
            <Button size="small" type="link" icon={<CopyOutlined />} onClick={() => quoteToClipboard(r)} />
          </Tooltip>
          <Tooltip title="补录/修正"><Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEdit(r)} /></Tooltip>
          <Popconfirm title="确认删除该报告？" onConfirm={() => removeReport(r.id)} okText="删除" cancelText="取消">
            <Button size="small" type="link" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const statBody = useMemo(() => {
    if (!dims) return <Spin />
    return (
      <div>
        <Row gutter={[10, 10]}>
          <Col xs={12} sm={8} lg={4}><StatTile label="报告总量" value={dims.total} icon={<DatabaseOutlined />} /></Col>
          <Col xs={12} sm={8} lg={4}><StatTile label="涉及金额" value={dims.amount_total} suffix="万元" color="#b45309" /></Col>
          <Col xs={12} sm={8} lg={4}><StatTile label="平均金额" value={dims.avg_amount} suffix="万元" /></Col>
          <Col xs={12} sm={8} lg={4}><StatTile label="年度跨度" value={dims.by_year?.length ? `${dims.by_year[0].key}-${dims.by_year[dims.by_year.length - 1].key}` : '—'} /></Col>
          <Col xs={12} sm={8} lg={4}><StatTile label="待补录" value={quality?.need_review_count ?? 0} color={quality?.need_review_count ? '#b45309' : undefined} icon={<WarningOutlined />} /></Col>
          <Col xs={12} sm={8} lg={4}><StatTile label="平均置信度" value={Math.round((quality?.avg_confidence ?? 0) * 100)} suffix="%" color="#15803d" /></Col>
        </Row>

        <Card size="small" title="年度趋势（柱=报告数，线=涉及金额）" style={{ marginTop: 12 }}>
          <TrendChart data={dims.by_year || []} />
        </Card>

        <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
          <Col xs={24} lg={8}>
            <Card size="small" title="报告类型分布">
              <BarChart data={dims.by_type || []} />
            </Card>
          </Col>
          <Col xs={24} lg={8}>
            <Card size="small" title="地区分布">
              <BarChart data={dims.by_region || []} />
            </Card>
          </Col>
          <Col xs={24} lg={8}>
            <Card size="small" title="问题类别分布">
              <BarChart data={dims.by_problem || []} />
            </Card>
          </Col>
        </Row>

        <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
          <Col xs={24} lg={8}>
            <Card size="small" title="处理结果结构">
              <DonutChart data={dims.by_conclusion_kind || []} />
            </Card>
          </Col>
          <Col xs={24} lg={8}>
            <Card size="small" title="严重程度构成">
              <DonutChart data={dims.by_severity || []} />
            </Card>
          </Col>
          <Col xs={24} lg={8}>
            <Card size="small" title="金额区间分布">
              <BarChart data={dims.by_amount_bucket || []} />
            </Card>
          </Col>
        </Row>

        {findings && findings.total > 0 && (
          <Card size="small" title="问题条目级分析（拆到「一、二、三」颗粒度）" style={{ marginTop: 12 }}
            extra={
              <Space size={6}>
                <Tag color="blue">共 {findings.total} 条</Tag>
                <Tag color="orange">涉及 {findings.amount_total} 万元</Tag>
              </Space>
            }>
            <Alert type="info" showIcon style={{ marginBottom: 10 }}
              message="报告级统计只能回答「哪类问题多」，拆到条目才能回答「哪个单位反复出问题、哪条金额最大」——这正是历史材料整理的真正价值。" />
            <Row gutter={[12, 12]}>
              <Col xs={24} lg={8}>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>条目问题类型分布</div>
                <BarChart data={findings.by_type || []} />
              </Col>
              <Col xs={24} lg={8}>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>问题高发单位 TOP（反复出问题）</div>
                <BarChart data={(findings.by_org || []).slice(0, 8)} />
              </Col>
              <Col xs={24} lg={8}>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>条目年度分布</div>
                <BarChart data={findings.by_year || []} />
              </Col>
            </Row>
            <Card size="small" title="金额最大的问题条目" style={{ marginTop: 12 }}>
              <Table
                rowKey="id" size="small" dataSource={findings.top_findings || []} pagination={false}
                columns={[
                  { title: '金额(万)', dataIndex: 'amount', width: 92, render: (v: number) => <b style={{ color: '#b91c1c' }}>{v || 0}</b> },
                  { title: '问题类型', dataIndex: 'problem_type', width: 104, render: (v: string) => <Tag>{v || '—'}</Tag> },
                  { title: '条目内容', dataIndex: 'content', ellipsis: true },
                ]}
              />
            </Card>
          </Card>
        )}
      </div>
    )
  }, [dims, quality, findings])

  const qualityBody = useMemo(() => {
    if (!quality) return <Spin />
    return (
      <div>
        <Alert
          type={quality.need_review_count > 0 ? 'warning' : 'success'} showIcon
          message={quality.need_review_count > 0
            ? `有 ${quality.need_review_count} 份报告字段不完整，需人工补录后方可用于完整统计`
            : '全部报告字段完整，可直接用于类案参考与统计分析'}
          description="数据全了才有分析：下方为各字段的覆盖情况，缺失字段会直接拉低该维度的统计可信度。"
          style={{ marginBottom: 12 }}
        />
        {quality.near_duplicates > 0 && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }}
            message={`发现 ${quality.near_duplicates} 份疑似重复报告（文号相同但内容不同），重复计入会虚增统计口径`} />
        )}
        <Row gutter={[12, 12]}>
          <Col xs={24} lg={12}>
            <Card size="small" title="字段覆盖情况">
              {quality.field_coverage?.map((f: any) => (
                <CoverageBar key={f.field} label={f.label} coverage={f.coverage} missing={f.missing} />
              ))}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card size="small" title="待补录队列" extra={<Tag color="orange">{quality.need_review_count} 份</Tag>}>
              {quality.need_review?.length ? (
                <Table
                  rowKey="id" size="small" dataSource={quality.need_review} pagination={false}
                  columns={[
                    { title: '报告', dataIndex: 'title', ellipsis: true, render: (v: string, r: any) => <a onClick={() => openDetail(r.id)}>{v}</a> },
                    { title: '置信度', dataIndex: 'confidence', width: 78, render: (v: number) => `${Math.round((v || 0) * 100)}%` },
                    {
                      title: '缺失字段', dataIndex: 'missing', width: 190,
                      render: (m: string[]) => (m || []).map((f) => <Tag key={f} color="orange">{FIELD_LABEL[f] || f}</Tag>),
                    },
                  ]}
                />
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无待补录报告" />}
            </Card>
          </Col>
        </Row>
        {quality?.near_duplicates > 0 && (
          <Card size="small" title="疑似重复报告" style={{ marginTop: 12 }}
            extra={<Tag color="orange">{quality.near_duplicates} 份</Tag>}>
            <Alert type="warning" showIcon style={{ marginBottom: 10 }}
              message="内容哈希不同但文号（或标题+年度+单位）相同——很可能是同一报告改了标题重新上传，请人工确认后删除其一。" />
            <Table
              rowKey="id" size="small" dataSource={quality.near_duplicate_items || []} pagination={false}
              columns={[
                { title: '报告', dataIndex: 'title', ellipsis: true, render: (v: string, r: any) => <a onClick={() => openDetail(r.id)}>{v}</a> },
                { title: '重复于', dataIndex: 'duplicate_title', ellipsis: true },
                { title: '判据', dataIndex: 'reason', width: 150 },
              ]}
            />
          </Card>
        )}

        <Card size="small" title="导入批次记录" style={{ marginTop: 12 }}>          {batches.length ? (
            <Table
              rowKey="id" size="small" dataSource={batches} pagination={false}
              columns={[
                { title: '批次号', dataIndex: 'batch_no', width: 150 },
                { title: '总数', dataIndex: 'total', width: 66 },
                { title: '成功', dataIndex: 'ok', width: 66, render: (v: number) => <span style={{ color: '#15803d' }}>{v}</span> },
                { title: '重复', dataIndex: 'duplicated', width: 66 },
                { title: '失败', dataIndex: 'failed', width: 66, render: (v: number) => <span style={{ color: v ? '#b91c1c' : undefined }}>{v}</span> },
                { title: '待补录', dataIndex: 'need_review', width: 76 },
                { title: '导入人', dataIndex: 'operator', width: 90 },
                { title: '时间', dataIndex: 'created_at', width: 130 },
              ]}
            />
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无导入批次" />}
        </Card>

        <Card size="small" title="法规引用有效性校验" style={{ marginTop: 12 }}
          extra={refStat && (
            <Space size={6}>
              <Tag>校验 {refStat.refs_checked} 处</Tag>
              <Tag color={refStat.bad_count ? 'red' : 'green'}>错条 {refStat.bad_count}</Tag>
              <Tag color="blue">法规未入库 {refStat.uncollected_count}</Tag>
            </Space>
          )}>
          <Alert type="warning" showIcon style={{ marginBottom: 10 }}
            message="报告援引的法规条款会对照法规库逐条核对——错条（引了不存在的条）必须人工改正，否则办案援引会出问题。" />
          {refStat?.bad_refs?.length ? (
            <Table
              rowKey="report_id" size="small" dataSource={refStat.bad_refs} pagination={false}
              columns={[
                { title: '报告', dataIndex: 'report_title', ellipsis: true },
                { title: '引用法规', dataIndex: 'law', width: 220, ellipsis: true },
                { title: '条款', dataIndex: 'article_no', width: 90 },
                { title: '问题', dataIndex: 'reason', width: 220 },
              ]}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未发现错条引用" />
          )}
          {refStat?.uncollected_laws?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>
                以下法规尚未收录进法规库（不算错误，补录法规后可自动校验）：
              </div>
              <Space wrap size={[6, 6]}>
                {Array.from(new Set<string>(refStat.uncollected_laws.map((x: any) => x.law))).map((law) => (
                  <Tag key={law} color="blue">{law}</Tag>
                ))}
              </Space>
            </div>
          )}
        </Card>
      </div>
    )
  }, [quality, batches, refStat])

  const rectifyBody = useMemo(() => {
    if (!recurring) return <Spin />
    const items = recurring.items || []
    const top = items[0]
    return (
      <div>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="屡查屡犯 = 同一单位在多次审计中反复出现同类问题，是最直接的「整改不力」信号。系统从已建档报告自动派生，无需新增录入。" />
        <Row gutter={[10, 10]}>
          <Col xs={12} sm={8} lg={6}><StatTile label="被审≥2次单位" value={recurring.audited_multi ?? 0} icon={<DatabaseOutlined />} /></Col>
          <Col xs={12} sm={8} lg={6}><StatTile label="屡查屡犯单位" value={recurring.recurring_org_count ?? 0} color={items.length ? '#b91c1c' : undefined} icon={<WarningOutlined />} /></Col>
          <Col xs={12} sm={8} lg={6}><StatTile label="最严重单位" value={top ? top.org : '—'} /></Col>
          <Col xs={12} sm={8} lg={6}><StatTile label="重复问题类型" value={top ? top.repeated_category_count : 0} suffix="类" color="#b45309" /></Col>
        </Row>
        {items.length === 0 ? (
          <Empty style={{ marginTop: 40 }} description="当前数据中未发现同一单位多次审计出现同类问题" />
        ) : (
          <>
            <Card size="small" title="屡查屡犯单位排名" style={{ marginTop: 12 }}
              extra={<Space size={6}><Tag color="blue">被审≥{recurring.threshold?.min_reports}次</Tag><Tag color="orange">同类跨报告≥{recurring.threshold?.min_repeat}次</Tag></Space>}>
              <Table
                rowKey="org" size="small" dataSource={items} pagination={false}
                columns={[
                  { title: '被审计单位', dataIndex: 'org', width: 220,
                    render: (v: string) => <a onClick={() => openRecurringOrg(v)}>{v}</a> },
                  { title: '地区', dataIndex: 'region', width: 90 },
                  { title: '被审次数', dataIndex: 'report_count', width: 80, render: (v: number) => <b>{v}</b> },
                  { title: '问题条目', dataIndex: 'finding_count', width: 80 },
                  { title: '屡查屡犯类别数', dataIndex: 'repeated_category_count', width: 110,
                    render: (v: number) => <Tag color="red">{v}</Tag> },
                  { title: '重复出现的问题类型', dataIndex: 'repeated_categories',
                    render: (cats: any[]) => (
                      <Space wrap size={[4, 4]}>
                        {(cats || []).map((c) => <Tag key={c.type} color="volcano" title={`跨 ${c.times} 份报告`}>{c.type}×{c.times}</Tag>)}
                      </Space>
                    ) },
                ]}
              />
            </Card>
            <Card size="small" title="地区整改不力分布" style={{ marginTop: 12 }}>
              <BarChart data={(recurring.by_region || []).map((x: any) => ({ key: x.region, count: x.recurring_org_count }))} />
            </Card>
          </>
        )}
      </div>
    )
  }, [recurring])

  return (
    <div>
      <PageHeader
        icon={<DatabaseOutlined />}
        title="历史报告库"
        subtitle="批量导入历史审计/调查/巡视报告 · 自动识别建档 · 类案参考 · 多维统计"
      />

      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'library',
            label: <span><DatabaseOutlined /> 报告库</span>,
            children: (
              <div>
                <Card size="small" style={{ marginBottom: 12 }}>
                  <Space wrap size={[8, 8]}>
                    <Input
                      style={{ width: 260 }} placeholder="标题 / 单位 / 问题关键词"
                      prefix={<SearchOutlined />} allowClear
                      value={qInput}
                      onChange={(e) => setQInput(e.target.value)}
                      onPressEnter={() => doSearch(qInput)}
                    />
                    <Button onClick={() => doSearch(qInput)} type="primary" ghost icon={<FileSearchOutlined />}>类案检索</Button>
                    <Select style={{ width: 140 }} allowClear placeholder="报告类型" value={filters.report_type || undefined}
                      options={(meta?.actual_types?.length ? meta.actual_types : meta?.report_types || []).map((t: string) => ({ label: t, value: t }))}
                      onChange={(v) => setFilters({ ...filters, report_type: v || '' })} />
                    <Select style={{ width: 100 }} allowClear placeholder="年度" value={filters.year}
                      options={(meta?.years || []).map((y: number) => ({ label: String(y), value: y }))}
                      onChange={(v) => setFilters({ ...filters, year: v })} />
                    <Select style={{ width: 150 }} allowClear placeholder="地区" value={filters.region || undefined}
                      options={(meta?.regions || []).map((r: string) => ({ label: r, value: r }))}
                      onChange={(v) => setFilters({ ...filters, region: v || '' })} />
                    <Select style={{ width: 130 }} allowClear placeholder="问题类别" value={filters.problem_category || undefined}
                      options={(meta?.problem_categories || []).map((c: string) => ({ label: c, value: c }))}
                      onChange={(v) => setFilters({ ...filters, problem_category: v || '' })} />
                    <Select style={{ width: 110 }} allowClear placeholder="状态" value={filters.status || undefined}
                      options={['已入库', '待补录', '已复核'].map((s) => ({ label: s, value: s }))}
                      onChange={(v) => setFilters({ ...filters, status: v || '' })} />
                    <Button onClick={() => { setFilters({ q: '', report_type: '', year: undefined, region: '', problem_category: '', status: '' }); setQInput(''); setSearchTerms([]); setPage(1) }}>重置</Button>
                    <Button type="primary" icon={<UploadOutlined />} onClick={() => { setImportResult(null); setImportOpen(true) }}>批量导入</Button>
                    <Button
                      danger
                      icon={<DeleteOutlined />}
                      disabled={!selectedIds.length}
                      onClick={() => {
                        delForm.setFieldsValue({ username: user?.username || '', password: '' })
                        setDelOpen(true)
                      }}
                    >
                      批量删除{selectedIds.length ? `（${selectedIds.length}）` : ''}
                    </Button>
                    <Button icon={<FileTextOutlined />} onClick={openRefs}>引用记录</Button>
                    <Button icon={<DownloadOutlined />} onClick={() => {
                      window.open('/api/historical-reports/export', '_blank')
                    }}>导出 xlsx</Button>
                  </Space>
                </Card>

                <Card size="small" title={`报告清单（${total} 份）`}
                  extra={selectedIds.length > 0 && (
                    <Space size={8}>
                      <Tag color="orange">已选 {selectedIds.length} 份</Tag>
                      <Button size="small" onClick={() => setSelectedIds([])}>取消选择</Button>
                    </Space>
                  )}>
                  <Table
                    rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns}
                    scroll={{ x: 1400 }}
                    expandable={{
                      expandedRowRender: (r) => (
                        <div>
                          <div style={{ marginBottom: 8, color: '#64748b' }}>
                            与检索词「{lastQuery}」精确命中 {r.snippets?.length || 0} 个问题片段
                            {r.cite_count ? <Tag color="gold" style={{ marginLeft: 8 }}>被引 {r.cite_count} 次</Tag> : null}
                          </div>
                          {(r.snippets || []).map((s: any) => (
                            <Card key={s.finding_id || s.content} size="small" type="inner" style={{ marginBottom: 8 }}
                              title={<Space size={6}>
                                <Tag color="blue">{s.problem_type || '问题'}</Tag>
                                {s.seq ? <Tag>{s.seq}</Tag> : null}
                              </Space>}
                              extra={
                                <Button size="small" type="link" icon={<CopyOutlined />} onClick={() => quoteSnippet(r, s)}>
                                  引用并复制
                                </Button>
                              }
                            >
                              <div style={{ whiteSpace: 'pre-wrap' }}>
                                <HighlightText text={s.content} terms={searchTerms} />
                              </div>
                            </Card>
                          ))}
                        </div>
                      ),
                      rowExpandable: (r) => (r.snippets?.length || 0) > 0,
                    }}
                    rowSelection={{
                      selectedRowKeys: selectedIds,
                      onChange: (keys) => setSelectedIds(keys),
                      preserveSelectedRowKeys: true,
                    }}
                    pagination={{
                      current: page, total, pageSize: 20, showSizeChanger: false,
                      onChange: (p) => setPage(p), showTotal: (t) => `共 ${t} 份`,
                    }}
                  />
                </Card>
              </div>
            ),
          },
          { key: 'stats', label: <span><BarChartOutlined /> 多维统计</span>, children: statBody },
          { key: 'quality', label: <span><CheckCircleOutlined /> 数据质检</span>, children: qualityBody },
          { key: 'rectify', label: <span><ThunderboltOutlined /> 整改跟踪</span>, children: rectifyBody },
        ]}
      />

      {/* 屡查屡犯 · 单单位整改时间线 */}
      <Modal
        open={orgDrawerOpen} onCancel={() => setOrgDrawerOpen(false)} width={760}
        footer={[<Button key="c" onClick={() => setOrgDrawerOpen(false)}>关闭</Button>]}
        title={recurringOrg ? `${recurringOrg.org} · 整改时间线` : '整改时间线'}
      >
        {recurringOrg && (
          <div>
            <Alert type={recurringOrg.repeated_types?.length ? 'warning' : 'info'} showIcon style={{ marginBottom: 12 }}
              message={recurringOrg.repeated_types?.length
                ? `该单位在多次审计中反复出现：${recurringOrg.repeated_types.join('、')}`
                : '该单位历次审计问题类型无重复'}
              description={`${recurringOrg.region} · 共 ${recurringOrg.report_count} 次审计`} />
            <Space direction="vertical" style={{ width: '100%' }} size={10}>
              {recurringOrg.timeline.map((t: any) => {
                const hasRepeat = (t.categories || []).some((c: string) => (recurringOrg.repeated_types || []).includes(c))
                return (
                  <Card key={t.rid} size="small" type="inner"
                    title={<Space wrap><Tag color="blue">{t.year}</Tag><span>{t.title}</span></Space>}
                    extra={<Tag color={hasRepeat ? 'red' : 'default'}>{t.report_type}</Tag>}>
                    <Space wrap size={[6, 6]}>
                      {(t.categories || []).map((c: string) => (
                        <Tag key={c} color={(recurringOrg.repeated_types || []).includes(c) ? 'volcano' : undefined}>
                          {c}{(recurringOrg.repeated_types || []).includes(c) ? ' · 屡查屡犯' : ''}
                        </Tag>
                      ))}
                    </Space>
                  </Card>
                )
              })}
            </Space>
          </div>
        )}
      </Modal>

      {/* 详情 */}
      <Modal
        open={detailOpen} onCancel={() => setDetailOpen(false)} width={900} footer={[
          <Button key="c" onClick={() => setDetailOpen(false)}>关闭</Button>,
          <Button key="e" type="primary" icon={<EditOutlined />} onClick={() => { setDetailOpen(false); openEdit(detail) }}>补录/修正</Button>,
        ]}
        title={detail?.title || '报告详情'}
      >
        {detail && (
          <div>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="报告文号">{detail.report_no || '—'}</Descriptions.Item>
              <Descriptions.Item label="报告日期">{detail.report_date || '—'}</Descriptions.Item>
              <Descriptions.Item label="报告类型">{detail.report_type || '—'}</Descriptions.Item>
              <Descriptions.Item label="审计类型">{detail.audit_type || '—'}</Descriptions.Item>
              <Descriptions.Item label="地区">{detail.region || '—'}</Descriptions.Item>
              <Descriptions.Item label="问题类别">{detail.problem_category || '—'}</Descriptions.Item>
              <Descriptions.Item label="被审计单位" span={2}>{detail.audited_org || '—'}</Descriptions.Item>
              <Descriptions.Item label="被审计对象">{detail.subject || '—'}</Descriptions.Item>
              <Descriptions.Item label="涉及金额">{detail.amount ? `${detail.amount} 万元` : '—'}</Descriptions.Item>
              <Descriptions.Item label="严重程度">{detail.severity ? <Tag color={SEV_COLOR[detail.severity]}>{detail.severity}</Tag> : '—'}</Descriptions.Item>
              <Descriptions.Item label="识别置信度">
                {Math.round((detail.extract_confidence || 0) * 100)}%
                {detail.missing_fields?.length > 0 && (
                  <Tag color="orange" style={{ marginLeft: 6 }}>缺 {detail.missing_fields.map((f: string) => FIELD_LABEL[f] || f).join('、')}</Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="标签" span={2}>
                {(detail.tags || []).map((t: string) => <Tag key={t}>{t}</Tag>)}
              </Descriptions.Item>
              <Descriptions.Item label="主要问题发现" span={2}>
                <div style={{ whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>{detail.findings || '—'}</div>
              </Descriptions.Item>
              <Descriptions.Item label="处理意见" span={2}>
                <div style={{ whiteSpace: 'pre-wrap', maxHeight: 140, overflow: 'auto' }}>{detail.conclusion || '—'}</div>
              </Descriptions.Item>
              <Descriptions.Item label="引用法规" span={2}>
                {(detail.legal_refs || []).length
                  ? detail.legal_refs.map((x: any, i: number) => <Tag key={i} color="cyan">{x.law} {x.article_no}</Tag>)
                  : '—'}
              </Descriptions.Item>
            </Descriptions>

            {reportFindings.length > 0 && (
              <Card size="small" title={`问题条目拆分（${reportFindings.length} 条）`} style={{ marginTop: 12 }}
                extra={<Tag color="blue">可做条目级统计</Tag>}>
                <Table
                  rowKey="id" size="small" dataSource={reportFindings} pagination={false}
                  columns={[
                    { title: '序号', dataIndex: 'seq', width: 62 },
                    { title: '问题类型', dataIndex: 'problem_type', width: 96, render: (v: string) => <Tag>{v || '—'}</Tag> },
                    { title: '金额(万)', dataIndex: 'amount', width: 84, render: (v: number) => (v ? <b style={{ color: '#b91c1c' }}>{v}</b> : '—') },
                    { title: '内容', dataIndex: 'content', ellipsis: true },
                    {
                      title: '操作', width: 104,
                      render: (_: any, f: any) => (
                        <Button size="small" type="link" icon={<CopyOutlined />}
                          onClick={() => quoteSnippet(detail, { finding_id: f.id, content: f.content, problem_type: f.problem_type })}>
                          引用并复制
                        </Button>
                      ),
                    },
                  ]}
                />
              </Card>
            )}

            {similar.length > 0 && (
              <Card size="small" title="相似历史报告（类案参考）" style={{ marginTop: 12 }}
                extra={<Tag color="blue"><ThunderboltOutlined /> TF-IDF 相似度</Tag>}>
                {similar.map((s: any) => (
                  <div key={s.id} style={{ padding: '6px 0', borderBottom: '1px solid #f6f8fb' }}>
                    <Space size={8}>
                      <Tag color="blue">{Math.round((s.score || 0) * 100)}%</Tag>
                      <a onClick={() => openDetail(s.id)}>{s.title}</a>
                      <Tag>{s.year}</Tag>
                      <Tag>{s.problem_category}</Tag>
                      {s.severity && <Tag color={SEV_COLOR[s.severity]}>{s.severity}</Tag>}
                      <Button size="small" type="link" icon={<CopyOutlined />} onClick={() => quoteToClipboard(s)}>
                        引用片段
                      </Button>
                      <Button size="small" type="link" icon={<FileTextOutlined />} onClick={() => citeReport(s)}>
                        引用并复制
                      </Button>
                    </Space>
                  </div>
                ))}
              </Card>
            )}
          </div>
        )}
      </Modal>

      {/* 类案引用记录（审计链） */}
      <Modal
        open={refsOpen} onCancel={() => setRefsOpen(false)} width={760}
        footer={[<Button key="c" onClick={() => setRefsOpen(false)}>关闭</Button>]}
        title="类案引用记录"
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="每次「引用并复制」都会在此留痕：谁、何时、引用了哪份报告/哪条问题，满足审计链要求。" />
        <Table
          rowKey="id" size="small" dataSource={refs} pagination={false}
          columns={[
            { title: '时间', dataIndex: 'created_at', width: 130 },
            { title: '引用人', dataIndex: 'username', width: 80 },
            { title: '检索词', dataIndex: 'query', width: 130, ellipsis: true, render: (v: string) => v || '—' },
            {
              title: '被引报告 / 问题条目', dataIndex: 'report_title', ellipsis: true,
              render: (v: string, r: any) => (
                <Space size={6}>
                  <a onClick={() => { setRefsOpen(false); openDetail(r.report_id) }}>{v}</a>
                  {r.finding_id ? <Tag color="blue">条目 #{r.finding_id}</Tag> : <Tag>报告级</Tag>}
                </Space>
              ),
            },
          ]}
        />
        {refs.length === 0 && <Empty style={{ marginTop: 20 }} description="暂无引用记录" />}
      </Modal>

      {/* 补录 */}
      <Modal
        open={editOpen} onCancel={() => setEditOpen(false)} title="补录 / 修正报告字段"
        onOk={saveEdit} okText="保存" cancelText="取消" width={720}
        destroyOnClose
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="自动识别难免有偏差，人工修正后系统会重算完整度与状态——这是数据可信的关键一步。" />
        <Form form={editForm} layout="vertical">
          <Row gutter={12}>
            <Col span={12}><Form.Item name="title" label="报告标题"><Input /></Form.Item></Col>
            <Col span={12}><Form.Item name="report_no" label="报告文号"><Input /></Form.Item></Col>
            <Col span={8}><Form.Item name="report_date" label="报告日期"><Input placeholder="YYYY-MM-DD" /></Form.Item></Col>
            <Col span={8}><Form.Item name="region" label="地区"><Input /></Form.Item></Col>
            <Col span={8}><Form.Item name="severity" label="严重程度">
              <Select options={['一般', '重要', '重大'].map((s) => ({ label: s, value: s }))} />
            </Form.Item></Col>
            <Col span={12}><Form.Item name="audited_org" label="被审计单位"><Input /></Form.Item></Col>
            <Col span={12}><Form.Item name="subject" label="被审计对象"><Input /></Form.Item></Col>
            <Col span={8}><Form.Item name="report_type" label="报告类型">
              <Select allowClear options={(meta?.report_types || []).map((t: string) => ({ label: t, value: t }))} />
            </Form.Item></Col>
            <Col span={8}><Form.Item name="audit_type" label="审计类型">
              <Select allowClear options={(meta?.audit_types || []).map((t: string) => ({ label: t, value: t }))} />
            </Form.Item></Col>
            <Col span={8}><Form.Item name="problem_category" label="问题类别">
              <Select allowClear options={(meta?.problem_categories || []).map((t: string) => ({ label: t, value: t }))} />
            </Form.Item></Col>
            <Col span={8}><Form.Item name="amount" label="涉及金额（万元）"><Input type="number" /></Form.Item></Col>
            <Col span={16}><Form.Item name="tags" label="标签（顿号或逗号分隔）"><Input /></Form.Item></Col>
            <Col span={24}><Form.Item name="findings" label="主要问题发现"><Input.TextArea rows={5} /></Form.Item></Col>
            <Col span={24}><Form.Item name="conclusion" label="处理意见"><Input.TextArea rows={3} /></Form.Item></Col>
            <Col span={12}><Form.Item name="status" label="状态">
              <Select options={['已入库', '待补录', '已复核'].map((s) => ({ label: s, value: s }))} />
            </Form.Item></Col>
          </Row>
        </Form>
      </Modal>

      {/* 批量删除确认（需账号密码） */}
      <Modal
        open={delOpen}
        onCancel={() => { setDelOpen(false); delForm.resetFields() }}
        title="批量删除历史报告"
        onOk={doBatchDelete}
        confirmLoading={deleting}
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        destroyOnClose
      >
        <Alert type="error" showIcon style={{ marginBottom: 12 }}
          message={`即将删除选中的 ${selectedIds.length} 份历史报告`}
          description="删除不可撤销（原件文件同时移除）。为防止误操作，需输入当前登录账号与密码确认。" />
        <Form form={delForm} layout="vertical">
          <Form.Item
            name="username" label="账号"
            rules={[{ required: true, message: '请输入账号' }]}
          >
            <Input placeholder="请输入当前登录账号" autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password" label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="请输入当前登录账号密码" autoComplete="current-password"
              onPressEnter={doBatchDelete} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 批量导入 */}
      <Modal
        open={importOpen} onCancel={() => setImportOpen(false)} title="批量导入历史报告" width={720}
        footer={<Button onClick={() => setImportOpen(false)}>关闭</Button>}
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="支持 Word(.docx) / PDF / TXT，可一次选择多个文件。"
          description="导入后系统自动识别报告类型、日期、地区、被审计单位、问题定性、涉及金额、处理意见等结构化字段；识别不全的会进入「待补录」队列，绝不静默丢字段。" />
        <Space style={{ marginBottom: 10 }}>
          <span>启用大模型补全缺失字段：</span>
          <Select size="small" style={{ width: 110 }} value={useLlm} onChange={setUseLlm}
            options={[{ label: '启用（更准）', value: true }, { label: '关闭（更快）', value: false }]} />
        </Space>
        <Upload.Dragger
          multiple
          accept=".docx,.pdf,.txt"
          fileList={fileList}
          showUploadList={false}
          disabled={importing}
          beforeUpload={() => false}
          onChange={(info) => {
            setFileList(info.fileList)
            doImport(info.fileList.map((f) => f.originFileObj as any).filter(Boolean))
          }}
        >
          <p className="ant-upload-drag-icon"><UploadOutlined /></p>
          <p className="ant-upload-text">点击或拖拽文件到此处批量导入</p>
          <p className="ant-upload-hint">单个文件不超过 30MB；重复文件自动识别并跳过</p>
        </Upload.Dragger>
        {importing && <div style={{ marginTop: 12 }}><Spin tip="正在解析与识别，请稍候（大模型补全较慢）…" /></div>}
        {importResult && (
          <div style={{ marginTop: 12 }}>
            <Divider orientation="left" plain>导入回执</Divider>
            <Space wrap>
              <Tag color="green">成功 {importResult.ok}</Tag>
              <Tag color="blue">重复 {importResult.duplicated}</Tag>
              <Tag color="red">失败 {importResult.failed}</Tag>
              <Tag color="orange">待补录 {importResult.need_review}</Tag>
              <Tag>批次 {importResult.batch_no}</Tag>
            </Space>
            <Table
              style={{ marginTop: 10 }} rowKey="file" size="small" pagination={false}
              dataSource={importResult.results || []}
              columns={[
                { title: '文件', dataIndex: 'file', ellipsis: true },
                {
                  title: '结果', dataIndex: 'status', width: 76,
                  render: (s: string) => <Tag color={s === '成功' ? 'green' : s === '重复' ? 'blue' : 'red'}>{s}</Tag>,
                },
                { title: '说明', dataIndex: 'note', ellipsis: true },
              ]}
            />
          </div>
        )}
      </Modal>
    </div>
  )
}
