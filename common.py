"""Common functions for different modules."""
import const #const.py

const.DEBUG_PRINT = True

const.AUTH_PASSWORD = 1
const.AUTH_API_TOKEN = 2

def dprint(data):
    """Prints debug information if DEBUG_PRINT mode is enabled."""
    if const.DEBUG_PRINT:
        print "DEBUG: %s" % str(data)
