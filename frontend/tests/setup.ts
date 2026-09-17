import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

beforeEach(() => {
  localStorage.clear();
  // Component tests must never reach a running monitoring server.
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Unexpected network request in component test")));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
