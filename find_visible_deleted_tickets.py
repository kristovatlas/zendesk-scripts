"""Finds all "deleted" but visible tickets using the zendesk /incremental

Usage:
    $ python find_visible_deleted_tickets.py domain username token-pw

Example:
    $ python find_visible_deleted_tickets.py bobco bob@bobco.com 0123456789abcde

TODO: use http.py
"""

import subprocess
import sys
import json
import pickle
import os

PICKLE_FILENAME = 'visible_deleted_tix_data.pkl'
TICKET_ID_FILENAME = 'visible_deleted_ids.txt'

def _main():
    domain = str(sys.argv[1])
    username = str(sys.argv[2])
    token_pass = str(sys.argv[3])

    tix = _find_tickets(domain, username, token_pass, start_time=0)
    print "\n\n----\nFound %d deleted but visible tickets:" % len(tix)
    _dump_ids_to_file(tix)
    print json.dumps(tix, indent=4, sort_keys=True)

def _dump_ids_to_file(tix):
    with open(TICKET_ID_FILENAME, 'w') as txt:
        for ticket in tix:
            txt.write("%s\n" % ticket['id'])
    print "Wrote %d ticket ids to '%s'" % (len(tix), TICKET_ID_FILENAME)

def _get_command_result(command):
    return subprocess.check_output(
        command, stderr=None, shell=True)

def _fetch_url(url, username, token_pass):
    #using curl is an ugly hack but makes code short and gives countdown timer
    cmd = 'curl -u %s/token:%s "%s"' % (username, token_pass, url)
    result = _get_command_result(cmd)
    return result[result.index('{'):]

def store_to_pickle(url, tix, unique_ticket_ids):
    """Write results to file."""
    pkl_file = open(PICKLE_FILENAME, 'wb')
    pickle.dump(url, pkl_file, pickle.HIGHEST_PROTOCOL)
    pickle.dump(tix, pkl_file, pickle.HIGHEST_PROTOCOL)
    pickle.dump(unique_ticket_ids, pkl_file, pickle.HIGHEST_PROTOCOL)
    pkl_file.close()
    print "Saved results to '%s'." % PICKLE_FILENAME

def load_from_pickle():
    """Load results from file."""
    pkl_file = open(PICKLE_FILENAME, 'rb')
    url = pickle.load(pkl_file)
    tix = pickle.load(pkl_file)
    unique_ticket_ids = pickle.load(pkl_file)
    return (url, tix, unique_ticket_ids)

def _find_tickets(domain, username, token_pass, start_time):
    url = (("https://%s.zendesk.com/api/v2/incremental/tickets.json?"
            "start_time=%d") % (domain, start_time))

    tix = []
    unique_ticket_ids = set()

    #if pickle file exists, grab data from that and pick up there instead
    if os.path.isfile(PICKLE_FILENAME):
        print "Found pickle file. Loading..."
        url, tix, unique_ticket_ids = load_from_pickle()
        os.remove(PICKLE_FILENAME)
        print("url = %s, %d unique ticket ids, and %d tix" %
              (url, len(tix), len(unique_ticket_ids)))

    try:
        while True:
            print "Fetching '%s'" % url
            data = json.loads(_fetch_url(url, username, token_pass))
            if 'tickets' not in data:
                break
            first_id = None
            last_id = None
            for ticket in data['tickets']:

                if first_id is None:
                    first_id = int(ticket['id'])
                if int(ticket['id']) > last_id:
                    last_id = int(ticket['id'])

                if (ticket['status'] == 'deleted' and
                        int(ticket['id']) not in unique_ticket_ids):
                    unique_ticket_ids.add(int(ticket['id']))
                    tix.append(ticket)

            print(("STATUS: Retrieved %d tickets (ticket ids %s through %s). %d "
                   "deleted but visible tickets so far.") %
                  (len(data['tickets']), str(first_id), str(last_id), len(tix)))

            if 'next_page' in data:
                url = data['next_page']
            else:
                break

    except KeyboardInterrupt:
        store_to_pickle(url=url, tix=tix, unique_ticket_ids=unique_ticket_ids)

    return tix


if __name__ == '__main__':
    _main()
