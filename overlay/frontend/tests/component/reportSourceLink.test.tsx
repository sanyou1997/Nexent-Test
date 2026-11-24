// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.

import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { LawSourceLink } from "@/components/common/lawSourceLink";

vi.mock("next/dynamic", () => ({
  default: () => ({ initialPage, excerpt }: { initialPage: number; excerpt: string }) => (
    <div role="dialog">{initialPage} · {excerpt}</div>
  ),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (s: string) => s, i18n: { language: "zh" } }) }));
vi.mock("@/services/storageService", () => ({
  storageService: { getPreviewUrl: (obj: string) => "/preview/" + obj },
  getLocalFileDownloadUrl: () => undefined,
  extractObjectNameFromUrl: () => null,
}));

it("opens the referenced page without leaving the opinion", () => {
  render(<LawSourceLink href="#report-source?object=attachments%2Fowner%2Fr.pdf&name=report.pdf&page=6">原拟处理意见</LawSourceLink>);
  const link = screen.getByRole("link", {name: "原拟处理意见"});
  expect(link).toHaveAttribute("href", "/preview/attachments/owner/r.pdf#page=6");
  fireEvent.click(link);
  expect(screen.getByRole("dialog")).toHaveTextContent("6 · 原拟处理意见");
});

it("renders only text for an invalid attachment key", () => {
  render(<LawSourceLink href="#report-source?object=../other.pdf&name=r.pdf&page=2">原句</LawSourceLink>);
  expect(screen.getByText("原句")).toBeVisible();
  expect(screen.queryByRole("link")).toBeNull();
});
