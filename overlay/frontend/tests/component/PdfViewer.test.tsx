// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.

import { act, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { PDF_DOCUMENT_OPTIONS } from "@/lib/pdfPreviewOptions.mjs";

const { documentMock } = vi.hoisted(() => ({
  documentMock: vi.fn<(props: Record<string, unknown>) => ReactNode>(() => null),
}));

vi.mock("react-pdf", () => ({
  Document: documentMock,
  Page: () => null,
  pdfjs: { GlobalWorkerOptions: {} },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { PdfViewer } from "@/components/common/PdfViewer";

describe("PdfViewer document resources", () => {
  it("keeps the cited page aligned when PDF placeholders change height", async () => {
    let resized = () => {};
    const observer = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) { resized = () => callback([], this); }
      observe() {} unobserve() {} disconnect() {}
    };
    documentMock.mockImplementation(({ children }) => <div>{children as ReactNode}</div>);
    try {
      const view = render(<PdfViewer url="blob:report" fileName="report.pdf" initialPage={5} />);
      const load = documentMock.mock.calls.at(-1)![0].onLoadSuccess as (pdf: unknown) => Promise<void>;
      await act(() => load({
        numPages: 8, getPage: async () => ({ getViewport: () => ({ width: 612, height: 792 }) }),
        getOutline: async () => [],
      }));
      const container = view.container.querySelector<HTMLDivElement>("#pdf-page-container")!;
      const target = container.querySelectorAll<HTMLDivElement>(".shadow-lg")[4];
      let top = 3000;
      vi.spyOn(target, "getBoundingClientRect").mockImplementation(() => ({top: top - container.scrollTop} as DOMRect));
      resized();
      await waitFor(() => expect(Math.abs(container.scrollTop - (top - 24))).toBeLessThanOrEqual(1));
      top = 3600;
      resized();
      await waitFor(() => expect(Math.abs(container.scrollTop - (top - 24))).toBeLessThanOrEqual(1));
    } finally {
      globalThis.ResizeObserver = observer;
      documentMock.mockImplementation(() => null);
    }
  });

  it("passes stable local font and CMap options to React-PDF across URL changes", () => {
    const view = render(
      <PdfViewer url="blob:chinese" fileName="chinese.pdf" />
    );
    expect(documentMock.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        file: "blob:chinese",
        options: PDF_DOCUMENT_OPTIONS,
      })
    );
    expect(PDF_DOCUMENT_OPTIONS.cMapUrl).toMatch(/\/pdfjs\/cmaps\/$/);
    expect(PDF_DOCUMENT_OPTIONS.cMapPacked).toBe(true);
    expect(PDF_DOCUMENT_OPTIONS.standardFontDataUrl).toMatch(
      /\/pdfjs\/standard_fonts\/$/
    );
    view.rerender(<PdfViewer url="blob:another" fileName="another.pdf" />);
    const props = documentMock.mock.calls.at(-1)?.[0];
    expect(props?.options).toBe(PDF_DOCUMENT_OPTIONS);
  });
});
