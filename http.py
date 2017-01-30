"""Methods for performing HTTP requests."""

from warnings import warn
from time import sleep
import socket
import urllib2
import sys
import subprocess

import const #const.py
from common import dprint #common.py
import base64mime #base64mime.py

const.NUM_SEC_SLEEP_DEFAULT = 60
const.NUM_HTTP_RETRIES = 3
const.NUM_SEC_TIMEOUT = 120

const.URLLIB = 1
const.CURL = 2
const.DEFAULT_HTTP_METHOD = const.CURL

class MaxTriesExceededError(Exception):
    """Max tries for HTTP request exceeeded."""
    pass

class RequestWithMethod(urllib2.Request):
    """Extension of `Request` permitting overriding HTTP request method."""
    def __init__(self, *args, **kwargs):
        self._method = kwargs.pop('method', None)
        urllib2.Request.__init__(self, *args, **kwargs)

    def get_method(self):
        if self._method:
            return self._method
        else:
            return super(RequestWithMethod, self).get_method()

def _handle_http_429(err):
    """
    Todos:
        Ideally we should get the HTTP response headers and extract the
        "Retry-After" value to inform us how many seconds we should sleep,
        but I haven't found a good way to do this with urllib2.
    """
    secs = const.NUM_SEC_SLEEP_DEFAULT

    print(("Reached maximum number of requests for Zendesk API  -- waiting for "
           "%d seconds before trying again.") % secs)
    sleep(secs)

def _handle_generic_http_err():
    secs = const.NUM_SEC_SLEEP_DEFAULT
    print(("Encountered a problem requesting data from the Zendesk API -- "
           "waiting %d seconds before trying agin.") % secs)
    sleep(secs)

def fetch_request(req):
    """Fetch urllib2 request and handle errors.

    Some Errors that this function handles:

    * HTTP 429: Too Many Requests: Number of requests per time interval for
        Zendesk user's SLA exceeded. This function will sleep for the number of
        seconds recommended by the "Retry-After" header, and try again.
    * HTTP 422: Unprocessable Entity: This can be returned, for example, when
        accessing the /incremental/tickets.json endpoint with a higher start
        time than currently exists. In such an instance, the JSON body of the
        response will be something such as:
        {"error":"InvalidValue","description":"Too recent start_time.
        Use a start_time older than 5 minutes"}

    Returns: str if page results can be fetched, otherwise None

    Raises:
        MaxTriesExceededError: Raised if max # of tries after failure is
            exceeded.
    """
    response = None
    for _ in range(0, const.NUM_HTTP_RETRIES + 1):
        try:
            response = urllib2.urlopen(req, timeout=const.NUM_SEC_TIMEOUT)
            if response is None:
                sys.exit("Could not open requested resource.")
            else:
                try:
                    if response.msg != 'OK':
                        warn("Server message in response: %s" % response.msg)
                except AttributeError:
                    pass
                return response.read()

        except urllib2.HTTPError as err:
            try:
                dprint("HTTP %d: %s" % (err.code, err.read()))
            except Exception:
                pass
            if err.code == 422:
                return None
            elif err.code == 429:
                _handle_http_429(err)
            else:
                _handle_generic_http_err()

        except urllib2.URLError as err:
            try:
                dprint("URLError: %s" % (err.reason))
            except Exception:
                pass
            if err.reason == 'Unprocessable Entity':
                return None
            elif err.reason == 'Too Many Requests':
                _handle_http_429(err)
            else:
                _handle_generic_http_err()

        except (socket.timeout, socket.error) as err:
            _handle_generic_http_err()

    raise MaxTriesExceededError

def fetch_curl(url, username, token_pass, is_delete=False):
    """Get HTTP responses for specified URL using cURL command."""
    #using curl is an ugly hack but makes code short and gives countdown timer
    cmd = 'curl -u %s/token:%s "%s"' % (username, token_pass, url)
    if is_delete:
        cmd = "%s -X DELETE" % cmd
    result = _get_command_result(cmd)
    return result[result.index('{'):] #ignore cURL output up until JSON starts

def _get_command_result(command):
    num_retries_remaining = const.NUM_SEC_SLEEP_DEFAULT
    while num_retries_remaining > 0:
        try:
            return subprocess.check_output(command, stderr=None, shell=True)
        except subprocess.CalledProcessError:
            #Triggered, for example, if curl returns error code 56 (ssl_read)
            sleep(const.NUM_SEC_SLEEP_DEFAULT)
            num_retries_remaining -= 1


def get_reponse(url, username, password, auth_type, is_delete=False,
                http_method=const.DEFAULT_HTTP_METHOD):
    """Submit HTTP request and fetch response.
    Supports both urllib2-style request and cURL-style request.

    Args:
        url (str): The url to fetch for.
        username (str)
        password (str)
        auth_type (int): AUTH_PASSWORD or AUTH_API_TOKEN
        is_delete (bool): Is this a delete operation? (Default: false)

    Returns: (str) Response, hopefully valid JSON
    """
    if http_method == const.URLLIB:
        req = None
        if is_delete:
            req = RequestWithMethod(url=url, method='DELETE')
        else:
            req = urllib2.Request(url)
        set_auth(req, username, password, auth_type)

        return fetch_request(req)

    elif http_method == const.CURL:
        if auth_type == const.AUTH_PASSWORD:
            raise ValueError("Password-based authentication for cURL lookup "
                             "not yet supported. (TODO)")
        elif auth_type == const.AUTH_API_TOKEN:
            return fetch_curl(url, username, password, is_delete)
        else:
            raise ValueError("Invalid value for auth_type.")
    else:
        raise ValueError("Invalid value for DEFAULT_HTTP_METHOD.")

def set_auth(request, username, secret, auth_type):
    """Sets the Basic HTTP Auth creds for an HTTP request to the Zendesk API.

    Args:
        request (`urllib2`.`Request`): The request object to be modified.
        username (str): The Zendesk username or email address to authenticate
            with.
        secret (str): The password or API key to authenticate with.
        auth_type (int): Type of authentication to Zendesk API. Valid values:
            * const.AUTH_PASSWORD
            * const.AUTH_API_TOKEN
    See:
    http://stackoverflow.com/questions/2407126/python-urllib2-basic-auth-problem
    """
    assert isinstance(request, urllib2.Request)
    assert isinstance(username, str)
    assert isinstance(secret, str)
    assert isinstance(auth_type, int)

    b64str = ''
    if auth_type == const.AUTH_PASSWORD:
        creds = '%s:%s' % (username, secret)
    elif auth_type == const.AUTH_API_TOKEN:
        creds = '%s/token:%s' % (username, secret)
    else:
        raise ValueError('Invalid auth_type')

    b64str = base64mime.encode(creds).replace('\n', '')
    request.add_header("Authorization", "Basic %s" % b64str)
