import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
    environmentOptions: {
      jsdom: { url: "https://www.youtube.com/" },
    },
  },
});
