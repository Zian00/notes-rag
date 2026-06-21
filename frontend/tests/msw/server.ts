import { setupServer } from "msw/node"
import { handlers } from "./handlers"

// MSW v2: create a Node.js-compatible mock server for unit/integration tests.
// The server intercepts fetch calls so tests never hit the real backend.
const server = setupServer(...handlers)

export { server }
