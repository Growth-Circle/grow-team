from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import notification_check

# push owns its own API and page routes. Whichever feature is built in
# this area adds its routes here without needing to touch zproject/urls.py.
api_patterns: list[URLPattern] = [
    rest_path("mobile_push/check", POST=notification_check.check_mobile_push),
]
page_patterns: list[URLPattern] = []
