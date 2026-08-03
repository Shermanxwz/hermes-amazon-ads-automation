# Amazon official contract references

This project intentionally does not copy or modify Amazon's full Postman collection.

The CI contract check reads the public collection from:

- `amzn/ads-advanced-tools-docs/postman/Amazon_Ads_API.postman_collection.json`

Run:

```bash
python scripts/sync_official_contracts.py --check
python scripts/sync_official_contracts.py --output official/postman-capabilities.local.json
```

The check verifies that the upstream collection still exposes the capability families used by
this project: authentication, profiles, Sponsored Products, Sponsored Brands, Sponsored Display,
reporting, Marketing Stream, recommendations, budgets, test accounts and exports.

The runtime does **not** depend on Postman. Hermes discovers the live Amazon Ads MCP schemas from
its own `mcp-amazon-ads` registry; Postman is the independent official API coverage reference.

The same CI job also sends an unauthenticated MCP `initialize` request to
`https://advertising-ai.amazon.com/mcp`. HTTP 401/403 is the expected result:
the endpoint is reachable and authentication remains mandatory. No OAuth token is used.
