"""화면 문구 번역. 원문(한국어)을 키로 쓰고, 영어 번역이 없으면 원문을 그대로 보여준다."""
from starlette.requests import Request

LANGS = {"ko": "한국어", "en": "English"}
DEFAULT_LANG = "en"

EN = {
    # 공통 / 레이아웃
    "현장 목록": "Jobs",
    "+ 집 등록": "+ New property",
    "사용자": "Users",
    "로그아웃": "Log out",
    "메뉴": "Menu",
    "휴대폰 번호": "Mobile number",
    "문자로 받은 6자리 코드로 로그인합니다. 비밀번호가 필요 없습니다.": "We'll text you a 6-digit code. No password needed.",
    "문자로 코드 받기": "Text me a code",
    "코드 입력": "Enter code",
    "{phone}(으)로 6자리 코드를 보냈습니다.": "We sent a 6-digit code to {phone}.",
    "6자리 코드": "6-digit code",
    "코드 다시 받기": "Send the code again",
    "문자가 오지 않나요? 등록되지 않은 번호에는 문자가 가지 않습니다. 관리자에게 번호 등록을 요청하세요.": "No text? We only send codes to registered numbers. Ask an admin to register yours.",
    "다른 번호로 로그인": "Use a different number",
    "코드를 다시 보냈습니다.": "We sent a new code.",
    "휴대폰 번호를 확인하세요.": "Please check the mobile number.",
    "문자 인증이 아직 설정되지 않았습니다. 이메일로 로그인하세요.": "Text sign-in isn't set up yet. Please use email.",
    "방금 코드를 보냈습니다. 1분 뒤에 다시 받을 수 있습니다.": "We just sent a code. You can request another in a minute.",
    "코드를 너무 많이 요청했습니다. 1시간 뒤에 다시 시도하세요.": "Too many codes requested. Please try again in an hour.",
    "문자를 보내지 못했습니다. 잠시 후 다시 시도하세요.": "We couldn't send the text. Please try again shortly.",
    "코드가 만료되었습니다. 코드를 다시 받으세요.": "This code has expired. Please request a new one.",
    "코드를 여러 번 틀렸습니다. 코드를 다시 받으세요.": "Too many wrong attempts. Please request a new code.",
    "코드가 맞지 않습니다.": "That code isn't right.",
    "{brand} 로그인 코드: {{code}} (10분간 유효)": "{brand} sign-in code: {{code}} (valid for 10 minutes)",
    "{brand} 휴대폰 번호 확인 코드: {{code}} (10분간 유효)": "{brand} number check code: {{code}} (valid for 10 minutes)",
    "{brand}에 등록되었습니다. 이 번호로 로그인하세요: {url}": "You've been added to {brand}. Sign in with this number: {url}",
    "문자 로그인 사용 중": "Text sign-in on",
    "번호를 등록하면 비밀번호 없이 문자 코드로 로그인할 수 있습니다.": "Add your number to sign in with a text code instead of a password.",
    "번호 확인": "Confirm number",
    "번호 바꾸기": "Change number",
    "번호 등록": "Add number",
    "이미 다른 계정에 등록된 번호입니다.": "This number is already used by another account.",
    "휴대폰 번호를 등록했습니다. 이제 문자 코드로 로그인할 수 있습니다.": "Number saved. You can now sign in with a text code.",
    "휴대폰 번호로 등록": "Add by mobile number",
    "사람 등록": "Add person",
    "번호를 등록해 두면 그 사람은 가입 절차 없이, 문자로 받은 코드로 바로 로그인합니다.": "Once their number is added, they can sign in straight away with a text code. No sign-up needed.",
    "로그인 안내 문자 보내기": "Text them how to sign in",
    "이름과 휴대폰 번호를 확인하세요.": "Please check the name and mobile number.",
    "이미 등록된 번호입니다.": "This number is already registered.",
    "등록했지만 안내 문자를 보내지 못했습니다. 로그인 주소를 직접 알려 주세요.": "Added, but the text couldn't be sent. Please share the sign-in link yourself.",
    "{name}님을 {role}(으)로 등록했습니다. 이 번호로 문자 코드를 받아 로그인할 수 있습니다.": "{name} added as {role}. They can sign in with a text code to this number.",
    "현장": "Jobs",
    "초대": "Invite",
    "내 정보": "Me",
    "언어": "Language",
    "사진 저장소가 아직 연결되지 않아 첨부할 수 없습니다. 내용만 저장해 주세요.": "Photo storage isn't connected yet, so attachments are disabled. Please save the details only.",
    "테스트 모드: DB 또는 사진 저장소가 연결되지 않아 입력한 내용이 사라질 수 있습니다.": "Test mode: no database or photo storage connected. Anything you enter may be lost.",
    "확인하지 않은 새 소식": "New since your last visit",
    "확인하지 않은 현장": "Jobs with unseen updates",
    "마지막으로 확인한 뒤 새 소식 {n}건": "{n} new update(s) since your last visit",
    "관리": "Admin",
    "모든 시각은 런던 시간(Europe/London) 기준": "All times are London time (Europe/London)",
    "관리자": "Admin",
    "매니저": "Manager",
    "변경": "Change",
    "알 수 없는 역할입니다.": "Unknown role.",
    "본인 역할은 바꿀 수 없습니다.": "You can't change your own role.",
    "{name}님의 역할을 {role}(으)로 바꿨습니다.": "{name} is now {role}.",
    "관리자: 모든 권한 · 매니저: 집 등록·상태 변경·견적·집주인 대화, 매니저·설치업자 초대·비활성화 (역할 변경과 다른 사람의 견적·대화 수정 불가) · 설치업자: 현장정보 작성, 견적 열람": "Admin: full access · Manager: properties, status, quotes, landlord contact, invite/deactivate managers and installers (can't change roles or edit others' quotes/contacts) · Installer: site surveys, view quotes",
    "설치업자": "Installer",
    "저장": "Save",
    "취소": "Cancel",
    "검색": "Search",
    "전체": "All",
    "선택": "Select",
    "삭제": "Remove",
    "지우기": "Clear",
    "저장 중…": "Saving…",

    # 상태
    "검토전": "Not reviewed",
    "검토필요": "Needs review",
    "검토중": "In review",
    "견적완료": "Quoted",
    "설치중": "Installing",
    "설치완료": "Installed",
    "보류": "On hold",

    # 기록 종류
    "현장정보": "Site survey",
    "견적": "Quote",
    "집주인 대화": "Landlord contact",

    # 선택값
    "기타": "Other",
    "전화": "Phone call",
    "문자/WhatsApp": "Text/WhatsApp",
    "이메일": "Email",
    "방문/대면": "In person",

    # 변경이력 작업
    "집 등록": "Property added",
    "집 정보 수정": "Property edited",
    "상태 변경": "Status changed",
    "기록 작성": "Record added",
    "기록 수정": "Record edited",
    "첨부 추가": "Files added",
    "첨부 삭제": "File removed",

    # 집 정보 항목
    "주소": "Address",
    "우편번호": "Postcode",
    "집주인": "Landlord",
    "집주인 연락처": "Landlord phone",
    "집주인 이메일": "Landlord email",
    "메모": "Notes",

    # 로그인 / 가입
    "로그인": "Log in",
    "관리자·매니저·설치업자 전용": "For admins, managers and installers only",
    "비밀번호": "Password",
    "계정이 없나요?": "No account?",
    "초대코드로 가입": "Sign up with an invite code",
    "가입하기": "Sign up",
    "가입": "Sign up",
    "관리자에게 받은 초대코드가 있어야 가입할 수 있습니다.": "You need an invite code from an admin to sign up.",
    "초대코드에 따라 관리자/매니저/설치업자 계정이 정해집니다.": "The code decides whether you join as an admin, manager or installer.",
    "초대코드": "Invite code",
    "이름": "Name",
    "연락처": "Phone",
    "비밀번호 (8자 이상)": "Password (8+ characters)",
    "비밀번호 확인": "Confirm password",
    "이미 계정이 있나요?": "Already have an account?",
    "이메일 또는 비밀번호가 맞지 않습니다.": "Incorrect email or password.",
    "비활성화된 계정입니다. 관리자에게 문의하세요.": "This account is deactivated. Please contact an admin.",
    "이름과 이메일을 정확히 입력하세요.": "Please enter your name and a valid email.",
    "비밀번호는 8자 이상이어야 합니다.": "Password must be at least 8 characters.",
    "비밀번호 확인이 일치하지 않습니다.": "Passwords do not match.",
    "초대코드가 올바르지 않습니다. 관리자에게 초대코드를 받아주세요.": "Invalid invite code. Please ask an admin for one.",
    "이미 가입된 이메일입니다.": "This email is already registered.",
    "{role} 계정으로 가입되었습니다.": "Signed up as {role}.",

    # 오류
    "권한이 없습니다.": "You don't have permission to do that.",
    "페이지를 찾을 수 없습니다.": "Page not found.",
    "오류가 발생했습니다.": "Something went wrong.",
    "알 수 없는 상태입니다.": "Unknown status.",

    # 목록 / 검색
    "주소, 우편번호, 집주인 이름, 메모 검색": "Search address, postcode, landlord, notes",
    "주소, 우편번호, 집주인 이름·연락처, 메모 검색": "Search address, postcode, landlord name/phone, notes",
    "정렬": "Sort",
    "최근 활동순": "Recent activity",
    "최근 등록순": "Newest",
    "오래된 등록순": "Oldest",
    "주소순": "Address A–Z",
    "검색 결과 {n}건": "{n} result(s)",
    "전체 {n}건": "{n} job(s) in total",
    "집주인 {name}": "Landlord {name}",
    "현장정보 {n} · 견적 {m}": "Surveys {n} · Quotes {m}",
    "최근 {when}": "Last activity {when}",
    "조건에 맞는 현장이 없습니다.": "No jobs match these filters.",
    "아직 등록된 집이 없습니다.": "No properties yet.",

    # 집 등록 / 수정
    "새 집 등록": "New property",
    "등록하기": "Add property",
    "주소 *": "Address *",
    "우편번호 (Postcode)": "Postcode",
    "예: Flat 3, 12 Baker Street, London": "e.g. Flat 3, 12 Baker Street, London",
    "예: NW1 6XE": "e.g. NW1 6XE",
    "집주인 이름": "Landlord name",
    "메모 (출입 방법, 방문 가능 시간 등)": "Notes (access, visiting hours, etc.)",
    "수정하면 이전 내용이 변경이력에 남습니다.": "Previous values are kept in the history when you save.",
    "주소를 입력하세요.": "Please enter an address.",
    "집이 등록되었습니다. 설치업자가 현장정보를 올릴 수 있습니다.": "Property added. Installers can now upload a site survey.",
    "집 정보가 수정되었습니다.": "Property updated.",

    # 상세
    "← 현장 목록": "← Jobs",
    "등록": "Added",
    "최종 수정": "Last edited",
    "계약 상태": "Job status",
    "변경 사유 (선택)": "Reason (optional)",
    "상태가 '{status}'(으)로 변경되었습니다.": "Status changed to '{status}'.",
    "새 기록 작성": "Add a record",
    "{type} 저장": "Save {type}",
    "상태변경": "Status",
    "변경이력": "History",
    "이 현장 기록 안에서 검색 (내용, 첨부파일명, 작성자)": "Search this job's records (text, file names, author)",
    "일시": "When",
    "작업": "Action",
    "내용": "Details",
    "작업자": "By",
    "기록 보기": "View record",
    "사유": "Reason",
    "수정 전 내용": "Before edit",
    "첨부 {n}개": "{n} file(s)",
    "변경된 항목: {fields}": "Changed: {fields}",
    "수정": "Edit",
    "작성 {when}": "Added {when}",
    "수정 {when}": "edited {when}",
    "내용수정 {n}회": "{n} edit(s)",
    "'{q}'에 해당하는 기록이 없습니다.": "No records match '{q}'.",
    "아직 기록이 없습니다.": "No records yet.",
    "내용을 입력하거나 파일을 첨부하세요.": "Please enter some details or attach a file.",
    "{type} 기록이 저장되었습니다.": "{type} saved.",

    # 기록 입력 필드
    "주거형태": "Property type",
    "면적 (㎡)": "Floor area (㎡)",
    "면적": "Floor area",
    "방 개수": "Rooms",
    "층수 / 해당 층": "Storeys / floor",
    "층수": "Storeys / floor",
    "설치 희망 대수·위치": "Units wanted & where",
    "설치 희망": "Units wanted",
    "예: 75": "e.g. 75",
    "예: 3": "e.g. 3",
    "예: 2층 건물 / 3층": "e.g. 2-storey / 3rd floor",
    "예: 거실1, 안방1": "e.g. living room 1, main bedroom 1",
    "집 구조·현장 메모": "Layout & site notes",
    "배관 경로, 실외기 위치, 전기 용량, 벽 재질, 주차/출입 등": "Pipe route, outdoor unit spot, electrics, wall type, parking/access, etc.",
    "견적 금액 (£)": "Quote amount (£)",
    "견적 금액": "Quote amount",
    "설치 대수": "Units",
    "예: 3,450": "e.g. 3,450",
    "예: 2대": "e.g. 2",
    "견적 내용": "Quote details",
    "모델, 포함 항목, 공사 일정, 조건 등": "Models, what's included, schedule, terms, etc.",
    "방법": "Channel",
    "대화 일시 (런던 시간)": "Date & time (London)",
    "대화 일시": "Date & time",
    "대화 내용": "Conversation",
    "집주인과 나눈 내용, 요청사항, 약속한 사항": "What was discussed, requests, agreements",
    "📎 사진·동영상·PDF 첨부 (여러 개 선택 가능)": "📎 Attach photos, videos or PDFs (multiple allowed)",
    "'{name}' 형식은 올릴 수 없습니다. (사진, 동영상, PDF만 가능)": "'{name}' can't be uploaded. (Photos, videos and PDFs only)",
    "'{name}' 파일이 너무 큽니다. (최대 {mb}MB)": "'{name}' is too large. (Max {mb}MB)",
    "업로드 정보가 올바르지 않습니다. 다시 시도하세요.": "Upload information is invalid. Please try again.",
    "'{name}' 업로드가 끝나지 않았습니다. 다시 시도하세요.": "'{name}' did not finish uploading. Please try again.",
    "사진 압축 중…": "Compressing photos…",
    "업로드 중 {i}/{n}": "Uploading {i}/{n}",
    "업로드에 실패했습니다. 인터넷 연결을 확인하고 다시 시도하세요.": "Upload failed. Check your connection and try again.",

    # 기록 수정
    "{type} 수정": "Edit {type}",
    "작성 {when} · {name}": "Added {when} · {name}",
    "최종 수정 {when}": "Last edited {when}",
    "현재 첨부": "Current attachments",
    "삭제를 체크한 첨부는 목록에서 숨겨지지만 기록 보존을 위해 서버에는 남고, 변경이력에 기록됩니다.":
        "Ticked files are hidden from the record but kept on the server and logged in the history.",
    "저장하면 수정 전 내용과 수정 시각이 변경이력에 남습니다.": "Saving keeps the previous version and the edit time in the history.",
    "이 기록의 변경이력": "History of this record",
    "기록이 수정되었습니다.": "Record updated.",

    # 사용자 관리
    "사용자 관리": "User management",
    "가입 시 이 코드를 입력해야 합니다. 코드가 유출되면 새로 발급하세요(이미 가입한 사용자는 영향 없음).":
        "New users must enter this code to sign up. If a code leaks, issue a new one (existing users are not affected).",
    "{role}용": "For {role}",
    "재발급": "Regenerate",
    "초대하기": "Invite",
    "초대 링크": "Invite link",
    "초대 링크 복사": "Copy invite link",
    "공유": "Share",
    "복사했습니다 ✓": "Copied ✓",
    "초대 링크를 보내면 받은 사람이 링크를 눌러 바로 가입합니다. 초대코드는 가입할 때 자동으로 채워집니다.": "Send the invite link and the person can sign up straight away. The invite code is filled in for them.",
    "{brand} 가입 링크입니다 ({role}). 아래 링크를 눌러 가입해 주세요.": "Here is your {brand} sign-up link ({role}). Tap the link below to sign up.",
    "코드가 유출된 것 같으면 관리자에게 재발급을 요청하세요.": "If a code may have leaked, ask an admin to issue a new one.",
    "비활성화한 계정은 로그인할 수 없고, 작성한 기록은 그대로 남습니다. 다시 활성화할 수도 있습니다.": "Deactivated accounts can't log in; their records are kept. You can reactivate them later.",
    "매니저는 매니저·설치업자 계정만 관리할 수 있습니다.": "Managers can only manage manager and installer accounts.",
    "{name}님을 비활성화할까요? 로그인할 수 없게 되고, 작성한 기록은 그대로 남습니다.": "Deactivate {name}? They won't be able to log in; their records are kept.",
    "{role} 초대코드를 새로 발급할까요?": "Issue a new {role} invite code?",
    "가입자 ({n})": "Members ({n})",
    "역할": "Role",
    "가입일": "Joined",
    "상태": "Status",
    "활성": "Active",
    "비활성": "Inactive",
    "비활성화": "Deactivate",
    "활성화": "Activate",
    "본인 계정은 비활성화할 수 없습니다.": "You can't deactivate your own account.",
    "{role} 초대코드를 새로 발급했습니다. 이전 코드로는 더 이상 가입할 수 없습니다.":
        "New {role} invite code issued. The old code no longer works.",
}

