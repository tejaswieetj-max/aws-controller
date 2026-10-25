# CALIBUR data quality report

**Generated:** 2026-10-25T08:16:23.282223+00:00  
**Research gate:** `AMBER`  
**Gate reason:** Profiling alone does not establish provenance, billing finality, billing-arrival delays, request-to-resource linkage, or empirical performance.

Files discovered: 11  
Files profiled: 11

> **Strict constraint:** profiling statistics are descriptive data-quality outputs only.
> They do not constitute experiment results, model evaluations, or research performance claims.
> Billing-period timestamps are period boundaries, not billing-record arrival times.
> No cross-dataset join has been established.

## `data/raw/alibaba_trace_2017/alibaba-trace-2017/batch_instance.csv`

- **Dataset:** alibaba_v2017/batch_instance.csv
- **Type:** csv
- **Size:** 858,546,781 bytes
- **SHA-256:** `195f25ef36484a2da9bb92c08e203224ed34a32f48fcbe11c01d94ecf308d817`
- **Encoding:** utf-8
- **Rows:** 16094656
- **Exact duplicate records:** None
- **Key uniqueness:** unknown

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `task_name` | `observed` | 0 | 0 | 0 | Opaque task identifier. |
| `job_name` | `observed` | 0 | 0 | 0 | Opaque job identifier. |
| `task_type` | `observed` | 3992098 | 0 | 3992098 | Integer type code; blank is a documented missing value in the trace. |
| `inst_seq_num` | `observed` | 3992098 | 0 | 3992098 | Instance sequence number within the task; blank is documented missing. |
| `start_time` | `observed` | 1342871 | 0 | 1342871 | Seconds relative to trace epoch; blank means not recorded. |
| `end_time` | `observed` | 0 | 0 | 0 | Seconds relative to trace epoch; blank means not recorded. |
| `machine_id` | `observed` | 0 | 0 | 0 | Opaque machine identifier. |
| `status` | `observed` | 0 | 0 | 0 | Categorical terminal/non-terminal state. |
| `seq_no` | `observed` | 2386677 | 0 | 2386677 | Retry sequence number; blank is documented missing. |
| `total_seq_num` | `observed` | 2386677 | 0 | 2386677 | Total retry count; blank is documented missing. |
| `cpu_avg` | `observed` | 14167556 | 0 | 14167556 | Average CPU utilisation ratio (0-1 normalised); blank is documented missing. |
| `mem_avg` | `observed` | 14166324 | 0 | 14166324 | Average memory utilisation ratio (0-1 normalised); blank is documented missing. |

> Dataset: alibaba_v2017/batch_instance.csv
> No header row present; column names assigned from official trace_201708.md documentation.
> Column count: documented=12, observed=12 (exact_match).
> Timestamps are seconds relative to the trace epoch; zero may indicate events before the trace window.
> Memory values are normalised/obfuscated; CPU core counts are NOT normalised in the same way.
> Measurements are NOT AWS billing amounts or AWS resource identifiers.
> No cross-dataset join with AWS FOCUS data has been established.
> Exact-duplicate detection skipped for large file (858,546,781 bytes > 50 MB); key uniqueness is UNKNOWN.

## `data/raw/alibaba_trace_2017/alibaba-trace-2017/batch_task.csv`

- **Dataset:** alibaba_v2017/batch_task.csv
- **Type:** csv
- **Size:** 4,631,973 bytes
- **SHA-256:** `50f3c50ab4a917ad06c1cee6ec601e1356708774d7962503e8eafdd720b2286d`
- **Encoding:** utf-8
- **Rows:** 80553
- **Exact duplicate records:** 0
- **Key uniqueness:** no_exact_duplicates_found

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `task_name` | `observed` | 0 | 0 | 0 | Opaque integer identifier from source; not a dollar value. |
| `inst_num` | `observed` | 0 | 0 | 0 | Number of instances in the task; observed count from source. |
| `job_name` | `observed` | 0 | 0 | 0 | Opaque integer identifier for the parent job. |
| `task_type` | `observed` | 0 | 0 | 0 | Integer type code from source trace. |
| `start_time` | `observed` | 0 | 0 | 0 | Seconds relative to trace epoch; unit documented in trace_201708.md. Zero or blank means event fell before or outside the trace window. |
| `status` | `observed` | 0 | 0 | 0 | Categorical terminal/non-terminal state from source. |
| `plan_cpu` | `observed` | 9017 | 0 | 9017 | Requested CPU cores; documented sentinel: blank means not recorded. |
| `plan_mem` | `observed` | 9017 | 0 | 9017 | Requested normalised memory; documented sentinel: blank means not recorded. |

