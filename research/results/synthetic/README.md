# SYNTHETIC ONLY — NOT EMPIRICAL EVIDENCE

Every file in this directory was produced by the legacy seeded generator in
`research/run.py`. These files contain **no real AWS billing records, no real
requests, no real admissions, no real reservations, and no real feedback
delays**.

They are retained only to reproduce an earlier software demonstration. They
must not be cited, aggregated, plotted, or compared as CALIBUR evidence, and
they must never be read by the accepted replay pipeline
(`research/replay/run.py`).

Classification for every artifact in this directory:

```text
SYNTHETIC_ONLY_NOT_EVIDENCE
```

The accepted research command is:

```sh
python -m research.replay.run \
  --protocol research/protocol.md \
  --manifest research/data_manifest.md
```

It writes an explicit `NOT_RUN_DATA_GAP.md` and a `status: NOT_RUN` manifest
when the required linked real observations are absent. It does not fall back to
these files.
