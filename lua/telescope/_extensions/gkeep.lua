local action_set = require("telescope.actions.set")
local action_state = require("telescope.actions.state")
local actions = require("telescope.actions")
local conf = require("telescope.config").values
local entry_display = require("telescope.pickers.entry_display")
local finders = require("telescope.finders")
local gkeep = require("gkeep")
local pickers = require("telescope.pickers")
local previewers = require("telescope.previewers.buffer_previewer")
local putils = require("telescope.previewers.utils")
local sorters = require("telescope.sorters")
local telescope = require("telescope")
local a = require("plenary.async_lib")
local async, await = a.async, a.await
local channel = a.util.channel

local function note_picker(opts)
  opts = opts or {}
  vim.fn._gkeep_preload()

  local parsed_prompt = ""

  local displayer = entry_display.create({
    separator = " ",
    items = {
      { width = 2 },
      { width = 2 },
      { remaining = true },
    },
  })

  local search = async(function(query)
    local tx, rx = channel.oneshot()
    function gkeep.on_search_results(ret_query, match_str, results)
      if query == ret_query then
        -- This hack is so we don't call tx() twice when queries are slow
        query = "__NOMATCH__"
        parsed_prompt = match_str
        tx(results)
      end
    end
    vim.fn._gkeep_search(query, "require('gkeep').on_search_results(...)")
    local results = await(rx())
    return results
  end)

  local function make_display(entry)
    local columns = {
      { entry.icon, entry.hl },
      entry.icon2,
      entry.title,
    }
    return displayer(columns)
  end

  local function make_entry(item)
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

  local previewer = previewers.new_buffer_previewer({
    title = "Note Preview",

    get_buffer_by_name = function(_, entry)
      return entry.filename
    end,

    define_preview = function(self, entry, status)
      vim.fn._gkeep_render_note(self.state.bufnr, entry.id)
      putils.highlighter(self.state.bufnr, "GoogleKeepNote")
    end,
  })

  local sorter = sorters.empty()
  local fuzzy = conf.generic_sorter(opts)
  sorter.scoring_function = function(_, prompt, line)
    return fuzzy:scoring_function(parsed_prompt, line)
  end

  pickers.new(opts, {
    prompt_title = "Google Keep Notes",
    finder = finders.new_dynamic({
      curr_buf = vim.api.nvim_get_current_buf(),
      fn = search,
      entry_maker = make_entry,
    }),
    sorter = sorter,
    previewer = previewer,
    -- Create our own mapping because the built-in telescope select action
    -- normalizes the path, which removes the second slash from gkeep://
    attach_mappings = function(prompt_bufnr)
      action_set.select:replace(function(prompt_bufnr, type)
        local entry = action_state.get_selected_entry()
        actions.close(prompt_bufnr)
        local cmd = action_state.select_key_to_edit_key(type)
        local fname = vim.fn.fnameescape(entry.filename)
        vim.cmd(string.format("%s %s", cmd, fname))
      end)
      return true
    end,
  }):find()
end

return telescope.register_extension({
  exports = {
    gkeep = note_picker,
  },
})
