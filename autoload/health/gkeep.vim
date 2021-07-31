

function! health#gkeep#check()
  call health#report_start('gkeep')
  if !has('python3')
    call health#report_error('Python provider error:', ["gkeep requires python3", "Run :checkhealth provider for more info"])
    return
  endif

  python3 import sys
  let exe = py3eval('sys.executable')
  let [ok, error] = provider#pythonx#CheckForModule(exe, 'gkeepapi', 3)
  if !ok
    call health#report_error(error)
  else
    python3 import gkeepapi
    let l:version = py3eval('gkeepapi.__version__')
    call health#report_ok("gkeepapi " . l:version . " installed")
  endif
  let [ok, error] = provider#pythonx#CheckForModule(exe, 'keyring', 3)
  if !ok
    call health#report_error(error)
  else
    call health#report_ok("keyring installed")
  endif

  if exists(':Gkeep') != 2
    call health#report_error('Remote plugin not found', 'Try running :UpdateRemotePlugins and restart')
    return
  endif

  let health = _gkeep_health()

  if health.logged_in
    call health#report_ok('Logged in as ' . health.email)
  else
    call health#report_warn('Not logged in', 'Try :Gkeep login')
  endif

  if !empty(health.sync_dir)
    call health#report_info("Syncing notes to " . health.sync_dir)
  endif
  if health.support_neorg
    call health#report_ok("Neorg support enabled")
  endif

  let status = GkeepStatus()
  if !empty(status)
    call health#report_info(status)
  endif
  call health#report_info("Log file: " . stdpath('cache') . '/gkeep.log')
endfunction
