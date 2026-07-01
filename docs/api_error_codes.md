# API Error Codes

| code | HTTP | Meaning |
|---|---:|---|
| bad_request | 400 | Invalid path/query parameter |
| validation_error | 400 | Missing or invalid JSON body |
| unauthorized | 401 | Authentication required |
| forbidden | 403 | Authenticated but insufficient permission |
| vip_required | 403 | VIP/admin required |
| rate_limited | 429 | Too many requests |
| prediction_failed | 503 | Prediction engine unavailable |
| billing_error | 400 | Billing/order operation failed |
| upstream_unavailable | 503 | External data provider unavailable |
| internal_error | 500 | Unexpected server failure |
