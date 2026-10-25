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
The applicable license or usage terms have not yet been independently verified. Confirm the terms in the official source before redistribution or publication of the data.s