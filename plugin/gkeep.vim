if exists('g:gkeep_sync_dir')
  aug GkeepSyncDirPreload
    au!
    au BufNew * call gkeep#preload_if_note(expand('<afile>'))
  aug END
  call gkeep#preload_if_note(bufname('%'))
endif

aug GkeepSyncOnFocus
  au!
  au FocusGained * silent! GkeepSync
aug END

if &termguicolors || has('gui_running')
  hi def link GKeepWhite Normal
  hi def GKeepRed      guifg=#EC8B83
  hi def GKeepOrange   guifg=#F6BE10
  hi def GKeepYellow   guifg=#FEF675
  hi def GKeepGreen    guifg=#D0FF8F
  hi def GKeepTeal     guifg=#B0FFEA
  hi def GKeepBlue     guifg=#CEF0F8
  hi def GKeepDarkBlue guifg=#B1CAFA
  hi def GKeepPurple   guifg=#D5ACFB
  hi def GKeepPink     guifg=#FACEE8
  hi def GKeepBrown    guifg=#E4CAA8
  hi def GKeepGray     guifg=#E8EAED
else
  hi def link GKeepWhite Normal
  hi def GKeepRed      ctermfg=Red
  hi def GKeepOrange   ctermfg=LightRed
  hi def GKeepYellow   ctermfg=Yellow
  hi def GKeepGreen    ctermfg=Green
  hi def GKeepTeal     ctermfg=DarkCyan
  hi def GKeepBlue     ctermfg=Blue
  hi def GKeepDarkBlue ctermfg=DarkBlue
  hi def GKeepPurple   ctermfg=DarkMagenta
  hi def GKeepPink     ctermfg=LightMagenta
  hi def GKeepBrown    ctermfg=Brown
  hi def GKeepGray     ctermfg=Gray
endif

hi def link GkeepStatus String
