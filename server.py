import os
import re
import httpx
from datetime import datetime, timezone
from typing import Literal
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_PAPER_DB_ID = os.getenv("NOTION_PAPER_DB_ID", "34617b872be68060a474e18a73510f38")
NOTION_STOCK_RESEARCH_DB_ID = os.getenv("NOTION_STOCK_RESEARCH_DB_ID", "39bf50a1aca04ad2a10079e958cf96d4")
NOTION_STOCK_STUDY_DB_ID = os.getenv("NOTION_STOCK_STUDY_DB_ID", "d1afcb876857487eb978c1a8e0952d05")
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

mcp = FastMCP("notion_summary_server")


async def _get_title_property_name(client: httpx.AsyncClient, headers: dict, db_id: str) -> str:
    """데이터베이스에서 title 타입 프로퍼티 이름을 동적으로 조회합니다."""
    try:
        r = await client.get(f"{NOTION_API_BASE}/databases/{db_id}", headers=headers)
        if r.status_code == 200:
            for name, prop in r.json().get("properties", {}).items():
                if prop.get("type") == "title":
                    return name
    except Exception:
        pass
    return "Name"


def _text_blocks(text: str) -> list[dict]:
    """Notion 블록 글자 수 제한(2000자)에 맞춰 텍스트를 분할합니다."""
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text[i : i + 1900]}}],
            },
        }
        for i in range(0, len(text), 1900)
    ]


@mcp.tool()
async def save_summary_to_notion(
    title: str,
    content: str,
    source_url: str,
    category: Literal["paper", "stock_research", "stock_study"],
    sub_category: str = "",
    source: str = "",
    tags: list[str] | None = None,
    difficulty: str = "",
    icon: str = "",
) -> str:
    """논문 요약 또는 주식 리서치/공부노트를 Notion에 저장합니다.

    Args:
        title: 페이지 제목
        content: 본문 내용 (요약, 분석 등)
        source_url: 원본 URL
        category: 저장 DB —
            'paper'(AI 논문),
            'stock_research'(뉴스/산업분석/증권사리포트),
            'stock_study'(용어/이론/지표/전략/산업지식)
        sub_category: stock_research → '뉴스'|'산업분석'|'증권사리포트'|'주간브리핑'
                      stock_study   → '용어'|'이론'|'지표'|'전략'|'산업지식'
        source: 출처 (언론사, 증권사명 등)
        tags: 태그 목록 (예: ['반도체', 'AI', '국내'])
        difficulty: stock_study 전용 — '기초'|'중급'|'고급'
    """
    if not NOTION_API_KEY:
        return "오류: NOTION_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."

    db_map = {
        "paper": NOTION_PAPER_DB_ID,
        "stock_research": NOTION_STOCK_RESEARCH_DB_ID,
        "stock_study": NOTION_STOCK_STUDY_DB_ID,
    }
    db_id = db_map[category]
    category_label = {"paper": "AI 논문", "stock_research": "주식 리서치", "stock_study": "주식 공부노트"}[category]

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            title_prop = await _get_title_property_name(client, headers, db_id)

            properties: dict = {
                title_prop: {"title": [{"type": "text", "text": {"content": title}}]}
            }

            if category == "stock_research":
                if sub_category:
                    properties["카테고리"] = {"select": {"name": sub_category}}
                properties["날짜"] = {"date": {"start": datetime.now(timezone.utc).strftime("%Y-%m-%d")}}
                if source:
                    properties["출처"] = {"rich_text": [{"type": "text", "text": {"content": source}}]}
                if tags:
                    properties["태그"] = {"multi_select": [{"name": t} for t in tags]}
                if source_url:
                    properties["URL"] = {"url": source_url}

            elif category == "stock_study":
                if sub_category:
                    properties["분류"] = {"select": {"name": sub_category}}
                if difficulty:
                    properties["난이도"] = {"select": {"name": difficulty}}
                if tags:
                    properties["태그"] = {"multi_select": [{"name": t} for t in tags]}
                if source:
                    properties["참고출처"] = {"rich_text": [{"type": "text", "text": {"content": source}}]}

            payload = {
                "parent": {"database_id": db_id},
                "properties": properties,
                **({"icon": {"type": "emoji", "emoji": icon}} if icon else {}),
                "children": [
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "rich_text": [
                                {"type": "text", "text": {"content": f"원본 링크: {source_url}"}}
                            ],
                            "icon": {"emoji": "🔗"},
                            "color": "gray_background",
                        },
                    },
                    *_text_blocks(content),
                    {"object": "block", "type": "divider", "divider": {}},
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": (
                                            f"저장일시: "
                                            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                                            f"  |  분류: {category_label}"
                                        )
                                    },
                                    "annotations": {"color": "gray"},
                                }
                            ]
                        },
                    },
                ],
            }

            response = await client.post(
                f"{NOTION_API_BASE}/pages",
                headers=headers,
                json=payload,
            )

        if response.status_code == 200:
            page_url = response.json().get("url", "")
            return (
                f"✅ Notion 저장 완료\n"
                f"제목: {title}\n"
                f"분류: {category_label}\n"
                f"페이지: {page_url}"
            )

        err = response.json()
        return (
            f"❌ Notion 저장 실패 (HTTP {response.status_code})\n"
            f"오류 코드: {err.get('code', 'unknown')}\n"
            f"메시지: {err.get('message', '알 수 없는 오류')}"
        )

    except httpx.TimeoutException:
        return "오류: 요청 시간 초과. 네트워크를 확인하세요."
    except httpx.RequestError as e:
        return f"오류: 네트워크 연결 실패 — {e}"


