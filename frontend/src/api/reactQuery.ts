// Single import surface for React Query hooks used by later features
// (documents, chat, etc.) so they don't need to know about client.ts directly.
export { $api } from "./client"