> Dataset: alibaba_v2017/batch_task.csv
> No header row present; column names assigned from official trace_201708.md documentation.
> Column count: documented=8, observed=8 (exact_match).
> Timestamps are seconds relative to the trace epoch; zero may indicate events before the trace window.
> Memory values are normalised/obfuscated; CPU core counts are NOT normalised in the same way.
> Measurements are NOT AWS billing amounts or AWS resource identifiers.
> No cross-dataset join with AWS FOCUS data has been established.

## `data/raw/alibaba_trace_2017/alibaba-trace-2017/container_event.csv`

- **Dataset:** alibaba_v2017/container_event.csv
- **Type:** csv
- **Size:** 875,035 bytes
- **SHA-256:** `8e2c2776983fb0867f5088352849773186eab22de9d878e10978b5018c68aeda`
- **Encoding:** utf-8
- **Rows:** 11102
- **Exact duplicate records:** 0
- **Key uniqueness:** no_exact_duplicates_found

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `timestamp` | `observed` | 0 | 0 | 0 | Seconds relative to trace epoch. |
| `event_type` | `observed` | 0 | 0 | 0 | Categorical event type (Create, Delete, etc.). |
| `container_id` | `observed` | 0 | 0 | 0 | Opaque container identifier. |
| `machine_id` | `observed` | 0 | 0 | 0 | Opaque machine identifier. |
| `cpu_request` | `observed` | 0 | 0 | 0 | Requested CPU cores at event time. |
| `mem_request` | `observed` | 0 | 0 | 0 | Requested normalised memory at event time. |
| `disk_request` | `observed` | 0 | 0 | 0 | Requested normalised disk at event time. |
| `cpu_affinity` | `observed` | 0 | 0 | 0 | CPU core affinity list (pipe-separated); blank means not set. |
| `extra` | `unknown` | 11102 | 0 | 11102 | Ninth column present in data; not documented in trace_201708.md. |

> Dataset: alibaba_v2017/container_event.csv
> No header row present; column names assigned from official trace_201708.md documentation.
> Column count: documented=9, observed=9 (exact_match).
> Timestamps are seconds relative to the trace epoch; zero may indicate events before the trace window.
> Memory values are normalised/obfuscated; CPU core counts are NOT normalised in the same way.
> Measurements are NOT AWS billing amounts or AWS resource identifiers.
> No cross-dataset join with AWS FOCUS data has been established.

## `data/raw/alibaba_trace_2017/alibaba-trace-2017/container_usage.csv`

- **Dataset:** alibaba_v2017/container_usage.csv
- **Type:** csv
- **Size:** 275,992,839 bytes
- **SHA-256:** `9ccb7abafdc492b173eb87b49c9cec3df30cb6e8fbfce09e7023be826de3a10a`
- **Encoding:** utf-8
- **Rows:** 1480906
- **Exact duplicate records:** None
- **Key uniqueness:** unknown

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `timestamp` | `observed` | 0 | 0 | 0 | Seconds relative to trace epoch; 300-second averaged sample. |
| `container_id` | `observed` | 0 | 0 | 0 | Opaque container identifier. |
| `cpu_util` | `observed` | 3 | 0 | 3 | CPU utilisation percentage (0-100); 300-second average. |
| `cpu_util_max` | `observed` | 0 | 0 | 0 | Maximum CPU utilisation in the 300-second window. |
| `mem_util` | `observed` | 5 | 0 | 5 | Memory utilisation percentage (0-100). |
| `disk_read_rate` | `observed` | 5 | 0 | 5 | Disk read rate, normalised units per trace documentation. |
| `disk_write_rate` | `observed` | 5 | 0 | 5 | Disk write rate, normalised units. |
| `disk_read_rate2` | `observed` | 5 | 0 | 5 | Secondary disk read rate measurement. |
| `net_in` | `observed` | 0 | 0 | 0 | Network ingress rate, normalised. |
| `net_out` | `observed` | 0 | 0 | 0 | Network egress rate, normalised. |
| `mem_util2` | `observed` | 0 | 0 | 0 | Secondary memory utilisation measurement. |
| `mem_util3` | `observed` | 0 | 0 | 0 | Tertiary memory utilisation measurement. |

