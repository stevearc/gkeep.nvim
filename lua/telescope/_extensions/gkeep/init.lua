local action_set = require("telescope.actions.set")
local action_state = require("telescope.actions.state")
local actions = require("telescope.actions")
local search = require("telescope._extensions.gkeep.search")
local telescope = require("telescope")
local title_search = require("telescope._extensions.gkeep.title_search")

-- Create our own mapping because the built-in telescope select action
-- normalizes the path, which removes the second slash from gkeep://
local function open_note_mappings(prompt_bufnr)
  action_set.select:replace(function(prompt_bufnr, type)
    local entry = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    local cmd = action_state.select_key_to_edit_key(type)
    local fname = vim.fn.fnameescape(entry.filename)
    vim.cmd(string.format("%s %s", cmd, fname))
  end)
  return true
end

local function insert_link_mappings(prompt_bufnr)
  action_set.select:replace(function(prompt_bufnr, type)
    local entry = action_state.get_selected_entry()
    actions.close(prompt_bufnr)
    local link = string.format("[%s](%s)", entry.title, entry.id)
    local cursor = vim.api.nvim_win_get_cursor(0)
    local row = cursor[1] - 1
    local col = cursor[2] + 1
    local line = vim.api.nvim_buf_get_lines(0, row, row + 1, true)[1]
    local new_line = string.sub(line, 0, col) .. link .. string.sub(line, col + 1)
    vim.api.nvim_buf_set_lines(0, row, row + 1, true, { new_line })
    vim.api.nvim_win_set_cursor(0, { row + 1, col + string.len(link) })
  end)
  return true
end

local conf = {
  find_method = "all_text",
  link_method = "title",
}

local function get_root_picker(default_opts)
  return function(opts)
    opts = vim.tbl_extend("keep", opts or {}, default_opts or {})
    local search_method = opts.search_method or conf[default_opts.method_key]
    if search_method == "all_text" then
      return search(opts)
    elseif search_method == "title" then
      return title_search(opts)
    else
      vim.api.nvim_err_writeln(string.format("Unknown search_method: %s", search_method))
    end
  end
end

return telescope.register_extension({
  setup = function(ext_config, config)
    conf = vim.tbl_extend("force", conf, ext_config or {})
  end,
  exports = {
    gkeep = get_root_picker({
      attach_mappings = open_note_mappings,
      method_key = "find_method",
    }),
    link = get_root_picker({
      attach_mappings = insert_link_mappings,
      method_key = "link_method",
    }),
  },
})
