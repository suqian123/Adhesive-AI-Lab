# VASP host-busy page warning — 2026-10-13

The server page reported that a new facet convergence validation could not
start because 48 `vasp_std` processes were present. Read-only process
inspection showed three active production jobs in campaign run
`CL5-00016-1b4ebc3d36`: `dft-o-111-v00-h00`, `dft-o-111-v00-h35`, and
`dft-o-111-v08-h00`, with 16 MPI ranks each. These are legitimate calculations,
not stray validation processes. The completed CeO2(111) shared validation was
still 14/14 passed and approved; CeO2(110) had 0/14 and had not started.

The page's five-second coordination loop attempted to start the pending
CeO2(110) matrix while the host was occupied. The process guard correctly
blocked simultaneous startup, but surfaced its guard exception as a warning.
The coordinator now returns `reason=host-busy` and the page shows an
informational waiting state. With the task status page open, the loop checks
again every five seconds and attempts startup once the host becomes free.

Both changed server files matched the local versions before deployment apart
from this patch. The prior files were backed up under
`work/server_access/backups/hostbusy-20261013-3A4Mil/` and the final page
wording under `work/server_access/backups/hostbusy-wording-20261013-DFCIYI/`.
No VASP process was stopped. The Streamlit service was not restarted because
it uses `KillMode=control-group` and the active MPI jobs are in that group.

After deployment, a server-side call to the new coordinator returned
`host-busy` without starting a validation runner. The same three production
workdirs still contained 16 VASP ranks each; the page service retained PID
269667 and both its server endpoint and the local SSH tunnel returned `ok`.
The local `test_campaign_runner.py` suite passed all 29 tests. Refresh the
browser page to load the changed UI code; `server.runOnSave` is disabled.
