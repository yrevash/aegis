---
id: doc-seed-0003
kind: runbook
category: technical
tags: [technical, login, outage, 500]
title: Runbook — login failures (HTTP 500)
---

# Runbook — login failures (HTTP 500)

When customers report login failures returning HTTP 500, first check the auth
service status page for an active incident. If an incident is open, link the
request to it and set status to waiting_customer with an ETA. If not, confirm the
customer's region and clear their cached session. Ask them to retry from an
incognito window. If the error persists, collect the request id and browser
console output, then assign to Tier-2 Technical. Record each step as a case note
so the resolution time is measurable.
