# 회사명 (번역하지 않음)
BRAND = "Kenit - Aircondition"

# 계약 상태 (표시 순서)
STATUSES = ["검토전", "검토필요", "검토중", "견적완료", "설치중", "설치완료", "보류"]

ROLES = {"staff": "관리자", "installer": "설치업자"}  # 내부 값 staff = 화면 표시 "관리자"

ENTRY_TYPES = {"survey": "현장정보", "quote": "견적", "conversation": "집주인 대화"}

# 기록 종류별 작성/열람 권한
ENTRY_RULES = {
    "survey": {"write": ("installer", "staff"), "read": ("installer", "staff")},
    "quote": {"write": ("staff",), "read": ("installer", "staff")},
    "conversation": {"write": ("staff",), "read": ("staff",)},  # 집주인 대화는 직원만 열람
}

PROPERTY_TYPES = ["Flat", "Maisonette", "Terraced", "End of terrace", "Semi-detached", "Detached", "Bungalow", "기타"]

CONVERSATION_CHANNELS = ["전화", "문자/WhatsApp", "이메일", "방문/대면", "기타"]

AUDIT_ACTIONS = {
    "house_create": "집 등록",
    "house_edit": "집 정보 수정",
    "status": "상태 변경",
    "entry_create": "기록 작성",
    "entry_edit": "기록 수정",
    "attachment_add": "첨부 추가",
    "attachment_delete": "첨부 삭제",
}

HOUSE_LABELS = {
    "address": "주소",
    "postcode": "우편번호",
    "landlord_name": "집주인",
    "landlord_phone": "집주인 연락처",
    "landlord_email": "집주인 이메일",
    "memo": "메모",
}

TIMEZONE = "Europe/London"
