# Publication checks on 2026-11-11

The release was assembled from existing local research artifacts. No model API request or CFD solver experiment was launched for publication.

- Verified SHA-256 hashes of 7,744 copied source/data files.
- Checked all 200 task definitions, 367 case counts and all 30 paper-subset reference mappings.
- Inspected 200 public input archives (6,698 members) for unsafe paths, links, hidden `ground_truth/` directories and evaluator files.
- Scanned publication files and decompressed public input contents for common credential/private-key patterns; no matches were found. This is a bounded packaging check, not a security certification.
- Confirmed all 200 input manifests declare no required external data. Inspected the specialized task-055 generation/input manifests separately because it has no generic source inventory. Third-party PDFs, runtime/solver binaries and large historical raw archives are excluded.
- Ran the documented WindPilot unit-test selection: 107 tests reported, 106 passed and 1 platform-dependent test skipped.
- Recomputed the saved-run summary: mean task scores 62.45105 and 72.4523633; numerical credit on 6 and 17 tasks; full scores on 0 and 3 tasks; mean actions 130.366667 and 104.933333; token reduction 12.851294%; ABL mean gain 18.475046.
- Regenerated 12 figure-data CSVs and compared their parsed rows with the supplied figure data; all 12 matched. Fig. 13's supplied task-068 CSV is retained separately.
- Built a fresh 30-task public/trusted bundle with zero model/solver calls. This generated bundle is explicitly distinguished from a frozen historical run bundle.
- Checked website source links and JavaScript syntax. Browser screenshot verification was unavailable because the local browser-control service could not start.

These checks verify publication packaging and saved-result computations. They do not upgrade any task to `release_valid`, verify physical accuracy on new observations, or qualify a new Linux execution environment.
