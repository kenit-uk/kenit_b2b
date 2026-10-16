#!/bin/bash
# 전체 테스트. 사용법: tests/run.sh            (SQLite)
#                     TEST_PG_URL=postgresql://... tests/run.sh   (SQLite + PostgreSQL. 그 DB는 매번 비워진다!)
cd "$(dirname "$0")/.."
PY=.venv/bin/python
TMP=$(mktemp -d)
TESTS="e2e e2e_unread e2e_roles e2e_sms migrate_check concurrent_init"
fail=0

run() {  # run <db 이름> <테스트> [환경변수...]
  local label=$1 name=$2; shift 2
  rm -rf "$TMP/t"
  if env "$@" DATA_DIR="$TMP/t/data" UPLOAD_DIR="$TMP/t/up" PYTHONPATH=. $PY "tests/$name.py" > "$TMP/out" 2>&1; then
    printf "  %-8s %-14s %s\n" "$label" "$name" "$(tail -1 "$TMP/out")"
  else
    printf "  %-8s %-14s FAILED\n" "$label" "$name"; grep -v "^\[초기\|^\[SMS" "$TMP/out" | tail -15; fail=1
  fi
}

reset_pg() {
  $PY -c "import psycopg,sys; c=psycopg.connect(sys.argv[1], autocommit=True); c.execute('DROP SCHEMA public CASCADE'); c.execute('CREATE SCHEMA public')" "$TEST_PG_URL"
}

for t in $TESTS; do run sqlite "$t"; done
if [ -n "$TEST_PG_URL" ]; then
  for t in $TESTS; do reset_pg; run postgres "$t" DATABASE_URL="$TEST_PG_URL"; done
fi
$PY tests/i18n_check.py || fail=1
rm -rf "$TMP"
exit $fail
