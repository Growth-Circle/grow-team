from django.urls.resolvers import URLPattern

# device_page owns its own API and page routes (see PLAN.md Sanji WP04, section
# 4.2). This module starts empty; the work package that builds this area
# fills it in without needing to touch zproject/urls.py again.
api_patterns: list[URLPattern] = []
page_patterns: list[URLPattern] = []
