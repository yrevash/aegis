---
id: doc-seed-0002
kind: policy
category: general
tags: [escalation, sla, priority]
title: Escalation and SLA policy
---

# Escalation and SLA policy

Every request carries a resolution SLA based on its priority: urgent = 8 hours,
high = 24 hours, medium = 48 hours, low = 72 hours. If a request is at risk of
breaching its SLA, escalate it to Tier-2 and set its priority no lower than high.
Reopened requests inherit the original SLA clock plus a fresh 24-hour grace
window. Escalations must be justified in a case note naming the blocking reason.
Enterprise and premium customers are escalated one tier earlier than standard.
