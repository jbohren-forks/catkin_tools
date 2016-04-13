#compdef catkin

local context state state_descr line
typeset -A opt_args

local packages;
packages=('aaa' 'aab' 'aac' 'ddd' 'foo' 'bar')

local profiles;
profiles=('default' 'debug')

_arguments -C \
  {-h,--help}'[Show usage help]'\
  '--force-color[Force colored output]'\
  '--no-color[Force non-colored output]'\
  ':verbs:->verbs'\
  '*:: :->args'\
  && ret=0

case "$state" in
  (verbs)
    local verbs;
    verbs=(
      'build:Build packages'
      'clean:Clean workspace components'
      'config:Configure workspace'
      'create:Create workspace components'
      'init:Initialize workspace'
      'list:List workspace components'
      'profile:Switch between configurations'
    )
    _describe -t verbs 'verb' verbs && ret=0
    ;;
  (args)
    case $line[1] in
      (build) 
        _arguments -C \
          {-w,--workspace}'[The workspace to build]:workspace:_files'\
          '--profile[Which configuration profile to use]:profile:($profiles)'\
          '--get-env[Print the environment for a given workspace]:package:->packages'\
          '--force-cmake[Force CMake to run]'\
          '--pre-clean[Execute clean target before building]'\
          {-c,--continue-on-failure}'[Keep building packages even when others fail]'\
          {-n,--dry-run}'[Show build process without actually building]'\
          '(--this)--unbuilt[Build packages which have not been built]'\
          '(--unbuilt)--this[Build this package]'\
          '(--start-with-this)--start-with[Skip all packages which this depends on]:package:($packages)'\
          '(--start-with)--start--with--this[Skip all packages which this depends]'\
          '--no-deps[Only build specified packages]'\
          && ret=0
        case "$state" in 
          (packages)
            _values 'packages' $packages && ret=0
            ;;
        esac;
        ;;
    esac;
    ;;
esac;

return 1;
