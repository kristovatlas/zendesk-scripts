"""Finds a ticket using the zendesk /incremental endpoint.

Usage:
    $ python find_ticket.py domain username token-pw ticket-id [unix_start_time]

unix_start_time is an optional integer argument that is set to 0 by default

Example:
    $ python find_ticket.py bobco bob@bobco.com 0123456789abcdef 1337 1438905600

"""

import sys
import json
import re
import datetime
import http #http.py

#The number of seconds that represent a "small" difference in time. After the
#timestamp increments a "small" amount several times in a row, it's time to
#give up.
NUM_SEC_SMALL_DIFF = 10
NUM_SMALL_DIFS_TO_QUIT = 3

def _main():
    domain = str(sys.argv[1])
    username = str(sys.argv[2])
    token_pass = str(sys.argv[3])
    ticket_id = int(sys.argv[4])
    start_time = 0
    if len(sys.argv) == 6:
        start_time = int(sys.argv[5])

    _find_ticket(domain, username, token_pass, ticket_id, start_time)

def _find_ticket(domain, username, token_pass, ticket_id, start_time):
    url = (("https://%s.zendesk.com/api/v2/incremental/tickets.json?"
            "start_time=%d") % (domain, start_time))

    last_start_time = None
    num_consecutive_small_diffs = 0

    unique_ticket_ids = set()

    while True:
        print "Fetching '%s'" % url
        data = json.loads(
            http.fetch_curl(url, username, token_pass, is_delete=False))
        assert 'tickets' in data
        num_higher = 0
        first_higher = None
        last_higher = None
        first_id = None
        last_id = None
        for ticket in data['tickets']:

            unique_ticket_ids.add(ticket['id'])
            if first_id is None:
                first_id = int(ticket['id'])
            last_id = int(ticket['id'])

            if int(ticket['id']) == ticket_id:
                print json.dumps(ticket, indent=4, sort_keys=True)
                sys.exit()

            elif int(ticket['id']) > ticket_id:
                num_higher += 1
                if first_higher is None:
                    first_higher = int(ticket['id'])
                last_higher = int(ticket['id'])

        if num_higher > 0:
            if first_higher > ticket_id:
                print("WARNING: First ticket found %s too high. Try earlier "
                      "time period.") % first_higher
            else:
                print(("WARNING: Found tickets with ids %s through %s higher "
                       "than target ticket id. The desired ticket may not "
                       "be accessible via the incremental search API.") %
                      (first_higher, last_higher))

        print("STATUS: Retrieved %d tickets (ticket ids %s through %s)" %
              (len(data['tickets']), str(first_id), str(last_id)))

        assert 'next_page' in data
        last_start_time = _get_start_time(url)
        url = data['next_page']
        next_start_time = _get_start_time(url)
        if (last_start_time is not None and
                is_within_24hrs(last_start_time) and
                _is_time_diff_small(last_start_time, next_start_time)):
            num_consecutive_small_diffs += 1
            if num_consecutive_small_diffs == NUM_SMALL_DIFS_TO_QUIT:
                print "Reached likely end of searchable tickets; giving up."
                break
        else:
            num_consecutive_small_diffs = 0
            print("Fetching up to 1000 tickets starting at %s (%s)" %
                  (_unix_time_to_str(next_start_time), str(next_start_time)))

    print "Retrieved %d unique tickets total." % len(unique_ticket_ids)

    print("Could not find a ticket with the specified ticket ID. This may mean "
          "that the ticket was created or modified too recently to be found.")

def _is_time_diff_small(timestamp1, timestamp2):
    return timestamp2 - timestamp1 <= NUM_SEC_SMALL_DIFF

def _get_start_time(url):
    """/api/v2/incremental/tickets.json?start_time=xxxxx"""
    match = re.search(r'start_time=(\d+)', url)
    if match:
        return int(match.groups(1)[0])

def is_within_24hrs(unix_timestamp):
    """Returns whether the timestamp was today/yesterday or not."""
    d1 = datetime.datetime.fromtimestamp(unix_timestamp)
    now = datetime.datetime.now()
    return (now - d1).days in {0, 1}

def _unix_time_to_str(unix_timestamp):
    return datetime.datetime.fromtimestamp(unix_timestamp
        ).strftime('%Y-%m-%d %H:%M:%S')

if __name__ == '__main__':
    _main()
