"""daily.py — 오늘 날짜 페이지가 없을 때만 Notion에 저장 (4개: 뉴스종합/주간브리핑/증권사리포트/공부노트)"""
import asyncio, sys, io, httpx, xml.etree.ElementTree as ET, os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import anthropic
import yfinance as yf

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
load_dotenv('d:/projects/python/notion-finance-digest/.env')
sys.path.insert(0, 'd:/projects/python/notion-finance-digest')

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

ITEMS = [
    {"label": "① 뉴스 종합",      "db": "stock_research", "check": f"[{TODAY}] 경제 뉴스 종합"},
    {"label": "② 주간브리핑",     "db": "stock_research", "check": f"[{TODAY}] 주간 투자 브리핑"},
    {"label": "③ Claude인사이트", "db": "stock_research", "check": f"[{TODAY}] Claude 인사이트"},
]

# DB 메타데이터 캐시 — 같은 DB에 대한 중복 GET 요청 방지
_title_prop_cache: dict[str, str] = {}


async def already_saved(client: httpx.AsyncClient, db_key: str, title_contains: str) -> bool:
    """해당 DB에 오늘 제목의 페이지가 이미 있는지 확인"""
    db_id = DB[db_key]
    if db_id not in _title_prop_cache:
        r = await client.get(f"https://api.notion.com/v1/databases/{db_id}", headers=HEADERS)
        _title_prop_cache[db_id] = next(
            (name for name, p in r.json().get("properties", {}).items() if p.get("type") == "title"),
            "Name"
        )
    title_prop = _title_prop_cache[db_id]
    r2 = await client.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=HEADERS,
        json={"filter": {"property": title_prop, "title": {"contains": title_contains}}}
    )
    return len(r2.json().get("results", [])) > 0


async def claude_summarize(prompt: str) -> str:
    system_prompt = (
        "당신은 투자 콘텐츠 초안을 작성하는 순수 텍스트 생성기입니다. "
        "사용자가 제공한 자료만 종합해 최종 본문 텍스트만 출력하세요. "
        "페이지 생성, 저장, 업데이트, 링크 생성은 하지 않습니다."
    )
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    summary = message.content[0].text.strip()
    if not summary:
        raise RuntimeError("Claude 요약 결과가 비어 있어 Notion 저장을 중단합니다.")
    return summary


async def _fetch_rss(client: httpx.AsyncClient, url: str, source: str, count: int) -> list[dict]:
    try:
        r = await client.get(url, follow_redirects=True, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        result = []
        for item in root.findall('.//item')[:count]:
            title_elem = item.find('title')
            desc_elem  = item.find('description')
            link_elem  = item.find('link')
            result.append({
                "title":  title_elem.text if title_elem is not None else "뉴스",
                "desc":   (desc_elem.text or "")[:300] if desc_elem is not None else "",
                "url":    link_elem.text if link_elem is not None else url,
                "source": source,
            })
        return result
    except Exception:
        return []


async def fetch_yna_items(count: int = 5) -> list[dict]:
    async with httpx.AsyncClient() as c:
        return await _fetch_rss(c, "https://www.yna.co.kr/rss/economy.xml", "연합뉴스", count)


async def fetch_finance_news(count_each: int = 3) -> list[dict]:
    """한국경제·매일경제 금융/증권 뉴스 수집"""
    async with httpx.AsyncClient() as c:
        hk, mk = await asyncio.gather(
            _fetch_rss(c, "https://www.hankyung.com/feed/finance", "한국경제", count_each),
            _fetch_rss(c, "https://www.mk.co.kr/rss/30100041/",   "매일경제", count_each),
        )
    return hk + mk


async def fetch_global_news(count_each: int = 3) -> list[dict]:
    """Reuters·CNBC·MarketWatch·연합뉴스 국제경제 수집"""
    async with httpx.AsyncClient() as c:
        rt, cnbc, mw, yna_int = await asyncio.gather(
            _fetch_rss(c, "https://feeds.reuters.com/reuters/businessNews",                          "Reuters",       count_each),
            _fetch_rss(c, "https://www.cnbc.com/id/10000664/device/rss/rss.html",                   "CNBC",          count_each),
            _fetch_rss(c, "https://feeds.marketwatch.com/marketwatch/topstories/",                  "MarketWatch",   count_each),
            _fetch_rss(c, "https://www.yna.co.kr/rss/international.xml",                            "연합뉴스 국제", count_each),
        )
    return rt + cnbc + mw + yna_int


def fetch_market_data() -> str:
    """주요 시장 지수·자산 가격 수집 (yfinance) — 주간 등락 포함"""
    TICKERS = {
        "KOSPI":    "^KS11",
        "KOSDAQ":   "^KQ11",
        "S&P500":   "^GSPC",
        "NASDAQ":   "^IXIC",
        "달러인덱스": "DX-Y.NYB",
        "WTI유가":  "CL=F",
        "금":       "GC=F",
        "달러/원":  "KRW=X",
    }
    lines = []
    for name, ticker in TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="8d")
            if hist.empty:
                continue
            cur   = hist["Close"].iloc[-1]
            week_ago = hist["Close"].iloc[0]
            chg_pct  = (cur - week_ago) / week_ago * 100
            sign = "▲" if chg_pct >= 0 else "▼"
            lines.append(f"{name}: {cur:,.2f}  {sign}{abs(chg_pct):.2f}% (주간)")
        except Exception:
            pass
    return "\n".join(lines) if lines else "(시장 데이터 수집 실패)"


