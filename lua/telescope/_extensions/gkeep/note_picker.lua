local action_set = require("telescope.actions.set")
local action_state = require("telescope.actions.state")
local actions = require("telescope.actions")
local conf = require("telescope.config").values
local gkeep = require("gkeep")
local pickers = require("telescope.pickers")
local util = require("telescope._extensions.gkeep.util")

local function note_picker(opts)
  opts = opts or {}
  vim.fn._gkeep_preload()

  local parsed_prompt = ""

  local search = function(self, prompt, process_result, process_complete)
    local query = prompt
    function gkeep.on_search_results(ret_query, match_str, results)
      if query == ret_query then
        -- This hack is so we don't call tx() twice when queries are slow
        query = "__NOMATCH__"
        parsed_prompt = match_str
        for _, item in ipairs(results) do
          process_result(self.entry_maker(item))
        end
        process_complete()
      end
    end
    vim.fn._gkeep_search(query, "require('gkeep').on_search_results(...)")
  end

  local sorter = conf.generic_sorter(opts)
  -- Scoring function should ignore flags in the query (e.g. '+p' or '-t')
  -- so make it operate on the parsed_prompt instead of the prompt
  local scoring_function = sorter.scoring_function
  sorter.scoring_function = function(self, prompt, line)
    return scoring_function(self, parsed_prompt, line)
  end

  pickers.new(opts, {
    prompt_title = "Google Keep Notes",
    finder = util.DynamicAsyncFinder:new({
      curr_buf = vim.api.nvim_get_current_buf(),
      fn = search,
      entry_maker = util.make_entry,
    }),
    sorter = sorter,
    previewer = util.previewer,
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

return note_picker
