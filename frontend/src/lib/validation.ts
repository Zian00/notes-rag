// Basic client-side shape check only — the backend is the source of truth for
// what counts as a valid, deliverable email address.
export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
// Mirrors the backend's minimum; the server still re-validates and is authoritative.
export const MIN_PASSWORD_LENGTH = 8