> Dataset: alibaba_v2017/container_usage.csv
> No header row present; column names assigned from official trace_201708.md documentation.
> Column count: documented=12, observed=12 (exact_match).
> Timestamps are seconds relative to the trace epoch; zero may indicate events before the trace window.
> Memory values are normalised/obfuscated; CPU core counts are NOT normalised in the same way.
> Measurements are NOT AWS billing amounts or AWS resource identifiers.
> No cross-dataset join with AWS FOCUS data has been established.
> Exact-duplicate detection skipped for large file (275,992,839 bytes > 50 MB); key uniqueness is UNKNOWN.

## `data/raw/alibaba_trace_2017/alibaba-trace-2017/server_event.csv`

- **Dataset:** alibaba_v2017/server_event.csv
- **Type:** csv
- **Size:** 47,615 bytes
- **SHA-256:** `336f7255add3ae3c08bf5163b15319f542365a3c7814ed3f712699560933968b`
- **Encoding:** utf-8
- **Rows:** 1352
- **Exact duplicate records:** 0
- **Key uniqueness:** no_exact_duplicates_found

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `timestamp` | `observed` | 0 | 0 | 0 | Seconds relative to trace epoch. |
| `machine_id` | `observed` | 0 | 0 | 0 | Opaque machine identifier. |
| `event_type` | `observed` | 0 | 0 | 0 | Categorical event type (add, remove, etc.). |
| `extra` | `unknown` | 1313 | 0 | 1313 | Fourth column present in data; may be optional per documentation. |
| `cpu_cores` | `observed` | 0 | 0 | 0 | Physical CPU core count; NOT normalised unlike memory fields. |
| `mem_size` | `observed` | 0 | 0 | 0 | Normalised memory capacity per trace_201708.md. |
| `disk_size` | `observed` | 0 | 0 | 0 | Disk capacity; units per trace documentation. |

> Dataset: alibaba_v2017/server_event.csv
> No header row present; column names assigned from official trace_201708.md documentation.
> Column count: documented=7, observed=7 (exact_match).
> Timestamps are seconds relative to the trace epoch; zero may indicate events before the trace window.
> Memory values are normalised/obfuscated; CPU core counts are NOT normalised in the same way.
> Measurements are NOT AWS billing amounts or AWS resource identifiers.
> No cross-dataset join with AWS FOCUS data has been established.

## `data/raw/alibaba_trace_2017/alibaba-trace-2017/server_usage.csv`

- **Dataset:** alibaba_v2017/server_usage.csv
- **Type:** csv
- **Size:** 19,030,775 bytes
- **SHA-256:** `032fa4193f4b426941fb1eecef5add3d8b0632aaf7cbd7663b0051ceb8a4637a`
- **Encoding:** utf-8
- **Rows:** 187963
- **Exact duplicate records:** 0
- **Key uniqueness:** no_exact_duplicates_found

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `timestamp` | `observed` | 0 | 0 | 0 | Seconds relative to trace epoch; 300-second averaged sample. |
| `machine_id` | `observed` | 0 | 0 | 0 | Opaque machine identifier. |
| `cpu_util` | `observed` | 1 | 0 | 1 | CPU utilisation percentage; 300-second average. |
| `cpu_util_max` | `observed` | 0 | 0 | 0 | Maximum CPU utilisation in the 300-second window. |
| `mem_util` | `observed` | 1 | 0 | 1 | Memory utilisation percentage. |
| `net_in` | `observed` | 1 | 0 | 1 | Network ingress rate, normalised units. |
| `net_out` | `observed` | 1 | 0 | 1 | Network egress rate, normalised units. |
| `disk_io` | `observed` | 1 | 0 | 1 | Disk I/O rate, normalised units. |

