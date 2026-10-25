# research/ingestion package — read-only source loaders for approved datasets.
#
# Public modules:
#   alibaba_loader          — reads the six Alibaba Cluster Trace 2017 CSV files
#   alibaba_workload_adapter — converts batch_task.csv to WorkloadObservation stream
#   focus_loader            — reads approved FOCUS 1.2 Parquet deliveries
#   focus_billing_adapter   — loads all deliveries, diffs them, builds FeasibilityResult
#   manifest_reader         — reads FOCUS manifest JSON files (schema + BCM delivery)
#   ledger_export_reader    — translates operator-exported DynamoDB records for evaluate.py
