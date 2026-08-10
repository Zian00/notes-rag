// Shown in the assistant bubble during the gap between sending a question and
// the first answer token arriving (retrieve → grade → rewrite → generate all
// happen before any text exists). The typewriter reveal buffer can't fill this
// gap because there's nothing to reveal yet, so this stands in for it.
export function ThinkingIndicator() {
  return (
    // role="status" so assistive tech announces the pending state; the visible
    // "Thinking" text is the label, so the dots themselves are decorative.
    <span role="status" className="inline-flex items-center gap-1.5 text-muted-foreground">
      Thinking
      <span aria-hidden="true" className="inline-flex items-center gap-1">
        {/* Staggered animation-delay makes the three dots bounce in sequence
            rather than in unison — a travelling wave instead of a single hop. */}
        <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
        <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
        <span className="size-1.5 animate-bounce rounded-full bg-current" />
      </span>
    </span>
  )
}