> Dataset: alibaba_v2017/server_usage.csv
> No header row present; column names assigned from official trace_201708.md documentation.
> Column count: documented=8, observed=8 (exact_match).
> Timestamps are seconds relative to the trace epoch; zero may indicate events before the trace window.
> Memory values are normalised/obfuscated; CPU core counts are NOT normalised in the same way.
> Measurements are NOT AWS billing amounts or AWS resource identifiers.
> No cross-dataset join with AWS FOCUS data has been established.

## `data/raw/alibaba_v2017/alibaba-trace-2017.tar.gz`

- **Dataset:** alibaba_v2017_archive
- **Type:** tar.gz
- **Size:** 243,321,159 bytes
- **SHA-256:** `b7efe9dc9caa75352de41b9ac34b329602cde3414fd7cab87c1d21a9a9871d66`
- **Encoding:** binary (gzip-compressed tar)
- **Rows:** None
- **Exact duplicate records:** None
- **Key uniqueness:** n/a — archive, not tabular data

### Archive members

| Member | Size (bytes) |
|---|---:|
| `alibaba-trace-2017` | 0 |
| `alibaba-trace-2017/batch_task.csv` | 4,631,973 |
| `alibaba-trace-2017/server_event.csv` | 47,615 |
| `alibaba-trace-2017/container_usage.csv` | 275,992,839 |
| `alibaba-trace-2017/batch_instance.csv` | 858,546,781 |
| `alibaba-trace-2017/server_usage.csv` | 19,030,775 |
| `alibaba-trace-2017/container_event.csv` | 875,035 |

> Archive inspected by listing members only.  No files were extracted or modified.
> Extracted files are already present under data/raw/alibaba_trace_2017/ — profiled separately.
> SHA-256 matches provenance recorded in research/data_manifest.md.

## `data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet`

- **Dataset:** focus12
- **Type:** parquet/snappy
- **Size:** 111,251 bytes
- **SHA-256:** `e829bfa81b344c07e7803d52bd7e9863eba2240e37cea4a701c2dc48f5220597`
- **Encoding:** binary (Parquet)
- **Rows:** 12200
- **Exact duplicate records:** None
- **Key uniqueness:** unknown — no candidate key justified for FOCUS rows

### Cost field summary

