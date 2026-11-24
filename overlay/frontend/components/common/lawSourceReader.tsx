// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.

"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslation } from "react-i18next";
import { Alert, Button, Spin } from "antd";
import { Download, Highlighter } from "lucide-react";
import { isLawObject, locateLawSource } from "@/lib/lawSource";
import { readLawSourceText, storageService } from "@/services/storageService";

export function LawSourceReader() {
  const params = useSearchParams();
  const key = params.get("object") || "";
  const cite = params.get("cite") || "";
  const { t } = useTranslation("common");
  const [source, setSource] = useState<{ key: string; text?: string; error?: boolean }>();
  const [attempt, setAttempt] = useState(0);
  const [downloadFailed, setDownloadFailed] = useState(false);

  useEffect(() => {
    if (!isLawObject(key)) return;
    const controller = new AbortController();
    setSource(undefined);
    readLawSourceText(key, controller.signal).then(text => {
      if (!controller.signal.aborted) setSource({ key, text });
    }).catch(() => {
      if (!controller.signal.aborted) setSource({ key, error: true });
    });
    return () => controller.abort();
  }, [key, attempt]);

  const text = source?.key === key ? source.text : undefined;
  const documentView = useMemo(() => text === undefined ? null : locateLawSource(text, cite), [text, cite]);
  const marked = useMemo(() => new Set(documentView ? [...documentView.highlights.values()].flat() : []), [documentView]);
  const jump = (line: number) => document.getElementById("law-line-" + line)?.scrollIntoView({ block: "center" });
  useEffect(() => {
    const first = documentView?.highlights.values().next().value?.[0];
    if (first !== undefined) jump(first);
  }, [documentView]);
  if (!isLawObject(key) || (source?.key === key && source?.error)) return (
    <section className="mx-auto max-w-2xl p-6" role="alert">
      <Alert type="warning" showIcon title={t("lawSource.unavailable")}
        action={isLawObject(key) && <Button onClick={() => setAttempt(n => n + 1)}>{t("lawSource.retry")}</Button>} />
    </section>
  );
  if (!documentView) return <div className="flex items-center justify-center gap-2 p-12" role="status"><Spin />{t("filePreview.loadingDocument")}</div>;
  const { lines, heading, version, effective, targets, highlights } = documentView;
  return (
    <section className="flex h-full min-h-0 flex-col bg-slate-50">
      <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-3 sm:px-8">
        <div className="mx-auto flex max-w-4xl items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">{heading || t("lawSource.open")}</h1>
            <p className="mt-1 text-sm text-slate-600">{[version, effective && t("lawSource.effective", { date: effective })].filter(Boolean).join(" · ")}</p>
          </div>
          <Button icon={<Download size={15} />} onClick={() => {
            setDownloadFailed(false);
            storageService.downloadFileWithAuth(key, (heading || "source") + (version ? "（" + version + "）" : "") + ".txt").catch(() => setDownloadFailed(true));
          }}>{t("filePreview.download")}</Button>
        </div>
        {targets.length > 0 && <div className="mx-auto mt-3 flex max-w-4xl flex-wrap items-center gap-2">
          <Highlighter size={16} aria-label={t("lawSource.citations")} />
          {targets.map((target, i) => <Button size="small" key={i} disabled={!highlights.has(i)}
            onClick={() => jump(highlights.get(i)![0])}>{target.label}</Button>)}
        </div>}
        {(targets.length === 0 || highlights.size !== targets.length) &&
          <p role="status" className="mx-auto mt-2 max-w-4xl text-sm text-slate-600">
            {t(targets.length ? "lawSource.notLocated" : "lawSource.fullText")}
          </p>}
        {downloadFailed && <p role="alert" className="mx-auto max-w-4xl text-sm">{t("lawSource.unavailable")}</p>}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-8">
        <article className="mx-auto max-w-4xl rounded-lg border border-slate-200 bg-white px-5 py-5 sm:px-10" aria-label={t("lawSource.fullDocument")}>
          {lines.map((line, i) => {
            if (!line.trim() || line.startsWith("法规定位：")) return null;
            return <p key={i} id={"law-line-" + i} className="my-2 whitespace-pre-wrap break-words text-base leading-8 text-slate-800">
              {marked.has(i) ? <mark className="rounded-sm bg-yellow-200 px-0.5 text-slate-950">{line}</mark> : line}
            </p>;
          })}
        </article>
      </div>
    </section>
  );
}
