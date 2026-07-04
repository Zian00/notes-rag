import "@testing-library/jest-dom"
import { server } from "./msw/server"

// jsdom doesn't implement window.matchMedia. next-themes (used by the shadcn
// Toaster in App.tsx) calls it on mount to detect the OS color scheme, which
// throws under jsdom without this polyfill. Stubbed as "no query ever matches"
// since tests don't assert on theme behavior.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}

// MSW v2 lifecycle. `listen()` runs at module scope (not inside `beforeAll`)
// because Vitest's setupFiles are evaluated before a test file's own
// top-level imports run. Modules like src/api/client.ts call
// `createFetchClient()` at import time, which captures `globalThis.fetch`
// once as its internal fetch implementation — if that capture happens
// before MSW patches the global, the client's requests bypass MSW entirely
// and hit the real network instead of the mock handlers.
server.listen()
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
