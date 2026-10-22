"""프로젝트 자료함 (시안 p.18~19).

- 회사가 도면·평면도·LATS·카탈로그·협의 기록을 올린다. 같은 자료의 새 버전은 group_id로 묶는다.
- 설치업자는 최신 버전에 NEW 표시를 보고, 열어서 '확인함'을 누른다(버전별 기록 → "도면을 못 봤다" 다툼 방지).
- 시공 전 필수 자료 3종(건물 도면, 제품 카탈로그·사양서, 최종 LATS)이 다 있어야 시공을 예약할 수 있다.
- 가격 문서(견적서·인보이스)는 올리지 않는다.
"""
from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import current_user, office_user
from .db import audit, get_db, now
from .main import UserError, flash, form_str, get_house_or_404, is_office, redirect, remove_files, render
from .survey import add_media, gather_uploads

router = APIRouter()


def latest_docs(conn, house_id: int, user):
    """자료마다 최신 버전 한 줄씩 + 내가 확인했는지."""
    return conn.execute(
        """SELECT d.*, u.name AS creator_name, m.id AS media_id, m.mime, m.original_name, m.size,
                  (SELECT at FROM doc_acks a WHERE a.document_id = d.id AND a.user_id = ?) AS my_ack,
                  (SELECT COUNT(*) FROM documents x WHERE x.group_id = d.group_id AND x.removed_at IS NULL) AS version_count
           FROM documents d JOIN users u ON u.id = d.created_by
           LEFT JOIN media m ON m.owner_type = 'document' AND m.owner_id = d.id AND m.deleted_at IS NULL
           WHERE d.house_id = ? AND d.removed_at IS NULL
             AND d.version = (SELECT MAX(version) FROM documents y WHERE y.group_id = d.group_id AND y.removed_at IS NULL)
           ORDER BY d.created_at DESC, d.id DESC""", (user["id"], house_id)).fetchall()


def required_status(docs) -> dict:
    have = {d["category"] for d in docs}
    return {k: k in have for k in C.REQUIRED_DOCS}


def install_pack_ready(conn, house_id: int) -> bool:
    """시공 예약 관문: 필수 자료 3종이 모두 올라와 있는가."""
    cats = {r["category"] for r in conn.execute(
        "SELECT DISTINCT category FROM documents WHERE house_id = ? AND removed_at IS NULL", (house_id,))}
    return all(k in cats for k in C.REQUIRED_DOCS)


def unacked_docs(conn, user) -> list:
    """설치업자가 아직 확인하지 않은 최신 자료 (배정된 프로젝트)."""
    return conn.execute(
        """SELECT d.id, d.house_id, d.title, d.version, h.address FROM documents d JOIN houses h ON h.id = d.house_id
           JOIN house_members mm ON mm.house_id = d.house_id AND mm.user_id = ?
           WHERE d.removed_at IS NULL
             AND d.version = (SELECT MAX(version) FROM documents y WHERE y.group_id = d.group_id AND y.removed_at IS NULL)
             AND NOT EXISTS (SELECT 1 FROM doc_acks a WHERE a.document_id = d.id AND a.user_id = ?)""",
        (user["id"], user["id"])).fetchall()


def doc_or_404(conn, house_id: int, doc_id: int):
    d = conn.execute("SELECT * FROM documents WHERE id = ? AND house_id = ? AND removed_at IS NULL", (doc_id, house_id)).fetchone()
    if not d:
        raise HTTPException(404)
    return d


def check_not_price(title: str, filename: str) -> None:
    low = f"{title} {filename}".lower()
    if any(w in low for w in C.PRICE_WORDS):
        raise UserError("가격 자료(견적서·인보이스)는 자료함에 올릴 수 없습니다. 회사 전용 견적 기록에 남기세요.")


