# Data Manifest

## Dataset 1: Alibaba Cluster Trace 2017

### Source and acquisition

- **Dataset version:** v2017
- **Archive filename:** `alibaba-trace-2017.tar.gz`
- **Archive location:** `data/raw/alibaba_v2017/alibaba-trace-2017.tar.gz`
- **Source URL:** http://aliopentrace.oss-cn-beijing.aliyuncs.com/v2017Traces/alibaba-trace-2017.tar.gz
- **Official trace documentation:** https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2017/trace_201708.md
- **Official download instructions:** https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2017/README2017.md
- **Access date:** 2026-10-24 IST
- **File size:** 243,321,159 bytes  ← verified on disk 2026-10-25
- **SHA-256:** `b7efe9dc9caa75352de41b9ac34b329602cde3414fd7cab87c1d21a9a9871d66`  ← verified on disk 2026-10-25
- **Download status:** Download completed; archive integrity test passed; extraction completed.
- **Terms/license:** Pending verification against the official repository and applicable dataset terms.

> **Provenance note (2026-10-25):** An earlier draft of this manifest recorded file size 105,860 bytes and
> SHA-256 `df688f94a119d2cc92b0347dc86b7ee4b3ec383a58671de2ce216347f375378b` under Dataset 1.
> Those values belong to the *second* FOCUS Parquet delivery
> (`data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet`, 105,860 bytes,
> confirmed SHA-256 `df688f94a119d2cc92b0347dc86b7ee4b3ec383a58671de2ce216347f375378b`).
> They were mis-filed here. The corrected archive size and hash are shown above.

### Data integrity

- Original archive retained unchanged.
- Extracted data directory: `data/raw/alibaba_trace_2017/alibaba-trace-2017/`
- Extracted files: `server_usage.csv`, `batch_task.csv`, `container_usage.csv`, `server_event.csv`, `batch_instance.csv`, `container_event.csv`.
- Extracted-file schema verified against the official schema.
- Raw source files must not be edited in place.

### Research relevance and limitations

This dataset provides cluster workload and resource-usage observations. It may support descriptive workload analysis, resource demand, and task execution studies.

It does not provide verified AWS billing amounts or establish a link between its workload records and actual AWS reservations. It must not be treated as a source of dollar costs or AWS billing outcomes.

**No unique key linking Alibaba records to FOCUS billing records or CALIBUR admission decisions exists or has been inferred. No such linkage will be fabricated.**

### Outstanding verification

- [ ] Verify and document the applicable terms/license.

---

## Dataset 2: AWS FOCUS 1.2 Billing Export

### Data-handling notice

Raw FOCUS exports contain account-specific identifiers (`BillingAccountId`, `SubAccountId`, S3 bucket names, resource ARNs). These files are retained locally for research purposes and **must not be committed to any public repository or shared outside the project team**. Only appropriately reviewed aggregate results and permitted schema information (column names, data types, row/cost totals) may be shared externally.

### Source and acquisition — Delivery 1 (primary)

- **Source:** AWS Data Exports — FOCUS 1.2 with AWS columns
- **Export name:** `CALIBUR_FOCUS_12`
- **Local file:** `data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet`
- **File size:** 111,251 bytes  ← verified on disk 2026-10-25
- **SHA-256:** `e829bfa81b344c07e7803d52bd7e9863eba2240e37cea4a701c2dc48f5220597`  ← verified on disk 2026-10-25
- **S3 delivery path:** `s3://calibur-focus12-582422215613-ap-south-1/calibur/focus12/CALIBUR_FOCUS_12/data/billing_period=2026-10/`
- **Acquisition date:** 2026-10-25 IST
- **Observed file contents:** 12,200 rows and 60 columns
- **Format:** Apache Parquet, Snappy compression
- **Currency:** USD in the inspected cost records
- **Billing period:** 2026-10-01 through 2026-11-01 UTC
- **Observed delivery timestamp:** 2026-10-24T19:27:14.926Z (2026-10-25 00:57:14.926 IST)

### Source and acquisition — Delivery 2

- **Local file:** `data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet`
- **File size:** 105,860 bytes  ← verified on disk 2026-10-25
- **SHA-256:** `df688f94a119d2cc92b0347dc86b7ee4b3ec383a58671de2ce216347f375378b`  ← verified on disk 2026-10-25
- **Delivery timestamp:** 2026-10-25T03:32:00.757Z (2026-10-25 09:02:00.757 IST)
- **Execution ID:** `ddede1a2-e372-34a5-aa28-36cacf5a98ef`
- **Observed file contents:** 12,404 rows and 60 columns

