function! gkeep#editor#Bullet() abort
  if exists('b:note_type') && b:note_type == 'note'
    let col = getcurpos()[2]
    if col <= matchend(getline('.'), '^\s*') + 1
      return '• '
    endif
  end
  return '*'
endfunction

function! gkeep#editor#TabOrIndent() abort
  if s:AtListItemStart()
    if indent('.') < &l:shiftwidth || b:note_type == 'note'
      " Start with a space/backspace because otherwise the EOL space generated
      " after creating a new line will get removed when we leave insert mode
      return "\<space>\<backspace>\<C-o>>>" . repeat("\<right>", &l:shiftwidth)
    else
      return ''
    endif
  endif
  return "\<Tab>"
endfunction

function! gkeep#editor#BackspaceOrDedent() abort
  if s:AtListItemStart()
    let indent = indent('.')
    if indent >= &l:shiftwidth
      let col = getcurpos()[2]
      call cursor(0, col - &l:shiftwidth)
      " Start with a space/backspace because otherwise the EOL space generated
      " after creating a new line will get removed when we leave insert mode
      let line = getline('.')
      let sufflen = len(line) - col + 1
      if sufflen > &l:shiftwidth
        let sufflen = &l:shiftwidth
      endif
      return "\<space>\<backspace>\<C-o><<" . repeat("\<left>", sufflen)
    elseif b:note_type == 'note'
      return "\<Backspace>\<Backspace>"
    else
      let line = getline('.')
      " If there is no text after the cursor, delete the list item
      if len(line) == &l:shiftwidth
        let lnum = getcurpos()[1]
        if lnum == nvim_buf_line_count(0)
          return "\<C-o>dd\<C-o>$"
        else
          return "\<C-o>dd\<C-o>k\<C-o>$"
        endif
      else
        " If there is text after the entry, join with previous line
        return "\<C-o>k\<C-o>J\<delete>\<delete>\<delete>\<delete>"
      endif
    endif
  endif
  return "\<Backspace>"
endfunction

function! s:FindHeaderEnd() abort
  let i = 1
  let line = getline(i)
  while i < 5 && (line =~ '^#' || line =~? '^id:' || line =~? '^labels:')
    let i += 1
    let line = getline(i)
  endwhile
  if getline(i) == ''
    let i += 1
  endif
  return i
endfunction

function! gkeep#editor#InsertCheckbox()
  if !exists('b:note_type') || b:note_type != 'list'
    return
  endif
  let lnum = getcurpos()[1]
  if lnum < s:FindHeaderEnd()
    return
  endif
  let line = getline('.')

  " Allow the user to create a new labels: line
  if lnum < 4 && stridx('labels: ', line) != -1
    return
  endif

  if match(line, '^\s*\[.\] ') != 0
    let spacing = indent('.')
    let norm_spacing = (spacing / &l:shiftwidth) * &l:shiftwidth
    let col = getcurpos()[2]
    if spacing > 0
      let line = line[spacing:]
    endif
    " If the line starts with a checkbox fragment, remove it
    let m = matchend(line, '^\[.\?\]\?')
    if m != -1
      let line = line[m:]
    endif
    call setline('.', repeat(' ', norm_spacing) . '[ ] ' . line)
    call cursor(0, col + &l:shiftwidth)
  endif
endfunction

function! s:AtListItemStart() abort
  if exists('b:note_type') 
    let col = getcurpos()[2]
    if b:note_type == 'list'
      if col <= matchend(getline('.'), '^\s*\[.\] ') + 1
        return v:true
      endif
    elseif b:note_type == 'note'
      if col <= matchend(getline('.'), '^\s*[+*\-•] ') + 1
        return v:true
      endif
    end
  endif
  return v:false
endfunction
