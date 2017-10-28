subdir('lib0')
subdir('lib1')

ego_lib = static_library('ego',
  dependencies : [lib0_dep, lib1_dep],
  install : true)

ego_headers = [
  'ego.h',
]

install_headers(ego_headers,
  subdir : 'ego')

ego_dep = declare_dependency(link_with : ego_lib,
  include_directories : include_directories('.'))
