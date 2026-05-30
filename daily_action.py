"""daily_action.py — GitHub Actions 전용 (daily.py와 동일 로직, Windows 경로 제거)"""
import asyncio, sys, io, httpx, xml.etree.ElementTree as ET, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import anthropic
import yfinance as yf
import holidays

load_dotenv()

KST = timezone(timedelta(hours=9))
_now = datetime.now(KST)
TODAY = _now.strftime("%Y-%m-%d")

if _now.weekday() not in (0, 2, 4):
    print(f"오늘({TODAY})은 실행일이 아닙니다 (월·수·금만 실행). 건너뜁니다.")
    sys.exit(0)

_kr_holidays = holidays.KR(years=_now.year)
if _now.date() in _kr_holidays:
    print(f"오늘({TODAY})은 한국 공휴일입니다. 건너뜁니다.")
    sys.exit(0)

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

PORTFOLIO_HOLDINGS = (
    "현재 보유 종목:\n"
    "- 삼성전자 (국내 개별주)\n"
    "- TIGER 미국S&P500 (S&P500 ETF) — 목표 비중 60%\n"
    "- KODEX 골드선물(H) (금 ETF) — 목표 비중 15%\n"
    "- ACE 미국30년국채액티브 (미국채 ETF 현물) — 목표 비중 25%\n"
    "- KIWOOM 국고채10년 (한국채권 ETF)\n"
    "- SOL 미국양자컴퓨팅TOP10 (테마 ETF)\n"
    "총 평가금액: 5,662,110원 / 수익률: +22.59%\n"
    "투자 방식: 목돈 집중 투자 (50만원↑ 모이면 시장 상황 보고 집중 매수, 월요일 오전 우선)\n"
    "매수 판단 기준: 지수 조정(-5%↑)→적극매수 / 과열(이격도 과대)→현금보유 / 횡보→소액분할"
)

ITEMS = [
    {"label": "① 뉴스 종합",      "db": "stock_research", "check": f"[{TODAY}] 경제 뉴스 종합"},
    {"label": "② 주간브리핑",     "db": "stock_research", "check": f"[{TODAY}] 주간 투자 브리핑"},
    {"label": "③ Claude인사이트", "db": "stock_research", "check": f"[{TODAY}] Claude 인사이트"},
    {"label": "④ 포트폴리오",     "db": "stock_research", "check": f"[{TODAY}] 포트폴리오 브리핑"},
]

RETENTION_DAYS = {
    "뉴스":       14,
    "주간브리핑": 30,
    "산업분석":   60,
    "포트폴리오": 60,
}

_title_prop_cache: dict[str, str] = {}


async def cleanup_old_entries(client: httpx.AsyncClient) -> int:
    """보존 기간이 지난 stock_research 항목을 아카이브합니다."""
    db_id = DB["stock_research"]
    total = 0
    for category, days in RETENTION_DAYS.items():
        cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
        r = await client.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=HEADERS,
            json={"filter": {"and": [
                {"property": "카테고리", "select": {"equals": category}},
                {"property": "날짜",    "date":   {"before": cutoff}},
            ]}},
        )
        pages = r.json().get("results", [])
        for page in pages:
            await client.patch(
                f"https://api.notion.com/v1/pages/{page['id']}",
                headers=HEADERS,
                json={"archived": True},
            )
        if pages:
            print(f"  [{category}] {len(pages)}개 삭제 ({days}일 초과)")
        total += len(pages)
    return total


async def already_saved(client: httpx.AsyncClient, db_key: str, title_contains: str) -> bool:
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


async def _fetch_rss(client: httpx.AsyncClient, url: str, source: str, count: int, retries: int = 2, retry_delay: float = 3.0) -> list[dict]:
    for attempt in range(retries + 1):
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
            if result:
                return result
        except Exception:
            pass
        if attempt < retries:
            await asyncio.sleep(retry_delay)
    return []


async def fetch_yna_items(count: int = 5) -> list[dict]:
    async with httpx.AsyncClient() as c:
        return await _fetch_rss(c, "https://www.yna.co.kr/rss/economy.xml", "연합뉴스", count)


async def fetch_finance_news(count_each: int = 3) -> list[dict]:
    async with httpx.AsyncClient() as c:
        hk, mk = await asyncio.gather(
            _fetch_rss(c, "https://www.hankyung.com/feed/finance", "한국경제", count_each),
            _fetch_rss(c, "https://www.mk.co.kr/rss/30100041/",   "매일경제", count_each),
        )
    return hk + mk


