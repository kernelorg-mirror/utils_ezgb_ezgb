# ezgb.lua -- read-only Lua library for git-bug repositories

ezgb.lua reads bug and identity data directly from git-bug's git
object storage using luagit2 (libgit2 bindings). It is read-only:
creating, modifying, or deleting bugs requires the Python library
or the git-bug CLI.

The primary use case is embedding in tools like cgit filters where
subprocess overhead is unacceptable and only read access is needed.

## Dependencies

- **luagit2** (`lua-git2`) -- libgit2 bindings for Lua
- **luaossl** -- OpenSSL bindings (SHA-256 for operation hashing)
- **lua-cjson** or **lua-json** -- JSON parsing

## Installation

From the ezgb source tree:

```bash
luarocks make lua-ezgb-scm-1.rockspec
```

## Usage

```lua
local ezgb = require("ezgb")

-- Open a repository (bare or non-bare)
ezgb.open("/path/to/repo")

-- List all bug refs
local refs = ezgb.list_bug_refs()
for _, ref in ipairs(refs) do
    print(ref.id, ref.commit)
end

-- Build a full bug snapshot
local bug = ezgb.build_bug(refs[1].id)
print(bug.id, bug.title, bug.status)
print("comments:", #bug.comments)
print("labels:", bug.labels)  -- set-table: {["label"] = true, ...}
print("creator:", bug.creator.name, bug.creator.email)

-- Resolve an identity by ID
local identity = ezgb.resolve_identity(identity_id)
print(identity.name, identity.email)

-- Resolve a short bug ID to its full 64-char hex ID
local full_id = ezgb.resolve_bug_id("abc1234", refs)

-- Clear cached data
ezgb.clear_cache()
```

## Data format

Bug snapshots are plain Lua tables:

- `bug.id` -- 64-character hex string
- `bug.title` -- current title (after replaying all operations)
- `bug.status` -- `1` (open) or `2` (closed)
- `bug.creator` -- table with `id`, `name`, `email`, `login`
- `bug.created_at` -- unix timestamp
- `bug.labels` -- set-table (`{["label_name"] = true, ...}`)
- `bug.comments` -- array of comment tables
- `bug.metadata` -- key-value table

Each comment table contains `id`, `author`, `text`, `created_at`,
`count`, and `attachment_ids`.

## Differences from the Python library

| Feature | Python (ezgb) | Lua (ezgb.lua) |
|---------|---------------|-----------------|
| Git access | pygit2 (libgit2) | luagit2 (libgit2) |
| Write operations | Yes (via git-bug CLI) | No |
| CLI cache listing | Yes | No |
| In-memory caching | Yes | Yes |
| Module state | Instance-based (GitBugRepo) | Module-level global |

## License

GPL-2.0-or-later
