"""현장조사 '액세서리·부가 사항' 선택지 — 관리자가 설정 화면에서 바꾼다.

settings 테이블의 'survey_options'(JSON)에 저장한다. 없으면 아래 기본값을 쓴다.
선택지는 지우지 않고 숨긴다(active=False). 예전 조사·작업 범위에 남은 값도 이름이 계속 보이게 하려는 것.
'lcd' 컨트롤은 LCD 색상 질문을 여는 특별한 항목이라 숨길 수는 있어도 지울 수 없다.
"""
import json
import secrets

from . import constants as C
from .db import get_setting, set_setting

KEY = "survey_options"

# 액세서리 항목의 답 종류
ANSWERS = {"include": C.INCLUDE_CHOICES, "need": C.HATCH_CHOICES}
ANSWER_TYPES = {"include": "포함 / 미포함 / 해당 없음", "need": "필요 / 불필요 / 해당 없음"}
EN = {"앱 (Wi-Fi)": "App (Wi-Fi)", "리모컨": "Remote", "벽부착 LCD": "Wall LCD", "화이트": "White", "블랙": "Black",
      "그릴 (Grille)": "Grille", "플레넘 (Plenum)": "Plenum", "점검구 (Access hatch)": "Access hatch"}


def _opt(key, ko, active=True):
    return {"key": key, "ko": ko, "en": EN.get(ko, ""), "active": active}


def default_options() -> dict:
    return {
        "controls": [_opt(k, v) for k, v in C.CONTROLS.items()],
        "lcd_colours": [_opt(k, v) for k, v in C.LCD_COLOURS.items()],
        "items": [
            {**_opt("grille", "그릴 (Grille)"), "answers": "include",
             "defaults": {t: C.ACC_DEFAULTS[t]["grille"] for t in C.INDOOR_TYPES}},
            {**_opt("plenum", "플레넘 (Plenum)"), "answers": "include",
             "defaults": {t: C.ACC_DEFAULTS[t]["plenum"] for t in C.INDOOR_TYPES}},
            {**_opt("hatch", "점검구 (Access hatch)"), "answers": "need",
             "defaults": {t: C.ACC_DEFAULTS[t]["hatch"] for t in C.INDOOR_TYPES}},
        ],
        "extras": [],          # '기타 부가 사항' 빠른 선택 (예: 콘센트 추가, 배수 펌프)
    }


def load(conn) -> dict:
    raw = get_setting(conn, KEY)
    data = default_options()
    if raw:
        try:
            saved = json.loads(raw)
            data.update({k: v for k, v in saved.items() if k in data})
        except ValueError:
            pass
    return data


def save(conn, data: dict) -> None:
    set_setting(conn, KEY, json.dumps(data, ensure_ascii=False))


def new_key() -> str:
    return "x" + secrets.token_hex(3)


def view(data: dict, lang: str) -> dict:
    """화면용: 지금 언어로 이름을 붙이고, 이름 찾기 표를 만든다 (숨긴 항목 포함)."""
    from .i18n import translate
    name = lambda o: (o.get("en") or o["ko"]) if lang == "en" else o["ko"]
    ans = {t: {k: translate(lang, v) for k, v in choices.items()} for t, choices in ANSWERS.items()}
    items = [{**it, "label": name(it), "choices": ans[it["answers"]]} for it in data["items"]]
    return {
        "controls": [{**o, "label": name(o)} for o in data["controls"]],
        "lcd_colours": [{**o, "label": name(o)} for o in data["lcd_colours"]],
        "acc_items": items,
        "extras": [{**o, "label": name(o)} for o in data["extras"]],
        # 이름 찾기 (예전 값 표시용, 숨긴 것 포함)
        "control_label": {o["key"]: name(o) for o in data["controls"]},
        "lcd_label": {o["key"]: name(o) for o in data["lcd_colours"]},
        "item_label": {it["key"]: name(it) for it in data["items"]},
        "answer_label": {it["key"]: ans[it["answers"]] for it in data["items"]},
    }


def active_items(data: dict) -> list:
    return [it for it in data["items"] if it.get("active", True)]


def active_keys(data: dict, group: str) -> list:
    return [o["key"] for o in data[group] if o.get("active", True)]


# ---------- 관리자 설정 화면 ----------

from fastapi import APIRouter, Depends, Request  # noqa: E402

from .auth import office_user  # noqa: E402
from .db import get_db  # noqa: E402

router = APIRouter()


@router.get("/settings/survey")
def settings_page(request: Request, user=Depends(office_user), conn=Depends(get_db)):
    from .main import render
    return render(request, "settings_survey.html", user=user, opts=load(conn), C=C, ANSWER_TYPES=ANSWER_TYPES, ANSWERS=ANSWERS)


def _rows(form, prefix: str, old: list) -> list:
    """이름 목록(온도 컨트롤·LCD 색상·부가 사항): 기존 항목은 이름·사용 여부만 바꾸고, 새 이름이 있으면 하나 추가."""
    from .main import form_str
    out = []
    for i, o in enumerate(old):
        ko = form_str(form, f"{prefix}{i}_ko", 60) or o["ko"]
        out.append({**o, "ko": ko, "en": form_str(form, f"{prefix}{i}_en", 60), "active": form.get(f"{prefix}{i}_active") == "1"})
    ko = form_str(form, f"{prefix}new_ko", 60)
    if ko:
        out.append({"key": new_key(), "ko": ko, "en": form_str(form, f"{prefix}new_en", 60), "active": True})
    return out


@router.post("/settings/survey")
async def settings_save(request: Request, user=Depends(office_user), conn=Depends(get_db)):
    from .main import flash, form_str, redirect
    form = await request.form()
    old = load(conn)
    data = {"controls": _rows(form, "c", old["controls"]), "lcd_colours": _rows(form, "l", old["lcd_colours"]),
            "extras": _rows(form, "e", old["extras"]), "items": []}

    def defaults_from(prefix, answers):
        out = {}
        for t in C.INDOOR_TYPES:
            v = form_str(form, f"{prefix}_def_{t}", 10)
            out[t] = v if v in ANSWERS[answers] else None        # 비워 두면 '꼭 확인'
        return out

    for i, it in enumerate(old["items"]):      # 답 종류는 바꾸지 않는다 (이미 고른 값이 엉뚱해지지 않게)
        data["items"].append({**it, "ko": form_str(form, f"i{i}_ko", 60) or it["ko"], "en": form_str(form, f"i{i}_en", 60),
                              "active": form.get(f"i{i}_active") == "1", "defaults": defaults_from(f"i{i}", it["answers"])})
    ko = form_str(form, "inew_ko", 60)
    if ko:
        answers = form_str(form, "inew_answers", 10)
        answers = answers if answers in ANSWERS else "include"
        data["items"].append({"key": new_key(), "ko": ko, "en": form_str(form, "inew_en", 60), "active": True,
                              "answers": answers, "defaults": defaults_from("inew", answers)})
    with conn:
        save(conn, data)
    flash(request, "현장조사 질문지를 저장했습니다. 새로 하는 현장조사부터 바로 적용됩니다.")
    return redirect("/settings/survey")
