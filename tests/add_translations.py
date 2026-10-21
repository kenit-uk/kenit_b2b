"""번역 추가 도구: python tests/add_translations.py pairs.json
이미 있는 키는 건너뛰고, EN 사전 끝(닫는 괄호 앞)에 안전하게(JSON 이스케이프) 넣는다."""
import json, re, sys

pairs = json.load(open(sys.argv[1]))
p = "app/i18n.py"
s = open(p).read()
existing = {json.loads(f'"{k}"') for k in re.findall(r'^\s+"((?:[^"\\]|\\.)*)":', s, re.M)}
add = {k: v for k, v in pairs.items() if k not in existing}
end = s.index("\n}\n", s.index("EN = {"))
block = "".join(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in add.items())
open(p, "w").write(s[:end + 1] + block + s[end + 1:])
print(f"added {len(add)}, skipped {len(pairs) - len(add)}")
