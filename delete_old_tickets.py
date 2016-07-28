"""Deletes old tickets via the v2 Zendesk API.

The only endpoint in the API to actually get all relevant tickets requires
iterating through ALL existing tickets, which may be very slow. Likeiwse, if you
have many old tickets, this may also be a slow operation. This script will
detect when your requests have exceeded the Rate Limit for your account based
on your SLA with Zendesk, and handle it gracefully.

Roles: In order to use this script, the user you are authenticating as must be
an Administrator. See the Bulk Delete Tickets action in the Zendesk API docs:
https://developer.zendesk.com/rest_api/docs/core/tickets#bulk-delete-tickets

Authentication: Auth can be performed either with a Username/Password
combination or Email address/API token combination. For documentation, see:
https://developer.zendesk.com/rest_api/docs/core/introduction#security-and-authentication

Examples:

    Delete all tickets 29 days or older in the "codebros-dot-com" Zendesk domain
    using the user account named "john":

    $ python delete_old_tickets.py -password john correcthorsebatterystaple codebros-dot-com 29

    Delete all tickets 30 days or older in the "codebros-dot-com" Zendesk domain
    using an API token under the user account with email address
    "sally@codebros.com":

    $ python delete_old_tickets.py -api-token sally@codebros.com H34h2hd38hFD29fah codebros-dot-com

"""

from warnings import warn
import re
import sys
from time import sleep
from datetime import datetime, date, timedelta
import urllib2
import json
import socket
from operator import itemgetter
from urllib import urlencode
import const #const.py
import prompt #prompt.py
import base64mime #base64mime.py

const.DEFAULT_DAYS_OLD = 30
const.AUTH_PASSWORD = 1
const.AUTH_API_TOKEN = 2

const.NUM_HTTP_RETRIES = 3
const.NUM_SEC_TIMEOUT = 120

const.NUM_SEC_SLEEP_DEFAULT = 60

const.DEBUG_PRINT = True

class MaxTriesExceededError(Exception):
    """Max tries for HTTP request exceeeded."""
    pass

class ZendeskQueryTypeEnum(object):
    """Specifies which API endpoint to use when enumerating tickets."""
    INCREMENTAL = 1
    SEARCH = 2

const.DEFAULT_QUERY_TYPE = ZendeskQueryTypeEnum.SEARCH

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

def _main():
    args = _get_args()

    print "Querying Zendesk for old tickets..."

    tickets = get_all_old_tickets_sorted(args, const.DEFAULT_QUERY_TYPE)
    ids = [ticket[0] for ticket in tickets]
    print(("Found %d total tickets, with an earliest creation date of '%s' "
           "(ticket ID %d) and latest creation date of '%s' (ticket ID %d)") %
          (len(tickets), tickets[0][1], tickets[0][0], tickets[-1][1],
           tickets[-1][0]))

    question = "Delete all %d old tickets?" % len(tickets)
    if prompt.query_yes_no(question, default='yes'):
        delete_tickets(args, ids)

        tickets = get_all_old_tickets_sorted(args, const.DEFAULT_QUERY_TYPE)
        print "After deletion, %d old tickets remain." % len(tickets)
    else:
        print "No tickets deleted."

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

def _get_args():
    """Returns the proper args from the command line as well as defaults."""
    args = dict()
    args['username'] = ''
    args['password'] = ''
    args['subdomain'] = ''
    args['days'] = const.DEFAULT_DAYS_OLD
    args['auth_type'] = -1

    if len(sys.argv) == 2 and sys.argv[1] in ('-h', '--help'):
        print_usage()

    if len(sys.argv) in (5, 6):
        auth_type = sys.argv[1]
        if auth_type == '-password':
            args['auth_type'] = const.AUTH_PASSWORD
        elif auth_type == '-api-token':
            args['auth_type'] = const.AUTH_API_TOKEN
        else:
            print "Error: Invalid auth type '$s'" % sys.argv[1]
            print_usage()
        try:
            args['username'] = str(sys.argv[2])
        except ValueError:
            print "Error: Invalid username '%s'" % sys.argv[2]
            print_usage()
        try:
            args['password'] = sys.argv[3]
        except ValueError:
            print "Error: Invalid password or API token '%s'" % sys.argv[3]
            print_usage()
        try:
            args['subdomain'] = sys.argv[4]
        except ValueError:
            print "Error: Invalid subdomain'%s'" % sys.argv[4]
            print_usage()
        if len(sys.argv) == 6:
            try:
                args['days'] = int(sys.argv[5])
            except ValueError:
                print("Error: Invalid argument '%s' for number of days old." %
                      sys.argv[5])
                print_usage()
    else:
        print_usage()

    return args

