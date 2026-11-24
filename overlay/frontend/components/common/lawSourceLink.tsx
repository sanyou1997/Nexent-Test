// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.

"use client";

import dynamic from "next/dynamic";
import { Children, useState, isValidElement, type AnchorHTMLAttributes, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { withBasePath } from "@/lib/basePath";
import { isLawObject } from "@/lib/lawSource";
import { extractObjectNameFromUrl, getLocalFileDownloadUrl, storageService } from "@/services/storageService";

const Preview = dynamic(() => import("./filePreviewDrawer").then(m => m.FilePreviewDrawer), { ssr: false });

function linkText(children: ReactNode): string {
  return Children.toArray(children).map(child =>
    isValidElement<{ children?: ReactNode }>(child) ? linkText(child.props.children) :
    typeof child === "string" || typeof child === "number" ? String(child) : ""
  ).join("");
}

export function LawSourceLink({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const { t, i18n } = useTranslation("common");
  const label = linkText(children);
  const [open, setOpen] = useState(false);
  if (href?.startsWith("#report-source?")) {
    const params = new URLSearchParams(href.slice("#report-source?".length));
    const object = params.get("object") || "";
    const name = params.get("name") || "";
    const page = Number(params.get("page"));
    if (!/^attachments\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_.-]+\.(pdf|docx|txt|md)$/i.test(object)
      || !name || name.length > 255) return <>{children}</>;
    const initialPage = Number.isSafeInteger(page) && page >= 1 ? page : undefined;
    const url = storageService.getPreviewUrl(object, name) + (initialPage ? "#page=" + initialPage : "");
    return <><a {...props} href={url} onClick={event => {
      if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        setOpen(true);
      }
    }}>{children}</a>
      {open && <Preview open onClose={() => setOpen(false)} objectName={object}
        fileName={name} initialPage={initialPage} excerpt={label} />}</>;
  }
  if (href === "#submitted-report") return <>{children}</>;
  const key = href?.startsWith("s3://") ? extractObjectNameFromUrl(href) : null;
  if (key && isLawObject(key) && /原件|原文|法规|条例|规定|规则|第[〇零一二三四五六七八九十百千0-9]+条/.test(label)) {
    const params = new URLSearchParams({ object: key, cite: label });
    const locale = i18n.language?.startsWith("en") ? "en" : "zh";
    return <a {...props} href={withBasePath("/" + locale + "/law-source?" + params.toString())}
      target="_blank" rel="noopener noreferrer" title={t("lawSource.open")}>{children}</a>;
  }
  return <a {...props} href={getLocalFileDownloadUrl(href) || href}>{children}</a>;
}