async def fetch_global_news(count_each: int = 3) -> list[dict]:
    async with httpx.AsyncClient() as c:
        rt, cnbc, mw, yna_int = await asyncio.gather(
            _fetch_rss(c, "https://feeds.reuters.com/reuters/businessNews",                          "Reuters",       count_each),
            _fetch_rss(c, "https://www.cnbc.com/id/10000664/device/rss/rss.html",                   "CNBC",          count_each),
            _fetch_rss(c, "https://feeds.marketwatch.com/marketwatch/topstories/",                  "MarketWatch",   count_each),
            _fetch_rss(c, "https://www.yna.co.kr/rss/international.xml",                            "연합뉴스 국제", count_each),
        )
    return rt + cnbc + mw + yna_int


def fetch_market_data() -> str:
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
            cur      = hist["Close"].iloc[-1]
            week_ago = hist["Close"].iloc[0]
            chg_pct  = (cur - week_ago) / week_ago * 100
            sign = "▲" if chg_pct >= 0 else "▼"
            lines.append(f"{name}: {cur:,.2f}  {sign}{abs(chg_pct):.2f}% (주간)")
        except Exception:
            pass
    return "\n".join(lines) if lines else "(시장 데이터 수집 실패)"


async def fetch_naver_research(count_each: int = 5) -> list[dict]:
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


def _make_prompt(label: str, news_items: list, finance_items: list, naver_items: list = [], global_items: list = [], market_text: str = "") -> str:
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
        combined = news_items + finance_items
        combined_text = "\n\n".join(
            f"[{i+1}] [{n.get('source', '뉴스')}] {n['title']}\n{n['desc']}"
            for i, n in enumerate(combined)
        )
        return f"""다음 {len(combined)}개 경제 뉴스를 투자자 관점에서 종합 분석해주세요.

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

뉴스 목록 (연합뉴스 + 한국경제·매일경제):
{combined_text}"""

    if label.startswith("②"):
        combined = news_items + finance_items
        combined_text = "\n\n".join(
            f"[{i+1}] [{n.get('source', '뉴스')}] {n['title']}\n{n['desc']}"
            for i, n in enumerate(combined)
        )
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

뉴스 목록 (연합뉴스 + 한국경제·매일경제):
{combined_text}"""

    if label.startswith("③"):
        return f"""날짜: {TODAY}
다음 뉴스와 증권사 리포트를 종합하여 특정 산업과 주요 지수에 대한 Claude만의 독자적인 인사이트를 제공해주세요.

⚠️ 중요: 단순 뉴스 요약이 아닙니다. 데이터를 종합하여 Claude가 직접 분석·예측·의견을 제시하는 문서입니다. 불확실성은 솔직하게 언급하되, 전문 애널리스트 수준의 독자적 시각을 보여주세요.

형식 규칙:
- ## 헤더 절대 금지. 섹션 구분은 반드시 "━━━ 이모지 섹션명 ━━━" 형식만 사용
- 짧은 bullet 나열 금지 — 각 섹션을 4~6문장의 문단으로 서술
- 수치, 종목명, 섹터명에 이모지를 자유롭게 활용

섹션 구성 (5개):
━━━ 주목 산업 심층 분석 ━━━
오늘 데이터에서 가장 주목할 산업 1~2개를 선정하고, 그 이유와 향후 3~6개월 전망을 Claude의 시각으로 분석해주세요. 증권사들이 같은 섹터에 집중하는 이유도 분석해주세요.

━━━ 주요 지수 전망 ━━━
KOSPI, KOSDAQ, S&P500 등 주요 지수의 현재 흐름과 단기 방향성에 대한 Claude의 견해를 제시해주세요. 지수별 핵심 변수를 짚어주세요.

━━━ 시장이 놓치고 있는 것 ━━━
현재 뉴스나 리포트에서 충분히 다루지 않지만 투자자가 주목해야 할 잠재 리스크 또는 기회를 Claude의 시각으로 제시해주세요.

