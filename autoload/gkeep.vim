function! gkeep#preload(...) abort
  if !exists('g:loaded_remote_plugins')
    runtime! plugin/rplugin.vim
  endif
  if exists(':GkeepLogin')
    call _gkeep_preload()
  endif
endfunction

function! gkeep#preload_if_note(bufname) abort
  let l:fullpath = fnamemodify(a:bufname, ':p')
  if l:fullpath =~? '^' . expand(g:gkeep_sync_dir)
    " python3 host doesn't start immediately when vim starts up
    if remote#host#IsRunning('python3')
      call gkeep#preload()
    else
      call timer_start(1000, funcref('gkeep#preload'))
    endif
  end
endfunction

function! gkeep#foldexpr() abort
  let l:prev = indent(v:lnum - 1)
  let l:cur = indent(v:lnum)
  if l:prev == -1
    return 0
  end
  if match(getline(v:lnum), '^\s*$') != -1
    return -1
  endif
  let l:prev = l:prev / 4
  let l:cur = l:cur / 4
  if l:prev <= l:cur && l:cur != 0
    return l:cur
  else
    return printf(">%d", l:cur + 1)
  endif
endfunction
