local entry_display = require("telescope.pickers.entry_display")
local previewers = require("telescope.previewers.buffer_previewer")
local putils = require("telescope.previewers.utils")

local M = {}

-- This taken from finders.lua in telescope.nvim
local _callable_obj = function()
  local obj = {}

  obj.__index = obj
  obj.__call = function(t, ...)
    return t:_find(...)
  end

  obj.close = function() end

  return obj
end

local DynamicAsyncFinder = _callable_obj()

function DynamicAsyncFinder:new(opts)
  opts = opts or {}

  local obj = setmetatable({
    curr_buf = opts.curr_buf,
    _find = opts.fn,
    entry_maker = opts.entry_maker,
  }, self)

  return obj
end

M.DynamicAsyncFinder = DynamicAsyncFinder

local displayer = entry_display.create({
  separator = " ",
  items = {
    { width = 2 },
    { width = 2 },
    { remaining = true },
  },
})

local function make_display(entry)
  local columns = {
    { entry.icon, entry.hl },
    entry.icon2,
    entry.title,
  }
  return displayer(columns)
end

M.make_entry = function(item)
  return {
    id = item.id,
    icon = item.icon,
    icon2 = item.icon2,
    hl = item.color,
    display = make_display,
    ordinal = item.title,
    title = item.title,
    filename = item.filename,
  }
end

M.previewer = previewers.new_buffer_previewer({
  title = "Note Preview",

  get_buffer_by_name = function(_, entry)
    return entry.filename
  end,

  define_preview = function(self, entry, status)
    vim.fn._gkeep_render_note(self.state.bufnr, entry.id)
    putils.highlighter(self.state.bufnr, "GoogleKeepNote")
  end,
})

return M
