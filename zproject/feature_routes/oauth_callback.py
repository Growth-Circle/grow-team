from django.urls import path
from django.urls.resolvers import URLPattern

from zerver.views.oauth_callback import oauth_callback_page

api_patterns: list[URLPattern] = []
page_patterns: list[URLPattern] = [
    path("oauth/callback/<str:provider>", oauth_callback_page),
]
