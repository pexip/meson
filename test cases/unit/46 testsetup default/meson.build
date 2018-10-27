project('testsetup default', 'c')

envcheck = find_program('envcheck.py')
test('test-env', envcheck)

add_test_setup_default('mydefault', env: 'TEST_ENV=1')
add_test_setup('other', env: 'TEST_ENV=2')
