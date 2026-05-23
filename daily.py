"""daily.py — 오늘 날짜 페이지가 없을 때만 Notion에 저장 (4개: 뉴스종합/주간브리핑/증권사리포트/공부노트)"""
import asyncio, sys, io, httpx, xml.etree.ElementTree as ET, os
from datetime import datetime
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
load_dotenv('d:/python/notion_summary_server/.env')
sys.path.insert(0, 'd:/python/notion_summary_server')

TODAY = datetime.now().strftime("%Y-%m-%d")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
DB = {
    "paper":          os.getenv("NOTION_PAPER_DB_ID",          "34617b872be68060a474e18a73510f38"),
    "stock_research": os.getenv("NOTION_STOCK_RESEARCH_DB_ID", "39bf50a1aca04ad2a10079e958cf96d4"),
    "stock_study":    os.getenv("NOTION_STOCK_STUDY_DB_ID",    "d1afcb876857487eb978c1a8e0952d05"),
}

# 오늘 저장할 4개 항목 (논문은 수동 첨부 시에만 별도 저장)
ITEMS = [
    {"label": "① 뉴스 종합",    "db": "stock_research", "check": f"[{TODAY}] 경제 뉴스 종합"},
    {"label": "② 주간브리핑",   "db": "stock_research", "check": f"[{TODAY}] 주간 투자 브리핑"},
    {"label": "③ 증권사리포트", "db": "stock_research", "check": f"[{TODAY}] 섹터 분석 리포트"},
    {"label": "④ 공부노트",     "db": "stock_study",    "check": f"[{TODAY}] 개념노트"},
]


async def already_saved(client: httpx.AsyncClient, db_key: str, title_contains: str) -> bool:
    """해당 DB에 오늘 제목의 페이지가 이미 있는지 확인"""
    db_id = DB[db_key]
    r = await client.get(f"https://api.notion.com/v1/databases/{db_id}", headers=HEADERS)
    title_prop = next(
        (name for name, p in r.json().get("properties", {}).items() if p.get("type") == "title"),
        "Name"
    )
    r2 = await client.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=HEADERS,
        json={"filter": {"property": title_prop, "title": {"contains": title_contains}}}
    )
    return len(r2.json().get("results", [])) > 0