async def fetch_naver_research(count_each: int = 5) -> list[dict]:
    """네이버 금융 리서치 — 산업분석·기업분석 증권사 리포트 목록"""
    from html.parser import HTMLParser

    class _TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self._row, self._cell = [], None, None

        def handle_starttag(self, tag, attrs):
            if tag == 'tr':
                self._row = []
            elif tag == 'td' and self._row is not None:
                self._cell = []

        def handle_endtag(self, tag):
            if tag == 'tr' and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None
            elif tag == 'td' and self._cell is not None and self._row is not None:
                self._row.append(''.join(self._cell).strip())
                self._cell = None

        def handle_data(self, data):
            if self._cell is not None and data.strip():
                self._cell.append(data.strip())

    SKIP = {'제목', '리포트명', '종목명', '섹터', '업종', '증권사', '날짜', '조회수'}
    results = []
    endpoints = [
        ("https://finance.naver.com/research/industry_list.naver", "산업분석"),
        ("https://finance.naver.com/research/company_list.naver", "기업분석"),
    ]
    async with httpx.AsyncClient() as c:
        for url, kind in endpoints:
            try:
                r = await c.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                text = r.content.decode('euc-kr', errors='replace')
                p = _TableParser()
                p.feed(text)
                added = 0
                for row in p.rows:
                    if added >= count_each:
                        break
                    if len(row) < 3 or not row[0] or not row[1]:
                        continue
                    if row[0] in SKIP or row[1] in SKIP:
                        continue
                    results.append({
                        "kind": kind,
                        "stock": row[0],
                        "title": row[1],
                        "brokerage": row[2] if len(row) > 2 else "",
                        "date": row[3] if len(row) > 3 else "",
                    })
                    added += 1
            except Exception:
                pass
    return results


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


def _make_prompt(label: str, news_items: list, finance_items: list, naver_items: list = [], global_items: list = [], market_text: str = "") -> str:
    """항목별 Claude 프롬프트 생성 — 필요한 데이터만 참조"""
    news_text = "\n\n".join(f"[{i+1}] {n['title']}\n{n['desc']}" for i, n in enumerate(news_items))
    finance_text = "\n\n".join(
        f"[{i+1}] [{n['source']}] {n['title']}\n{n['desc']}"
        for i, n in enumerate(finance_items)
    )
    global_text = "\n\n".join(
        f"[{i+1}] [{n['source']}] {n['title']}\n{n['desc']}"
        for i, n in enumerate(global_items)
    ) if global_items else "(수집 없음)"
    naver_text = "\n".join(
        f"[{i+1}] [{r['brokerage']}] {r['stock']} | {r['title']} ({r['date']})"
        for i, r in enumerate(naver_items)
    ) if naver_items else "(수집 없음)"
    if label.startswith("①"):
        return f"""다음 {len(news_items)}개 경제 뉴스를 투자자 관점에서 종합 분석해주세요.

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
{news_text}"""

    if label.startswith("②"):
        return f"""날짜: {TODAY}
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
{news_text}"""

    if label.startswith("③"):
        return f"""날짜: {TODAY}
다음 뉴스와 증권사 리포트를 종합하여 특정 산업과 주요 지수에 대한 Claude만의 독자적인 인사이트를 제공해주세요.

⚠️ 중요: 단순 뉴스 요약이 아닙니다. 데이터를 종합하여 Claude가 직접 분석·예측·의견을 제시하는 문서입니다. 불확실성은 솔직하게 언급하되, 전문 애널리스트 수준의 독자적 시각을 보여주세요.

형식 규칙:
- ## 헤더 절대 금지. 섹션 구분은 반드시 "━━━ 이모지 섹션명 ━━━" 형식만 사용
- 짧은 bullet 나열 금지 — 각 섹션을 4~6문장의 문단으로 서술
- 수치, 종목명, 섹터명에 이모지를 자유롭게 활용

섹션 구성 (4개):
━━━ 주목 산업 심층 분석 ━━━
오늘 데이터에서 가장 주목할 산업 1~2개를 선정하고, 그 이유와 향후 3~6개월 전망을 Claude의 시각으로 분석해주세요. 증권사들이 같은 섹터에 집중하는 이유도 분석해주세요.

━━━ 주요 지수 전망 ━━━
KOSPI, KOSDAQ, S&P500 등 주요 지수의 현재 흐름과 단기 방향성에 대한 Claude의 견해를 제시해주세요. 지수별 핵심 변수를 짚어주세요.

━━━ 시장이 놓치고 있는 것 ━━━
현재 뉴스나 리포트에서 충분히 다루지 않지만 투자자가 주목해야 할 잠재 리스크 또는 기회를 Claude의 시각으로 제시해주세요.

━━━ Claude의 핵심 의견 ━━━
이번 주 투자자가 가장 집중해야 할 1가지 핵심 포인트를 Claude가 직접 제시하고, 그 근거를 설득력 있게 설명해주세요.

📊 주요 시장 지표 (실시간):
{market_text if market_text else "(수집 없음)"}

오늘의 국내 뉴스 (연합뉴스):
{news_text}

오늘의 국내 경제지 (한국경제·매일경제):
{finance_text if finance_text.strip() else "(수집 없음)"}

오늘의 해외 뉴스 (Reuters·CNBC·MarketWatch·연합뉴스 국제):
{global_text}

오늘의 증권사 리포트 목록 (네이버 금융):
{naver_text}"""

    return ""


