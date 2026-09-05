import { describe, expect, it } from "vitest";
import { isClosed, opTone, workStatus } from "../src/ds/status";
import { scaleValues } from "../src/ds/review";
import { dayLong, dayNum, dayShort, days, num, outOf } from "../src/ds/format";

describe("workStatus", () => {
  it("maps the backend work statuses onto the seven homework statuses", () => {
    expect(workStatus("draft")).toEqual({ tone: "draft", label: "Черновик" });
    expect(workStatus("pending_review")).toEqual({
      tone: "sent",
      label: "Сдана",
    });
    expect(workStatus("queued")).toEqual({ tone: "sent", label: "Сдана" });
    expect(workStatus("in_review")).toEqual({
      tone: "review",
      label: "На ревью",
    });
    expect(workStatus("ready_to_publish")).toEqual({
      tone: "review",
      label: "На ревью",
    });
    expect(workStatus("needs_changes")).toEqual({
      tone: "fix",
      label: "Нужны правки",
    });
    expect(workStatus("passed")).toEqual({ tone: "pass", label: "Зачтена" });
    expect(workStatus("failed")).toEqual({ tone: "fail", label: "Не зачтена" });
  });
  it("derives «На повторном ревью» from the attempt number", () => {
    expect(workStatus("pending_review", 2)?.tone).toBe("rereview");
    expect(workStatus("in_review", 3)?.tone).toBe("rereview");
    expect(workStatus("needs_changes", 2)?.tone).toBe("fix");
    expect(workStatus("passed", 2)?.tone).toBe("pass");
  });
  it("resolves a published review through its decision", () => {
    expect(workStatus("published", 1, "passed")?.tone).toBe("pass");
    expect(workStatus("published", 1, null)).toBeNull();
    expect(workStatus("canceled")).toBeNull();
  });
  it("knows closed works and operation tones", () => {
    expect(isClosed("passed")).toBe(true);
    expect(isClosed("in_review")).toBe(false);
    expect(opTone("succeeded")).toBe("ok");
    expect(opTone("retryable_failed")).toBe("bad");
    expect(opTone("unknown_outcome")).toBe("late");
    expect(opTone("running")).toBe("busy");
  });
});

describe("scaleValues", () => {
  it("builds the value row from max points and step", () => {
    expect(scaleValues(1, 0.5)).toEqual([0, 0.5, 1]);
    expect(scaleValues(0.5, 0.5)).toEqual([0, 0.5]);
    expect(scaleValues(2, 1)).toEqual([0, 1, 2]);
  });
  it("collapses very long scales and keeps the maximum", () => {
    expect(scaleValues(10, 0.5)).toEqual([0, 0.5, 10]);
    expect(scaleValues(1, 0.3)).toEqual([0, 0.3, 0.6, 0.9, 1]);
  });
  it("rejects invalid input", () => {
    expect(scaleValues(-1, 0.5)).toEqual([]);
    expect(scaleValues(1, 0)).toEqual([]);
  });
});

describe("format", () => {
  const at = new Date(2026, 1, 18, 21, 40);
  it("prints dates the way the reference screens do", () => {
    expect(dayShort(at)).toBe("18 фев");
    expect(dayLong(at)).toBe("18 февраля, 21:40");
    expect(dayNum(at)).toBe("18.02, 21:40");
    expect(dayShort(null)).toBe("—");
  });
  it("prints numbers with a comma and without trailing zeros", () => {
    expect(num(4.5)).toBe("4,5");
    expect(num(6)).toBe("6");
    expect(outOf(3.5, 6)).toBe("3,5 из 6");
    expect(days(1)).toBe("1 день");
    expect(days(2)).toBe("2 дня");
    expect(days(6)).toBe("6 дней");
    expect(days(21)).toBe("21 день");
  });
});
