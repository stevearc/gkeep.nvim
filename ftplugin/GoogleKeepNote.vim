setlocal expandtab foldmethod=expr foldexpr=gkeep#foldexpr()
set comments=b:*,b:-,b:+,b:•
setlocal autoindent
set conceallevel=2
set concealcursor=

" text-width autowrap doesn't insert bullets
setlocal formatoptions-=c
setlocal formatoptions+=orj

inoremap <expr> <buffer> <Tab> gkeep#editor#TabOrIndent()
inoremap <expr> <buffer> <Backspace> gkeep#editor#BackspaceOrDedent()
inoremap <expr> <buffer> * gkeep#editor#Bullet()
nnoremap gf <cmd>GkeepGoto<CR>

set omnifunc=_gkeep_omnifunc


aug GoogleKeepCheckboxes
  au! * <buffer>
  au TextChangedI <buffer> call gkeep#editor#InsertCheckbox()
aug END
