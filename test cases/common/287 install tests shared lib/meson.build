project('install tests shared lib', 'c')

# An installed shared library (like a "production" library).
mylib = shared_library('mylib', 'lib.c', install : true)

# A test that links to the installed library.
mytest = executable('mytest', 'test.c', link_with : mylib)
test('mytest', mytest)
