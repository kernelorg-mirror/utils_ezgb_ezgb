rockspec_format = "3.0"
package = "lua-ezgb"
version = "scm-1"
source = {
   url = "git+https://git.kernel.org/pub/scm/utils/ezgb/ezgb.git",
}
description = {
   summary = "Read-only library for git-bug repositories",
   detailed = [[
      ezgb.lua reads bug and identity data from git-bug repositories
      by parsing git objects directly via luagit2.  Designed for use
      inside cgit's Lua filter system, but works standalone too.
   ]],
   homepage = "https://git.kernel.org/pub/scm/utils/ezgb/ezgb.git",
   license = "GPL-2.0-or-later",
}
dependencies = {
   "lua >= 5.1",
   "lua-git2",
   "luaossl",
}
build = {
   type = "builtin",
   modules = {
      ezgb = "lua/ezgb.lua",
   },
}
