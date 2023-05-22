local M = {}

M.dispatch = function(...)
  vim.schedule_wrap(vim.fn._gkeep_dispatch)(...)
end

M.rename_buffers = function(renames)
  for _, tuple in ipairs(renames) do
    local bufnr, old_name, new_name = unpack(tuple)
    vim.api.nvim_buf_call(bufnr, function()
      vim.cmd(string.format("silent! saveas! %s", new_name))
    end)
    -- saveas will change the buffer to point to the new file, and it creates a
    -- *new* buffer for the old file. We need to find and delete that new buffer
    for _, newbuf in ipairs(vim.api.nvim_list_bufs()) do
      if old_name == vim.api.nvim_buf_get_name(newbuf) then
        vim.bo[newbuf].buflisted = false
        vim.api.nvim_buf_delete(newbuf, { force = true })
        break
      end
    end
  end
end

M.on_ephemeral_buf_read = function(ft)
  if ft == "norg" then
    -- neorg was inserting a duplicate @document.meta at the top of the buffer
    -- because treesitter thought the buffer was empty. This lets treesitter
    -- know that we've updated the buffer content.
    local parser = vim.treesitter.get_parser(0, "norg")
    parser:parse()
  end
end

local win_positions = {}
M.save_win_positions = function(bufnr)
  win_positions = {}
  for _, winid in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(winid) == bufnr then
      vim.api.nvim_win_call(winid, function()
        local view = vim.fn.winsaveview()
        table.insert(win_positions, { winid, view })
      end)
    end
  end
end

M.restore_win_positions = function()
  for _, pair in ipairs(win_positions) do
    local winid, view = unpack(pair)
    vim.api.nvim_win_call(winid, function()
      pcall(vim.fn.winrestview, view)
    end)
  end
  win_positions = {}
end

return M
