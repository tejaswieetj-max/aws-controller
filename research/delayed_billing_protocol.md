# CALIBUR Delayed-Billing Evidence Protocol

## Timestamp semantics

Keep these timestamps distinct:

- **Charge-period time:** the period to which a provider billing record applies.
- **S3 LastModified:** the modification time reported for the delivered S3 object.
- **Collector observed time:** the UTC time this collector observed the object.
- **First-observed billing-record time:** the earliest collector observation of a
  documented billing record key. This cannot be inferred from ChargePeriodStart,
  ChargePeriodEnd, or S3 LastModified alone.

The current collector records object-level observations. It does not yet establish
the first time each billing record became available to CALIBUR.

## Telemetry versus billed cost

CloudWatch usage and performance metrics are operational telemetry. They must not
be treated as final provider-billed cost unless a documented, validated pricing
calculation explicitly estimates cost; such estimates remain estimates.

Cost Explorer is a delayed cost-reporting source, not a real-time billing feed.
Repeated queries do not count as independent observations when the underlying
billing data has not changed.

## Revision evidence

Do not claim a billing revision merely because two exports have different row
counts, delivery timestamps, or aggregate totals.

A revision requires a documented stable billing-record key matched across
separately observed snapshots, with a changed cost or other revision-relevant
field. Record the source manifest/version and both collector observation times.
If identity cannot be established, report the difference as unmatched or
unresolved, not as a confirmed revision.

## Current evidence boundary

The collector has recorded S3 object metadata and observation times. These are
object-level observations, not proof of billing-record first availability,
request-to-resource linkage, runtime linkage, or a billing revision.

The CALIBUR research gate remains AMBER until genuine linked request, admission,
resource, runtime, billing, and observation-time evidence supports the claims.
