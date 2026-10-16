# Woodstone JobTrack (kenit_b2b)

런던 에어컨 설치 현장 기록 서비스입니다. 관리자와 설치업자만 가입해서 씁니다. FastAPI + SQLite라 별도 DB 서버가 필요 없습니다.
화면은 **한국어 / English**로 쓸 수 있습니다(상단에서 전환, 사용자별 저장).

## 로컬 실행

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8080
```

브라우저에서 http://127.0.0.1:8080 을 엽니다. 로컬에서는 DB와 사진이 이 폴더의 `data/`, `uploads/`에 저장됩니다.

처음 실행하면 터미널에 **관리자용 / 매니저용 / 설치업자용 초대코드**가 출력됩니다. 가입할 때 이 코드를 입력해야 하며, 어떤 코드를 넣었는지로 역할이 정해집니다.
가입 후에는 관리자가 `사용자 관리` 메뉴에서 초대코드를 확인·재발급하고, 역할을 바꾸거나 계정을 비활성화할 수 있습니다.

## 기능

| | 관리자 | 매니저 | 설치업자 |
|---|---|---|---|
| 집(현장) 등록·정보 수정 | ✅ | ✅ | |
| 계약 상태 변경 (검토전 → 검토필요 → 검토중 → 견적완료 → 설치중 → 설치완료 / 보류) | ✅ | ✅ | |
| 현장정보 작성 (주거형태, 면적, 방 개수, 층수, 설치 희망, 구조 메모, 사진·동영상) | ✅ | ✅ | ✅ |
| 견적 작성 (금액, 대수, 내용, 첨부) | ✅ | ✅ | 열람만 |
| 집주인 대화 기록 (방법, 일시, 내용, 첨부) | ✅ | ✅ | 볼 수 없음 |
| 다른 사람이 쓴 견적·집주인 대화 수정 | ✅ | 본인 것만 | |
| 사용자 관리·초대코드·역할 변경 | ✅ | | |

- **수정 기록**: 모든 기록에 작성 시각·최종 수정 시각·수정자·수정 횟수가 표시되고, 수정 전 내용은 `변경이력` 탭에 그대로 남습니다. 첨부를 삭제해도 서버에서 지우지 않고 삭제 표시만 합니다(관리자는 계속 열람 가능).
- **수정 권한**: 본인이 쓴 기록은 본인이 수정. 견적·집주인 대화는 관리자도 수정 가능(매니저는 본인 것만).
- **검색**: 목록에서 상태 칩, 키워드(주소·우편번호·집주인·메모, 관리자는 연락처까지), 정렬. 현장 상세에서는 기록 내용·첨부파일명·작성자로 검색.
- 시각은 모두 런던 시간 기준으로 표시합니다.
- **언어**: 상단의 한국어/English 링크로 전환하며 계정에 저장됩니다. 처음 접속 시 브라우저 언어가 한국어면 한국어, 그 외에는 영어로 보입니다. 화면 문구는 [app/i18n.py](app/i18n.py)에서 수정합니다. 사용자가 입력한 내용(메모·대화 등)은 번역하지 않습니다.
- 업로드: JPG/PNG/WEBP/GIF/HEIC, MP4/MOV, PDF. 파일당 최대 25MB(`MAX_UPLOAD_MB`).

## 테스트

```bash
tests/run.sh                                           # SQLite
TEST_PG_URL=postgresql://... tests/run.sh               # SQLite + PostgreSQL (그 DB는 매번 비워짐)
```

## 설정 (환경변수)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DATA_DIR` | `./data` | SQLite DB와 세션 키 저장 위치 |
| `UPLOAD_DIR` | `./uploads` | 첨부파일 저장 위치 |
| `SECRET_KEY` | 자동 생성 (`data/.secret_key`) | 로그인 쿠키 서명 키 |
| `COOKIE_SECURE` | 꺼짐 | HTTPS로 운영할 때 `1` |
| `MAX_UPLOAD_MB` | `25` | 파일당 최대 크기 |
| `S3_BUCKET` | 없음 | 설정하면 사진은 이 버킷에 저장하고 DB는 이 버킷으로 실시간 백업 |
| `S3_ENDPOINT` | 없음 | R2: `https://<계정ID>.r2.cloudflarestorage.com` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | 없음 | R2 API 토큰(Object Read & Write) |
| `S3_REGION` | `auto` | R2는 `auto` |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | 없음 | 문자 인증 발송(Twilio). 없으면 로컬에서는 코드가 서버 로그에 찍히는 시험 모드, Vercel에서는 문자 로그인이 꺼짐 |
| `TWILIO_FROM` | 없음 | 발신 번호(+44…) 또는 영문 발신자명(예: `Woodstone`). `TWILIO_MESSAGING_SERVICE_SID`를 대신 써도 됨 |

## 배포와 데이터 보호

`Dockerfile`로 배포합니다(Railway 등). 운영 환경에는 아래를 꼭 설정합니다.

- 영구 디스크(Volume)를 `/data`에 연결
- `S3_BUCKET`, `S3_ENDPOINT`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` (Cloudflare R2)
- `SECRET_KEY`: 임의의 긴 문자열. 서버를 새로 만들어도 로그인이 유지됩니다.
- `COOKIE_SECURE=1`

이렇게 설정하면 데이터가 이렇게 보호됩니다.

| 데이터 | 보호 방식 |
|---|---|
| 사진·첨부 | 서버가 아니라 R2에 바로 저장. 앱은 첨부를 지우지 않고 삭제 표시만 함 |
| DB (기록 전체) | [Litestream](https://litestream.io)이 1초 간격으로 R2에 복제, 30일치 보관 |
| 서버 디스크 유실 | 새 서버가 시작될 때 DB가 없으면 R2에서 자동 복구 |
| 기록 수정·첨부 삭제 | 앱 안에서 이전 내용이 변경이력에 남음 |

`S3_BUCKET` 없이 실행하면 시작할 때 백업이 꺼져 있다는 경고가 출력됩니다.

### 특정 시점으로 되돌리기

실수로 데이터가 잘못 바뀌었을 때 30일 안의 시점으로 되돌릴 수 있습니다. 서버 콘솔에서 실행합니다.

```bash
# 2026-10-10 09:00 (UTC) 시점의 DB를 별도 파일로 복구해 확인
litestream restore -config /etc/litestream.yml -timestamp 2026-10-10T09:00:00Z -o /data/db/restored.db /data/db/app.db
```

확인한 뒤 앱을 멈추고 `restored.db`를 `app.db` 자리에 놓고 다시 시작합니다.
