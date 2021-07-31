if exists("b:current_syntax")
  finish
endif

syn match keepTitlePre /^# / nextgroup=keepTitle
syn match keepTitle /.*$/ contained
syn region keepId start="^id:" end='$' keepend contains=keepIdPre skipwhite
syn match keepIdPre /^id/ contained nextgroup=keepSep skipwhite
syn region keepLabels start="^labels:" end="$" contains=keepLabelPre,keepQuoteLabel,keepLabel skipwhite
syn region keepQuoteLabel start='"' end='"' contained
syn match keepLabel /[^,]\+/ contained
syn match keepLabelPre /^labels/ contained nextgroup=keepSep skipwhite
syn match keepSep /:/ contained skipwhite
syn match keepListItemUnchecked /^\s*\[[ \-]\]/
syn match keepListItemChecked /^\s*\[[xX]\].*$/

" Links in the format [title](id)
syn region keepLink start="\[[^\]]*\]([^)]*" end=")" skipwhite keepend contains=keepLinkTitleStart,keepLinkTitle,keepLinkTitleEnd,keepLinkIDStart,keepLinkID,keepLinkIDEnd
syn match keepLinkTitle "[^\]]*" contained skipwhite nextgroup=keepLinkTitleEnd
syn match keepLinkID /[^)]*/ contained skipwhite conceal nextgroup=keepLinkIDEnd
syn match keepLinkTitleStart /\[/ contained skipwhite conceal nextgroup=keepLinkTitle
syn match keepLinkTitleEnd /\]/ contained skipwhite conceal nextgroup=keepLinkIDStart
syn match keepLinkIDStart /(/ contained skipwhite conceal nextgroup=keepLinkID
syn match keepLinkIDEnd /)/ contained skipwhite conceal

" Links in the format gkeep://{id}/{title}.{ext}
syn region keepUrl start='gkeep://' end='\.\w{2,4}\s' keepend contains=keepUrlProto,keepUrlProto,keepUrlSep,keepUrlTitle
syn match keepUrlTitle ".*" contained
syn match keepUrlProto "gkeep://" contained conceal nextgroup=keepUrlID
syn match keepUrlID "[^/]*" contained conceal nextgroup=keepUrlSep
syn match keepUrlSep "/" contained conceal nextgroup=keepUrlTitle


let b:current_syntax = "keep"
hi default link keepTitlePre Constant
hi default link keepTitle Title
hi default link keepIdPre Constant
hi default link keepLabelPre Constant
hi default link keepLabel String
hi default link keepQuoteLabel String
hi default link keepListItemUnchecked Special
hi default link keepListItemChecked Comment
hi default link keepLinkTitle String
hi default link keepLinkID Constant
hi default link keepUrlTitle String
hi default link keepUrlID Constant
