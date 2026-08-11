from typing import Any

# Shared "field omitted" sentinel for partial-update methods across repositories
# and the services that call them — None is a real value for nullable FK
# columns like group_id (ungroup), so it can't double as "leave unchanged".
# A single shared object (not one redefined per repository module) means every
# comparison against UNSET, wherever it's imported from, checks the same identity.
UNSET: Any = object()
