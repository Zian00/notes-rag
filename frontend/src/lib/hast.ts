// Shared shape/helpers for the two custom rehype plugins (rehypeStreamingCursor,
// rehypeCitationMarkers) that walk react-markdown's hast tree directly.

export interface HastNode {
  type: string
  tagName?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
  value?: string
}

// Code blocks render via MessageBubble's syntax-highlighter path, which takes
// the whole block as one raw string rather than reading hast children
// individually — text inside a `code` element must be treated as opaque by
// any plugin that otherwise splices React elements into arbitrary text nodes.
export function isInsideCode(ancestors: readonly unknown[]): boolean {
  return ancestors.some((a) => (a as HastNode).tagName === "code")
}
