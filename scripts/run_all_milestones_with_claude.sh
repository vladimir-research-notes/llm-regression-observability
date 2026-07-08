#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Drive the milestone build with Claude Code: one `claude -p` run per milestone.
#
#   ./scripts/run_all_milestones_with_claude.sh            # milestones 0..13
#   ./scripts/run_all_milestones_with_claude.sh 4 6        # a subrange
#
# After each milestone the script stops if any of these is true:
#   - Claude exits non-zero (agent failure)
#   - no new commit was created
#   - the working tree is dirty
#   - `pytest` fails (once pyproject.toml exists)
#   - the optional cost guard trips
# so a broken milestone halts the chain for inspection instead of compounding.
# ---------------------------------------------------------------------------

START="${1:-0}"
END="${2:-13}"

TEMPLATE="docs/prompts/run-one-milestone-template.md"

# --- Permissions -----------------------------------------------------------
# Non-interactive but NOT dangerous. Two layers:
#   1. `.claude/settings.json` (committed) allowlists the safe dev commands the
#      milestones need (python3, pytest, git add/commit/..., mkdir, chmod, ...)
#      and denies network / push / destructive commands. Anything outside the
#      allowlist still requires approval, so the runner halts rather than run it.
#   2. `--permission-mode acceptEdits` (default below) auto-accepts file writes
#      within the project so Edit/Write don't prompt.
# We never use `--dangerously-skip-permissions`.
#
# Override CLAUDE_EXTRA_ARGS to change this (e.g. CLAUDE_EXTRA_ARGS="" to force
# interactive approval of edits, or add flags). The `-` form keeps an explicit
# empty override, only falling back to the default when the var is unset.
DEFAULT_EXTRA_ARGS="--permission-mode acceptEdits"
CLAUDE_EXTRA_ARGS="${CLAUDE_EXTRA_ARGS-$DEFAULT_EXTRA_ARGS}"

# --- Cost / usage guard (best-effort) --------------------------------------
# Set WEEKLY_BUDGET_USD to enable. Before each milestone the script stops if the
# cumulative estimated cost has reached COST_FRACTION (default 0.80) of it. Cost
# is Claude Code's own `total_cost_usd` estimate from --output-format json. The
# guard can only stop BETWEEN milestones; use MAX_TURNS to bound a single one.
WEEKLY_BUDGET_USD="${WEEKLY_BUDGET_USD:-}"
COST_FRACTION="${COST_FRACTION:-0.80}"
MAX_TURNS="${MAX_TURNS:-}"

LOG_DIR="logs"
COST_LOG="${LOG_DIR}/milestone-costs.log"
CUMULATIVE_USD=0

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: missing $TEMPLATE (run from the repository root)."
  exit 1
fi

mkdir -p "$LOG_DIR"

THRESHOLD_USD=""
if [ -n "$WEEKLY_BUDGET_USD" ]; then
  THRESHOLD_USD="$(python3 -c "print(f'{float(${WEEKLY_BUDGET_USD}) * float(${COST_FRACTION}):.4f}')")"
  echo "Cost guard : ON"
  echo "  weekly budget : \$${WEEKLY_BUDGET_USD}"
  echo "  stop fraction : ${COST_FRACTION}"
  echo "  stop at >=    : \$${THRESHOLD_USD} cumulative estimated cost"
else
  echo "Cost guard : OFF  (set WEEKLY_BUDGET_USD to enable a ${COST_FRACTION} stop)"
fi
[ -n "$MAX_TURNS" ] && echo "Per-milestone turn cap : ${MAX_TURNS}"
[ -n "$CLAUDE_EXTRA_ARGS" ] && echo "Extra claude args      : ${CLAUDE_EXTRA_ARGS}"

echo "Running milestones $START..$END with Claude Code (one claude -p run per milestone)."
echo "Stops on: Claude failure, no new commit, dirty tree, pytest failure, or cost guard."
echo

for MILESTONE in $(seq "$START" "$END"); do
  echo "======================================================================"
  echo "Milestone $MILESTONE"
  echo "======================================================================"

  # Pre-flight cost guard (only stops between milestones).
  if [ -n "$THRESHOLD_USD" ]; then
    OVER="$(python3 -c "print(1 if float(${CUMULATIVE_USD}) >= float(${THRESHOLD_USD}) else 0)")"
    if [ "$OVER" = "1" ]; then
      echo "COST GUARD TRIPPED: cumulative \$${CUMULATIVE_USD} >= \$${THRESHOLD_USD}."
      echo "Stopping BEFORE Milestone $MILESTONE."
      echo "Resume later with: ./scripts/run_all_milestones_with_claude.sh $MILESTONE $END"
      exit 0
    fi
  fi

  BEFORE_COMMIT="$(git rev-parse HEAD)"
  RUN_LOG="${LOG_DIR}/milestone-$(printf '%02d' "$MILESTONE").log"

  PROMPT="Use ${TEMPLATE}. Implement Milestone ${MILESTONE} only, per docs/milestones/. Do not implement later milestones. Do not push. Commit locally when the milestone is complete. Stop after exactly this one milestone."

  CLAUDE_ARGS=(-p "$PROMPT" --output-format json)
  [ -n "$MAX_TURNS" ] && CLAUDE_ARGS+=(--max-turns "$MAX_TURNS")
  # shellcheck disable=SC2206
  [ -n "$CLAUDE_EXTRA_ARGS" ] && CLAUDE_ARGS+=($CLAUDE_EXTRA_ARGS)

  # Run Claude; capture the JSON result to a (gitignored) log.
  claude "${CLAUDE_ARGS[@]}" | tee "$RUN_LOG"
  echo

  # Surface the agent's final summary text.
  python3 -c "import json; d=json.load(open('${RUN_LOG}')); print(d.get('result',''))" 2>/dev/null || true

  # Accumulate estimated cost.
  RUN_USD="$(python3 -c "import json; d=json.load(open('${RUN_LOG}')); print(d.get('total_cost_usd') or d.get('cost_usd') or 0)" 2>/dev/null || echo 0)"
  CUMULATIVE_USD="$(python3 -c "print(f'{float(${CUMULATIVE_USD}) + float(${RUN_USD}):.4f}')")"
  echo "$(date -u +%FT%TZ) milestone=$MILESTONE run_usd=$RUN_USD cumulative_usd=$CUMULATIVE_USD" >> "$COST_LOG"

  AFTER_COMMIT="$(git rev-parse HEAD)"

  if [ "$BEFORE_COMMIT" = "$AFTER_COMMIT" ]; then
    echo "ERROR: Milestone $MILESTONE did not create a new commit. Stopping so you can inspect."
    git status
    exit 1
  fi

  if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: working tree is not clean after Milestone $MILESTONE. Stopping so you can inspect."
    git status
    exit 1
  fi

  echo
  echo "Milestone $MILESTONE completed."
  echo "Commit        : $AFTER_COMMIT"
  echo "Run est. cost : \$${RUN_USD}"
  echo "Cumulative    : \$${CUMULATIVE_USD}"
  echo

  if [ -f pyproject.toml ]; then
    echo "Running smoke validation after Milestone $MILESTONE..."
    python3 -m pytest -q || {
      echo "ERROR: pytest failed after Milestone $MILESTONE. Stopping so you can inspect."
      exit 1
    }
  fi

  echo
done

echo "======================================================================"
echo "All requested milestones completed: $START..$END"
echo "Total estimated cost: \$${CUMULATIVE_USD}"
echo "======================================================================"
git log --oneline -20
