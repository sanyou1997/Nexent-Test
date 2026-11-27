import { Alert, Card } from 'antd'
import { FileImageOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import OcrPanel from '../components/OcrPanel'

export default function Ocr() {
  // api 仅用于类型一致性占位（OcrPanel 内部已自行调用）
  void api
  return (
    <>
      <PageHeader
        icon={<FileImageOutlined />}
        title="OCR 图片识别（原文对照）"
        subtitle="拍照 / 扫描材料 · 复用 Nexent 识别服务 · 不占大模型资源"
      />

      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="复用 Nexent 识别服务（Umi-OCR / MinerU，可在「识别服务」中切换）"
        description="纯本机离线识别，模型仅约 10MB 级，不调用任何大模型(LLM)资源；支持图片与扫描件 PDF，识别结果可『原文对照』逐行核验。" />

      <Card>
        <OcrPanel accept="all" />
      </Card>
    </>
  )
}
