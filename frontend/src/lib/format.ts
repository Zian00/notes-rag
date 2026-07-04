// Pure, unit-testable formatting helpers for document metadata display.

const BYTES_PER_KB = 1024
const BYTES_PER_MB = BYTES_PER_KB * 1024

/**
 * Formats a byte count as a human-readable size string.
 * - < 1 KB  -> whole bytes ("512 B")
 * - < 1 MB  -> whole kilobytes ("48 KB")
 * - >= 1 MB -> megabytes with 1 decimal place ("3.4 MB")
 */
export function formatFileSize(bytes: number): string {
  // Guard against negative/NaN/non-finite input so a bad value renders as "0 B"
  // instead of nonsense like "NaN MB" or "-1 B".
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "0 B"
  }
  if (bytes < BYTES_PER_KB) {
    return `${bytes} B`
  }
  if (bytes < BYTES_PER_MB) {
    return `${Math.round(bytes / BYTES_PER_KB)} KB`
  }
  return `${(bytes / BYTES_PER_MB).toFixed(1)} MB`
}

/** Formats an ISO 8601 timestamp as a locale short date (e.g. "Jan 1, 2026"). */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

/** Splits a comma-separated tags string into a trimmed, non-empty string[]. */
export function parseTags(raw: string): string[] {
  return raw
    .split(",")
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0)
}
