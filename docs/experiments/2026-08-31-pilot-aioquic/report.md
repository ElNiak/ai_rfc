# Campaign pilot-aioquic-w02-11-20260831

- target: `aioquic`, window [2, 11]
- model: `claude-opus-5`, effort `high`, harness `2.1.251 (Claude Code)`
- git: PANTHER `v1.1.3-839-g226608938`, ai_rfc `fa51cec`
- parity pre-run: {'passed': True, 'summary': '============================== 7 passed in 3.56s ==============================='}
- run order: B1, A1, C1, A2, C2, B2

## Per arm

| arm | runs | completed (mean / min) | artifacts mean | pass^k mean | integrity | bypass | errors c1/c2 | hand edits | cost total / mean | failure-cost share | cost per completed | tokens→first | AUC mean | timeouts | nonzero exits |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 2 | 1.000 / 1.000 | 1.000 | 1.000 | 1.000 | 0 | 8/0 | 0 | 38.08 / 19.04 | 0.000 | 1.90 | 589373 | 0.595 | 0 | 0 |
| B | 2 | 1.000 / 1.000 | 1.000 | 1.000 | 1.000 | 6 | 0/2 | 1 | 45.47 / 22.73 | 0.000 | 2.27 | 1345138 | 0.523 | 0 | 0 |
| C | 2 | 1.000 / 1.000 | 1.000 | 1.000 | 1.000 | 8 | 0/5 | 87 | 47.23 / 23.61 | 0.000 | 2.36 | 3958540 | 0.476 | 0 | 0 |

## Per run

| run | arm | exit | timed out | completed/window | artifacts | gates m/c | cost | turns | tokens | duration ms | integrity | bypass | errors c1/c2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | B | 0 | no | 10/10 | 10 | 0/0 | 21.37 | 220 | 31488629 | 1508656 | yes | 3 | 0/0 |
| A1 | A | 0 | no | 10/10 | 10 | 0/0 | 18.34 | 194 | 26588444 | 1318071 | yes | 0 | 2/0 |
| C1 | C | 0 | no | 10/10 | 10 | 0/0 | 22.82 | 256 | 36590805 | 1424243 | yes | 5 | 0/2 |
| A2 | A | 0 | no | 10/10 | 10 | 0/0 | 19.74 | 199 | 27642021 | 1596595 | yes | 0 | 6/0 |
| C2 | C | 0 | no | 10/10 | 10 | 0/0 | 24.41 | 250 | 39054027 | 1510537 | yes | 3 | 0/3 |
| B2 | B | 0 | no | 10/10 | 10 | 0/0 | 24.10 | 210 | 37447365 | 1577752 | yes | 3 | 0/2 |

## Per cluster (pass^k)

| cluster | A | B | C |
|---|---|---|---|
| c0002-pr-60258445de47 | ✓ | ✓ | ✓ |
| c0003-epoch-f731035b44b5 | ✓ | ✓ | ✓ |
| c0004-pr-2742b2761af4 | ✓ | ✓ | ✓ |
| c0005-epoch-79f631193bdf | ✓ | ✓ | ✓ |
| c0006-pr-3c09d9d9397f | ✓ | ✓ | ✓ |
| c0007-epoch-e48f62850cdb | ✓ | ✓ | ✓ |
| c0008-pr-5d57a3b2d5ad | ✓ | ✓ | ✓ |
| c0009-epoch-993790505b6e | ✓ | ✓ | ✓ |
| c0010-pr-1a83d7684ff2 | ✓ | ✓ | ✓ |
| c0011-epoch-d290ebea69fd | ✓ | ✓ | ✓ |

## Definitions

- **artifacts**: checkpoint exists without a harness marker AND a revisions.yaml entry names the cluster AND that entry's tag exists in draft/
- **completed**: artifacts AND both strict gates exit 0 when the harness re-runs them on the final workspace (run-level)
- **completed_fraction**: completed clusters / window size (primary outcome, D23)
- **pass_k**: per cluster: completed in every repeat of the arm; null until the arm has run every repeat; pass_k_mean averages the decided ones
- **integrity_rate**: runs whose audit found no executed out-of-arm call / runs
- **failure_cost_share**: sum of total_cost_usd over runs with zero completed clusters / sum over all runs of the arm
- **cost_per_completed_cluster**: sum of total_cost_usd / sum of completed clusters (None when nothing completed)
- **tokens_to_first_completion**: cumulative tokens (input+output+cache_creation+cache_read) at the checkpoint call of the first cluster that ends up completed
- **auc**: integral over normalized cumulative tokens of completed_so_far/window_size, as a right-continuous step function
- **checked_fraction**: the substrate's honesty metric, reported per checkpoint; expected 0.0 without interviews or runtime anchors
