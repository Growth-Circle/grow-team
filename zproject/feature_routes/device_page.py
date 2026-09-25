from django.urls.resolvers import URLPattern

# device_page owns its own API and page routes. This module starts empty;
# whichever feature is built in this area fills it in without needing
# to touch zproject/urls.py again.
api_patterns: list[URLPattern] = []
page_patterns: list[URLPattern] = []
