import { useMemo } from "react"
import { useTheme } from "next-themes"
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter"
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism"
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash"
import c from "react-syntax-highlighter/dist/esm/languages/prism/c"
import cpp from "react-syntax-highlighter/dist/esm/languages/prism/cpp"
import java from "react-syntax-highlighter/dist/esm/languages/prism/java"
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript"
import json from "react-syntax-highlighter/dist/esm/languages/prism/json"
import python from "react-syntax-highlighter/dist/esm/languages/prism/python"
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql"
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript"
import { StreamingCursor } from "@/components/chat/StreamingCursor"
import { CITATION_MARKER_CLASS } from "@/lib/rehypeCitationMarkers"
import { CURSOR_CLASS } from "@/lib/rehypeStreamingCursor"
import type { Components } from "react-markdown"

// Registered once at module load — a deliberately small, study-notes-relevant
// subset (not react-syntax-highlighter's full language bundle) to keep bundle
// size down (see docs/design/2026-07-31-markdown-rendering-citation-fidelity-design.md §8).
SyntaxHighlighter.registerLanguage("bash", bash)
SyntaxHighlighter.registerLanguage("c", c)
SyntaxHighlighter.registerLanguage("cpp", cpp)
SyntaxHighlighter.registerLanguage("java", java)
SyntaxHighlighter.registerLanguage("javascript", javascript)
SyntaxHighlighter.registerLanguage("json", json)
SyntaxHighlighter.registerLanguage("python", python)
SyntaxHighlighter.registerLanguage("sql", sql)
SyntaxHighlighter.registerLanguage("typescript", typescript)

// react-markdown passes hast `className` through as either a string or an
// array (hast stores it as string[]) depending on the node's origin — the two
// component overrides below that inspect className need it normalized to one.
function toClassString(className: unknown): string | undefined {
  if (Array.isArray(className)) return className.join(" ")
  return typeof className === "string" ? className : undefined
}

// Hand-styled component overrides using the app's existing Tailwind tokens,
// rather than @tailwindcss/typography's "prose" classes — a typography plugin's
// article-length defaults would need heavy overriding to fit this compact bubble.
export function useMarkdownComponents(onCitationClick: (n: number) => void): Components {
  const { resolvedTheme } = useTheme()
  const codeTheme = resolvedTheme === "dark" ? oneDark : oneLight

  // Memoized: react-markdown fully remounts its rendered tree whenever the
  // `components` object's identity changes, even if nothing it actually
  // renders changed — a fresh object every render caused visible DOM nodes to
  // be replaced (not just re-rendered) on unrelated state updates elsewhere in
  // MessageBubble (e.g. when the citations SSE event arrives).
  return useMemo<Components>(
    () => ({
      p: ({ children }) => <p className="[overflow-wrap:anywhere]">{children}</p>,
      ul: ({ children }) => <ul className="ml-4 list-disc space-y-1">{children}</ul>,
      ol: ({ children }) => <ol className="ml-4 list-decimal space-y-1">{children}</ol>,
      li: ({ children }) => <li>{children}</li>,
      strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
      em: ({ children }) => <em className="italic">{children}</em>,
      blockquote: ({ children }) => (
        <blockquote className="border-l-2 border-border pl-3 text-muted-foreground">
          {children}
        </blockquote>
      ),
      hr: () => <hr className="border-border" />,
      table: ({ children }) => (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs">{children}</table>
        </div>
      ),
      thead: ({ children }) => <thead className="border-b border-border">{children}</thead>,
      tbody: ({ children }) => <tbody>{children}</tbody>,
      tr: ({ children }) => <tr className="border-b border-border/60">{children}</tr>,
      th: ({ children }) => <th className="px-2 py-1 font-medium">{children}</th>,
      td: ({ children }) => <td className="px-2 py-1">{children}</td>,
      a: ({ href, children }) => (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2 hover:text-primary"
        >
          {children}
        </a>
      ),
      // rehypeStreamingCursor marks its inserted element with CURSOR_CLASS so it
      // can be swapped for the real animated cursor here — a plain `span` was
      // chosen for the marker (over a fictional custom tag) so it survives
      // react-markdown's hast-to-JSX conversion without special-casing there.
      span: ({ className, children, ...rest }) => {
        const classes = toClassString(className)
        if (classes?.split(" ").includes(CURSOR_CLASS)) {
          return <StreamingCursor />
        }
        return (
          <span className={classes} {...rest}>
            {children}
          </span>
        )
      },
      // rehypeCitationMarkers marks its inserted elements with CITATION_MARKER_CLASS
      // + a data-citation-n attribute holding the 1-based marker number.
      button: (props) => {
        const className = toClassString(props.className)
        const rawN = (props as Record<string, unknown>)["data-citation-n"]
        const n = typeof rawN === "string" ? rawN : undefined
        if (
          n &&
          typeof className === "string" &&
          className.split(" ").includes(CITATION_MARKER_CLASS)
        ) {
          return (
            <button
              type="button"
              className="mx-0.5 cursor-pointer rounded bg-accent px-1 align-super text-[0.7em] font-medium text-accent-foreground hover:bg-accent/80"
              onClick={() => onCitationClick(Number(n))}
            >
              [{n}]
            </button>
          )
        }
        return <button type="button">{props.children}</button>
      },
      // Never render an actual <img>: an image tag fires a network request the
      // instant it's rendered, with no click required — a known markdown-image
      // exfiltration vector if a document ever got an indirect prompt injection
      // to emit one. Rendered content only ever came from the user's own notes,
      // but this closes the vector for near-zero cost rather than trusting that.
      img: ({ alt }) =>
        alt ? <span className="italic text-muted-foreground">[image: {alt}]</span> : null,
      code(props) {
        const { children, className, ...rest } = props
        const match = /language-(\w+)/.exec(className || "")
        if (!match) {
          return (
            <code
              className="rounded bg-black/10 px-1 py-0.5 font-mono text-[0.85em] dark:bg-white/10"
              {...rest}
            >
              {children}
            </code>
          )
        }
        return (
          <SyntaxHighlighter
            language={match[1]}
            style={codeTheme}
            customStyle={{ margin: 0, borderRadius: "0.5rem", fontSize: "0.8rem" }}
            PreTag="div"
          >
            {String(children).replace(/\n$/, "")}
          </SyntaxHighlighter>
        )
      },
      pre: ({ children }) => <pre className="my-1 overflow-x-auto">{children}</pre>,
    }),
    [codeTheme, onCitationClick]
  )
}
