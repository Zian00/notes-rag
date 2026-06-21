import js from "@eslint/js"
import globals from "globals"
import reactHooks from "eslint-plugin-react-hooks"
import reactRefresh from "eslint-plugin-react-refresh"
import jsxA11y from "eslint-plugin-jsx-a11y"
import tseslint from "typescript-eslint"
import prettierConfig from "eslint-config-prettier"
import { defineConfig, globalIgnores } from "eslint/config"

export default defineConfig([
  // Ignore build output, generated API types, and shadcn generated UI primitives
  globalIgnores(["dist", "src/api/schema.ts", "src/components/ui"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      // Disable ESLint formatting rules that would conflict with Prettier
      prettierConfig,
    ],
    plugins: {
      // Accessibility linting for JSX elements
      "jsx-a11y": jsxA11y,
    },
    rules: {
      // Enforce basic accessibility rules (warn level so they don't block CI)
      ...jsxA11y.configs.recommended.rules,
    },
    languageOptions: {
      globals: globals.browser,
    },
  },
])
