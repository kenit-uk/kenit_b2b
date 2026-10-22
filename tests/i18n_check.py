"""템플릿/파이썬에서 번역 함수에 넘기는 한국어 문자열이 모두 영어 사전에 있는지 확인"""
import glob, re, sys
sys.path.insert(0, ".")
from app.i18n import EN
from app import constants as C

hangul = re.compile(r"[가-힣]")
keys = set()
for p in glob.glob("app/templates/*.html"):
    t = open(p).read()
    keys |= set(re.findall(r"_\(\s*'([^']*)'", t)) | set(re.findall(r'_\(\s*"([^"]*)"', t))
    keys |= set(re.findall(r"\('([^']*[가-힣][^']*)', ", t))
import os
for p in ["app/main.py", "app/sms.py", "app/survey.py", "app/scope.py", "app/documents.py", "app/visits.py", "app/approvals.py", "app/account.py", "app/nextstep.py", "app/licences.py", "app/survey_options.py", "app/payments.py", "app/attendance.py"]:
    if not os.path.exists(p):
        continue
    src = open(p).read()
    keys |= set(re.findall(r'(?:flash\(request, |UserError\(|error=|message=|notice=|HTTPException\(\d+, |return |translate\([^,]+, )"([^"]*[가-힣][^"]*)"', src))
    keys |= set(re.findall(r'\("([^"]*[가-힣][^"]*)", "[a-z.]', src))
    keys |= set(re.findall(r'(?:if kind == "login"\s*else |translate\(lang, )"([^"]*[가-힣][^"]*)"', src))
    keys |= set(re.findall(r'(?:error = |raise UserError\(|raise HTTPException\(\d+, |out\.append\()"([^"]*[가-힣][^"]*)"', src))
    keys |= set(re.findall(r'(?:\bstep\(|\blink\(|\bpost\(|"key": |"label": )"([^"]*[가-힣][^"]*)"', src))   # 다음 할 일·예약 경고
for d in (C.DOC_CATEGORIES, C.REQUIRED_DOCS, C.VISIT_KINDS, C.VISIT_STATUS, C.WORK_DONE, C.VARIATION_KINDS,
          C.VARIATION_STATUS, C.MESSAGE_KINDS, C.INDOOR_TYPES, C.KW_OPTIONS, C.CONTROLS, C.LCD_COLOURS, C.INCLUDE_CHOICES, C.HATCH_CHOICES, C.ODU_MOUNTS,
          C.SYSTEMS, C.YES_NO, C.NEEDED, C.TRUNKING, C.TRUNKING_COLOURS, C.ACCESS, C.SURVEY_STATUS, C.STAGES):
    keys |= set(d.values())
keys |= set(C.STATUSES) | set(C.ROLES.values()) | set(C.ENTRY_TYPES.values()) | set(C.AUDIT_ACTIONS.values()) \
     | set(C.HOUSE_LABELS.values()) | set(C.CONVERSATION_CHANNELS) | {p for p in C.PROPERTY_TYPES if hangul.search(p)}
missing = sorted(k for k in keys if hangul.search(k) and k not in EN)
# 같은 한국어 키가 사전에 두 번 있으면 뒤의 것이 앞의 번역을 덮어쓴다 → 오류로 처리
src = open("app/i18n.py").read()
dups = sorted({k for k in re.findall(r'^\s+"([^"]+)":', src, re.M) if len(re.findall(r'^\s+"' + re.escape(k) + r'":', src, re.M)) > 1})
if dups:
    print("duplicate keys:", dups)
    missing = missing or dups
print(len(keys), "keys checked;", "missing:", missing or "none")
sys.exit(1 if missing else 0)