| Field | Currency | Sum | Min | Max | Non-null |
|---|---|---:|---:|---:|---:|
| `BilledCost` | USD (all rows) | -1.3e-07 | -0.504 | 0.03 | 12200 |
| `EffectiveCost` | USD (all rows) | -1.3e-07 | -0.504 | 0.03 | 12200 |
| `ListCost` | USD (all rows) | -1.3e-07 | -0.504 | 0.03 | 12200 |

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `BilledCost` | `observed` | 0 | 0 | 0 | Actual billed amount in BillingCurrency; required FOCUS 1.2 column. |
| `BillingCurrency` | `observed` | 0 | 0 | 0 | ISO 4217 currency code; required FOCUS 1.2 column. |
| `BillingPeriodEnd` | `observed` | 0 | 0 | 0 | UTC timestamp marking end of billing period; required FOCUS 1.2 column. This is a billing-period boundary, NOT a billing-record arrival timestamp. |
| `BillingPeriodStart` | `observed` | 0 | 0 | 0 | UTC timestamp marking start of billing period; required FOCUS 1.2 column. This is a billing-period boundary, NOT a billing-record arrival timestamp. |
| `ChargeCategory` | `observed` | 0 | 0 | 0 | Charge classification (Usage, Credit, Tax, etc.); required FOCUS 1.2 column. |
| `ChargeFrequency` | `observed` | 0 | 0 | 0 | Charge recurrence type; required FOCUS 1.2 column. |
| `InvoiceIssuerName` | `observed` | 0 | 0 | 0 | Issuing entity name; required FOCUS 1.2 column. |
| `ProviderName` | `observed` | 0 | 0 | 0 | Cloud provider name; required FOCUS 1.2 column. |
| `RegionId` | `observed` | 0 | 0 | 0 | Provider-assigned region identifier; required FOCUS 1.2 column. |
| `RegionName` | `observed` | 0 | 0 | 0 | Human-readable region name; required FOCUS 1.2 column. |
| `ServiceCategory` | `observed` | 0 | 0 | 0 | FinOps taxonomy service grouping; required FOCUS 1.2 column. |
| `SubAccountType` | `observed` | 0 | 0 | 0 | Sub-account kind (e.g. AWS Account); required FOCUS 1.2 column. |
| `BillingAccountType` | `observed` | 0 | 0 | 0 | Billing-account kind; required FOCUS 1.2 column. |
| `EffectiveCost` | `observed` | 0 | 0 | 0 | Cost after commitment discounts; FOCUS 1.2 optional column. |
| `ListCost` | `observed` | 0 | 0 | 0 | On-demand list-price cost; FOCUS 1.2 optional column. |
| `ChargePeriodStart` | `observed` | 0 | 0 | 0 | UTC start of charge period; FOCUS 1.2 optional. NOT a billing-record arrival timestamp. |
| `ChargePeriodEnd` | `observed` | 0 | 0 | 0 | UTC end of charge period; FOCUS 1.2 optional. NOT a billing-record arrival timestamp. |
| `ResourceId` | `observed` | 606 | 0 | 0 | Provider resource ARN/identifier; FOCUS 1.2 optional. Required for linkage to CALIBUR admission records — absent rows break linkage. |
| `ServiceName` | `observed` | 0 | 0 | 0 | Provider service name; FOCUS 1.2 optional. |
| `Tags` | `observed` | 0 | 0 | 12200 | Key-value tag map; FOCUS 1.2 optional. No CALIBUR reservation-id or team tags observed in this delivery. |
| `InvoiceId` | `observed` | 12200 | 0 | 0 | Invoice identifier; FOCUS 1.2 optional. Fully null in this delivery. |
| `SkuId` | `observed` | 82 | 0 | 0 | Provider SKU identifier; FOCUS 1.2 optional. |
| `ConsumedQuantity` | `observed` | 411 | 0 | 0 | Actual usage quantity; FOCUS 1.2 optional. |
| `ConsumedUnit` | `observed` | 411 | 0 | 0 | Unit for ConsumedQuantity; FOCUS 1.2 optional. |
| `x_Operation` | `observed` | 316 | 0 | 0 | AWS-specific operation string (FOCUS AWS extension column). |
| `x_ServiceCode` | `observed` | 0 | 0 | 0 | AWS-specific service code (FOCUS AWS extension column). |
| `AvailabilityZone` | `unknown` | 10300 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `BillingAccountId` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `BillingAccountName` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CapacityReservationId` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CapacityReservationStatus` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ChargeClass` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ChargeDescription` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountCategory` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountId` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountName` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountQuantity` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountStatus` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountType` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountUnit` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ContractedCost` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ContractedUnitPrice` | `unknown` | 411 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ListUnitPrice` | `unknown` | 411 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCategory` | `unknown` | 82 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrency` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrencyContractedUnitPrice` | `unknown` | 411 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrencyEffectiveCost` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrencyListUnitPrice` | `unknown` | 411 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingQuantity` | `unknown` | 82 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingUnit` | `unknown` | 82 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PublisherName` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ResourceName` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ResourceType` | `unknown` | 606 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ServiceSubcategory` | `unknown` | 12200 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SkuMeter` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SkuPriceDetails` | `unknown` | 10755 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SkuPriceId` | `unknown` | 411 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SubAccountId` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SubAccountName` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `x_Discounts` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |

> Dataset: AWS FOCUS 1.2 billing export (CALIBUR_FOCUS_12).
> Parquet metadata: org.apache.spark.version present in file metadata.
> Billing period: 2026-10-01 00:00:00+00:00 to 2026-11-01 00:00:00+00:00 (billing-period boundary timestamps, NOT billing-arrival timestamps).
> BillingPeriodStart/End and ChargePeriodStart/End are period-boundary timestamps. The time this record first became available to a controller has NOT been recorded.
> Tags: 0 rows with non-empty tag maps out of 12200. No CALIBUR reservation-id or team tags are present. Linkage between these billing records and CALIBUR admission decisions is NOT established.
> InvoiceId: fully null in this delivery — no invoice number is available for deduplication.
> One delivery snapshot only. Billing revisions have not been observed.
> Currency: USD for all 12,200 rows.
> ChargeClass: fully null — no adjustment/correction class values present in this delivery.
> BilledCost sum ≈ 0 due to credit offset; this does not mean future costs will be zero.

