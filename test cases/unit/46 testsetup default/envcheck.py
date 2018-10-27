#!/usr/bin/env python3

import os

assert('TEST_ENV' in os.environ)
print('TEST_ENV is', os.environ['TEST_ENV'])
