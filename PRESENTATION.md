# Impact Per Token v2
## MCP 자작 서버로 임팩트 끌어올리기
**제출자**: 진용 | **제출일**: 2026-05-14 | **GitHub**: notion_summary_server

---

## 사용 환경: Hermes 대신 Claude Code를 쓴 이유

강의에서는 Hermes(OpenRouter 기반 유료 서비스)를 권장했지만, 이 프로젝트는 **Claude Code CLI**로 진행했습니다.

| 항목 | Hermes | Claude Code |
|------|--------|-------------|
| 모델 | OpenRouter 경유 다양한 모델 | Claude (Anthropic 직접) |
| MCP 연동 | Hermes UI에서 서버 등록 | `claude_desktop_config.json`에 서버 등록 |
| Agent 구조 | Model + Harness | **동일** — Model + Harness |
| 비용 | 유료 (OpenRouter 크레딧) | API 사용량 기준 |

**핵심**: Hermes와 Claude Code는 **Agent 아키텍처가 동일**합니다.  
둘 다 `Model + Harness` 구조이고, MCP 서버를 Tool Schema로 연결해 사용합니다.  
Hermes 대신 Claude Code를 써도 이번 과제(MCP 서버 자작 + 실험)의 핵심 개념을 모두 검증할 수 있습니다.

---

## 프로젝트 개요

### 만든 MCP 서버: `notion_summary_server`

**목적**: Claude가 논문·주식 뉴스를 분석할 때마다 Notion에 자동 저장되지 않아 흩어지는 문제 해결

**기술 스택**
- `FastMCP` (Python) — MCP 서버 프레임워크
- `httpx` — Notion API 비동기 HTTP 클라이언트
- `Notion API v1` — 페이지 생성 / 블록 추가

```
Claude Code (Model + Harness)
    │
    └── MCP Tool 호출: save_summary_to_notion
            │
            └── httpx → Notion API
                    │
                    ├── category="paper"         → AI 논문 DB
                    ├── category="stock_research" → 주식 리서치 DB
                    └── category="stock_study"    → 주식 공부노트 DB
```

**구현 도구 2개**

| 도구 | 역할 |
|------|------|
| `save_summary_to_notion` | 카테고리별 Notion DB에 새 페이지 생성 |
| `update_notion_page` | 기존 페이지에 섹션 추가(append) |

**DB 라우팅 로직**
- `category="paper"` → AI 논문 DB
- `category="stock_research"` → 주식 리서치 DB (뉴스/산업분석/증권사리포트/주간브리핑)
- `category="stock_study"` → 주식 공부노트 DB

---

## 실험 02: Tool 성공 vs 실패 결과 비교

### 공통 과제
> "삼성전자 HBM3E 공급 확대 뉴스를 요약해서 Notion에 저장해줘"

---

### 조건 A — MCP 없음 (베이스라인)

**Claude의 행동**: 텍스트로 요약 출력 후 종료

```
"삼성전자가 HBM3E 12단 제품 공급을 확대한다고 밝혔습니다.
주요 AI 고객사 3곳과 장기 공급 계약을 체결…"
```

| 항목 | 결과 |
|------|------|
| Notion 저장 여부 | ❌ 없음 |
| 소요 시간 | 즉시 (텍스트 출력만) |
| 사용자 추가 작업 | Notion 수동 복사·붙여넣기 필요 |
| 검색/재사용 | ❌ 불가 |
| 재현성 | ❌ 대화창 닫으면 사라짐 |

---

### 조건 B — 정상 MCP (notion_summary_server)

**실측 결과**: API 호출 2회 → 1.42초 완료

```
HTTP GET  /databases/39bf50a... → 200 OK  (title 프로퍼티 동적 조회)
HTTP POST /pages               → 200 OK  (페이지 생성)
```

```
✅ Notion 저장 완료
제목: [실험02-B] 삼성전자 HBM3E 공급 확대 발표
분류: 주식 리서치
페이지: https://www.notion.so/...
```

| 항목 | 결과 |
|------|------|
| Notion 저장 여부 | ✅ 자동 |
| 소요 시간 | **1.42s** |
| 사용자 추가 작업 | 없음 |
| 검색/재사용 | ✅ DB 태그·날짜 필터 가능 |
| 재현성 | ✅ URL 영구 보존 |

---

### 조건 C — 망가진 MCP (server_broken.py, 결함 3개 주입)

**주입한 결함**

| 결함 번호 | 종류 | 내용 |
|----------|------|------|
| 결함 1 | 잘못된 DB ID | `BROKEN_DB_ID = "00000000...32자리"` → object_not_found |
| 결함 2 | description 누락 | docstring 없음 → 모델이 도구 목적 추론 불가 |
| 결함 3 | timeout=0.001s | 요청이 항상 TimeoutException 발생 |

**실측 결과**: 0.476초 만에 실패

```
❌ 오류: 요청 시간 초과 (timeout=0.001s — 고의 결함)
```

| 항목 | 결과 |
|------|------|
| Notion 저장 여부 | ❌ 실패 |
| 소요 시간 | **0.476s** (timeout으로 조기 종료) |
| 실패 발생 레이어 | Layer 3 (Harness — httpx 클라이언트) |
| 에러 격리 | ✅ 예외 처리로 상위 레이어 보호 |

---

### 실험 02 결론

```
조건 A (MCP 없음) → 저장 불가, 사용자 수작업
조건 B (정상 MCP) → 1.42s 완료, 완전 자동화
조건 C (망가진 MCP) → 0.476s에 실패, 실패 레이어 = Harness
```

> **핵심 인사이트**: "The model didn't get dumber. The harness failed."  
> 도구 실패는 모델 지능의 문제가 아니라 **Harness 설계의 문제**다.

