set -u
out=()
for dir in $(find "$HOME" -maxdepth 3 -name pyproject.toml -not -path '*/virtualenv/*' 2>/dev/null | xargs -r -n1 dirname); do
  [ -d "$dir/src/marketingiq" ] || continue
  hits=$(grep -rlE "auth_login_throttles|company_discovery_policies|decision_maker" "$dir/src" 2>/dev/null | sed "s#$dir/##" | head -n 6 | tr '\n' ' ')
  newest=$(find "$dir/src" -name '*.py' -printf '%TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | sort | tail -n 1)
  heads=$(ls "$dir/migrations/versions" 2>/dev/null | grep -E '^2026' | sort | tail -n 1)
  out+=("${dir#$HOME/} | newest src $newest | last migration $heads | feature code: ${hits:-none}")
done
for zip in $(find "$HOME" -maxdepth 3 -name 'MarketingIQ*.zip' 2>/dev/null | tr ' ' '?'); do
  zip="${zip//\?/ }"
  info=$(python3 - "$zip" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
migs = sorted(n.rsplit("/", 1)[-1] for n in names if "/migrations/versions/2026" in n)
latest = max((i.date_time for i in z.infolist()), default=None)
feature = [n for n in names if n.endswith(".py") and "/src/" in n and any(
    k in z.read(n).decode("utf-8", "ignore") for k in ("auth_login_throttles", "company_discovery_policies", "decision_maker"))]
print(f"files {len(names)} | newest {latest[:5] if latest else '-'} | last migration {migs[-1] if migs else '-'} | feature files {len(feature)}: {' '.join(f.split('/src/',1)[-1] for f in feature[:5])}")
PY
)
  out+=("ZIP ${zip#$HOME/} | $info")
done
msg=$(printf '%s\n' "${out[@]}" | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g')
echo "::notice title=Code locations::$msg"
