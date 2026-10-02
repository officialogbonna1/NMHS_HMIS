import { describe, expect, it } from "vitest";
import { apiBaseUrl } from "./client.js";

describe("apiBaseUrl", () => {
  it("is /api on this origin when nothing is configured (the Vite proxy)", () => {
    expect(apiBaseUrl(undefined)).toBe("/api");
    expect(apiBaseUrl("")).toBe("/api");
    expect(apiBaseUrl("   ")).toBe("/api");
  });

  it("points at the separately hosted API when configured", () => {
    expect(apiBaseUrl("https://hmis-api.onrender.com")).toBe("https://hmis-api.onrender.com/api");
    expect(apiBaseUrl("https://hmis-api.onrender.com/")).toBe("https://hmis-api.onrender.com/api");
  });
});