KO_WEEKDAYS = "월화수목금토일"


def detect_lang(request: Request) -> str:
    lang = request.session.get("lang")
    if lang in LANGS:
        return lang
    accept = request.headers.get("accept-language", "").lower()
    return "ko" if accept.startswith("ko") else DEFAULT_LANG


def translate(lang: str, text, **kwargs) -> str:
    if text is None:
        return ""
    text = str(text)
    out = EN.get(text, text) if lang == "en" else text
    return out.format(**kwargs) if kwargs else out


def translate_message(lang: str, key: str, args: dict) -> str:
    """알림 메시지용: 인자 중 상태·역할·기록 종류 같은 용어만 번역한다(파일명, 검색어 등은 그대로)."""
    from .constants import ENTRY_TYPES, ROLES, STATUSES

    terms = set(STATUSES) | set(ROLES.values()) | set(ENTRY_TYPES.values())
    return translate(lang, key, **{k: translate(lang, v) if v in terms else v for k, v in args.items()})


def fmt_datetime(lang: str, dt) -> str:
    if lang == "en":
        return f"{dt:%a} {dt.day} {dt:%b %Y}, {dt:%H:%M}"
    return f"{dt:%Y-%m-%d} ({KO_WEEKDAYS[dt.weekday()]}) {dt:%H:%M}"
