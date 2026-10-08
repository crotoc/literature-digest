#!/usr/bin/env bash
# 7 条依赖方向检查。任何一条红了就不许合。
# 规则来自 refs/v2-module-architecture.md「CI 静态检查」。
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
report() {  # report <规则号> <说明> <命中内容>
  printf '\033[31mFAIL\033[0m rule %s: %s\n' "$1" "$2"
  printf '%s\n' "$3" | sed 's/^/      /'
  fail=1
}
ok() { printf '\033[32m ok \033[0m rule %s: %s\n' "$1" "$2"; }

py() { grep -rn --include='*.py' "$@" 2>/dev/null; }

# 1. caps 不许向上依赖，且 caps 下不许有 models.py
hits=$(py -E '^\s*(from|import)\s+(domain|features|app)\b' caps/)
mdl=$(find caps -name 'models.py' 2>/dev/null)
if [ -n "$hits" ] || [ -n "$mdl" ]; then
  report 1 'caps/ 不得 import domain/features/app，且不得有 models.py' "$hits$mdl"
else ok 1 'caps/ 零向上依赖、零 models.py'; fi

# 2. adapters 不许碰 domain / features
hits=$(py -E '^\s*(from|import)\s+(domain|features)\b' adapters/)
if [ -n "$hits" ]; then report 2 'adapters/ 不得 import domain/features' "$hits"
else ok 2 'adapters/ 零 domain/features 依赖'; fi

# 3. 跨 domain 只许 import 对方 contract
#    注意必须用 grep -P：负向断言 (?!...) 在 ERE 下不支持，-E 会让这条规则空跑。
hits=''
for d in domain/*/; do
  [ -d "$d" ] || continue
  self=$(basename "$d")
  h=$(py -P "^\s*from\s+domain\.(?!${self}\b)[A-Za-z_][A-Za-z0-9_]*\.(models|service)\b" "$d")
  [ -n "$h" ] && hits="${hits}${h}
"
done
if [ -n "$(printf '%s' "$hits" | tr -d '[:space:]')" ]; then
  report 3 '跨 domain 只许 import 对方 contract' "$hits"
else ok 3 '跨 domain 仅经由 contract'; fi

# 4. features 之间互不 import（横向组合是 app 的职责）
hits=''
for d in features/*/; do
  [ -d "$d" ] || continue
  self=$(basename "$d")
  h=$(py -P "^\s*from\s+features\.(?!${self}\b)" "$d")
  [ -n "$h" ] && hits="${hits}${h}
"
done
if [ -n "$(printf '%s' "$hits" | tr -d '[:space:]')" ]; then
  report 4 'features 之间不得互相 import（横向组合是 app 的职责）' "$hits"
else ok 4 'features 之间零互依赖'; fi

# 5. 页面层不碰 SQL / models
hits=$(py -E '^\s*(import\s+sqlalchemy|from\s+sqlalchemy|from\s+[a-z_.]*models\s+import)' app/pages/)
if [ -n "$hits" ]; then report 5 'app/pages/ 不得 import sqlalchemy 或任何 models' "$hits"
else ok 5 'app/pages/ 零 SQL/models'; fi

# 6. 只有 infra/db.py 能造 engine / session 工厂
hits=$(py -E 'create_engine\(|sessionmaker\(' infra/ caps/ domain/ adapters/ features/ app/ \
       | grep -v '^infra/db.py:')
if [ -n "$hits" ]; then report 6 '除 infra/db.py 外禁止 create_engine(/sessionmaker(' "$hits"
else ok 6 '唯一 engine/session 工厂'; fi

# 7. 可插拔性：删掉任一 adapter 或任一 feature，其余 pytest 仍须全绿
if [ "${SKIP_RULE7:-0}" = "1" ]; then
  printf '\033[33mskip\033[0m rule 7: 可插拔性（SKIP_RULE7=1）\n'
else
  targets=$(ls -d adapters/*/*.py features/*/ 2>/dev/null | grep -v '__init__.py' || true)
  if [ -z "$targets" ]; then
    printf '\033[33mskip\033[0m rule 7: 还没有 adapter/feature 可删\n'
  else
    PY=./.venv/bin/python
    [ -x "$PY" ] || PY=python3
    stash=$(mktemp -d)
    for t in $targets; do
      base=$(basename "$t")
      mv "$t" "$stash/" 2>/dev/null || continue
      if ! "$PY" -m pytest -q -p no:cacheprovider >/dev/null 2>&1; then
        report 7 "删掉 $t 后 pytest 不绿（存在隐式依赖）" "$t"
      fi
      rm -rf "$t"
      mv "$stash/$base" "$t"
    done
    rmdir "$stash" 2>/dev/null || true
    [ "$fail" = "0" ] && ok 7 '任一 adapter/feature 可删'
  fi
fi

echo
if [ "$fail" = "0" ]; then echo "依赖方向检查全绿"; else echo "依赖方向检查有失败项"; fi
exit "$fail"
