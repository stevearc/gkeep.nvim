local conf = require("telescope.config").values
local gkeep = require("gkeep")
local pickers = require("telescope.pickers")
local util = require("telescope._extensions.gkeep.util")

local function search_picker(opts)
  opts = opts or {}
  vim.fn._gkeep_preload()

  local parsed_prompt = ""

  local search = function(self, prompt, process_result, process_complete)
    local query = prompt
    function gkeep.on_search_results(ret_query, match_str, results)
      if query == ret_query then
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
    local score = scoring_function(self, parsed_prompt, line)
    -- Don't filter out any results (filters when score == -1) because we might
    -- have matched on the content not the title
    return score < 0 and 0 or score
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
    attach_mappings = opts.attach_mappings,
  }):find()
end

return search_picker
