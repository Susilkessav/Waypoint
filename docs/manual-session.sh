# Source this from the repository root after ./demo prepare.
# It only configures this shell and the current take's private manual-test directory.
# No servers, model calls, approvals, or browser actions start here.

if [ ! -f .waypoint/recording/current.json ] || [ ! -f pyproject.toml ]; then
    printf '%s\n' 'From the Waypoint repository, run ./demo prepare first.' >&2
    return 1
fi

_wp_manual_exports="$(uv run --locked python - <<'PY'
import json
import shlex
from pathlib import Path

current = json.loads(Path('.waypoint/recording/current.json').read_text())
take = Path(current['directory'])
lab = take / 'manual'
lab.mkdir(parents=True, exist_ok=True)
values = {
    'WP_TAKE': str(take), 'WP_LAB': str(lab),
    'WP_DB': str(take / 'state.db'),
    'WP_BASE': f"http://127.0.0.1:{current['port']}",
}
for name, value in values.items():
    print(f'export {name}={shlex.quote(value)}')
PY
)" || return 1
eval "$_wp_manual_exports"
unset _wp_manual_exports

# Fictional fixture credentials. Offline exercises ignore the user's .env.
export MERIDIAN_USER=operator1 MERIDIAN_PASS=changeme WAYPOINT_NO_DOTENV=1

# These conveniences delegate to the public CLI and preserve its exit status.
# Give the member first, then extra replay options, e.g. wp_lookup 12345 --inject reorder.
wp_lookup() {
    if [ "$#" -lt 1 ]; then
        printf '%s\n' 'Usage: wp_lookup MEMBER_ID [replay options]' >&2
        return 2
    fi
    local member="$1"
    shift
    uv run --locked waypoint replay lookup_member_balance --version 1.2.0 \
        --input "member_id=$member" --base-url "$WP_BASE" --state-db "$WP_DB" \
        --evidence-root "$WP_LAB/runs" --headed "$@"
}

wp_current() {
    if [ "$#" -lt 1 ]; then
        printf '%s\n' 'Usage: wp_current MEMBER_ID [replay options]' >&2
        return 2
    fi
    local member="$1"
    shift
    uv run --locked waypoint replay lookup_member_balance --version 1.3.0 \
        --input "member_id=$member" --base-url "$WP_BASE" --state-db "$WP_DB" \
        --evidence-root "$WP_LAB/runs" --headed "$@"
}

wp_account() {
    uv run --locked waypoint replay open_sub_account --version 1.0.0 \
        --input member_id=12345 --input 'account_type=Money Market' \
        --input initial_deposit=250.00 --base-url "$WP_BASE" --state-db "$WP_DB" \
        --evidence-root "$WP_LAB/runs" --headed "$@"
}

printf 'Manual session ready. App: %s\nFiles: %s\n' "$WP_BASE" "$WP_LAB"
