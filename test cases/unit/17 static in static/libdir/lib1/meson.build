lib1_lib = static_library('lib1',
  'thebest.c',
  include_directories : include_directories('.'),
  install : false)

lib1_dep = declare_dependency(link_with : lib1_lib,
  include_directories : include_directories('.'))