## `data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json`

- **Dataset:** unknown
- **Type:** unsupported (.json)
- **Size:** 4,927 bytes
- **SHA-256:** `898bb08c152fee6a2d8af114b28f41235d938045849666c636c161010943ed16`
- **Encoding:** unknown
- **Rows:** None
- **Exact duplicate records:** None
- **Key uniqueness:** unknown

> File type '.json' is not supported by the profiler.

## `data/raw/focus12/CALIBUR_FOCUS_12-Manifest.json`

- **Dataset:** unknown
- **Type:** unsupported (.json)
- **Size:** 3,050 bytes
- **SHA-256:** `ceb0afedd5c6964889673f45b9b1a842f9d8926a3a5f04c0acdf4c323afe4bb0`
- **Encoding:** unknown
- **Rows:** None
- **Exact duplicate records:** None
- **Key uniqueness:** unknown

> File type '.json' is not supported by the profiler.

## `data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet`

- **Dataset:** focus12
- **Type:** parquet/snappy
- **Size:** 105,860 bytes
- **SHA-256:** `df688f94a119d2cc92b0347dc86b7ee4b3ec383a58671de2ce216347f375378b`
- **Encoding:** binary (Parquet)
- **Rows:** 12404
- **Exact duplicate records:** None
- **Key uniqueness:** unknown — no candidate key justified for FOCUS rows

### Cost field summary

| Field | Currency | Sum | Min | Max | Non-null |
|---|---|---:|---:|---:|---:|
| `BilledCost` | USD (all rows) | -1.3e-07 | -0.504 | 0.03 | 12404 |
| `EffectiveCost` | USD (all rows) | -1.3e-07 | -0.504 | 0.03 | 12404 |
| `ListCost` | USD (all rows) | -1.3e-07 | -0.504 | 0.03 | 12404 |

