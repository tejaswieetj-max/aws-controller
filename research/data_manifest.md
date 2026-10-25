# Data Manifest

## Dataset: Alibaba Cluster Trace 2017

- **Dataset version:** v2017
- **Archive filename:** `alibaba-trace-2017.tar.gz`
- **Archive location:** `data/raw/alibaba_v2017/alibaba-trace-2017.tar.gz`
- **Source URL:** http://aliopentrace.oss-cn-beijing.aliyuncs.com/v2017Traces/alibaba-trace-2017.tar.gz
- **Official trace documentation:** https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2017/trace_201708.md
- **Official download instructions:** https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2017/README2017.md

## Acquisition Metadata

- **Access date:** 2026-10-24 IST
- **File size:** 243,321,159 bytes
- **SHA-256:** `b7efe9dc9caa75352de41b9ac34b329602cde3414fd7cab87c1d21a9a9871d66`
- **Download status:** Download completed; archive integrity test passed; extraction command executed.
- **Terms/license:** Pending verification against the official repository and applicable dataset terms.

## Data Integrity and Handling

- The original downloaded archive is retained unchanged.
- Extracted files are stored separately under `data/raw/alibaba_trace_2017/`.
- The checksum identifies the exact archive used.
- Raw source files must not be edited in place. Analysis should use read-only loading or separate derived outputs.

## Scope and Research Relevance

This dataset provides cluster workload and resource-usage observations. It may support descriptive analysis of workload behavior, resource demand, and task execution.

It does not, by itself, provide verified AWS billing amounts or establish a link between workload records and actual AWS reservations. It must not be treated as a source of dollar costs or AWS billing outcomes.

## Outstanding Verification

- [x] Record file size in bytes.
- [x] Record SHA-256 checksum.
- [x] Record access date.
- [x] Verify archive integrity.
- [ ] Verify and document the applicable terms/license.
- [ ] Inspect extracted files and source schema before selecting tables for analysis.
- **Extracted data directory:** `data/raw/alibaba_trace_2017/alibaba-trace-2017/`
- **Extracted directory size:** Approximately 1.1 GB, as reported by `du -sh`.
- **Files observed:** `server_usage.csv`, `batch_task.csv`, `container_usage.csv`, `server_event.csv`, `batch_instance.csv`, `container_event.csv`.
- **Schema verification:** Pending.