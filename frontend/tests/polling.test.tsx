import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useResource } from "../src/ui";
it("stops polling terminal results and cancels timers on unmount", async () => {
  vi.useFakeTimers();
  try {
    const load = vi
      .fn()
      .mockResolvedValueOnce({ state: "pending" })
      .mockResolvedValue({ state: "succeeded" });
    const hook = renderHook(() =>
      useResource<{ state: string }>(
        load,
        "operation",
        4000,
        (v) => v.state === "pending",
      ),
    );
    await act(async () => {});
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(load).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(load).toHaveBeenCalledTimes(2);
    hook.unmount();
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
