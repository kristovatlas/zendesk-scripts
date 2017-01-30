"""Test pickle.

"""

import pickle
import pprint

data1 = {'a': [1, 2.0, 3, 4+6j],
         'b': ('string', u'Unicode string'),
         'c': None}

my_set = set()
my_set.add(1)
my_set.add(1)
my_set.add(2)
my_set.add(3)

output = open('data.pkl', 'wb')

# Pickle dictionary using protocol 0.
pickle.dump(data1, output)

# Pickle the list using the highest protocol available.
pickle.dump(my_set, output, -1)

output.close()

pkl_file = open('data.pkl', 'rb')

data1 = pickle.load(pkl_file)
pprint.pprint(data1)

data2 = pickle.load(pkl_file)
pprint.pprint(data2)

data2.add(3)

pprint.pprint(data2)
data2.add(4)
pprint.pprint(data2)

pkl_file.close()
