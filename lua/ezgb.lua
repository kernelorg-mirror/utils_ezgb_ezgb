-- SPDX-License-Identifier: GPL-2.0-or-later
-- Copyright (C) 2026 by the Linux Foundation
--
-- ezgb.lua -- read-only library for git-bug repositories
--
-- Reads bug and identity data from git-bug's git object storage
-- using luagit2 (libgit2 bindings) for git access.
--
-- Dependencies:
--   luagit2                (git object access via libgit2)
--   lua-cjson or lua-json  (JSON parsing)
--   luaossl                (SHA-256 for operation hashing)

-- ── Dependencies ──────────────────────────────────────────────────────

local ok, json = pcall(require, "cjson")
if not ok then
    ok, json = pcall(require, "json")
    if not ok then
        error("ezgb: requires lua-cjson or lua-json")
    end
end

local digest = require("openssl.digest")

local M = {}

-- ── Constants ─────────────────────────────────────────────────────────

M.OP_CREATE       = 1
M.OP_SET_TITLE    = 2
M.OP_ADD_COMMENT  = 3
M.OP_SET_STATUS   = 4
M.OP_LABEL_CHANGE = 5
M.OP_EDIT_COMMENT = 6
M.OP_NOOP         = 7
M.OP_SET_METADATA = 8

M.STATUS_OPEN   = 1
M.STATUS_CLOSED = 2

M.SUPPORTED_BUG_FORMAT      = 4
M.SUPPORTED_IDENTITY_FORMAT = 2

local ID_LENGTH = 64

-- Detect the JSON module's null sentinel by decoding an actual null.
-- cjson uses a userdata, lua-json uses a function, others vary.
local json_null
do
    local decoded = json.decode("[null]")
    json_null = decoded[1]
end

-- ── Helpers ───────────────────────────────────────────────────────────

-- Return v if it is a real value, otherwise return default.
-- Treats cjson.null as absent.
local function jval(v, default)
    if v == nil or v == json_null then return default end
    return v
end

-- SHA-256 hex digest
local function sha256_hex(data)
    local b = digest.new("sha256"):final(data)
    local hex = ""
    for i = 1, #b do
        hex = hex .. string.format("%02x", string.byte(b, i))
    end
    return hex
end

-- ── Git access via luagit2 ─────────────────────────────────────────────

local git2 = require("git2")

-- Repository handle, set by M.open()
local repo = nil

function M.open(path)
    local r, err = git2.Repository(path)
    if not r then
        error("ezgb: cannot open repository: " .. tostring(path))
    end
    repo = r
end