def get_all_old_tickets_sorted(args, query_type):
    """Fetches list of all ticket ID and creation dates for 'old' tickets.

    Tickets will be sorted from oldest creation date first to newest last.

    Args:
        args (`dict`): The list of arguments received in the `_main function:
            * subdomain
            * username
            * password
            * auth_type
            * days
        query_type (int): The query endpoint used for fetching ticket data.
            See: `ZendeskQueryTypeNum`

    Returns:
        List: List of tuples (id, created_at) where "id" is an integer id for
            a ticket and "created_at" is a str representing the date that the
            ticket record was created in YYYY-MM-DD format.
    """
    tickets = None
    if query_type == ZendeskQueryTypeEnum.INCREMENTAL:
        tickets = get_all_ids_and_creation_dates_incremental(args)
    elif query_type == ZendeskQueryTypeEnum.SEARCH:
        tickets = get_all_ids_and_creation_dates_search(args)
    else:
        raise TypeError

    old_tickets = []
    for ticket_tuple in tickets:
        assert len(ticket_tuple) == 2
        ticket_create_date = ticket_tuple[1]
        if _days_since_today(ticket_create_date) > int(args['days']):
            old_tickets.append(ticket_tuple)

    return sorted(old_tickets, key=itemgetter(1))

def get_all_ids_and_creation_dates_search(args):
    """Fetches list of all ticket IDs and creation dates.

    This uses the /search API endpoint, and so will only return a limited
    subset of all tickets accessible through various other endpoints.

    Ticket fields in JSON returned by API:
    *
    """
    url = get_search_url(args['subdomain'], args['days'])
    tickets = []
    while url is not None:
        dprint(url)
        search_req = urllib2.Request(url)
        set_auth(search_req, args['username'], args['password'],
                 args['auth_type'])
        print "Fetching tickets from Zendesk via /search..."
        result = None
        try:
            result = _fetch_request(search_req)
        except MaxTriesExceededError:
            print("WARNING! Maximum number of tries for fetching data was met. "
                  "This may indicate that not all tickets will be returned.")
            return tickets
        if result is not None:
            result_json = json.loads(result)
            if 'results' in result_json:
                dprint("Retreived %d old tickets." % len(result_json['results']))
                for ticket in result_json['results']:
                    assert 'created_at' in ticket and 'id' in ticket
                    ticket_id = int(ticket['id'])
                    matches = re.match(r'^(\d{4}-\d{2}-\d{2}).*$',
                                       ticket['created_at'])
                    if matches is not None and len(matches.groups()) > 0:
                        created_date = matches.group(1)
                        tickets.append((ticket_id, created_date))
                        dprint("Found ticket id %d created %s" %
                               (ticket_id, created_date))
                    else:
                        sys.exit(("Could not find date in '%s' for ticket id "
                                  "%s") % (ticket['created_at'], ticket['id']))

                if 'next_page' in result_json:
                    url = result_json['next_page']
                else:
                    sys.exit("Expected the 'next_page' field in result "
                             "returned from Zendesk API, but it was missing.")
            else:
                sys.exit("Received unrecognized response from Zendesk API: %s" %
                         str(result_json.read()))
    return tickets

