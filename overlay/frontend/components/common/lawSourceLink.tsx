// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.

"use client";

import { Children, isValidElement, type AnchorHTMLAttributes, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { withBasePath } from "@/lib/basePath";
import { isLawObject } from "@/lib/lawSource";
import { extractObjectNameFromUrl, getLocalFileDownloadUrl } from "@/services/storageService";

function linkText(children: ReactNode): string {
  return Children.toArray(children).map(child =>
    isValidElement<{ children?: ReactNode }>(child) ? linkText(child.props.children) :
    typeof child === "string" || typeof child === "number" ? String(child) : ""
  ).join("");
}

export function LawSourceLink({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const { t, i18n } = useTranslation("common");
  const label = linkText(children);
  const key = href?.startsWith("s3://") ? extractObjectNameFromUrl(href) : null;
  if (key && isLawObject(key) && /原件|原文|法规|条例|规定|规则|第[〇零一二三四五六七八九十百千0-9]+条/.test(label)) {
    const params = new URLSearchParams({ object: key, cite: label });
    const locale = i18n.language?.startsWith("en") ? "en" : "zh";
    return <a {...props} href={withBasePath("/" + locale + "/law-source?" + params.toString())}
      target="_blank" rel="noopener noreferrer" title={t("lawSource.open")}>{children}</a>;
  }
  return <a {...props} href={getLocalFileDownloadUrl(href) || href}>{children}</a>;
}