@router.get("/houses/{house_id}/docs")
def docs_page(request: Request, house_id: int, cat: str = "", user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    docs = latest_docs(conn, house_id, user)
    shown = [d for d in docs if not cat or d["category"] == cat]
    acks = {}
    if is_office(user):   # 회사: 자료마다 배정된 설치업자 중 누가 확인했는지
        for d in docs:
            acks[d["id"]] = conn.execute(
                """SELECT u.name, a.at FROM house_members m JOIN users u ON u.id = m.user_id
                   LEFT JOIN doc_acks a ON a.document_id = ? AND a.user_id = u.id WHERE m.house_id = ? ORDER BY u.name""",
                (d["id"], house_id)).fetchall()
    return render(request, "docs.html", user=user, house=house, docs=shown, all_docs=docs, cat=cat, C=C,
                  required=required_status(docs), acks=acks)


@router.post("/houses/{house_id}/docs")
async def doc_upload(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    form = await request.form()
    category = form_str(form, "category", 20)
    title = form_str(form, "title", 120)
    note = form_str(form, "note", 1000)
    group = form_str(form, "group_id", 12)
    base = None
    if group.isdigit():   # 기존 자료의 새 버전
        base = conn.execute("""SELECT * FROM documents WHERE house_id = ? AND group_id = ? AND removed_at IS NULL
                               ORDER BY version DESC LIMIT 1""", (house_id, int(group))).fetchone()
        if not base:
            raise HTTPException(404)
        category, title = base["category"], title or base["title"]
    if category not in C.DOC_CATEGORIES or not title:
        flash(request, "자료 종류와 이름을 입력하세요.", "error")
        return redirect(f"/houses/{house_id}/docs")
    try:
        saved = await gather_uploads(conn, form, house_id)
        if len(saved) != 1:
            remove_files(saved)
            raise UserError("파일을 하나 고르세요.")
        check_not_price(title, saved[0]["original_name"])
    except UserError as e:
        if 'saved' in locals():
            remove_files(saved)
        flash(request, e.key, "error", **e.args_)
        return redirect(f"/houses/{house_id}/docs")
    ts = now()
    try:
        with conn:
            version = (base["version"] + 1) if base else 1
            doc_id = conn.insert("""INSERT INTO documents (house_id, group_id, version, category, title, note, created_by, created_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                 (house_id, base["group_id"] if base else None, version, category, title, note, user["id"], ts))
            if not base:
                conn.execute("UPDATE documents SET group_id = ? WHERE id = ?", (doc_id, doc_id))
            add_media(conn, saved, house_id, "document", doc_id, user["id"])
            audit(conn, house_id=house_id, actor_id=user["id"], action="doc_upload", detail=f"{title} v{version}")
    except Exception:
        remove_files(saved)
        raise
    flash(request, "자료를 올렸습니다. 배정된 설치업자에게 NEW로 표시됩니다.")
    return redirect(f"/houses/{house_id}/docs/{doc_id}")


@router.get("/houses/{house_id}/docs/{doc_id}")
def doc_view(request: Request, house_id: int, doc_id: int, user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    doc = doc_or_404(conn, house_id, doc_id)
    versions = conn.execute(
        """SELECT d.id, d.version, d.created_at, d.note, (SELECT at FROM doc_acks a WHERE a.document_id = d.id AND a.user_id = ?) AS my_ack
           FROM documents d WHERE d.group_id = ? AND d.removed_at IS NULL ORDER BY d.version DESC""",
        (user["id"], doc["group_id"])).fetchall()
    media = conn.execute("SELECT * FROM media WHERE owner_type = 'document' AND owner_id = ? AND deleted_at IS NULL",
                         (doc_id,)).fetchone()
    my_ack = next((v["my_ack"] for v in versions if v["id"] == doc_id), None)
    return render(request, "doc.html", user=user, house=house, doc=doc, versions=versions, media=media, my_ack=my_ack, C=C,
                  is_latest=versions and versions[0]["id"] == doc_id)


@router.post("/houses/{house_id}/docs/{doc_id}/ack")
def doc_ack(request: Request, house_id: int, doc_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    doc = doc_or_404(conn, house_id, doc_id)
    if not conn.execute("SELECT 1 FROM doc_acks WHERE document_id = ? AND user_id = ?", (doc_id, user["id"])).fetchone():
        with conn:
            conn.execute("INSERT INTO doc_acks (document_id, user_id, at) VALUES (?, ?, ?)", (doc_id, user["id"], now()))
            audit(conn, house_id=house_id, actor_id=user["id"], action="doc_ack", detail=f"{doc['title']} v{doc['version']}")
    flash(request, "'{title}' v{n}을 확인했다고 기록했습니다.", title=doc["title"], n=doc["version"])
    return redirect(f"/houses/{house_id}/docs/{doc_id}")


@router.post("/houses/{house_id}/docs/{doc_id}/remove")
def doc_remove(request: Request, house_id: int, doc_id: int, user=Depends(office_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    doc = doc_or_404(conn, house_id, doc_id)
    with conn:
        conn.execute("UPDATE documents SET removed_at = ?, removed_by = ? WHERE id = ?", (now(), user["id"], doc_id))
        audit(conn, house_id=house_id, actor_id=user["id"], action="doc_remove", detail=f"{doc['title']} v{doc['version']}")
    flash(request, "자료를 내렸습니다. 기록은 남습니다.")
    return redirect(f"/houses/{house_id}/docs")
