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

How this works:
1. Uses the /incremental ticket query API endpoint to find all visible tickets.
2. Uses the normal /destroy_many API endpoint to put those tickets into a
"deleted" status, removing their accessibility from the web UI but not the
/incremental ticket query API endpoint.
3. Uses an undocumented version of /destroy_many to change sensitive fields
in the "deleted" tickets to "SCRUBBED" or "x", thus "scrubbing" the tickets.

Note that, at the time of writing this description, you cannot skip step 2 and
go directly to step 3.

Examples:

    Delete all tickets 29 days or older in the "codebros-dot-com" Zendesk domain
    using the user account named "john":

    $ python delete_old_tickets.py -password john correcthorsebatterystaple codebros-dot-com 29

    Delete all tickets 30 days or older in the "codebros-dot-com" Zendesk domain
    using an API token under the user account with email address
    "sally@codebros.com":

    $ python delete_old_tickets.py -api-token sally@codebros.com H34h2hd38hFD29fah codebros-dot-com

"""

import re
import sys
from datetime import datetime, date, timedelta
import json
from operator import itemgetter
from urllib import urlencode
import const #const.py
import prompt #prompt.py
import http #http.py
from common import dprint, const #common.py

const.DEFAULT_DAYS_OLD = 30
const.DELETE_ONLY_CLOSED = True

#deleted|pending|new|solved|hold|closed|open
const.DELETE_WORTHY_STATUS = ["solved", "closed", "deleted"]

#set to None to disable; primarily intended for debugging
const.MAX_TICKETS = None

class ZendeskQueryTypeEnum(object):
    """Specifies which API endpoint to use when enumerating tickets."""
    INCREMENTAL = 1
    SEARCH = 2

const.DEFAULT_QUERY_TYPE = ZendeskQueryTypeEnum.INCREMENTAL

def _main():
    args = _get_args()

    print "Querying Zendesk for old tickets..."

    tickets = get_all_old_tickets_sorted(
        args, const.DEFAULT_QUERY_TYPE, const.DELETE_ONLY_CLOSED)
    ids = [ticket[0] for ticket in tickets]
    if len(tickets) == 0:
        print "Could not find any tickets matching the specified criteria."
    else:
        print(("Found %d total tickets, with an earliest creation date of '%s' "
               "(ticket ID %d) and latest creation date of '%s' (ticket ID "
               "%d)") % (len(tickets), tickets[0][1], tickets[0][0],
                         tickets[-1][1], tickets[-1][0]))

    question = "Delete all %d old tickets?" % len(tickets)
    if prompt.query_yes_no(question, default='yes'):
        delete_tickets(args, ids, scrub=False)
        delete_tickets(args, ids, scrub=True)

        tickets = get_all_old_tickets_sorted(
            args, const.DEFAULT_QUERY_TYPE, const.DELETE_ONLY_CLOSED)
        print "After deletion, %d old tickets remain." % len(tickets)
    else:
        print "No tickets deleted."


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

def get_all_old_tickets_sorted(args, query_type, closed_only=True):
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
        closed_only (bool): Include only tickets that have been closed.

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
        assert len(ticket_tuple) == 3
        ticket_create_date = ticket_tuple[1]
        ticket_status = ticket_tuple[2]
        if _days_since_today(ticket_create_date) > int(args['days']):
            if not closed_only or ticket_status in const.DELETE_WORTHY_STATUS:
                old_tickets.append(ticket_tuple)

    return sorted(old_tickets, key=itemgetter(1))

def get_all_ids_and_creation_dates_search(args):
    """Fetches list of all ticket IDs and creation dates.

    This uses the /search API endpoint, and so will only return a limited
    subset of all tickets accessible through various other endpoints.

    Ticket fields in JSON returned by API:
    * created_at
    * id
    * status = deleted|pending|new|solved|hold|closed|open
    ...

    Returns:
        List of 3-tuples (ticket id, created at YYYY-MM-DD, status)
    """
    url = get_search_url(args['subdomain'], args['days'])
    tickets = []
    while url is not None:
        dprint(url)

        #get_reponse(url, username, password, auth_type, is_delete=False,
        #                 http_method=const.DEFAULT_HTTP_METHOD)


        #search_req = urllib2.Request(url)
        #set_auth(search_req, args['username'], args['password'],
        #         args['auth_type'])
        print "Fetching tickets from Zendesk via /search..."
        result = None
        try:
            result = http.get_reponse(
                url=url, username=args['username'], password=args['password'],
                auth_type=args['auth_type'], is_delete=False,
                http_method=http.const.CURL)

        except http.MaxTriesExceededError:
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
                    ticket_status = ticket['status']
                    matches = re.match(r'^(\d{4}-\d{2}-\d{2}).*$',
                                       ticket['created_at'])
                    if matches is not None and len(matches.groups()) > 0:
                        created_date = matches.group(1)
                        tickets.append((ticket_id, created_date, ticket_status))
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

        if const.MAX_TICKETS is not None and len(tickets) >= const.MAX_TICKETS:
            break

    return tickets

def get_all_ids_and_creation_dates_incremental(args):
    """Fetches list of all ticket IDs and creation dates.

    This uses the /incremental API endpoint, and therefore also will return
    tickets that are "archived" or which have been set to a "deleted" status but
    are still accessible through the API.

    Ticket fields in JSON returned by API:
    * created_at
    * id
    * status = deleted|pending|new|solved|hold|closed|open
    ...

    Args:
        args (`dict`): The list of arguments received in the `_main function:
            * subdomain
            * username
            * password
            * auth_type

    Returns:
        List: List of 3-tuples (id, created_at, status) where "id" is an integer
            id for a ticket, "created_at" is a str representing the date that
            the ticket record was created in YYYY-MM-DD format, and "status"
            is a str representing the status of the ticket in Zendesk.
    """

    url = get_list_all_tix_url(args['subdomain'])
    tickets = []
    while url is not None:
        dprint(url)
        #search_req = urllib2.Request(url)
        #set_auth(search_req, args['username'], args['password'],
        #         args['auth_type'])
        print "Fetching up to 1000 tickets from Zendesk..."
        #result = http.fetch_request(search_req)

        result = http.get_reponse(
            url=url, username=args['username'], password=args['password'],
            auth_type=args['auth_type'], is_delete=False,
            http_method=http.const.CURL)

        if result is not None:
            result_json = json.loads(result)
            if 'tickets' in result_json:
                dprint("Received %d tickets" % len(result_json['tickets']))
                for ticket in result_json['tickets']:
                    assert 'created_at' in ticket and 'id' in ticket
                    ticket_id = int(ticket['id'])
                    status = ticket['status']
                    matches = re.match(r'^(\d{4}-\d{2}-\d{2}).*$',
                                       ticket['created_at'])
                    if matches is not None and len(matches.groups()) > 0:
                        created_date = matches.group(1)
                        tickets.append((ticket_id, created_date, status))
                        #print("DEBUG: Found ticket id %d created %s" %
                        #      (ticket_id, created_date))
                    else:
                        sys.exit(("Could not find date in '%s' for ticket id "
                                  "%s") % (ticket['created_at'], ticket['id']))

                if 'next_page' in result_json:
                    url = result_json['next_page']
                    #if len(tickets) > 100: #DEBUG
                    #    url = None  #DEBUG
                else:
                    sys.exit("Expected the 'next_page' field in result "
                             "returned from Zendesk API, but it was missing.")
            else:
                msg = ""
                try:
                    msg = ("Received unrecognized response from Zendesk API: %s" %
                         str(result_json.read()))
                except AttributeError:
                    msg = ("Received unrecognized response from Zendesk API: %s" %
                         str(result_json))
                print msg
                print("Exiting ticket collection process. Examine the error "
                      "message above to determine whether tickets may have "
                      "been missed.")
                return tickets
        else:
            #result was None, usually due to HTTP 422 "Too recent start_time.
            #Use a start_time older than 5 minutes"
            url = None

        if const.MAX_TICKETS is not None and len(tickets) >= const.MAX_TICKETS:
            break

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

def get_scrub_delete_url(subdomain, ids):
    """Builds a URL that with scrub data from the specified deleted tickets.

    Args:
        subdomain (str): The Zendesk subdomain for your organization.
        ids (List[int]): A list of ticket ids to delete. Cannot exceed 100 ids.
    """
    if len(ids) > 100:
        raise OverflowError("Too many ticket ids specified in "
                            "get_scrub_delete_url().")

    ids_str = ','.join([str(ticket_id) for ticket_id in ids])
    url = (("https://%s.zendesk.com/api/lotus/tickets/deleted/destroy_many.json"
            "?ids=%s") % (subdomain, ids_str))
    return url

def print_usage():
    """Prints syntax for usage and exits the program."""
    print("usage:\tpython delete-old-tickets.py auth-type zendesk-username "
          "zendesk-password zendesk-subdomain [number-days-considered-old "
          "(Default: 30)]")
    sys.exit()

def delete_tickets(args, ids, scrub=False):
    """Deletes the tickets specified in the search results JSON.

    Args:
        args (`dict`): The list of arguments received in the `_main function:
            * subdomain
            * username
            * password
            * auth_type
        ids (List[int]): A list of IDs of tickets to delete.
        scrub (Optioanl[bool]): Indicates whether to do a delete operation or a
            ticket scrub operation. Default: delete (False)

    The bulk delete API operation can only delete up to 100 tickets at a time:
    https://developer.zendesk.com/rest_api/docs/core/tickets#bulk-delete-tickets
    """
    while len(ids) > 0:
        bulk_ids = ids[:100]
        ids = ids[100:]
        delete_url = ""
        if scrub:
            delete_url = get_scrub_delete_url(args['subdomain'], bulk_ids)
        else:
            delete_url = get_bulk_delete_url(args['subdomain'], bulk_ids)
        dprint(delete_url)
        #delete_req = RequestWithMethod(url=delete_url, method='DELETE')
        #set_auth(delete_req, args['username'], args['password'],
        #         args['auth_type'])

        #http.fetch_request(delete_req)
        resp = http.get_reponse(url=delete_url, username=args['username'],
            password=args['password'], auth_type=args['auth_type'],
            is_delete=True, http_method=http.const.CURL)
        dprint(resp)

def _days_since_today(original_date):
    """Get number of days since the specified date, as of now.

    Args:
        original_date (str): Original date.
    """
    diff = datetime.today() - datetime.strptime(original_date, "%Y-%m-%d")
    return diff.days



if __name__ == '__main__':
    _main()