async def claude_summarize(prompt: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        'claude', '-p', prompt,
        '--disallowedTools',
        'mcp__notion_summary_server__save_summary_to_notion,mcp__notion_summary_server__update_notion_page',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode('utf-8', errors='replace').strip()


async def _fetch_rss(client: httpx.AsyncClient, url: str, source: str, count: int) -> list[dict]:
    r = await client.get(url, follow_redirects=True, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(r.content)
    result = []
    for item in root.findall('.//item')[:count]:
        desc_elem = item.find('description')
        desc = (desc_elem.text or "") if desc_elem is not None else ""
        result.append({
            "title":  item.find('title').text or "뉴스",
            "desc":   desc[:300],
            "url":    item.find('link').text or url,
            "source": source,
        })
    return result


async def fetch_yna_items(count: int = 5) -> list[dict]:
    async with httpx.AsyncClient() as c:
        return await _fetch_rss(c, "https://www.yna.co.kr/rss/economy.xml", "연합뉴스", count)


async def fetch_finance_news(count_each: int = 4) -> list[dict]:
    """한국경제·매일경제 금융/증권 뉴스 수집"""
    async with httpx.AsyncClient() as c:
        hk, mk = await asyncio.gather(
            _fetch_rss(c, "https://www.hankyung.com/feed/finance", "한국경제", count_each),
            _fetch_rss(c, "https://www.mk.co.kr/rss/30100041/", "매일경제", count_each),
        )
    return hk + mk


async def fetch_arxiv_paper() -> dict:
    """논문 수동 첨부 시 사용 (자동 실행 아님)"""
    url = (
        "https://export.arxiv.org/api/query"
        "?search_query=cat:cs.AI+AND+cat:cs.LG"
        "&start=0&max_results=1&sortBy=submittedDate&sortOrder=descending"
    )
    async with httpx.AsyncClient() as c:
        r = await c.get(url, follow_redirects=True, timeout=15)
    root = ET.fromstring(r.content)
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    entry = root.find('a:entry', ns)
    return {
        "title":    entry.find('a:title', ns).text.strip().replace('\n', ' '),
        "abstract": entry.find('a:summary', ns).text.strip().replace('\n', ' '),
        "url":      entry.find('a:id', ns).text.strip(),
    }


async def main():
    from server import save_summary_to_notion

    print(f"[{TODAY}] 일일 Notion 저장")
    print("=" * 45)

    # 1. 오늘 이미 저장된 항목 확인
    print("저장 여부 확인 중...")
    async with httpx.AsyncClient(timeout=30) as check_client:
        exist_flags = await asyncio.gather(*[
            already_saved(check_client, item["db"], item["check"]) for item in ITEMS
        ])

    skip = dict(zip([item["check"] for item in ITEMS], exist_flags))

    for item, done in zip(ITEMS, exist_flags):
        mark = "이미 저장됨" if done else "저장 필요"
        print(f"  {item['label']:<14} {mark}")

    need = [item for item, done in zip(ITEMS, exist_flags) if not done]
    if not need:
        print()
        print(f"  오늘({TODAY}) 모두 이미 저장되어 있습니다.")
        print("=" * 45)
        return

    # 2. 데이터 수집
    print(f"\n데이터 수집 중...")
    news_items, finance_items = await asyncio.gather(fetch_yna_items(5), fetch_finance_news(4))
    news_text = "\n\n".join(f"[{i+1}] {n['title']}\n{n['desc']}" for i, n in enumerate(news_items))
    finance_text = "\n\n".join(
        f"[{i+1}] [{n['source']}] {n['title']}\n{n['desc']}"
        for i, n in enumerate(finance_items)
    )
    print(f"  연합뉴스: {len(news_items)}건")
    print(f"  한국경제·매일경제: {len(finance_items)}건")

    # 3. 필요한 항목만 Claude 요약 생성 (병렬)
    print(f"\nClaude 요약 생성 중 (필요한 {len(need)}개만)...")

    prompts = {
        # ① 뉴스 종합
        ITEMS[0]["check"]: f"""다음 {len(news_items)}개 경제 뉴스를 투자자 관점에서 종합 분석해주세요.

형식 규칙:
- ## 헤더 절대 금지. 섹션 구분은 반드시 "━━━ 이모지 섹션명 ━━━" 형식만 사용
- 짧은 bullet 나열 금지 — 각 섹션을 4~6문장의 문단으로 서술
- 섹션 제목과 본문에 내용에 어울리는 이모지를 자유롭게 선택해 포함

섹션 구성 (4개):
━━━ 오늘의 핵심 ━━━
오늘 뉴스에서 가장 중요한 흐름 1~2가지를 짚어주세요.

━━━ 주요 이슈 분석 ━━━
각 뉴스의 배경과 맥락을 연결하여 설명해주세요.

━━━ 시장 영향 ━━━
이 이슈들이 주식·채권·환율 등 시장에 어떤 영향을 줄지 분석해주세요.

━━━ 투자 시사점 ━━━
개인 투자자 입장에서 실질적으로 참고할 만한 내용을 서술해주세요.

뉴스 목록:
{news_text}""",

        # ② 주간브리핑
        ITEMS[1]["check"]: f"""날짜: {TODAY}
다음 뉴스를 바탕으로 주간 투자 브리핑을 작성해주세요.

형식 규칙:
- ## 헤더 절대 금지. 섹션 구분은 반드시 "━━━ 이모지 섹션명 ━━━" 형식만 사용
- 짧은 bullet 나열 금지 — 각 섹션을 4~6문장의 문단으로 서술
- 섹션 제목과 본문에 내용에 어울리는 이모지를 자유롭게 선택해 포함

섹션 구성 (4개):
━━━ 글로벌 시장 동향 ━━━
미국·중국 등 주요국의 경제 흐름과 이번 주 핵심 변수를 분석해주세요.

━━━ 국내 시장 동향 ━━━
KOSPI·KOSDAQ 흐름, 수급 동향, 국내 주요 이슈를 설명해주세요.

━━━ 유망 섹터 ━━━
이번 주 모멘텀이 있는 섹터 2~3개와 그 이유를 설명해주세요.

━━━ 이번 주 투자 전략 ━━━
리스크 관리와 관심 가져야 할 투자 포인트를 제시해주세요.

뉴스 목록:
{news_text}""",

        # ③ 증권사리포트
        ITEMS[2]["check"]: f"""다음은 한국경제·매일경제의 금융·증권 뉴스입니다.
가장 주목할 기업 또는 섹터를 선정해 증권사 분석 리포트 형식으로 작성해주세요.

형식 규칙:
- ## 헤더 절대 금지. 섹션 구분은 반드시 "━━━ 이모지 섹션명 ━━━" 형식만 사용
- 짧은 bullet 나열 금지 — 각 섹션을 4~6문장의 문단으로 서술
- 섹션 제목과 본문에 내용에 어울리는 이모지를 자유롭게 선택해 포함
- 리포트 상단에 "선정 기업/섹터: ○○○" 한 줄 먼저 작성
- 뉴스에 등장한 실제 증권사명(KB증권, 키움증권, 삼성증권 등)이 있으면 반드시 언급

섹션 구성 (4개):
━━━ 기업·섹터 현황 ━━━
선정한 기업 또는 섹터의 최근 동향과 산업 내 위치를 설명해주세요.

━━━ 투자 포인트 ━━━
이 기업·섹터에 주목해야 하는 핵심 이유 2~3가지를 설명해주세요.

━━━ 투자의견 ━━━
뉴스에 언급된 증권사 의견, 목표주가, 투자의견(매수/중립/매도)을 포함해 작성해주세요.

━━━ 리스크 요인 ━━━
투자 시 주의해야 할 리스크 요인과 모니터링 포인트를 설명해주세요.

뉴스 목록 (한국경제·매일경제):
{finance_text}""",

        # ④ 공부노트
        ITEMS[3]["check"]: f"""다음 뉴스와 관련된 핵심 투자 개념 1가지를 골라 공부노트를 작성해주세요.

형식 규칙:
- ## 헤더 절대 금지. 섹션 구분은 반드시 "━━━ 이모지 섹션명 ━━━" 형식만 사용
- 짧은 bullet 나열 금지 — 각 섹션을 4~6문장의 문단으로 서술
- 섹션 제목과 본문에 내용에 어울리는 이모지를 자유롭게 선택해 포함
- 상단에 "오늘의 개념: ○○○" 한 줄 먼저 작성
- 초보 투자자도 이해할 수 있도록 쉽게 설명

섹션 구성 (3개):
━━━ 개념 정의 ━━━
이 개념이 무엇인지, 왜 중요한지 설명해주세요.

━━━ 실제 사례 ━━━
뉴스 또는 최근 시장에서 이 개념이 적용된 구체적인 사례를 들어주세요.

━━━ 투자 적용법 ━━━
이 개념을 실제 투자 의사결정에 어떻게 활용할 수 있는지 설명해주세요.

뉴스:
{news_items[0]['title']}
{news_items[0]['desc']}""",
    }

    need_keys = [item["check"] for item in need]
    summaries_list = await asyncio.gather(*[claude_summarize(prompts[k]) for k in need_keys])
    summaries = dict(zip(need_keys, summaries_list))

    # 4. Notion 저장
    print(f"\nNotion 저장 중...")
    saved = 0

    for item in ITEMS:
        key = item["check"]
        if skip[key]:
            print(f"  {item['label']:<14} — 건너뜀")
            continue

        summary = summaries[key]

        if item["label"].startswith("①"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] 경제 뉴스 종합", content=summary,
                source_url="https://www.yna.co.kr/economy",
                category="stock_research", sub_category="뉴스",
                source="연합뉴스", tags=["경제"],
            )
        elif item["label"].startswith("②"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] 주간 투자 브리핑", content=summary,
                source_url="https://www.yna.co.kr/economy",
                category="stock_research", sub_category="주간브리핑",
                source="연합뉴스", tags=["경제"],
            )
        elif item["label"].startswith("③"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] 섹터 분석 리포트", content=summary,
                source_url="https://www.hankyung.com/finance",
                category="stock_research", sub_category="증권사리포트",
                source="한국경제·매일경제", tags=["경제"],
            )
        elif item["label"].startswith("④"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] 개념노트: {news_items[0]['title'][:40]}",
                content=summary, source_url=news_items[0]['url'],
                category="stock_study", sub_category="이론",
                difficulty="기초", tags=["경제"],
            )

        ok = "완료" in result
        print(f"  {item['label']:<14} — {'✅ 저장됨' if ok else '❌ 실패'}")
        if ok:
            saved += 1

    print()
    print("=" * 45)
    print(f"  완료: {saved}개 신규 저장")
    print("=" * 45)


asyncio.run(main())
