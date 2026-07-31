# Refactoring baseline

This baseline freezes controller behaviour before `program_core.py` is compartmentalized. It does
not change production code, scientific rules, schemas, prompts, persistence, or output formats.

`tests/fixtures/program_baseline.json` records:

- the stable case identity and programme counts from the complete synthetic workflow;
- canonical hashes of every accepted stage result after removing only the run-specific top-level
  `packet_id`;
- exact byte lengths and SHA-256 hashes of every final output artifact.

`tests/test_cli_contract.py` freezes the explicit Python exports and the CLI success and error
envelopes. The existing end-to-end workflow owns the scientific and artifact baseline.

During structural extraction, update this fixture only if a separately reviewed change is intended
to alter behaviour. Moving code between modules must leave it unchanged.
