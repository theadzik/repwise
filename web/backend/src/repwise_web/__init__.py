"""The service behind the repwise web page.

Nothing in it remembers anything between requests: what the page shows, which
run is in progress and the Garmin session are all rows in PostgreSQL, so any
replica can answer any request and a restart forgets nothing.
"""
