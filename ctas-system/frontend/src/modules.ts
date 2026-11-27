// 各功能模块打磨进度（按路由键）。
// 用法：侧边栏导航、工作台能力卡片统一展示角标。
// 状态含义：
//   done      已完成（体验好，用户认可，可放心使用）
//   polishing 打磨中（基础可用，正在逐模块完善）

export type ModStatusKey = 'done' | 'polishing'

export interface ModStatus {
  label: string
  color: string
}

// 已完成：语音转录、OCR 图片识别（含手写增强）；其余模块统一为「打磨中」。
// 注：/assistant、/education 已从导航与工作台收起，此处保留状态便于将来重新开放。
export const MODULE_STATUS: Record<string, ModStatus> = {
  '/transcripts': { label: '已完成', color: 'green' },
  '/ocr': { label: '已完成', color: 'green' },

  // —— 打磨中：基础可用，正在逐模块完善 ——
  '/dashboard': { label: '打磨中', color: 'blue' },
  '/qa': { label: '打磨中', color: 'blue' },
  '/verify': { label: '打磨中', color: 'blue' },
  '/assistant': { label: '打磨中', color: 'blue' },
  '/education': { label: '打磨中', color: 'blue' },
  '/archives': { label: '打磨中', color: 'blue' },
  '/data-intake': { label: '已完成', color: 'green' },
  '/petitions': { label: '打磨中', color: 'blue' },
  '/cases': { label: '打磨中', color: 'blue' },
  '/trials': { label: '打磨中', color: 'blue' },
  '/conduct': { label: '打磨中', color: 'blue' },
  '/inspections': { label: '打磨中', color: 'blue' },
  '/documents': { label: '打磨中', color: 'blue' },
  '/bank': { label: '打磨中', color: 'blue' },
  '/supervise': { label: '打磨中', color: 'blue' },
  '/alerts': { label: '打磨中', color: 'blue' },
  '/clue': { label: '打磨中', color: 'blue' },
  '/integrity': { label: '打磨中', color: 'blue' },
  '/regulations': { label: '打磨中', color: 'blue' },
  '/audit': { label: '打磨中', color: 'blue' },
  '/users': { label: '打磨中', color: 'blue' },
}

/** 返回某路由对应的进度角标（无则不展示） */
export function modStatus(key: string): ModStatus | undefined {
  return MODULE_STATUS[key]
}