### Manifest files

Both files exist at `data/raw/focus12/` and were verified on disk 2026-10-25:

| File | Size | Corresponds to |
|---|---|---|
| `CALIBUR_FOCUS_12-Manifest.json` | 3,050 bytes | Delivery 2 (execution ID `ddede1a2-e372-34a5-aa28-36cacf5a98ef`) |
| `CALIBUR_FOCUS_12-Manifest-FOCUS.json` | 4,927 bytes | Delivery 2 (execution ID `ddede1a2-e372-34a5-aa28-36cacf5a98ef`) |

> **Important:** These manifests correspond to Delivery 2 (timestamp `2026-10-25T03:32:00.757Z`),
> not to Delivery 1. Do not treat them as metadata for the earlier Parquet file.

The FOCUS schema manifest confirms:
- `DataGenerator`: Amazon Web Services, Inc.
- `DataGeneratorVersion`: 1.0.0
- `Schema.FocusVersion`: 1.2
- `Schema.SchemaId`: 1.0.0 (schema creation date `2025-11-19T00:00:00Z`)
- 60 column definitions matching the columns present in both Parquet files.

No manifest file exists for Delivery 1. Its provenance rests on the SHA-256 recorded above.

### S3 account-ID discrepancy

The S3 delivery path recorded above uses account `582422215613`. The export ARN in the standard manifest reads `arn:aws:bcm-data-exports:us-east-1:582422215623:export/…` (account `582422215623`). These differ by one digit in the penultimate position. **This discrepancy requires your confirmation** — see required decisions below.

### Observed cost summary

- Positive usage charges: $18.77972.
- Credits: -$18.77972.
- These amounts offset in the inspected snapshot. This does not establish that future costs will be zero.

### Unique-key status

No verified unique billing-record identity key has been established for these FOCUS exports. FOCUS 1.2 does not mandate a row-level stable ID field. `InvoiceId` is fully null in both deliveries. `ResourceId` is null for 606 of 12,200 rows in Delivery 1. The snapshot comparison used descriptive grouping fields, not a verified identity key. **No key will be invented or inferred without documented confirmation from AWS.**

### Snapshot comparison results (descriptive only)

- Delivery 1: 12,200 rows; Delivery 2: 12,404 rows.
- 10,166 groups matched; 20 groups in Delivery 1 only; 194 groups in Delivery 2 only.
- The 194 newer-only groups represent 224 rows with a combined `BilledCost` ≈ −$0.556063.
- One matched group changed: row count increased from 1 to 2; total `BilledCost` remained zero.
- Grouping uses descriptive fields only. Results must not be interpreted as proven billing revisions or new billing events without a verified row-level key.

### Data integrity and research limitations

- Preserve all raw Parquet files, manifests, and checksums unchanged.
- Store each future delivery separately with its own exact byte size and SHA-256.
- Different delivery timestamps alone do not prove that a billing record was revised or establish when a record first became available.
- No linkage exists between these billing records and CALIBUR admission decisions, request IDs, or runtime events.
- Billing arrival delays and revisions have not been empirically established.

This dataset supports descriptive analysis of genuine AWS billing records and future snapshot comparisons. It is not yet sufficient to validate CALIBUR's delayed-feedback or budget-safety claims.

### Outstanding verification

- [x] Acquire and inspect one AWS FOCUS delivery.
- [x] Record SHA-256 for both deliveries.
- [x] Record exact byte sizes for both deliveries.
- [x] Confirm manifest files exist and identify which delivery they correspond to.
- [x] Confirm FOCUS schema version from manifest (1.2 confirmed).
- [x] Confirm two deliveries are available and row counts differ.
- [x] Document that no verified unique row-level key exists.
- [x] Document that no Alibaba-to-FOCUS or admission-to-billing linkage exists.
- [x] Add data-handling / private-data notice.
- [ ] **BLOCKED — your decision required:** Confirm the S3 account-ID discrepancy (`582422215613` in the S3 path vs `582422215623` in the export ARN). Determine which is correct and update this manifest.
- [ ] Compare at least two deliveries using a verified row-level key, once one is established.
- [ ] Establish real request/admission/runtime-to-billing linkage.
- [ ] Establish observed billing-record arrival and revision history.
- [ ] Verify and document the Alibaba dataset license/terms.
