// brand/ has no dependencies of its own; lint borrows mobile-app's ESLint (same version the rest of the
// JavaScript in this repository uses). Run: pnpm lint (from brand/).
import js from "../mobile-app/node_modules/@eslint/js/src/index.js";
import globals from "../mobile-app/node_modules/globals/index.js";

export default [
  { ignores: ["exports/**"] },
  js.configs.recommended,
  {
    files: ["**/*.mjs"],
    languageOptions: { ecmaVersion: 2022, sourceType: "module", globals: { ...globals.node } },
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-console": ["error", { allow: ["warn", "error", "info"] }],
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
];