---

## 실험 03: Orchestration 3패턴 비교

### 공통 과제
> "AI, 반도체, 금융 뉴스를 각각 요약해서 Notion에 저장하기 (3건)"

---

### 패턴 1 — Single Agent (순차 실행)

```python
for news in [AI, 반도체, 금융]:
    save_summary_to_notion(news)   # 하나씩 순서대로
```

**타임라인**
```
t=0.00s ─── [AI 저장 시작]
t=1.49s ─── [AI 완료] ─── [반도체 저장 시작]
t=2.54s ─── [반도체 완료] ─── [금융 저장 시작]
t=3.58s ─── [금융 완료]
(연결 setup 오버헤드 포함 총 6.88s)
```

| 측정 항목 | 결과 |
|----------|------|
| 총 소요시간 | **6.88s** |
| 추정 토큰 | 2,550 |
| 성공 건수 | 3/3 |
| 실패 격리 | ❌ 앞 작업 실패 시 전체 중단 |
| 구현 복잡도 | 낮음 |

---

### 패턴 2 — Planner + Executor (단계 분리)

```
Planner: "작업 3개를 구조화된 리스트로 반환"
         → [{"id":1,"topic":"AI"}, {"id":2,"topic":"반도체"}, ...]

Executor: 리스트를 받아 순서대로 저장 실행
```

**타임라인**
```
t=0.00s ─── [Planner: 계획 생성 +50ms]
t=0.05s ─── [Executor: AI 저장 시작]
t=1.20s ─── [AI 완료] ─── [반도체 시작]
t=2.16s ─── [반도체 완료] ─── [금융 시작]
t=3.26s ─── [금융 완료]
```

| 측정 항목 | 결과 |
|----------|------|
| 총 소요시간 | **3.27s** |
| 추정 토큰 | 2,670 (+120 Planner 오버헤드) |
| 성공 건수 | 3/3 |
| 실패 격리 | ⚠️ Planner 실패 시 전체 중단, Executor는 단계별 격리 |
| 구현 복잡도 | 중간 |

---

### 패턴 3 — Parallel Sub-Agent (병렬 동시 실행)

```python
await asyncio.gather(
    save_one("AI",   ...),    # sub-agent 1
    save_one("반도체", ...),  # sub-agent 2
    save_one("금융",  ...),   # sub-agent 3
)
```

**타임라인**
```
t=0.00s ─── [AI 시작] [반도체 시작] [금융 시작]  ← 동시 발사
t=1.18s ───────────────────────── [금융 완료]
t=1.49s ────────────── [반도체 완료]
t=1.89s ─ [AI 완료]  ← 가장 느린 작업이 전체 완료 시간 결정
```

| 측정 항목 | 결과 |
|----------|------|
| 총 소요시간 | **1.89s** |
| 추정 토큰 | 2,550 (Single과 동일) |
| 성공 건수 | 3/3 |
| 실패 격리 | ✅ 개별 sub-agent 독립 실패, 나머지 계속 진행 |
| 구현 복잡도 | 중간 |

---

### 실험 03 벤치마크 표 (실측)

| 패턴 | 총 소요시간 | 토큰 | 성공 | 실패 격리 | 추천 상황 |
|------|:-----------:|:----:|:----:|:--------:|----------|
| Single | 6.88s | 2,550 | 3/3 | ❌ | 단순 단일 작업 |
| Planner+Executor | 3.27s | 2,670 | 3/3 | ⚠️ | 복잡한 의존 관계 |
| **Parallel** | **1.89s** | **2,550** | **3/3** | **✅** | **독립적 I/O 다건** |

**속도 비율**: Parallel은 Single 대비 **3.6× 빠름**

---

### 실험 03 결론

```
이 작업(독립적 I/O 3건)엔 Parallel이 최선.

Single          → 구현 쉽지만, 순차 I/O 대기 시간이 누적됨
Planner+Executor → 작업 간 의존성이 있거나 동적 계획이 필요할 때 유용
Parallel        → 독립적 I/O 다건 처리에 3.6× 속도 이점, 실패 격리도 우수
```

---

## 전체 회고

### 이 작업에서 배운 것

1. **Harness 품질이 성능을 결정한다**  
   결함 3개만 넣어도 1.42s 완료 → 0.476s 실패로 변한다.  
   모델 지능이 아니라 도구 설계(timeout, DB ID, description)가 병목.

2. **description은 모델의 인지 환경이다**  
   docstring이 없으면 모델이 도구를 언제 써야 할지 추론하지 못한다.  
   Tool Schema = 에이전트의 지식 지도.

3. **I/O 병목은 Parallel로 해소된다**  
   토큰 수는 같아도 레이턴시는 3.6배 차이.  
   독립적인 외부 API 호출은 항상 병렬화를 우선 검토해야 한다.

4. **Planner+Executor의 진짜 가치는 속도가 아니다**  
   계획을 별도 단계로 분리하면 → 계획 검증·수정 가능, 단계별 로깅, 재시작 지점 확보.

---

## GitHub 구조

```
notion_summary_server/
├── server.py                    # 정상 MCP 서버 (도구 2개)
├── server_broken.py             # 실험 02 결함 주입 버전
├── daily.py                     # 일일 자동화 스크립트 (뉴스·시장 데이터 수집 → Notion 저장)
├── experiment_orchestration.py  # 실험 03 벤치마크 스크립트
├── experiment_results.json      # 실측 데이터
├── CLAUDE.md                    # Harness 운영 규칙
├── requirements.txt
└── README.md
```

---

*제출: kts123@kookmin.ac.kr | 발표 자료 형식: Markdown*
