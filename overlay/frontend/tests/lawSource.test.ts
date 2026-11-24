// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.

import { describe, expect, it } from "vitest";
import { isLawObject, locateLawSource } from "../lib/lawSource";

const source = [
  "甲条例", "版本：2023年修订", "施行日期：2024-01-01", "",
  "法规定位：《甲条例》；2023年修订；第九十七条", "第九十七条　收受方条款。",
  "第二款文字。", "法规定位：《甲条例》；2023年修订；第九十八条",
  "第九十八条　赠送方条款，提及第九十七条。", "（一）第一项。", "（二）第二项。",
].join("\r\n");

describe("legal source preview", () => {
  it("locates separate and combined citations within one source", () => {
    expect(locateLawSource(source, "《甲条例》（2023年修订）第97条").highlights.get(0)).toEqual([5,6]);
    const view = locateLawSource(source, "甲条例第九十七条、第九十八条");
    expect(view.highlights.get(1)).toEqual([8,9,10]);
  });
  it("narrows the highlight to a cited paragraph or item", () => {
    expect(locateLawSource(source, "第97条第二款").highlights.get(0)).toEqual([6]);
    expect(locateLawSource(source, "第98条第（二）项").highlights.get(0)).toEqual([10]);
  });
  it("leaves ambiguous, mismatched and absent targets unmarked", () => {
    for (const label of ["第200条","第97条第四款","《乙办法》第97条","甲条例（2018年修订）第97条","原件"])
      expect(locateLawSource(source,label).highlights.size).toBe(0);
    expect(locateLawSource(source+"\n第九十七条 另一个版本","第97条").highlights.size).toBe(0);
  });
  it("only accepts local text object keys", () => {
    expect(isLawObject("attachments/user/code.txt")).toBe(true);
    for (const key of ["https://example.org/source.txt","attachments/../code.txt","attachments/user/code.pdf"])
      expect(isLawObject(key)).toBe(false);
  });
});
