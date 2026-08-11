# Chat-attached documents are group-scoped library items, not conversation-scoped attachments

**Status:** accepted

When a user attaches a file from the chat composer (T10), the resulting `Document` is scoped only
by `group_id`, exactly like a document uploaded from the Documents page — there is no relation
tying it to the specific conversation it was attached from. This departs from the common
ChatGPT/Slack/Discord pattern, where an attachment belongs to the message/conversation and is
typically not visible outside it.

We chose group-scoping because documents must remain retrievable by every chat in the group for
RAG, not just the chat they were attached from — study material uploaded mid-conversation should
be usable by every other chat studying that same group afterward. Retrofitting conversation-scoped
attachments later would require a new relation/table, so this is deliberate and hard to reverse:
future features should not assume an "attached to conversation X" relationship exists.
