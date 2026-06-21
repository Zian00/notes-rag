import "@testing-library/jest-dom"
import { server } from "./msw/server"

// MSW v2 lifecycle hooks — run before/after every test to ensure
// request handlers are reset between tests and the mock server is torn down cleanly.
beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
