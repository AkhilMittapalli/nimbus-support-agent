---
doc_id: api-rate-limits
title: Developer API Limits
category: technical
tiers: [pro, enterprise]
---
# Developer API Limits

The Nimbus REST API is available on Pro and Enterprise plans. API keys are created in Admin Console > Developers.

## Rate limits

- **Pro:** 600 requests per minute per organisation, 10 concurrent uploads.
- **Enterprise:** 6,000 requests per minute per organisation, 100 concurrent uploads. Higher limits can be requested through the TAM.

When a limit is exceeded the API returns HTTP 429 with a `Retry-After` header. Clients should back off exponentially and retry.

## Webhooks

Webhooks deliver file events (created, updated, deleted, shared). Failed deliveries are retried 8 times over 24 hours. Webhooks that keep failing for 3 days are disabled automatically and the organisation's admins are emailed.
