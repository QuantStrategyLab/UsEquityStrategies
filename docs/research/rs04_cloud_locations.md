# RS-04 and SOXL de-risk cloud input locations

The B3 and SOXL de-risk qualification workflows share the non-secret repository
variable `RS04_INPUT_ROOT`. Configure it after verifying the approved cloud
location. Its generic default is `gs://research-inputs/research/v2/input/`;
this example bucket is not provisioned or discovered by these workflows.

The value must be an exact `gs://<bucket>/research/v2/input/` root, including the
trailing slash. The `archive_prefix` workflow input selects one dataset directly
under that root. B3 can inspect only that dataset's `manifest.json`; it retains
the existing Linux, main-branch, first-attempt, authentication and synthetic-test
checks. SOXL de-risk qualification performs no data read. No new IAM, provider
credentials, resources or acquisition calls are required by this configuration.

For example, with synthetic names only:

```text
RS04_INPUT_ROOT=gs://synthetic-study-inputs/research/v2/input/
archive_prefix=gs://synthetic-study-inputs/research/v2/input/fixture-rs04
```

An absent or empty variable uses the generic default. Malformed roots, traversal,
and locators outside the configured root are rejected. Configuring a root does
not select another archive, change the registered RS-04 digest, establish a live
effective-config snapshot, or supply historical context. Missing evidence keeps
both studies parked. Current plugin signals and a candidate configuration alone
do not establish historical inputs or recorded-live decision parity.

Keep verified personal locations in repository variables and private metadata
inventories. Do not commit them, real market data, or credentials to this public
repository. A refreshed archive requires separate identity verification and
cannot silently replace the registered RS-04 archive.
