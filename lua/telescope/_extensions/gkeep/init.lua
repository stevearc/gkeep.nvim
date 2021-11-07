local note_picker = require("telescope._extensions.gkeep.note_picker")
local telescope = require("telescope")

return telescope.register_extension({
  exports = {
    gkeep = note_picker,
  },
})
