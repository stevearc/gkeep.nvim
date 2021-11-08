local conf = require("telescope.config").values
local scheduler = require("plenary.async").util.scheduler
local pickers = require("telescope.pickers")
local util = require("telescope._extensions.gkeep.util")

local function title_search(opts)
  opts = opts or {}
  vim.fn._gkeep_preload()

  local results = vim.NIL
  local search = function(self, prompt, process_result, process_complete)
    while results == vim.NIL do
      results = vim.fn._gkeep_all_notes()
      scheduler()
    end
    for _, item in ipairs(results) do
      process_result(self.entry_maker(item))
    end
    process_complete()
  end

  pickers.new(opts, {
    prompt_title = "Google Keep Notes",
    finder = util.DynamicAsyncFinder:new({
      curr_buf = vim.api.nvim_get_current_buf(),
      fn = search,
      entry_maker = util.make_entry,
    }),
    sorter = conf.generic_sorter(opts),
    previewer = util.previewer,
    attach_mappings = opts.attach_mappings,
  }):find()
end

return title_search
