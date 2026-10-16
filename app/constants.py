# 회사명 (번역하지 않음)
BRAND = "Woodstone JobTrack"
BRAND_SHORT = "Woodstone"  # 문자 메시지 등 짧게 쓸 때
COMPANY = "Woodstone Building Services Ltd"

# 이전 버전의 계약 상태 (변경이력 표시와 데이터 옮기기용으로만 남김)
STATUSES = ["검토전", "검토필요", "검토중", "견적완료", "설치중", "설치완료", "보류"]

# 프로젝트 9단계 (단계 이동은 회사만). 별도로 "보류" 표시가 있다.
STAGES = {
    1: "현장조사 예약",
    2: "현장조사",
    3: "견적·범위 협의",
    4: "최종 확정",
    5: "일정 조율",
    6: "1차 시공",
    7: "2차 시공",
    8: "인수인계",
    9: "완료 확인",
}
# 이전 상태 → 단계
STATUS_TO_STAGE = {"검토전": 1, "검토필요": 2, "검토중": 3, "견적완료": 4, "설치중": 6, "설치완료": 9, "보류": 1}

# 내부 값 staff = 화면 표시 "관리자"(모든 권한), manager = "매니저"(현장 업무만)
ROLES = {"staff": "관리자", "manager": "매니저", "installer": "설치업자"}
ADMIN_ROLES = ("staff",)
OFFICE_ROLES = ("staff", "manager")  # 집 등록·상태 변경·견적·집주인 대화를 다루는 역할
# 역할별로 초대(초대코드 전달)·비활성화할 수 있는 역할. 매니저는 관리자를 초대하거나 건드릴 수 없다.
MANAGEABLE = {"staff": ("staff", "manager", "installer"), "manager": ("manager", "installer")}

ENTRY_TYPES = {"survey": "현장정보", "quote": "견적", "conversation": "집주인 대화"}

# 기록 종류별 작성/열람 권한
ENTRY_RULES = {
    "survey": {"write": ("installer", "staff", "manager"), "read": ("installer", "staff", "manager")},
    "quote": {"write": ("staff", "manager"), "read": ("staff", "manager")},  # 가격은 설치업자에게 절대 보이지 않는다
    "conversation": {"write": ("staff", "manager"), "read": ("staff", "manager")},  # 집주인 대화는 설치업자에게 안 보임
}

PROPERTY_TYPES = ["Flat", "Maisonette", "Terraced", "End of terrace", "Semi-detached", "Detached", "Bungalow", "기타"]

CONVERSATION_CHANNELS = ["전화", "문자/WhatsApp", "이메일", "방문/대면", "기타"]

AUDIT_ACTIONS = {
    "house_create": "집 등록",
    "house_edit": "집 정보 수정",
    "status": "상태 변경",
    "stage": "단계 변경",
    "hold": "보류",
    "unhold": "보류 해제",
    "member_add": "설치업자 배정",
    "member_remove": "설치업자 배정 해제",
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
