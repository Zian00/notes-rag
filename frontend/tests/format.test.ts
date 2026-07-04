import { formatFileSize, formatDate, parseTags } from "@/lib/format"

describe("formatFileSize", () => {
  it("formats 0 bytes", () => {
    expect(formatFileSize(0)).toBe("0 B")
  })

  it("keeps sub-KB values in whole bytes", () => {
    expect(formatFileSize(512)).toBe("512 B")
  })

  it("formats KB-range values as whole kilobytes", () => {
    expect(formatFileSize(2048)).toBe("2 KB")
  })

  it("formats MB-range values with 1 decimal place", () => {
    expect(formatFileSize(3 * 1024 * 1024 + 0.4 * 1024 * 1024)).toBe("3.4 MB")
  })

  it("falls back to 0 B for negative input", () => {
    expect(formatFileSize(-1)).toBe("0 B")
  })

  it("falls back to 0 B for NaN input", () => {
    expect(formatFileSize(Number.NaN)).toBe("0 B")
  })

  it("falls back to 0 B for non-finite input", () => {
    expect(formatFileSize(Number.POSITIVE_INFINITY)).toBe("0 B")
  })
})

describe("formatDate", () => {
  it("formats a known ISO string into a deterministic non-empty string containing the year", () => {
    const formatted = formatDate("2026-01-01T00:00:00Z")
    expect(formatted).toContain("2026")
    expect(formatted.length).toBeGreaterThan(0)
  })
})

describe("parseTags", () => {
  it("splits and trims comma-separated tags", () => {
    expect(parseTags("a, b ,c")).toEqual(["a", "b", "c"])
  })

  it("drops empty entries", () => {
    expect(parseTags("a, , b")).toEqual(["a", "b"])
  })

  it("returns an empty array for an empty string", () => {
    expect(parseTags("")).toEqual([])
  })
})
