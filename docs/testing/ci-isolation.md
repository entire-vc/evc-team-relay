# CI test isolation

The control-plane suite creates its shared in-memory SQLite schema once per
process. Ordinary tests run inside a physical outer transaction, with session
commits releasing SAVEPOINTs. Test setup, HTTP handlers and startup session
factories all join that boundary; teardown rolls back committed test data.

Use `@pytest.mark.db_commit` for tests requiring actual commits or independent
connections to the shared engine. They rebuild their schema before and after
execution. Connection-pool lifetime tests keep their own file-backed SQLite
engine and real commit semantics. Ending an ordinary test's outer transaction
fails teardown instead of silently weakening isolation.

The required `test-python` job remains serial. After verifying the entire suite
in serial and shuffled order, a dedicated daily schedule sets
`TEST_ISOLATION_DAILY=1` on `main`. Its additional blocking job runs:

```sh
uv run --with pytest-xdist==3.8.0 pytest -n 2 --dist load --shuffle-seed="$CI_PIPELINE_ID"
```

Replay the printed pipeline ID as `--shuffle-seed=<integer>`. Each worker collects
the same seeded permutation; assignment to workers may vary. The explicit budget
is two worker processes and at most four SQLite DBAPI connections (one shared
in-memory connection plus one independent lifetime-test connection per worker).
This suite consumes zero PostgreSQL connections. Migration jobs retain their
separate PostgreSQL services. `-n auto`, `-n logical` and values above two are
rejected by the test configuration. xdist is installed only for the shuffled job;
application dependencies and the ordinary test environment are unchanged.

Component filtering applies only to merge requests and existing feature-branch
pushes. Changes confined to one known component select its checks. Shared files,
unknown paths, mixed-component changes, root renames/deletions and new branches
run all affected checks. Main, manual/API and scheduled pipelines run every
component. Security, release, artifact and topology gates remain blocking.
`scripts/test_ci_rules.py` evaluates actual YAML with GitLab's Ruby matcher and
includes mutation controls for unsafe filtering.

CI/docs/test-only changes do not trigger production deployment. Deploy rules
retain unknown runtime paths and require both security scans. Check the matcher
contract when changing component directories or deploy inputs.

For measurements, compare the same MR's baseline and candidate with the same
existing tests. Report each job's runner duration and the sum, pipeline wall-clock
and queue time separately, and test/pass/skip counts. Report new regression tests
and new gates as additions rather than claiming their cost as a speedup. There
is no required numerical speed target.