def _extract_page_id(page_id_or_url: str) -> str:
    """Notion URL 또는 ID 문자열에서 순수 페이지 ID(32자리 hex)를 추출합니다."""
    clean = page_id_or_url.strip().split("?")[0].rstrip("/")
    last = clean.split("/")[-1]
    # 대시 제거 후 32자리 hex 추출
    hex_only = re.sub(r"[^0-9a-fA-F]", "", last)
    if len(hex_only) >= 32:
        return hex_only[-32:]
    # URL이 아닌 이미 ID 형식인 경우
    return page_id_or_url.replace("-", "").strip()


@mcp.tool()
async def update_notion_page(
    page_id: str,
    content: str,
    section_title: str = "",
) -> str:
    """기존 Notion 페이지에 내용을 추가(append)합니다.

    Args:
        page_id: Notion 페이지 ID 또는 URL
        content: 추가할 내용
        section_title: 섹션 제목 (선택). 입력 시 구분선과 함께 섹션명이 표시됨
    """
    if not NOTION_API_KEY:
        return "오류: NOTION_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."

    pid = _extract_page_id(page_id)

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    blocks: list[dict] = [{"object": "block", "type": "divider", "divider": {}}]

    if section_title:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": f"━━━ {section_title} ━━━"},
                        "annotations": {"bold": True},
                    }
                ]
            },
        })

    blocks.extend(_text_blocks(content))

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(
                f"{NOTION_API_BASE}/blocks/{pid}/children",
                headers=headers,
                json={"children": blocks},
            )

        if response.status_code == 200:
            return (
                f"✅ 페이지 업데이트 완료\n"
                f"페이지 ID: {pid}\n"
                f"추가된 섹션: {section_title or '(제목 없음)'}"
            )

        err = response.json()
        return (
            f"❌ 업데이트 실패 (HTTP {response.status_code})\n"
            f"오류 코드: {err.get('code', 'unknown')}\n"
            f"메시지: {err.get('message', '알 수 없는 오류')}"
        )

    except httpx.TimeoutException:
        return "오류: 요청 시간 초과. 네트워크를 확인하세요."
    except httpx.RequestError as e:
        return f"오류: 네트워크 연결 실패 — {e}"


if __name__ == "__main__":
    mcp.run()