def get_all_ids_and_creation_dates_incremental(args):
    """Fetches list of all ticket IDs and creation dates.

    This uses the /incremental API endpoint, and therefore also will return
    tickets that are "archived" or which have been set to a "deleted" status but
    are still accessible through the API.

    Ticket fields in JSON returned by API:
    * created_at
    * id

    Args:
        args (`dict`): The list of arguments received in the `_main function:
            * subdomain
            * username
            * password
            * auth_type

    Returns:
        List: List of tuples (id, created_at) where "id" is an integer id for
            a ticket and "created_at" is a str representing the date that the
            ticket record was created in YYYY-MM-DD format.
    """

    url = get_list_all_tix_url(args['subdomain'])
    tickets = []
    while url is not None:
        dprint(url)
        search_req = urllib2.Request(url)
        set_auth(search_req, args['username'], args['password'],
                 args['auth_type'])
        print "Fetching up to 1000 tickets from Zendesk..."
        result = _fetch_request(search_req)
        if result is not None:
            result_json = json.loads(result)
            if 'tickets' in result_json:
                dprint("Retrived %d tickets" % len(result_json['tickets']))
                for ticket in result_json['tickets']:
                    assert 'created_at' in ticket and 'id' in ticket
                    ticket_id = int(ticket['id'])
                    matches = re.match(r'^(\d{4}-\d{2}-\d{2}).*$',
                                       ticket['created_at'])
                    if matches is not None and len(matches.groups()) > 0:
                        created_date = matches.group(1)
                        tickets.append((ticket_id, created_date))
                        #print("DEBUG: Found ticket id %d created %s" %
                        #      (ticket_id, created_date))
                    else:
                        sys.exit(("Could not find date in '%s' for ticket id "
                                  "%s") % (ticket['created_at'], ticket['id']))

                if 'next_page' in result_json:
                    url = result_json['next_page']
                    if len(tickets) > 100: #DEBUG
                        url = None  #DEBUG
                else:
                    sys.exit("Expected the 'next_page' field in result "
                             "returned from Zendesk API, but it was missing.")
            else:
                sys.exit("Received unrecognized response from Zendesk API: %s" %
                         str(result_json.read()))
    return tickets

def get_search_url(subdomain, days):
    """Builds a URL that will query for all 'old' tickets.

    Uses the /search API endpoint.

    Args:
        subdomain (str): The ZenDesk subdomain for your organization.
        days (int): The number of days ago a ticket was created in order to
            be considered 'old' and eligible for deletion.
    """
    params = {'query': ('created<%s type:ticket' % _get_first_new_date(days))}
    url = ("https://%s.zendesk.com/api/v2/search.json?%s" %
           (subdomain, urlencode(params)))
    return url

def _get_first_new_date(days):
    """Returns the date in the past that is not considered 'old' as a string."""
    #http://stackoverflow.com/questions/441147/how-can-i-subtract-a-day-from-a-python-date#441152
    return str(date.today() - timedelta(days=(days - 1)))

def get_list_all_tix_url(subdomain):
    """Builds a URL that will query for all tickets that haven't been deleted.

    Args:
        subdomain (str): The Zendesk subdomain for your organization.
    """
    url = (("https://%s.zendesk.com/api/v2/incremental/tickets.json?"
            "start_time=0") % subdomain)
    return url

def get_bulk_delete_url(subdomain, ids):
    """Builds a URL that will delete the specified tickets.

    Args:
        subdomain (str): The Zendesk subdomain for your organization.
        ids (List[int]): A list of ticket ids to delete. Cannot exceed 100 ids.
    """
    if len(ids) > 100:
        raise OverflowError("Too many ticket ids specified in "
                            "get_bulk_delete_url().")
    ids_str = ','.join([str(ticket_id) for ticket_id in ids])
    url = ("https://%s.zendesk.com/api/v2/tickets/destroy_many.json?ids=%s" %
           (subdomain, ids_str))
    return url

def print_usage():
    """Prints syntax for usage and exits the program."""
    print("usage:\tpython delete-old-tickets.py auth-type zendesk-username "
          "zendesk-password zendesk-subdomain [number-days-considered-old "
          "(Default: 30)]")
    sys.exit()

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

def _fetch_request(req):
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

def delete_tickets(args, ids):
    """Deletes the tickets specified in the search results JSON.

    Args:
        args (`dict`): The list of arguments received in the `_main function:
            * subdomain
            * username
            * password
            * auth_type
        ids (List[int]): A list of IDs of tickets to delete.

    The bulk delete API operation can only delete up to 100 tickets at a time:
    https://developer.zendesk.com/rest_api/docs/core/tickets#bulk-delete-tickets
    """
    while len(ids) > 0:
        bulk_ids = ids[:100]
        ids = ids[100:]
        delete_url = get_bulk_delete_url(args['subdomain'], bulk_ids)
        dprint(delete_url)
        delete_req = RequestWithMethod(url=delete_url, method='DELETE')
        set_auth(delete_req, args['username'], args['password'],
                 args['auth_type'])

        _fetch_request(delete_req)

def _days_since_today(original_date):
    """Get number of days since the specified date, as of now.

    Args:
        original_date (str): Original date.
    """
    diff = datetime.today() - datetime.strptime(original_date, "%Y-%m-%d")
    return diff.days

def dprint(string):
    """Prints debug information if DEBUG_PRINT mode is enabled."""
    if const.DEBUG_PRINT:
        print "DEBUG: %s" % str(string)

if __name__ == '__main__':
    _main()
