import os
import httpx
from datetime import datetime, timezone
from typing import Literal
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_STOCK_DB_ID = os.getenv("NOTION_STOCK_DB_ID", "33117b872be68187a1b4ddc51261856e")
NOTION_PAPER_DB_ID = os.getenv("NOTION_PAPER_DB_ID", "34617b872be68060a474e18a73510f38")
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
    category: Literal["stock", "paper"],
) -> str:
    """AI 논문 또는 주식 기사 요약을 Notion 데이터베이스에 새 페이지로 저장합니다.

    Args:
        title: 페이지 제목 (기사 또는 논문 제목)
        content: 요약 내용
        source_url: 원본 기사 또는 논문 URL
        category: 저장 대상 DB — 'stock'(주식 기사) 또는 'paper'(AI 논문)
    """
    if not NOTION_API_KEY:
        return "오류: NOTION_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."

    db_id = NOTION_STOCK_DB_ID if category == "stock" else NOTION_PAPER_DB_ID
    category_label = "주식 기사" if category == "stock" else "AI 논문"

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            title_prop = await _get_title_property_name(client, headers, db_id)

            payload = {
                "parent": {"database_id": db_id},
                "properties": {
                    title_prop: {
                        "title": [{"type": "text", "text": {"content": title}}]
                    }
                },
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
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [{"type": "text", "text": {"content": "요약"}}]
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


if __name__ == "__main__":
    mcp.run()
