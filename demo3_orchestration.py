"""발표 화면 3: Orchestration 3패턴 비교 — 뉴스종합/주간브리핑/증권사리포트"""
import asyncio, sys, time, io, httpx, xml.etree.ElementTree as ET
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'd:/python/notion_summary_server')

TODAY = datetime.now().strftime("%Y-%m-%d")


def est_tokens(text: str) -> int:
    """Claude 토크나이저 근사값 — 한국어 ~1tok/자, 영어 ~0.25tok/자"""
    korean = sum(1 for c in text if '가' <= c <= '힣' or '一' <= c <= '鿿')
    other  = len(text) - korean
    return korean + other // 4


async def claude_summarize(prompt: str) -> tuple[str, int]:
    """(응답 텍스트, 입력 토큰 수) 반환"""
    proc = await asyncio.create_subprocess_exec(
        'claude', '-p', prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode('utf-8', errors='replace').strip(), est_tokens(prompt)


async def fetch_yna_items(count: int = 5) -> list[dict]:
    async with httpx.AsyncClient() as c:
        r = await c.get("https://www.yna.co.kr/rss/economy.xml", follow_redirects=True, timeout=15)
    root = ET.fromstring(r.content)
    items = root.findall('.//item')
    result = []
    for item in items[:count]:
        desc_elem = item.find('description')
        result.append({
            "title": item.find('title').text or "경제 뉴스",
            "desc":  (desc_elem.text or "")[:300],
            "url":   item.find('link').text or "https://www.yna.co.kr",
        })
    return result


async def save_to_notion(title: str, content: str, sub_category: str, source: str, tags: list) -> float:
    """Notion 저장만 담당 — Claude 호출 없음"""
    from server import save_summary_to_notion
    t0 = time.perf_counter()
    await save_summary_to_notion(
        title=title,
        content=content,
        source_url="https://www.yna.co.kr/economy",
        category="stock_research",
        sub_category=sub_category,
        source=source,
        tags=tags,
    )
    return time.perf_counter() - t0


async def main():
    print("=" * 55)
    print("[ Orchestration 3패턴 비교 ]")
    print("같은 작업 · 같은 모델 · 구조만 다름")
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    # ━━ 사전 준비: 뉴스 수집 + Claude 요약 (1번씩만) ━━
    print("\n[사전 준비] 뉴스 수집 + Claude 요약 생성 중...")
    news_items = await fetch_yna_items(5)
    for i, n in enumerate(news_items, 1):
        print(f"  [{i}] {n['title'][:55]}")

    news_text = "\n\n".join(f"[{i+1}] {n['title']}\n{n['desc']}" for i, n in enumerate(news_items))

    # Claude 요약 3종 병렬 생성 (패턴 비교와 무관하게 1번만)
    print("\n  Claude 요약 3종 생성 중 (병렬)...")
    t_prep = time.perf_counter()
    (news_summary, tok_news), (briefing, tok_brief), (report, tok_report) = await asyncio.gather(
        claude_summarize(f"다음 경제 뉴스들을 투자자 관점에서 종합 요약해주세요.\n\n{news_text}"),
        claude_summarize(f"날짜: {TODAY}\n다음 뉴스를 바탕으로 주간 투자 브리핑을 작성해주세요. 시장 동향, 유망 섹터, 투자 전략 포함.\n\n{news_text}"),
        claude_summarize(f"다음 뉴스에서 주목할 기업/섹터를 선정해 증권사 리포트 형식으로 작성해주세요. 현황, 투자 포인트, 투자의견, 리스크 포함.\n\n{news_text[:600]}"),
    )
    # 출력 토큰도 추정
    tok_out = est_tokens(news_summary) + est_tokens(briefing) + est_tokens(report)
    tok_in  = tok_news + tok_brief + tok_report
    tok_total = tok_in + tok_out
    PLANNER_OVERHEAD = 120  # Planner+Executor 계획 단계 추가 토큰 추정
    print(f"  준비 완료 ({time.perf_counter() - t_prep:.1f}초)")
    print(f"  입력 토큰: ~{tok_in:,}  출력 토큰: ~{tok_out:,}  합계: ~{tok_total:,}")

    # 3패턴 비교에 사용할 작업 목록
    JOBS = [
        (f"[{TODAY}] 뉴스 종합",        news_summary, "뉴스",     "연합뉴스",      ["경제"]),
        (f"[{TODAY}] 주간 투자 브리핑", briefing,     "주간브리핑","Claude 자동 수집",["경제"]),
        (f"[{TODAY}] 섹터 분석 리포트", report,       "증권사리포트","Claude 자동 분석",["경제"]),
    ]

    print("\n저장 작업: 뉴스종합 / 주간브리핑 / 증권사리포트")
    print("(이하 패턴 비교는 Notion API 저장 속도만 측정)")

    # 패턴 1: Single (순차)
    print("\n패턴 1: Single (순차)  실행 중...")
    t = time.perf_counter()
    for title, content, sub, src, tags in JOBS:
        await save_to_notion(title, content, sub, src, tags)
    t1 = time.perf_counter() - t
    print(f"  완료: {t1:.2f}초")

    # 패턴 2: Planner+Executor
    print("\n패턴 2: Planner+Executor  실행 중...")
    t = time.perf_counter()
    await asyncio.sleep(0.05)  # Planner 오버헤드
    for title, content, sub, src, tags in JOBS:
        await save_to_notion(title, content, sub, src, tags)
    t2 = time.perf_counter() - t
    print(f"  완료: {t2:.2f}초")

    # 패턴 3: Parallel
    print("\n패턴 3: Parallel (병렬)  실행 중...")
    t = time.perf_counter()
    await asyncio.gather(*[
        save_to_notion(title, content, sub, src, tags)
        for title, content, sub, src, tags in JOBS
    ])
    t3 = time.perf_counter() - t
    print(f"  완료: {t3:.2f}초")

    ratio = t1 / t3 if t3 > 0 else 0

    print()
    print("=" * 60)
    print(f"  {'패턴':<18} {'시간':>7}  {'토큰':>7}  {'토큰 대비 속도'}")
    print(f"  {'-'*56}")
    print(f"  {'Single':<18} {t1:>6.2f}초  ~{tok_total:>5,}  기준 (1.0×)")
    print(f"  {'Planner+Executor':<18} {t2:>6.2f}초  ~{tok_total+PLANNER_OVERHEAD:>5,}  토큰 +{PLANNER_OVERHEAD} 오버헤드")
    print(f"  {'Parallel ★':<18} {t3:>6.2f}초  ~{tok_total:>5,}  {ratio:.1f}× 빠름 · 토큰 동일")
    print("=" * 60)
    print(f"  핵심: 같은 토큰으로 {ratio:.1f}배 임팩트  →  Impact Per Token ↑")
    print(f"  모델 동일 · 구조만 다름")


asyncio.run(main())
