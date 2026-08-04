import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  // `tsconfig.json` sets `jsx: "preserve"` because Next does its own JSX
  // transform. Vite reads that too and would hand untransformed JSX to the
  // runtime, so the transform has to be re-enabled here — for tests only.
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    // `.tsx` as well as `.ts`. While this matched only `.ts`, no component or
    // hook test could execute at all — which is why every test in the suite
    // had ended up in `lib/`. The gap was structural, not a coverage choice.
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
