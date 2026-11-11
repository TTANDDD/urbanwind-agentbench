# Open engineering environment

Use a normal Linux shell inside your own persistent task OS. The workspace is
/home/user/Desktop. You may edit your task files, install tools with apt/pip,
use the Internet, write anywhere in your owned OS, and run background work.
Internet access uses a separate network namespace with NAT. Host loopback
services are unavailable; normal public HTTPS and package installation work.
Guest root maps to an unprivileged host account; it does not grant host access.
Use `apt-get -o APT::Sandbox::User=root ...` where the single-user namespace
cannot map the package downloader's extra user. Long intended solves may run
until the task's remaining time. Background jobs must redirect stdout/stderr
to persistent logs; e.g. `nohup ... >log.name 2>&1 &`. Temporary files and jobs
persist across actions and are closed only when the task terminates.

shell/run accept arbitrary commands. Stage/case/input/output annotations are
optional; `cwd` changes the actual working directory. No stage, configuration,
stale state, comparison or public check vetoes execution or finish. Actual
commands, original stdout/stderr, times, exits and CPU estimates are recorded
automatically under the read-only harness_trace directory. An outer exit0 may
still contain a failed inner solver; preserve both and inspect real logs.

Read task_public_guide.json and submission_format.json for observed public
containers, direct evidence roles and fixed public identifiers. The optional
open_contracts.py helper validates and serializes your supplied values. It does
not create numerical values, successful gates, times, exit codes or physical
evidence. Do not replace original sampling headers/arrays with invented data.
Unresolved contract access is explicitly listed. Public formats can be partial;
read the original task_spec/reporting_contract/case_plan/assets as well.

Use agent_plan.md/agent_progress.md for persistent plan and verified progress.
They are supplied alongside the latest15 complete assistant exchanges. Use a
read-only review for a concrete plan before a long solve and again for saved
delivery if useful. At most2 reviews are available; all review actions and
API usage are counted. Review/inspect advise; they are not numerical judges.
Only you edit the workspace. A successful solve can be reused for corrected
postprocessing unless the physics/input dependency actually changed.

Each task has16 assigned logical CPUs on different physical cores. You can
choose serial/MPI/threading and affinity within that allocation. More slots do
not implyMPI16 or a fixed speedup. The host kernel denies complete procfs
mounts; /proc provides limited hardware metadata rather than a complete ps
view. This environment is not identical to a fullVM.
Default `mpirun -np 16 ...` is qualified. Hydra automatic `-bind-to core/user`
is not qualified with the limited procfs; use default binding or explicit
process/thread affinity inside your assigned CPU list. A request to enlarge
affinity is intersected with that list, as with an ordinary CPU allocation.
The single mapped user also cannot install every multi-user system service.
These are documented host limits, not task-specific execution gates.
Private grading and
reference files, API credentials and other projects are outside your OS.
Independent grading runs only after all task background jobs close. Installed
guest packages are not installed into the trusted grader's separate runtime.

Budget:150 actual actions,15 full assistant exchanges and the per-task active
time stated in the prompt. No extra output cap, temperature, top_p, seed or
thinking parameter is sent to the official provider. Missing/failed request
usage remains unknown. Do not invent costs or claim that End/self-check equals
numerical acceptance.
