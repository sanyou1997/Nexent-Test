// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Modal, Select, Switch } from "antd";
import {
  AudioLines,
  CheckCircle2,
  CircleAlert,
  FileScan,
  Files,
  LoaderCircle,
  Plug,
  Play,
} from "lucide-react";
import { fetchWithAuth, getAuthHeaders } from "@/lib/auth";
import { API_BASE_URL } from "@/services/api";

type Kind = "pdf" | "audio";
const providers = {
  pdf: [
    { id: "mineu", name: "MineU", en: "MineU" },
    {
      id: "paddle_ocr",
      name: "PaddleOCR · 通用文字识别",
      en: "PaddleOCR · Text recognition",
    },
    {
      id: "paddle_layout",
      name: "PP-StructureV3 · 版面与表格",
      en: "PP-StructureV3 · Layout and tables",
    },
    {
      id: "umi_ocr",
      name: "Umi-OCR · 离线文字识别",
      en: "Umi-OCR · Offline OCR",
    },
  ],
  audio: [
    { id: "paraformer", name: "Paraformer", en: "Paraformer" },
    { id: "qwen_asr", name: "Qwen3-ASR", en: "Qwen3-ASR" },
  ],
};
type Catalog = {
  active: Record<Kind, string>;
  urls: Record<string, string>;
  allowed_origins: string[];
  qwen_model: string;
  qwen_origins: string[];
};
type Segment = { start: number; end: number; text: string };
type PdfSettings = {
  url: string;
  language: string;
  mode: string;
  tables: boolean;
};
type Result = {
  status: string;
  pages?: string[];
  segments?: Segment[];
  transcript?: string;
  duration?: number;
  message?: string;
};
const pdfDefault: PdfSettings = {
  url: "http://host.docker.internal:18001",
  language: "ch",
  mode: "auto",
  tables: true,
};
const empty: Result = { status: "waiting" };
const audioSuffix =
  /\.(wav|mp3|m4a|aac|flac|ogg|oga|opus|amr|wma|aif|aiff|caf|weba)$/i;
