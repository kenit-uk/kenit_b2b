# Woodstone JobTrack 용어집 (유비쿼터스 언어)

화면(한국어·영어), 안내 문구, 작업 기록, 코드 주석은 아래 용어만 쓴다. 같은 것을 다른 말로 부르지 않는다.

| 용어 | English | 뜻 | 쓰지 않는 말 | 코드 이름 |
|---|---|---|---|---|
| 프로젝트 | Project | 집 한 곳의 에어컨 설치 건 하나 (WS-100001) | 현장, 집, 작업, 잡(Job), Property | `houses` |
| 프로젝트 정보 | Project details | 주소·우편번호·집주인 등 | 집 정보 | `houses` 컬럼 |
| 집주인 | Landlord | 프로젝트의 고객 | 고객(문맥상 집주인일 때) | `landlord_*` |
| 회사 | Office | 관리자와 매니저를 합쳐 부르는 말 | 직원(회사 쪽만 가리킬 때) | `OFFICE_ROLES` |
| 관리자 | Admin | 모든 권한 | 직원, staff | `staff` |
| 매니저 | Manager | 역할 변경 빼고 회사 업무 | — | `manager` |
| 설치업자 | Installer | 배정된 프로젝트만 보는 현장 인력 | 기사, 작업자 | `installer` |
| 사용자 관리 | Users | 설치업자·관리자·매니저 계정 관리 화면 | 사용자 정보, 직원 정보, 설치업자 메뉴 | `/users` |
| 단계 | Stage | 1 현장조사 예약 ~ 9 완료 | 상태(계약 상태) | `stage` |
| 보류 | On hold | 단계와 상관없이 멈춤 | — | `on_hold` |
| 다음 할 일 | Next step | 지금 누가 무엇을 해야 하는지 한 가지 | — | `nextstep.py` |
| 확인할 일 | To review | 회사가 확인해야 하는 항목 (보고·메시지·변경 요청·날짜 후보 등) | 승인 대기, 승인 | `pending_items` |
| 홈 | Home | 회사 첫 화면 (대시보드) | 승인 대기 | `/approvals` |
| 현장조사 | Site survey | 설치업자가 방·실외기를 기록해 회사로 보내는 조사 | 현장정보 | `surveys` |
| 현장 메모 | Site notes | 예전 방식의 자유 기록 (새로 쓰지 않음) | 현장정보 | `entries.type = survey` |
| 작업 범위 | Scope | 버전(v1, v2…)으로 관리하는 설치 내용, 확정하면 잠김 | 범위서, 사양 | `scope_versions` |
| 변경 요청 (VO) | Change request (VO) | 확정된 작업 범위를 바꾸는 요청. 고객 결정: 시공 금지 / 시공 가능 / 원래대로 진행 | 작업 변경, 변경 필요, Variation | `variations` |
| 자료 / 자료함 | Documents | 도면·LATS·카탈로그·협의 기록. 가격 자료는 올리지 않음 | 문서, 파일 | `documents` |
| 가격 자료 | Price documents | 견적서·인보이스. 회사 전용 견적 기록에만 | 가격 문서 | `PRICE_WORDS` |
| 견적 | Quote | 회사 전용 금액 기록 | — | `entries.type = quote` |
| 프로젝트 관리 | Projects (menu) | 프로젝트 목록·일정 관리·현장조사 질문지·정산·결제 관리를 묶은 상위 메뉴 | — | — |
| 일정 관리 | Schedule | 방문 전체를 주 단위로 보는 화면 | 달력, 일정(메뉴 이름) | `/calendar` |
| 현장조사 질문지 | Survey questions | 액세서리·부가 사항 선택지 설정 (관리자) | 현장조사 항목, 설정 | `/settings/survey` |
| 정산·결제 관리 | Payments | 프로젝트별 계약금·중도금·잔금, 결제 관문 | 결제 메뉴 | `/payments` |
| 계약금 / 중도금 / 잔금 | Deposit / Interim / Balance | 청구 전 → 청구함 → 받음 | — | `payments.kind` |
| 결제 관문 | Payment gate | 켜면 그 결제를 받아야 다음 단계 (1차 시공 예약 / 2차 시공 예약 / 완료) | — | `payment_gates` 설정 |
| 사용자 관리 | Users (menu) | 설치업자 관리·직원 관리·사용자 초대를 묶은 상위 메뉴 | — | — |
| 방문 | Visit | 현장조사·1차 시공·2차 시공·기타 방문 하나 | 작업 예약, 작업 | `visits` |
| 현장 기록 | Site record | 방문 하나의 도착·사진·작업 끝 보고 화면 | 현장 화면 | `/visits/{id}` |
| 현장 도착 | Arrived | 방문 당일 누르는 버튼, 위치 1회 저장 | 체크인 | `checkins.kind = arrive` |
| 작업 끝 보고 | End-of-day report | 방문을 마치며 보내는 보고 (한 일·문제·사진) | 오늘 작업 보고, 일일 보고 | `day_reports` |
| 오늘 작업 끝 | Finish today | 작업 끝 보고를 보내는 버튼 | — | `/visits/{id}/finish` |
| 작업 기록 | Activity log | 누가 언제 무엇을 했는지, 수정 불가 | 변경이력(탭 이름은 유지) | `audit_log` |
