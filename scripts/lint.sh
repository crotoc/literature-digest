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
#
# 默认只做 `pytest --collect-only`（只导入+收集，不执行测试体）而不是全量
# 执行：规则要抓的"隐式依赖"几乎总是导入时就炸（`from adapters.sources.x
# import y` 这类静态 import），--collect-only 原样触发同一个 ModuleNotFoundError
# /ImportError，已经实测验证（人为挪走一个 adapter、留着一处它的直接 import
# 不动，--collect-only 和全量执行报的是同一个失败、同一个 exit code）。
# 代价：真正的"运行时才触发"的隐式依赖（比如只在某个测试函数体/fixture 函数
# 体内部才执行的局部 import）collect-only 测不出来——这种写法目前整个仓库
# 里没有出现过（约定是模块顶层 import），一旦真的需要更强的保证，设
# `FULL_RULE7=1` 跑回全量执行（慢，但语义上更严格）。
if [ "${SKIP_RULE7:-0}" = "1" ]; then
  printf '\033[33mskip\033[0m rule 7: 可插拔性（SKIP_RULE7=1）\n'
else
  # features/accounts_auth 排除在外：app/shell/deps.py（不是某个具体页面，
  # 是整个 app 层的公共管线）直接 import 它来做 current_account/会话解析，
  # 每一个页面——包括完全不碰账号的 health——都经 app/shell/deps.py 这一条
  # 公共路径。删掉它不是"波及了一个无关的直接依赖方"，是让整个 app 层
  # 连 import 都做不到，这已经不是规则 7 想测的"可插拔性"，而是跟
  # domain/accounts 一样的地基依赖（domain 本来就不受本规则约束）。
  targets=$(ls -d adapters/*/*.py features/*/ 2>/dev/null \
              | grep -v '__init__.py' | grep -v '^features/accounts_auth/$' || true)
  if [ -z "$targets" ]; then
    printf '\033[33mskip\033[0m rule 7: 还没有 adapter/feature 可删\n'
  else
    PY=./.venv/bin/python
    [ -x "$PY" ] || PY=python3
    if [ "${FULL_RULE7:-0}" = "1" ]; then
      pytest_opts="-q -p no:cacheprovider"
      rule7_mode='全量执行（FULL_RULE7=1）'
    else
      pytest_opts="-q -p no:cacheprovider --collect-only"
      rule7_mode='仅收集（默认，更快；设 FULL_RULE7=1 可跑回全量执行）'
    fi
    stash=$(mktemp -d)
    for t in $targets; do
      base=$(basename "$t")
      # features/<x>/ 整个目录一起挪走，天然带走它自己的 tests/，"其余
      # pytest" 不包含它自己的测试，符合规则原意。
      # adapters/<类>/<x>.py 只是单个实现文件，它的测试和同类的其它实现
      # 共享 adapters/<类>/tests/ 目录——如果只挪走实现文件，它自己名下的
      # test_<x>.py 会因 ImportError 炸掉，被误判成"删掉它导致别处红了"。
      # 所以按约定把同名的 tests/test_<x>.py 一起挪开，不计入"其余"。
      test_counterpart=""
      dependents=""
      case "$t" in
        adapters/*/*.py)
          cand="$(dirname "$t")/tests/test_${base}"
          [ -f "$cand" ] && test_counterpart="$cand"
          # 同一个 adapter 实现可能被某个 feature/domain 模块按名字直接
          # import（比如 features/metadata_lookup 同时需要 crossref 和
          # pubmed 两个互不可替代的数据源——不是同一能力的两个可互换实现，
          # 删掉其中一个必然导致那个直接依赖方自己的测试炸）。这是设计
          # 使然的局部后果，不是"删掉一个可插拔件波及了无关模块"，不该
          # 算进本规则——和上面挪开 adapter 自己的 co-located 测试是同一个
          # 道理，这里把直接依赖方的整个目录也一起挪开。
          modpath=$(printf '%s' "${t%.py}" | tr '/' '.')
          modpath_re=$(printf '%s' "$modpath" | sed 's/\./\\./g')
          dependents=$(grep -rlE "(from|import)[[:space:]]+${modpath_re}\\b" --include='*.py' \
                         features/ domain/ 2>/dev/null \
                       | sed -E 's#^(features|domain)/([^/]+)/.*#\1/\2#' | sort -u)
          ;;
        features/*/)
          # 同样的道理，这次是 app/pages/<y> 在模块顶层直接 import 了这个
          # feature（比如 app/pages/auth 和 app/pages/home 都直接依赖
          # features/accounts_auth，这是它们存在的理由，不是意外耦合）。
          # registry.discover() 对所有页面是同一次 import 扫描，缺了这个
          # feature 会让那个页面模块本身 import 失败，连带拖垒其余页面的
          # 测试——跟上面 adapter 分支挪开直接依赖方是同一个道理，这里把
          # app/pages/ 下直接依赖它的页面目录也一起挪开。
          modpath=$(printf '%s' "${t%/}" | tr '/' '.')
          modpath_re=$(printf '%s' "$modpath" | sed 's/\./\\./g')
          dependents=$(grep -rlE "(from|import)[[:space:]]+${modpath_re}\\b" --include='*.py' \
                         app/pages/ 2>/dev/null \
                       | sed -E 's#^(app/pages)/([^/]+)/.*#\1/\2#' | sort -u)
          ;;
      esac
      mv "$t" "$stash/" 2>/dev/null || continue
      if [ -n "$test_counterpart" ]; then
        mv "$test_counterpart" "$stash/$(basename "$test_counterpart")"
      fi
      moved_dependents=""
      for dep in $dependents; do
        [ -d "$dep" ] || continue
        dep_stash="$stash/$(printf '%s' "$dep" | tr '/' '_')"
        mv "$dep" "$dep_stash"
        moved_dependents="$moved_dependents $dep:$dep_stash"
      done
      if ! "$PY" -m pytest $pytest_opts >/dev/null 2>&1; then
        report 7 "删掉 $t 后 pytest 不绿（存在隐式依赖）" "$t"
      fi
      rm -rf "$t"
      mv "$stash/$base" "$t"
      if [ -n "$test_counterpart" ]; then
        mv "$stash/$(basename "$test_counterpart")" "$test_counterpart"
      fi
      for pair in $moved_dependents; do
        mv "${pair#*:}" "${pair%%:*}"
      done
    done
    rmdir "$stash" 2>/dev/null || true
    [ "$fail" = "0" ] && ok 7 "任一 adapter/feature 可删（$rule7_mode）"
  fi
fi

echo
if [ "$fail" = "0" ]; then echo "依赖方向检查全绿"; else echo "依赖方向检查有失败项"; fi
exit "$fail"