export function isAudioAttachment(name: string, mime?: string) {
  return (
    audioSuffix.test(name) || Boolean(mime?.toLowerCase().startsWith("audio/"))
  );
}
function stamp(s: number) {
  const n = Math.round(s * 100);
  return (
    [
      Math.floor(n / 360000),
      Math.floor(n / 6000) % 60,
      Math.floor(n / 100) % 60,
    ]
      .map((v) => String(v).padStart(2, "0"))
      .join(":") +
    "." +
    String(n % 100).padStart(2, "0")
  );
}
function useLabel() {
  const { i18n } = useTranslation();
  return (zh: string, en: string) =>
    i18n.language?.startsWith("en") ? en : zh;
}
async function request(path: string, method = "GET", body?: unknown) {
  const response = await fetchWithAuth(
    API_BASE_URL + "/document_parsers/" + path,
    {
      method,
      headers: getAuthHeaders(),
      cache: "no-store",
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }
  );
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "请求失败 (" + response.status + ")"
    );
  return data;
}
function Segments({ items }: { items: Segment[] }) {
  return (
    <ol className="max-h-72 divide-y overflow-auto rounded-lg border bg-background px-4">
      {items.map((s, i) => (
        <li key={i} className="grid gap-1.5 py-3 text-sm">
          <span className="font-mono text-xs tabular-nums text-teal-700 dark:text-teal-300">
            {stamp(s.start)} – {stamp(s.end)}
          </span>
          <p className="m-0 whitespace-pre-wrap break-words leading-6">
            {s.text}
          </p>
        </li>
      ))}
    </ol>
  );
}
export function ParserSettingsButton() {
  const t = useLabel();
  const [open, setOpen] = useState(false),
    [kind, select] = useState<Kind>("pdf");
  const [pdf, setPdf] = useState<PdfSettings>(pdfDefault),
    [audio, setAudio] = useState({ url: "http://host.docker.internal:18095" });
  const [busy, setBusy] = useState(""),
    [ready, setReady] = useState(false),
    [note, setNote] = useState(""),
    [bad, setBad] = useState(false);
  const [preview, setPreview] = useState<Result>(empty);
  const defaults = { pdf: "mineu", audio: "paraformer" };
  const [catalog, setCatalog] = useState<Catalog>({
    active: defaults,
    urls: {},
    allowed_origins: [],
    qwen_model: "Qwen/Qwen3-ASR-0.6B",
    qwen_origins: [],
  });
  const [choice, setChoice] = useState(defaults);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const vendor =
    providers[kind].find((item) => item.id === choice[kind]) ||
    providers[kind][0];
  const otherOcr = kind === "pdf" && vendor.id !== "mineu";
  const qwen = vendor.id === "qwen_asr";
  const [model, setModel] = useState("Qwen/Qwen3-ASR-0.6B");
  const isAudio = kind === "audio";
  const clear = () => {
    setNote("");
    setBad(false);
    setPreview(empty);
  };
  const act = async (action: string) => {
    clear();
    setBusy(action);
    try {
      if (action === "load") {
        setOpen(true);
        setReady(false);
        const [p, a, catalog] = await Promise.all([
          request("settings"),
          request("asr/settings"),
          request("providers"),
        ]);
        setCatalog(catalog);
        setChoice(catalog.active);
        setUrls(catalog.urls);
        setModel(catalog.qwen_model);
        setPdf(p);
        setAudio(a);
        setReady(true);
      } else {
        const local = qwen
          ? { url: urls.qwen_asr, model }
          : isAudio
            ? audio
            : otherOcr
              ? { url: urls[vendor.id] }
              : pdf;
        const result =
          action === "save" || otherOcr || qwen
            ? await request(
                "providers/" + kind + (action === "save" ? "" : "/sample"),
                action === "save" ? "PUT" : "POST",
                { provider: vendor.id, local }
              )
            : await request((isAudio ? "asr/" : "") + action, "POST", local);
        if (action === "save") {
          setCatalog(result);

          setNote(
            t(
              "已保存。此租户后续解析将使用所选服务。",
              "Saved for subsequent files in this tenant."
            )
          );
        }
        if (action === "test_connection")
          setNote(
            t("连接正常", "Connected") +
              " · " +
              (isAudio ? result.runtime : "MineU " + result.version)
          );
        if (action === "sample") {
          setPreview(result);
          setBad(!result.verified);
          setNote(
            result.verified
              ? isAudio
                ? t("试转写通过", "Transcription verified")
                : t("试解析通过", "PDF verified")
              : t("请复核样本结果", "Check the sample result")
          );
        }
      }
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
      setBad(true);
    } finally {
      setBusy("");
    }
  };
  const disabled = !ready || Boolean(busy);
  return (
    <>
      <Button icon={<Files size={15} />} onClick={() => void act("load")}>
        {t("文件解析", "File parsing")}
      </Button>
      <Modal
        open={open}
        width={620}
        title={t("文件解析", "File parsing")}
        onCancel={() => {
          if (!busy) {
            setOpen(false);
          }
        }}
        footer={null}
        maskClosable={!busy}
      >
        <div
          className="grid grid-cols-2 gap-3 pb-5 pt-3"
          role="group"
          aria-label={t("文件类型", "File type")}
        >
          {(["pdf", "audio"] as Kind[]).map((k) => {
            const current = k === kind,
              Icon = k === "audio" ? AudioLines : FileScan;
            return (
              <button
                type="button"
                key={k}
                aria-pressed={current}
                disabled={Boolean(busy)}
                onClick={() => {
                  select(k);
                  clear();
                }}
                className={
                  "flex min-w-0 items-center gap-3 rounded-xl border p-3.5 text-left transition-colors focus-visible:outline-2 " +
                  (current
                    ? "border-primary bg-primary/5"
                    : "border-border bg-background hover:bg-muted/50")
                }
              >
                <span
                  className={
                    "grid size-9 shrink-0 place-items-center rounded-lg " +
                    (k === "audio"
                      ? "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300"
                      : "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300")
                  }
                >
                  <Icon size={19} />
                </span>
                <span className="min-w-0">
                  <strong className="block text-sm">
                    {k === "audio"
                      ? t("音频转写", "Audio transcription")
                      : t("PDF 文档", "PDF documents")}
                  </strong>
                  <span className="block text-xs text-muted-foreground">
                    {providers[k]
                      .map((p) =>
                        p.id === catalog.active[k]
                          ? t(p.name, p.en).split(" · ")[0]
                          : ""
                      )
                      .join("")}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
        <div className="grid gap-4 border-y py-5">
          <label className="grid gap-1.5 text-sm font-medium">
            {t("本租户的本地服务", "Local service for this tenant")}
            <Select
              disabled={disabled}
              value={choice[kind]}
              onChange={(id) => {
                setChoice({ ...choice, [kind]: id });

                clear();
              }}
              options={providers[kind].map((p) => ({
                value: p.id,
                label: t(p.name, p.en),
              }))}
            />
          </label>
          <>
            <label className="grid gap-1.5 text-sm font-medium">
              {t("服务地址", "Service address")}
              <Input
                disabled={disabled}
                value={
                  qwen
                    ? urls.qwen_asr || ""
                    : isAudio
                      ? audio.url
                      : otherOcr
                        ? urls[vendor.id] || ""
                        : pdf.url
                }
                onChange={(e) => {
                  clear();
                  otherOcr || qwen
                    ? setUrls({ ...urls, [vendor.id]: e.target.value })
                    : isAudio
                      ? setAudio({ url: e.target.value })
                      : setPdf({ ...pdf, url: e.target.value });
                }}
              />
            </label>
            {qwen ? (
              <>
                <label className="grid gap-1.5 text-sm font-medium">
                  {t("服务中的模型名", "Served model name")}
                  <Input
                    value={model}
                    disabled={disabled}
                    onChange={(e) => {
                      setModel(e.target.value);
                      clear();
                    }}
                  />
                </label>
                <div className="grid gap-1 rounded-lg bg-muted/55 px-3 py-2.5 text-xs leading-5 text-muted-foreground">
                  <span>
                    {t(
                      "本地录音识别 · 多语言与中文方言",
                      "Local transcription · Multilingual speech and Chinese dialects"
                    )}
                  </span>
                  <span>
                    {t(
                      "平台调用 /v1/audio/transcriptions，保留服务返回的语音段时间。",
                      "Uses /v1/audio/transcriptions and retains timestamps returned by the service."
                    )}
                  </span>
                  <span>
                    {t("可用地址", "Available origins")} ·{" "}
                    {catalog.qwen_origins.join("、")}
                  </span>
                </div>
              </>
            ) : otherOcr ? (
              <div className="grid gap-1.5 rounded-lg bg-muted/55 px-3 py-2.5 text-xs leading-5 text-muted-foreground">
                <span>
                  {vendor.id === "paddle_layout"
                    ? t(
                        "按页提取版面、正文及表格。",
                        "Extracts layout, text and tables by page."
                      )
                    : t(
                        "按原件页码提取文字，空白页保留原位置。",
                        "Extracts page text and preserves blank pages."
                      )}{" "}
                  {t("接口", "Endpoint")}{" "}
                  <code>
                    {vendor.id === "umi_ocr"
                      ? "/api/ocr"
                      : vendor.id === "paddle_layout"
                        ? "/layout-parsing"
                        : "/ocr"}
                  </code>
                </span>
                <span>
                  {t(
                    "填写服务根地址，平台自动补全接口路径。可用地址",
                    "Enter the service origin. Available origins"
                  )}{" "}
                  · {catalog.allowed_origins.join("、")}
                </span>
              </div>
            ) : isAudio ? (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/55 px-3 py-2.5 text-xs text-muted-foreground">
                <span>
                  {t("普通话 · 分段时间戳", "Mandarin · Segment timestamps")}
                </span>
                <span>Paraformer · FSMN-VAD · CT-Punc</span>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <label className="grid gap-1.5 text-sm">
                    {t("识别语言", "Language")}
                    <Select
                      disabled={disabled}
                      value={pdf.language}
                      onChange={(language) => {
                        clear();
                        setPdf({ ...pdf, language });
                      }}
                      options={[
                        ["ch", t("中文及英文", "Chinese and English")],
                        ["ch_server", "中文（Server）"],
                        ["latin", "Latin"],
                        ["korean", "한국어"],
                        ["cyrillic", "Cyrillic"],
                        ["arabic", "العربية"],
                      ].map(([value, label]) => ({ value, label }))}
                    />
                  </label>
                  <label className="grid gap-1.5 text-sm">
                    {t("解析模式", "Mode")}
                    <Select
                      disabled={disabled}
                      value={pdf.mode}
                      onChange={(mode) => {
                        clear();
                        setPdf({ ...pdf, mode });
                      }}
                      options={[
                        { value: "auto", label: t("自动识别", "Automatic") },
                        { value: "ocr", label: t("强制 OCR", "Force OCR") },
                        { value: "txt", label: t("文字层", "Text layer") },
                      ]}
                    />
                  </label>
                </div>
                <label className="flex items-center justify-between text-sm">
                  {t("识别表格", "Recognize tables")}
                  <Switch
                    disabled={disabled}
                    checked={pdf.tables}
                    onChange={(tables) => {
                      clear();
                      setPdf({ ...pdf, tables });
                    }}
                  />
                </label>
              </>
            )}
          </>
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-4">
          {!otherOcr && !qwen && (
            <Button
              icon={<Plug size={14} />}
              loading={busy === "test_connection"}
              disabled={disabled}
              onClick={() => void act("test_connection")}
            >
              {t("测试连接", "Test connection")}
            </Button>
          )}
          <Button
            icon={<Play size={14} />}
            loading={busy === "sample"}
            disabled={disabled}
            onClick={() => void act("sample")}
          >
            {isAudio
              ? t("试转写", "Try transcription")
              : t("试解析", "Parse sample")}
          </Button>
          <Button
            className="ml-auto"
            type="primary"
            loading={busy === "save"}
            disabled={disabled}
            onClick={() => void act("save")}
          >
            {t("保存设置", "Save settings")}
          </Button>
        </div>
        {(note || busy === "load") && (
          <div
            role="status"
            className={
              "mt-3 flex items-center gap-2 text-sm " +
              (bad ? "text-destructive" : "text-muted-foreground")
            }
          >
            {busy === "load" ? (
              <LoaderCircle size={15} className="animate-spin" />
            ) : bad ? (
              <CircleAlert size={15} />
            ) : (
              <CheckCircle2 size={15} />
            )}{" "}
            {note || t("正在读取配置…", "Loading…")}
          </div>
        )}
        {preview.transcript && (
          <p className="mt-3 max-h-60 overflow-auto whitespace-pre-wrap text-sm">
            {t(
              "转写正文 · 服务未提供时间戳",
              "Transcript · No timestamps supplied"
            )}
            <br />
            {preview.transcript}
          </p>
        )}
        {preview.segments && preview.segments.length > 0 && (
          <div className="mt-3">
            <Segments items={preview.segments} />
          </div>
        )}
        {preview.pages?.length ? (
          <div className="mt-3 max-h-60 overflow-auto rounded-lg border px-3">
            {preview.pages.map((p, i) => (
              <div className="py-2" key={i}>
                <strong className="text-xs text-muted-foreground">
                  PDF · {i + 1}
                </strong>
                <p className="m-0 whitespace-pre-wrap text-sm">{p}</p>
              </div>
            ))}
          </div>
        ) : null}
      </Modal>
    </>
  );
}
export function AgentParserSwitch({ agentId }: { agentId: number }) {
  const t = useLabel();
  const [flags, setFlags] = useState<{
      enabled: boolean;
      audio_enabled: boolean;
    } | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setFlags(null);
    setError("");
    if (agentId > 0)
      request("agents/" + agentId)
        .then((s) => {
          if (alive) setFlags(s);
        })
        .catch(() => {});
    return () => {
      alive = false;
    };
  }, [agentId]);
  if (!flags) return null;
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 border-b bg-background px-4 py-2 text-sm">
      <span className="inline-flex items-center gap-2 font-medium">
        <Files size={16} />
        {t("文件解析", "File parsing")}
      </span>
      {(["enabled", "audio_enabled"] as const).map((key) => (
        <label
          key={key}
          className="inline-flex items-center gap-2 text-muted-foreground"
        >
          {key === "enabled"
            ? t("PDF 文档", "PDF documents")
            : t("音频转写", "Audio transcription")}
          <Switch
            size="small"
            disabled={busy}
            checked={flags[key]}
            onChange={async (value) => {
              setBusy(true);
              setError("");
              try {
                setFlags(
                  await request("agents/" + agentId, "PUT", { [key]: value })
                );
              } catch (e) {
                setError(String(e));
              } finally {
                setBusy(false);
              }
            }}
          />
        </label>
      ))}
      <span role="status">{error}</span>
    </div>
  );
}
export function FileDetails({
  objectName,
  audio = false,
}: {
  objectName: string;
  audio?: boolean;
}) {
  const t = useLabel();
  const [open, show] = useState(false),
    [result, setResult] = useState<Result>(empty),
    [page, selectPage] = useState(1),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const data = await request("attachment", "POST", {
          object_name: objectName,
        });
        if (!alive) return;
        setResult(data);
        setError("");
        if (
          ["waiting", "processing", "queued", "running"].includes(data.status)
        )
          timer = setTimeout(read, 2500);
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    void read();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [open, objectName]);
  const pages = result.pages || [],
    segments = result.segments || [],
    missing = pages.flatMap((p, i) => (p ? [] : [i + 1]));
  const state: Record<string, string> = {
    waiting: t(
      "尚无结果，请确认已发送附件并启用相应解析。",
      "Send the attachment with parsing enabled."
    ),
    processing: t("正在解析…", "Parsing…"),
    queued: t("录音已排队…", "Queued…"),
    running: t("正在转写录音…", "Transcribing…"),
    error:
      result.message || t("解析失败，请检查服务后重试。", "Parsing failed."),
  };
  return (
    <>
      <Button
        size="small"
        type="link"
        onClick={() => {
          selectPage(1);
          setResult(empty);
          show(true);
        }}
      >
        {audio ? t("转写详情", "Transcript") : t("解析详情", "Parsed pages")}
      </Button>
      <Modal
        width={620}
        open={open}
        onCancel={() => show(false)}
        title={
          audio
            ? t("录音转写", "Audio transcript")
            : t("PDF 解析详情", "PDF parsing details")
        }
        footer={null}
      >
        <p role="status" className="my-3 text-sm text-muted-foreground">
          {result.status === "done"
            ? audio
              ? result.transcript
                ? t(
                    "转写正文 · 服务未提供分段时间戳",
                    "Transcript · No segment timestamps supplied"
                  )
                : result.duration == null
                  ? t("录音转写", "Audio transcript")
                  : t(
                      `录音时长 ${stamp(result.duration || 0)} · ${segments.length} 个语音片段`,
                      `${stamp(result.duration || 0)} · ${segments.length} segments`
                    )
              : t(
                  `共 ${pages.length} 页，已提取 ${pages.length - missing.length} 页`,
                  `${pages.length - missing.length} of ${pages.length} pages extracted`
                )
            : state[result.status]}
        </p>
        {audio &&
          result.status === "done" &&
          (segments.length ? (
            <Segments items={segments} />
          ) : result.transcript ? (
            <p className="whitespace-pre-wrap">{result.transcript}</p>
          ) : (
            <p>
              {t(
                "未识别到语音，请回听原件。",
                "No speech detected. Check the recording."
              )}
            </p>
          ))}
        {missing.length > 0 && (
          <p>
            {t("待核页", "Pages to verify")} · {missing.join(", ")}
          </p>
        )}
        {!audio && pages.length > 0 && (
          <>
            <div className="my-3 flex gap-2">
              <Select
                value={page}
                onChange={selectPage}
                options={pages.map((_, i) => ({
                  value: i + 1,
                  label: "PDF · " + (i + 1),
                }))}
              />
              <Button
                loading={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    setResult(
                      await request("retry_page", "POST", {
                        object_name: objectName,
                        page,
                      })
                    );
                  } catch (e) {
                    setError(String(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {t("本页重新 OCR", "Retry page OCR")}
              </Button>
            </div>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap">
              {pages[page - 1] ||
                t("未提取到文字，待人工核对。", "No text extracted.")}
            </pre>
          </>
        )}
        {error && <p role="alert">{error}</p>}
      </Modal>
    </>
  );
}