| Field | Classification | Missing | Invalid | Sentinel | Reason |
|---|---|---:|---:|---:|---|
| `BilledCost` | `observed` | 0 | 0 | 0 | Actual billed amount in BillingCurrency; required FOCUS 1.2 column. |
| `BillingCurrency` | `observed` | 0 | 0 | 0 | ISO 4217 currency code; required FOCUS 1.2 column. |
| `BillingPeriodEnd` | `observed` | 0 | 0 | 0 | UTC timestamp marking end of billing period; required FOCUS 1.2 column. This is a billing-period boundary, NOT a billing-record arrival timestamp. |
| `BillingPeriodStart` | `observed` | 0 | 0 | 0 | UTC timestamp marking start of billing period; required FOCUS 1.2 column. This is a billing-period boundary, NOT a billing-record arrival timestamp. |
| `ChargeCategory` | `observed` | 0 | 0 | 0 | Charge classification (Usage, Credit, Tax, etc.); required FOCUS 1.2 column. |
| `ChargeFrequency` | `observed` | 0 | 0 | 0 | Charge recurrence type; required FOCUS 1.2 column. |
| `InvoiceIssuerName` | `observed` | 0 | 0 | 0 | Issuing entity name; required FOCUS 1.2 column. |
| `ProviderName` | `observed` | 0 | 0 | 0 | Cloud provider name; required FOCUS 1.2 column. |
| `RegionId` | `observed` | 0 | 0 | 0 | Provider-assigned region identifier; required FOCUS 1.2 column. |
| `RegionName` | `observed` | 0 | 0 | 0 | Human-readable region name; required FOCUS 1.2 column. |
| `ServiceCategory` | `observed` | 0 | 0 | 0 | FinOps taxonomy service grouping; required FOCUS 1.2 column. |
| `SubAccountType` | `observed` | 0 | 0 | 0 | Sub-account kind (e.g. AWS Account); required FOCUS 1.2 column. |
| `BillingAccountType` | `observed` | 0 | 0 | 0 | Billing-account kind; required FOCUS 1.2 column. |
| `EffectiveCost` | `observed` | 0 | 0 | 0 | Cost after commitment discounts; FOCUS 1.2 optional column. |
| `ListCost` | `observed` | 0 | 0 | 0 | On-demand list-price cost; FOCUS 1.2 optional column. |
| `ChargePeriodStart` | `observed` | 0 | 0 | 0 | UTC start of charge period; FOCUS 1.2 optional. NOT a billing-record arrival timestamp. |
| `ChargePeriodEnd` | `observed` | 0 | 0 | 0 | UTC end of charge period; FOCUS 1.2 optional. NOT a billing-record arrival timestamp. |
| `ResourceId` | `observed` | 622 | 0 | 0 | Provider resource ARN/identifier; FOCUS 1.2 optional. Required for linkage to CALIBUR admission records — absent rows break linkage. |
| `ServiceName` | `observed` | 0 | 0 | 0 | Provider service name; FOCUS 1.2 optional. |
| `Tags` | `observed` | 0 | 0 | 12404 | Key-value tag map; FOCUS 1.2 optional. No CALIBUR reservation-id or team tags observed in this delivery. |
| `InvoiceId` | `observed` | 12404 | 0 | 0 | Invoice identifier; FOCUS 1.2 optional. Fully null in this delivery. |
| `SkuId` | `observed` | 82 | 0 | 0 | Provider SKU identifier; FOCUS 1.2 optional. |
| `ConsumedQuantity` | `observed` | 418 | 0 | 0 | Actual usage quantity; FOCUS 1.2 optional. |
| `ConsumedUnit` | `observed` | 418 | 0 | 0 | Unit for ConsumedQuantity; FOCUS 1.2 optional. |
| `x_Operation` | `observed` | 323 | 0 | 0 | AWS-specific operation string (FOCUS AWS extension column). |
| `x_ServiceCode` | `observed` | 0 | 0 | 0 | AWS-specific service code (FOCUS AWS extension column). |
| `AvailabilityZone` | `unknown` | 10472 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `BillingAccountId` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `BillingAccountName` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CapacityReservationId` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CapacityReservationStatus` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ChargeClass` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ChargeDescription` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountCategory` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountId` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountName` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountQuantity` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountStatus` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountType` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `CommitmentDiscountUnit` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ContractedCost` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ContractedUnitPrice` | `unknown` | 418 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ListUnitPrice` | `unknown` | 418 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCategory` | `unknown` | 82 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrency` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrencyContractedUnitPrice` | `unknown` | 418 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrencyEffectiveCost` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingCurrencyListUnitPrice` | `unknown` | 418 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingQuantity` | `unknown` | 82 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PricingUnit` | `unknown` | 82 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `PublisherName` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ResourceName` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ResourceType` | `unknown` | 622 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `ServiceSubcategory` | `unknown` | 12404 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SkuMeter` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SkuPriceDetails` | `unknown` | 10935 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SkuPriceId` | `unknown` | 418 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SubAccountId` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `SubAccountName` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |
| `x_Discounts` | `unknown` | 0 | 0 | 0 | Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists. |

> Dataset: AWS FOCUS 1.2 billing export (CALIBUR_FOCUS_12).
> Parquet metadata: org.apache.spark.version present in file metadata.
> Billing period: 2026-10-01 00:00:00+00:00 to 2026-11-01 00:00:00+00:00 (billing-period boundary timestamps, NOT billing-arrival timestamps).
> BillingPeriodStart/End and ChargePeriodStart/End are period-boundary timestamps. The time this record first became available to a controller has NOT been recorded.
> Tags: 0 rows with non-empty tag maps out of 12404. No CALIBUR reservation-id or team tags are present. Linkage between these billing records and CALIBUR admission decisions is NOT established.
> InvoiceId: fully null in this delivery — no invoice number is available for deduplication.
> One delivery snapshot only. Billing revisions have not been observed.
> Currency: USD for all 12,200 rows.
> ChargeClass: fully null — no adjustment/correction class values present in this delivery.
> BilledCost sum ≈ 0 due to credit offset; this does not mean future costs will be zero.