function M._git_for_each_ref(pattern)
    local ref_names = git2.Reference.list(repo)
    if not ref_names then return {} end
    local refs = {}
    for i = 0, ref_names:count() - 1 do
        local name = ref_names:str(i)
        if name:sub(1, #pattern) == pattern then
            local ref = git2.Reference.lookup(repo, name)
            if ref then
                local oid = ref:target()
                local date = 0
                if oid then
                    local commit = git2.Commit.lookup(repo, oid)
                    if commit then date = commit:time() end
                end
                refs[#refs + 1] = {
                    name = name,
                    oid = oid and oid:fmt() or "",
                    date = date,
                }
            end
        end
    end
    return refs
end

function M._git_read_blob(oid_hex)
    local oid = git2.OID.hex(oid_hex)
    if not oid then return nil end
    local blob = git2.Blob.lookup(repo, oid)
    if not blob then return nil end
    local content = blob:rawcontent()
    return content
end

function M._git_tree_entries(tree_oid_hex)
    local oid = git2.OID.hex(tree_oid_hex)
    if not oid then return nil end
    local tree = git2.Tree.lookup(repo, oid)
    if not tree then return nil end
    local entries = {}
    for i = 0, tree:entrycount() - 1 do
        local entry = tree:entry_byindex(i)
        if entry then
            entries[#entries + 1] = {
                name = entry:name(),
                oid  = entry:id():fmt(),
                mode = entry:filemode(),
            }
        end
    end
    return entries
end

function M._git_commit_log(ref)
    local ref_obj = git2.Reference.lookup(repo, ref)
    if not ref_obj then return {} end
    local target = ref_obj:target()
    if not target then return {} end

    local revwalk = git2.RevWalk.new(repo)
    -- SORT_TOPOLOGICAL (1) + SORT_REVERSE (4) = oldest first
    revwalk:sorting(5)
    revwalk:push(target)

    local commits = {}
    local commit_oid = revwalk:next()
    while commit_oid do
        local commit = git2.Commit.lookup(repo, commit_oid)
        if commit then
            local tree = commit:tree()
            if tree then
                commits[#commits + 1] = {
                    oid      = commit_oid:fmt(),
                    tree_oid = tree:id():fmt(),
                }
            end
        end
        commit_oid = revwalk:next()
    end
    return commits
end

-- ── Raw op extraction ─────────────────────────────────────────────────
--
-- git-bug identifies each operation by SHA-256-hashing its JSON
-- serialization.  That serialization is produced by Go's json.Marshal,
-- which escapes <, >, & as \u003c, \u003e, \u0026.  Neither Lua's
-- cjson nor Python's json.dumps reproduces those escapes, so
-- re-encoding a decoded op produces different bytes and a wrong hash
-- (breaking OP_EDIT_COMMENT matching, where the "target" field carries
-- the Go-computed hash).
--
-- To get correct hashes we extract each op's raw JSON string directly
-- from the blob, preserving Go's escaping verbatim.  Unlike Python,
-- Lua has no raw_decode equivalent, so we track brace depth and string
-- boundaries manually.

local function extract_raw_ops(blob_json)
    local ops_start = blob_json:find('"ops"%s*:%s*%[')
    if not ops_start then return {} end
    local bracket_pos = blob_json:find('%[', ops_start)
    if not bracket_pos then return {} end
    local pos = bracket_pos + 1
    local ops = {}
    local len = #blob_json
    while pos <= len do
        -- skip whitespace and commas
        local ws_end = blob_json:match("^[%s,]*()", pos)
        if ws_end then pos = ws_end end
        if pos > len then break end
        local c = blob_json:byte(pos)
        if c == 0x5D then break end -- ']' = end of ops array
        if c == 0x7B then           -- '{' = start of op object
            local depth = 0
            local in_str = false
            local start = pos
            while pos <= len do
                c = blob_json:byte(pos)
                if in_str then
                    if c == 0x5C then     -- '\' = escape next char
                        pos = pos + 1
                    elseif c == 0x22 then -- '"' = end of string
                        in_str = false
                    end
                else
                    if c == 0x22 then     -- '"'
                        in_str = true
                    elseif c == 0x7B then -- '{'
                        depth = depth + 1
                    elseif c == 0x7D then -- '}'
                        depth = depth - 1
                        if depth == 0 then
                            ops[#ops + 1] = blob_json:sub(start, pos)
                            pos = pos + 1
                            break
                        end
                    end
                end
                pos = pos + 1
            end
        else
            pos = pos + 1
        end
    end
    return ops
end

-- ── Combined IDs ──────────────────────────────────────────────────────
--
-- Mirrors git-bug's CombineIds: interleaves primary (bug) and secondary
-- (op hash) into a single 64-char ID.
-- Pattern: PSPSPSPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPP

function M.combine_ids(primary, secondary)
    local result = {}
    local pi, si = 1, 1
    for i = 0, ID_LENGTH - 1 do
        if i == 1 or i == 3 or i == 5 or i == 9
           or (i >= 10 and i % 5 == 4) then
            result[#result + 1] = secondary:sub(si, si)
            si = si + 1
        else
            result[#result + 1] = primary:sub(pi, pi)
            pi = pi + 1
        end
    end
    return table.concat(result)
end

-- ── Operation hashing ─────────────────────────────────────────────────

function M.op_hash(raw_op_json)
    return sha256_hex(raw_op_json)
end

-- ── Format version checking ──────────────────────────────────────────

local function check_format_version(entries, prefix, supported)
    for _, e in ipairs(entries) do
        local ver_str = e.name:match("^" .. prefix .. "(%d+)$")
        if ver_str then
            local ver = tonumber(ver_str)
            if ver ~= supported then
                return nil, string.format(
                    "unsupported format: %s%d (expected %s%d)",
                    prefix, ver, prefix, supported
                )
            end
            return true
        end
    end
    return true -- no version entry found, assume OK
end

-- ── Identity cache ────────────────────────────────────────────────────

local identity_cache = {}

local function fallback_identity(iid)
    return {id = iid, name = iid, email = iid, login = ""}
end

-- ── Ref enumeration ───────────────────────────────────────────────────

function M.list_bug_refs(since)
    since = since or 0
    local refs = M._git_for_each_ref("refs/bugs/")
    local result = {}
    for _, ref in ipairs(refs) do
        if since <= 0 or (ref.date and ref.date >= since) then
            local bid = ref.name:match("([^/]+)$")
            if bid then
                result[#result + 1] = {
                    id = bid, oid = ref.oid, date = ref.date,
                }
            end
        end
    end
    return result
end

function M.list_identity_refs()
    local refs = M._git_for_each_ref("refs/identities/")
    local result = {}
    for _, ref in ipairs(refs) do
        local iid = ref.name:match("([^/]+)$")
        if iid then
            result[#result + 1] = {id = iid, oid = ref.oid}
        end
    end
    return result
end

-- ── Bug ID resolution ─────────────────────────────────────────────────

function M.resolve_bug_id(bid, refs)
    if #bid == ID_LENGTH then return bid end
    if not refs then
        refs = M.list_bug_refs()
    end
    local candidates = {}
    for _, ref in ipairs(refs) do
        if ref.id:sub(1, #bid) == bid then
            candidates[#candidates + 1] = ref.id
        end
    end
    if #candidates == 1 then return candidates[1] end
    if #candidates > 1 then
        return nil, string.format(
            "bug ID prefix %s matches %d bugs", bid, #candidates
        )
    end
    return nil, string.format("no bug matching ID prefix %s", bid)
end

-- ── Identity resolution ───────────────────────────────────────────────

function M.resolve_identity(iid)
    if identity_cache[iid] then return identity_cache[iid] end

    local commits = M._git_commit_log("refs/identities/" .. iid)
    if not commits or #commits == 0 then
        local fb = fallback_identity(iid)
        identity_cache[iid] = fb
        return fb
    end

    -- Latest commit is last in oldest-first order
    local latest = commits[#commits]
    local entries = M._git_tree_entries(latest.tree_oid)
    if not entries then
        local fb = fallback_identity(iid)
        identity_cache[iid] = fb
        return fb
    end

    -- Find the 'version' blob (identity data, not 'ops')
    local version_oid
    for _, e in ipairs(entries) do
        if e.name == "version" then
            version_oid = e.oid
            break
        end
    end
    if not version_oid then
        local fb = fallback_identity(iid)
        identity_cache[iid] = fb
        return fb
    end

    local raw = M._git_read_blob(version_oid)
    if not raw then
        local fb = fallback_identity(iid)
        identity_cache[iid] = fb
        return fb
    end

    local decode_ok, data = pcall(json.decode, raw)
    if not decode_ok then
        local fb = fallback_identity(iid)
        identity_cache[iid] = fb
        return fb
    end

    -- Validate format version (stored in the JSON, not the filename)
    local fmt_ver = jval(data.version, nil)
    if fmt_ver ~= nil and fmt_ver ~= M.SUPPORTED_IDENTITY_FORMAT then
        local fb = fallback_identity(iid)
        identity_cache[iid] = fb
        return fb
    end

    local identity = {
        id    = iid,
        name  = jval(data.name, iid),
        email = jval(data.email, iid),
        login = jval(data.login, ""),
    }
    identity_cache[iid] = identity
    return identity
end

-- ── Op-pack reading ───────────────────────────────────────────────────
--
-- Walk the commit chain for a bug and return operation packs paired
-- with their raw op JSON strings (needed for op_hash).

local function get_op_packs(bid)
    local commits = M._git_commit_log("refs/bugs/" .. bid)
    if not commits or #commits == 0 then
        return nil, "no commits for bug " .. bid
    end

    local packs = {}
    local raw_ops_lists = {}

    for idx, c in ipairs(commits) do
        local entries = M._git_tree_entries(c.tree_oid)
        if entries then
            -- Check format version on the first commit's tree
            if idx == 1 then
                local ver_ok, err = check_format_version(
                    entries, "version%-", M.SUPPORTED_BUG_FORMAT
                )
                if not ver_ok then return nil, err end
            end

            -- Find and read the ops blob
            for _, e in ipairs(entries) do
                if e.name == "ops" then
                    local raw = M._git_read_blob(e.oid)
                    if raw then
                        local decode_ok, pack = pcall(json.decode, raw)
                        if decode_ok then
                            packs[#packs + 1] = pack
                            raw_ops_lists[#raw_ops_lists + 1] =
                                extract_raw_ops(raw)
                        end
                    end
                    break
                end
            end
        end
    end

    return packs, raw_ops_lists
end

-- ── Bug building (operation replay) ──────────────────────────────────

function M.build_bug(bid)
    local full_id, err = M.resolve_bug_id(bid)
    if not full_id then return nil, err end

    local packs, raw_ops_lists = get_op_packs(full_id)
    if not packs then return nil, raw_ops_lists end -- error string
    if #packs == 0 then
        return nil, "no operation packs for bug " .. full_id
    end

    local title = ""
    local is_open = true
    local creator = nil
    local created_at = 0
    local labels = {}
    local comments = {}
    local comment_count = 0
    local metadata = {}
    -- Map op hashes to comment tables for OP_EDIT_COMMENT
    local op_hash_map = {}

    for pack_idx, pack in ipairs(packs) do
        local author_id = ""
        local author_tbl = jval(pack.author, nil)
        if author_tbl then
            author_id = jval(author_tbl.id, "")
        end
        local raw_ops = raw_ops_lists[pack_idx] or {}
        local ops = jval(pack.ops, {})

        for op_idx, op in ipairs(ops) do
            local op_type = jval(op.type, 0)
            local timestamp = jval(op.timestamp, 0)
            local raw_json = raw_ops[op_idx]
            local op_id = raw_json and M.op_hash(raw_json) or ""

            if op_type == M.OP_CREATE then
                title = jval(op.title, "")
                local author = M.resolve_identity(author_id)
                creator = author
                created_at = timestamp
                local message = jval(op.message, "")
                if message ~= "" then
                    local files = jval(op.files, {})
                    local combined_id = M.combine_ids(full_id, op_id)
                    local cmt = {
                        id = combined_id,
                        author = author,
                        text = message,
                        created_at = timestamp,
                        count = comment_count,
                        attachment_ids = files,
                    }
                    comments[#comments + 1] = cmt
                    op_hash_map[op_id] = cmt
                    comment_count = comment_count + 1
                end
                local op_meta = jval(op.metadata, nil)
                if op_meta then
                    for k, v in pairs(op_meta) do
                        metadata[k] = v
                    end
                end

            elseif op_type == M.OP_SET_TITLE then
                title = jval(op.title, title)

            elseif op_type == M.OP_ADD_COMMENT then
                local message = jval(op.message, "")
                local files = jval(op.files, {})
                local op_author = M.resolve_identity(author_id)
                local combined_id = M.combine_ids(full_id, op_id)
                local cmt = {
                    id = combined_id,
                    author = op_author,
                    text = message,
                    created_at = timestamp,
                    count = comment_count,
                    attachment_ids = files,
                }
                comments[#comments + 1] = cmt
                op_hash_map[op_id] = cmt
                comment_count = comment_count + 1

            elseif op_type == M.OP_SET_STATUS then
                local status_val = jval(op.status, M.STATUS_OPEN)
                is_open = (status_val == M.STATUS_OPEN)

            elseif op_type == M.OP_LABEL_CHANGE then
                for _, lbl in ipairs(jval(op.added, {})) do
                    labels[lbl] = true
                end
                for _, lbl in ipairs(jval(op.removed, {})) do
                    labels[lbl] = nil
                end

            elseif op_type == M.OP_EDIT_COMMENT then
                local target = jval(op.target, "")
                local matched = op_hash_map[target]
                if matched then
                    matched.text = jval(op.message, "")
                    local new_files = jval(op.files, {})
                    if #new_files > 0 then
                        matched.attachment_ids = new_files
                    end
                end

            elseif op_type == M.OP_SET_METADATA then
                local new_meta = jval(op.new_metadata, nil)
                if new_meta then
                    for k, v in pairs(new_meta) do
                        metadata[k] = v
                    end
                end

            -- OP_NOOP: nothing to do
            end
        end
    end

    if not creator then
        creator = fallback_identity("unknown")
    end

    return {
        id         = full_id,
        title      = title,
        status     = is_open and M.STATUS_OPEN or M.STATUS_CLOSED,
        creator    = creator,
        created_at = created_at,
        labels     = labels,
        comments   = comments,
        metadata   = metadata,
    }
end

function M.build_bug_summary(bid)
    local full_id, err = M.resolve_bug_id(bid)
    if not full_id then return nil, err end

    local packs = get_op_packs(full_id)
    if not packs then return nil, "failed to read op packs for " .. full_id end
    if #packs == 0 then
        return nil, "no operation packs for bug " .. full_id
    end

    local title = ""
    local is_open = true
    local creator_id = ""
    local created_at = 0
    local labels = {}
    local comment_count = 0

    for _, pack in ipairs(packs) do
        local author_id = ""
        local author_tbl = jval(pack.author, nil)
        if author_tbl then
            author_id = jval(author_tbl.id, "")
        end
        local ops = jval(pack.ops, {})

        for _, op in ipairs(ops) do
            local op_type = jval(op.type, 0)

            if op_type == M.OP_CREATE then
                title = jval(op.title, "")
                creator_id = author_id
                created_at = jval(op.timestamp, 0)
                if jval(op.message, "") ~= "" then
                    comment_count = comment_count + 1
                end

            elseif op_type == M.OP_SET_TITLE then
                title = jval(op.title, title)

            elseif op_type == M.OP_ADD_COMMENT then
                comment_count = comment_count + 1

            elseif op_type == M.OP_SET_STATUS then
                local status_val = jval(op.status, M.STATUS_OPEN)
                is_open = (status_val == M.STATUS_OPEN)

            elseif op_type == M.OP_LABEL_CHANGE then
                for _, lbl in ipairs(jval(op.added, {})) do
                    labels[lbl] = true
                end
                for _, lbl in ipairs(jval(op.removed, {})) do
                    labels[lbl] = nil
                end
            end
        end
    end

    return {
        id            = full_id,
        title         = title,
        status        = is_open and M.STATUS_OPEN or M.STATUS_CLOSED,
        creator_id    = creator_id,
        created_at    = created_at,
        labels        = labels,
        comment_count = comment_count,
    }
end

-- ── Cache management ──────────────────────────────────────────────────

function M.clear_cache()
    identity_cache = {}
end

return M
