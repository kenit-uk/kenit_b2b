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
    "survey_start": "현장조사 시작",
    "survey_submit": "현장조사 제출",
    "survey_photo_request": "추가 사진 요청",
    "survey_reviewed": "현장조사 검토 완료",
    "scope_create": "작업 범위 버전 생성",
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

# ---------- 현장조사 (시안 p.6~8) ----------
INDOOR_TYPES = {"wall": "벽걸이", "cassette": "카세트", "ducted": "덕트", "console": "콘솔"}
KW_OPTIONS = {"2.5": "2.5 kW", "3.5": "3.5 kW", "5.0": "5.0 kW", "7.0": "7.0 kW", "unknown": "모르겠음"}
CONTROLS = {"app": "앱 (Wi-Fi)", "remote": "리모컨", "lcd": "벽부착 LCD"}
LCD_COLOURS = {"white": "화이트", "black": "블랙"}
INCLUDE_CHOICES = {"incl": "포함", "excl": "미포함", "na": "해당 없음"}
HATCH_CHOICES = {"need": "필요", "noneed": "불필요", "na": "해당 없음"}
# 실내기 형태별 기본값: None = 반드시 물어봄 (시안 p.7 개발자 메모)
ACC_DEFAULTS = {
    "wall": {"grille": "na", "plenum": "na", "hatch": "na"},
    "cassette": {"grille": None, "plenum": "na", "hatch": "na"},
    "ducted": {"grille": None, "plenum": None, "hatch": None},
    "console": {"grille": "na", "plenum": "na", "hatch": "na"},
}
ODU_MOUNTS = {"ground": "지면", "bracket": "벽 브래킷", "roof": "지붕"}
SYSTEMS = {"single": "싱글 스플릿", "multi": "멀티 스플릿", "multiv": "Multi V", "unknown": "모르겠음"}
YES_NO = {"yes": "예", "no": "아니요"}
NEEDED = {"need": "필요", "noneed": "불필요"}
TRUNKING = {"incl": "포함", "excl": "미포함"}
TRUNKING_COLOURS = {"white": "화이트", "black": "블랙", "other": "기타"}
ACCESS = {"ladder": "사다리", "scaffold": "비계", "none": "필요 없음"}
SURVEY_STATUS = {"draft": "작성 중", "submitted": "회사 검토 대기", "reviewed": "검토 완료"}
