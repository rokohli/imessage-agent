import { afterEach, expect, it, vi } from "vitest";
import { request } from "./api";
afterEach(() => vi.unstubAllGlobals());
it("handles unreachable services", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
  await expect(request("/summary")).rejects.toThrow(
    "Cannot reach the local backend",
  );
});
it("handles sanitized API errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ error: "invalid_input" }),
      }),
  );
  await expect(request("/summary")).rejects.toThrow("invalid input");
});
it("gets a session token and sends JSON for writes", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ token: "synthetic-token" }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });
  vi.stubGlobal("fetch", fetch);
  await request("/settings", "PUT", { timezone: "UTC" });
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/settings",
    expect.objectContaining({
      headers: {
        "Content-Type": "application/json",
        "X-Dashboard-Token": "synthetic-token",
      },
      body: '{"timezone":"UTC"}',
    }),
  );
});
