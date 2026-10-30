## Dataset: Alibaba Cluster Trace 2017

### Provenance
- Source: Alibaba Cluster Trace Program, v2017.
- Source repository: https://github.com/alibaba/clusterdata
- Trace documentation: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2017/trace_201708.md
- Data type: Production cluster workload and resource-usage observations.
- Local raw archive: `data/raw/alibaba_v2017/alibaba-trace-2017.tar.gz`

### Structure
The extracted dataset contains six CSV files: `batch_instance.csv`, `batch_task.csv`, `container_event.csv`, `container_usage.csv`, `server_event.csv`, and `server_usage.csv`.

The CSV files have no header rows. Column names and ordering must be verified against the official schema before analysis.

### Measurement and interpretation limitations
- Timestamps are measured in seconds relative to the trace period; zero can indicate an event before the trace period.
- Usage measurements are collected at 60-second intervals and averaged over 300 seconds, according to the source documentation.
- Usage data is disclosed for only 12 consecutive hours, although server and batch records span approximately 24 hours.
- Memory and disk resource values are normalized or obfuscated for confidentiality. CPU core counts are not normalized in the same way.
- Resource measurements must retain their original units and definitions. They must not be interpreted as dollars, AWS prices, AWS usage, or actual AWS bills.
- The source documents missing identifiers and missing usage information in some records. These limitations must be checked and reported during analysis.

### Permitted research use in this project
This dataset may support descriptive workload analysis, including resource utilization, task execution duration where timestamps permit, retries, failures, and requested-versus-observed resource behavior.

It does not establish actual AWS billing outcomes, billing-arrival delays, or a verified mapping between Alibaba records and AWS reservations. Results from this dataset alone cannot validate CALIBUR's real-world AWS budget-safety performance.

### Terms and license
The applicable license or usage terms have not yet been independently verified. Confirm the terms in the official source before redistribution or publication of the data.

---

## Dataset: AWS FOCUS 1.2 Billing Export

### Provenance
- Source: AWS Data Exports, FOCUS 1.2 with AWS columns, export name `CALIBUR_FOCUS_12`.
- Three deliveries acquired: 2026-10-24T19:27Z (111,251 bytes), 2026-10-25T03:32Z (105,860 bytes), and 2026-10-25T11:18Z (113,134 bytes). Each is retained in its own local path.
- FOCUS schema version 1.2 confirmed from `CALIBUR_FOCUS_12-Manifest-FOCUS.json` (`DataGenerator`: Amazon Web Services, Inc.; `FocusVersion`: 1.2).
- Manifests on disk correspond to Delivery 2 only; no manifest exists for Delivery 1 or Delivery 3. Missing manifests are not reconstructed.

### Unique-key status
No verified unique billing-record identity key has been established. `InvoiceId` is fully null in the inspected data. No key will be invented. Snapshot comparison used descriptive grouping only and must not be treated as a row-level identity comparison.

### No Alibaba-to-FOCUS or admission-to-billing linkage
No verified key or join path connects Alibaba Cluster Trace records to FOCUS billing records or CALIBUR admission decisions. These datasets must not be joined without a documented, confirmed key. No such linkage will be fabricated.

### Data-handling notice
Raw FOCUS exports contain account-specific identifiers (`BillingAccountId`, `SubAccountId`, S3 bucket names, resource ARNs). These files must not be committed to any public repository or shared outside the project team. Only appropriately reviewed aggregate results and permitted schema information may be shared externally.

### Measurement and interpretation limitations
- `BillingPeriodStart`, `BillingPeriodEnd`, `ChargePeriodStart`, `ChargePeriodEnd` are billing-period boundary timestamps, not billing-record arrival times.
- No billing-record arrival time, first-available timestamp, or revision timestamp is present in the FOCUS schema.
- `BilledCost` is type `double` per the manifest; values are in USD. The sum approximately offsets to zero due to credits in the inspected snapshot; this does not establish that future costs will be zero.
- `ResourceId` is null for a substantial fraction of rows; linkage to CALIBUR admission records via `ResourceId` alone is not possible without additional attribution.

### Permitted research use in this project
Descriptive analysis of genuine AWS billing records, snapshot-to-snapshot comparisons, and schema documentation. Not yet sufficient to validate CALIBUR's delayed-feedback or budget-safety claims.
