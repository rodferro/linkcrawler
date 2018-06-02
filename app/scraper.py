from db import link

import re
import requests
from bs4 import BeautifulSoup
from collections import deque

def extract_links(from_url, n_iter=100):
    count = 0
    queue = deque()
    queue.append(from_url)
    while queue:
        from_url = queue.popleft()
        html = requests.get(from_url).text
        html_soup = BeautifulSoup(html, 'html.parser')
        a_tags = html_soup.find_all("a", href=re.compile("^https?://"))
        for a_tag in a_tags:
            to_url = a_tag.get('href')
            save_link(from_url, to_url)
            queue.append(to_url)
            # let's limit the number of iterations, 
            # otherwise we could be here for a very long time.
            count += 1
            if count > n_iter:
                return

def save_link(from_url, to_url):
    link.insert_ignore(dict(from_url=from_url, to_url=to_url), 
                       ['from_url', 'to_url'])

def list_links():
    return link.all()