async def main():
    from server import save_summary_to_notion

    print(f"[{TODAY}] 일일 Notion 저장")
    print("=" * 45)

    # 1. 오늘 이미 저장된 항목 확인 (DB 메타데이터 캐시 활용)
    print("저장 여부 확인 중...")
    async with httpx.AsyncClient(timeout=30) as check_client:
        exist_flags = await asyncio.gather(*[
            already_saved(check_client, item["db"], item["check"]) for item in ITEMS
        ])

    skip = dict(zip([item["check"] for item in ITEMS], exist_flags))

    for item, done in zip(ITEMS, exist_flags):
        print(f"  {item['label']:<14} {'이미 저장됨' if done else '저장 필요'}")

    need = [item for item, done in zip(ITEMS, exist_flags) if not done]
    if not need:
        print(f"\n  오늘({TODAY}) 모두 이미 저장되어 있습니다.")
        print("=" * 45)
        return

    # 2. 필요한 소스만 수집
    need_labels  = {item["label"][0] for item in need}
    need_yna     = bool(need_labels & {'①', '②', '③'})
    need_finance = '③' in need_labels
    need_global  = '③' in need_labels
    need_naver   = '③' in need_labels
    need_market  = '③' in need_labels

    print(f"\n데이터 수집 중...")
    news_items, finance_items, global_items, naver_items = [], [], [], []
    market_text = ""

    gather_tasks, task_keys = [], []
    if need_yna:
        gather_tasks.append(fetch_yna_items(5));       task_keys.append('yna')
    if need_finance:
        gather_tasks.append(fetch_finance_news(3));    task_keys.append('finance')
    if need_global:
        gather_tasks.append(fetch_global_news(3));     task_keys.append('global')
    if need_naver:
        gather_tasks.append(fetch_naver_research(5));  task_keys.append('naver')

    if need_market:
        market_text = await asyncio.get_event_loop().run_in_executor(None, fetch_market_data)

    if gather_tasks:
        collected = await asyncio.gather(*gather_tasks)
        for key, data in zip(task_keys, collected):
            if key == 'yna':       news_items    = data
            elif key == 'finance': finance_items = data
            elif key == 'global':  global_items  = data
            elif key == 'naver':   naver_items   = data

    if news_items:
        print(f"  연합뉴스: {len(news_items)}건")
    if finance_items:
        print(f"  국내 경제지(한국경제·매일경제): {len(finance_items)}건")
    if global_items:
        print(f"  해외(Reuters·CNBC·MarketWatch·연합뉴스 국제): {len(global_items)}건")
    if naver_items:
        print(f"  네이버 금융 리서치: {len(naver_items)}건")
    if market_text:
        print(f"  시장 데이터: KOSPI·KOSDAQ·S&P500 등 8개 지표")

    # 3. 필요한 항목만 Claude 요약 생성 (병렬)
    print(f"\nClaude 요약 생성 중 (필요한 {len(need)}개만)...")
    prompts = {
        item["check"]: _make_prompt(item["label"], news_items, finance_items, naver_items, global_items, market_text)
        for item in need
    }
    summaries_list = await asyncio.gather(*[claude_summarize(prompts[item["check"]]) for item in need])
    summaries = {item["check"]: s for item, s in zip(need, summaries_list)}

    # 4. Notion 저장
    print(f"\nNotion 저장 중...")
    saved = 0

    for item in ITEMS:
        key = item["check"]
        if skip[key]:
            print(f"  {item['label']:<14} — 건너뜀")
            continue

        summary = summaries[key]
        label = item["label"]
        result = "❌ 저장 대상 없음"

        if label.startswith("①"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] 경제 뉴스 종합", content=summary,
                source_url="https://www.yna.co.kr/economy",
                category="stock_research", sub_category="뉴스",
                source="연합뉴스", tags=["경제"], icon="📰",
            )
        elif label.startswith("②"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] 주간 투자 브리핑", content=summary,
                source_url="https://www.yna.co.kr/economy",
                category="stock_research", sub_category="주간브리핑",
                source="연합뉴스", tags=["경제"], icon="📊",
            )
        elif label.startswith("③"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] Claude 인사이트",
                content=summary,
                source_url="https://finance.naver.com/research/",
                category="stock_research", sub_category="산업분석",
                source="Claude 자체분석", tags=["경제", "인사이트"], icon="🔍",
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
