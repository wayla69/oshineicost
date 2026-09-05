# -*- coding: utf-8 -*-
"""Connectors that collect customer reviews from the platforms the chain owns a
presence on, into the same row shape `POST /api/feedback/upload` accepts:

    {date, branch, source, rating, text, external_id, url, author, reply,
     replied_at, location, sentiment_hint}

  google_gbp  — Google Business Profile (the Maps listings): API + Takeout
  facebook    — Facebook Pages: recommendations (reviews) + post comments

No connector talks to the network itself: each takes an `http(method, url,
headers, body) -> (status, json)` callable, so the tests inject a fake and
fetch_feedback.py passes urllib. Credentials live in backend/.feedback_config.json
(gitignored, copied by Backup DB.bat with the other secrets).
"""