━━━ ETF 추천 ━━━
현재 시장 상황에서 투자 매력이 높은 ETF를 구체적으로 추천해주세요. 한국 ETF(TIGER·KODEX·SOL·ACE·KIWOOM 계열 등)와 해외 ETF(QQQ·SPY·SOXX·TLT·GLD 등) 각각 1~2개씩, 추천 근거·현재 진입 매력도·핵심 리스크를 포함하여 서술해주세요.

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

    if label.startswith("④"):
        combined = news_items + finance_items
        combined_text = "\n\n".join(
            f"[{i+1}] [{n.get('source', '뉴스')}] {n['title']}\n{n['desc']}"
            for i, n in enumerate(combined[:10])
        )
        return f"""날짜: {TODAY}
다음 시장 데이터와 뉴스를 바탕으로 아래 포트폴리오에 특화된 브리핑과 Claude의 인사이트를 제공해주세요.

⚠️ 중요: 실제 보유 종목 기준으로 각 자산의 현재 상황을 분석하고, 목돈 집중 투자 방식에 맞는 실용적인 매수 판단을 제시하세요. 단순 뉴스 요약이 아닌 포트폴리오 맞춤형 분석입니다.

형식 규칙:
- ## 헤더 절대 금지. 섹션 구분은 반드시 "━━━ 이모지 섹션명 ━━━" 형식만 사용
- 짧은 bullet 나열 금지 — 각 섹션을 4~6문장의 문단으로 서술
- 수치, 종목명, 이모지를 자연스럽게 활용

{PORTFOLIO_HOLDINGS}

섹션 구성 (4개):
━━━ 📊 보유 종목별 현황 분석 ━━━
삼성전자, TIGER 미국S&P500, KODEX 골드선물(H), ACE 미국30년국채액티브, KIWOOM 국고채10년, SOL 미국양자컴퓨팅TOP10 각각의 최근 시장 흐름과 현재 상태를 분석해주세요.

━━━ ⏰ 이번 주 매수 타이밍 판단 ━━━
현재 시장이 과열·조정·횡보 중 어디에 해당하는지 판단하고, 목돈 집중 투자 기준으로 이번 주 추가 매수 여부와 우선 비중(어떤 종목을 얼마나)을 구체적으로 제안해주세요.

━━━ ⚠️ 포트폴리오 리스크 점검 ━━━
현재 보유 구조의 주요 리스크(달러/원 환율, 미국채 금리 방향, SOL 양자컴퓨팅 테마 변동성 등)를 분석하고, 방어 또는 헤지 전략을 제시해주세요.

━━━ 💡 Claude의 포트폴리오 인사이트 ━━━
이 포트폴리오의 구조적 강점과 개선 가능한 부분에 대한 Claude만의 솔직하고 구체적인 시각을 제시해주세요. 장기 관점에서의 조언도 포함해주세요.

📊 주요 시장 지표 (실시간):
{market_text if market_text else "(수집 없음)"}

오늘의 주요 뉴스 (국내 + 해외 상위 10건):
{combined_text if combined_text.strip() else "(수집 없음)"}"""

    return ""


async def main():
    from server import save_summary_to_notion

    print(f"[{TODAY}] 일일 Notion 저장")
    print("=" * 45)

    print("오래된 항목 정리 중...")
    async with httpx.AsyncClient(timeout=30) as cleanup_client:
        deleted = await cleanup_old_entries(cleanup_client)
    if deleted == 0:
        print("  정리할 항목 없음")
    print()

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

    need_labels  = {item["label"][0] for item in need}
    need_yna     = bool(need_labels & {'①', '②', '③', '④'})
    need_finance = bool(need_labels & {'①', '②', '③', '④'})
    need_global  = bool(need_labels & {'③', '④'})
    need_naver   = bool(need_labels & {'③'})
    need_market  = bool(need_labels & {'③', '④'})

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

    print(f"  연합뉴스: {len(news_items)}건")
    print(f"  국내 경제지(한국경제·매일경제): {len(finance_items)}건")
    if global_items:  print(f"  해외(Reuters·CNBC·MarketWatch·연합뉴스 국제): {len(global_items)}건")
    if naver_items:   print(f"  네이버 금융 리서치: {len(naver_items)}건")
    if market_text:   print(f"  시장 데이터: KOSPI·KOSDAQ·S&P500 등 8개 지표")

    # ①② 저장 항목은 연합뉴스 + 국내 경제지 합산이 0건이면 스킵 (④는 시장 데이터만으로도 진행)
    if not news_items and not finance_items:
        for item in need[:]:
            if item["label"][0] in ('①', '②'):
                print(f"  → {item['label']}: 국내 뉴스 0건으로 저장 건너뜁니다")
                skip[item["check"]] = True
                need.remove(item)

    if not need:
        print(f"\n  저장할 항목이 없습니다 (데이터 없음).")
        print("=" * 45)
        return

    print(f"\nClaude 요약 생성 중 (필요한 {len(need)}개만)...")
    prompts = {
        item["check"]: _make_prompt(item["label"], news_items, finance_items, naver_items, global_items, market_text)
        for item in need
    }
    summaries_list = await asyncio.gather(*[claude_summarize(prompts[item["check"]]) for item in need])
    summaries = {item["check"]: s for item, s in zip(need, summaries_list)}

    print(f"\nNotion 저장 중...")
    saved = 0

    for item in ITEMS:
        key = item["check"]
        if skip.get(key):
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
        elif label.startswith("④"):
            result = await save_summary_to_notion(
                title=f"[{TODAY}] 포트폴리오 브리핑",
                content=summary,
                source_url="https://app.notion.com/p/33117b872be681e99650d41885035b99",
                category="stock_research", sub_category="포트폴리오",
                source="Claude 자체분석", tags=["포트폴리오", "인사이트"], icon="💼",
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
