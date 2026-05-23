# notion_summary_server

매일 자동으로 시장 데이터·뉴스를 수집해 Notion에 저장하는 투자 리서치 자동화 시스템.  
MCP 서버(`server.py`)와 일일 자동화 스크립트(`daily.py`)로 구성됩니다.

> ⚠️ **Notion 관련 작업은 이 폴더를 VS Code에서 열고 사용해야 합니다.**  
> `CLAUDE.md`가 이 폴더에 위치해 있어, 여기서 열어야 Claude가 Notion 규칙을 적용합니다.

---

## 📁 파일 구조

```
notion_summary_server/
├── CLAUDE.md                    # Claude Code 작업 규칙
├── AGENTS.md                    # Codex 작업 규칙
├── daily.py                     # 일일 자동화 스크립트 (메인)
├── server.py                    # MCP 서버 (Notion 저장 도구)
├── server_broken.py             # 실험용 결함 주입 버전
├── experiment_orchestration.py  # 오케스트레이션 실험 스크립트
├── experiment_results.json      # 실험 실측 데이터
├── .env                         # 환경변수 (직접 생성, git 제외)
├── .env.example                 # 환경변수 템플릿
├── requirements.txt
└── README.md
```

---

## 🤖 일일 자동화 (daily.py)

### 실행 명령어

```powershell
& "D:\Anaconda3\envs\project\python.exe" "D:\projects\python\notion_summary_server\daily.py"
```

### 저장 항목 (3개)

| 항목 | 제목 형식 | 아이콘 | DB |
|------|-----------|--------|----|
| ① 뉴스 종합 | `[YYYY-MM-DD] 경제 뉴스 종합` | 📰 | 주식 리서치 |
| ② 주간브리핑 | `[YYYY-MM-DD] 주간 투자 브리핑` | 📊 | 주식 리서치 |
| ③ Claude 인사이트 | `[YYYY-MM-DD] Claude 인사이트` | 🔍 | 주식 리서치 |

- 오늘 날짜 페이지가 이미 있으면 자동 스킵
- 3개 항목 Claude 요약 병렬 생성 후 순차 저장

### 데이터 소스

| 소스 | 항목 | 수집량 |
|------|------|--------|
| 연합뉴스 RSS | ①②③ | 5건 |
| 한국경제 RSS | ③ | 3건 |
| 매일경제 RSS | ③ | 3건 |
| Reuters RSS | ③ | 3건 |
| CNBC RSS | ③ | 3건 |
| MarketWatch RSS | ③ | 3건 |
| 연합뉴스 국제경제 RSS | ③ | 3건 |
| 네이버 금융 리서치 | ③ | 10건 (제목·증권사명) |
| yfinance 시장 데이터 | ③ | KOSPI·KOSDAQ·S&P500·NASDAQ·달러인덱스·WTI·금·달러/원 |

---

## 🗄️ Notion DB 구조

모두 **"Finance & 투자"** 페이지 (`33117b872be68187a1b4ddc51261856e`) 내 인라인 DB.

| DB | ID | 용도 |
|---|---|---|
| 📰 주식 리서치 | `39bf50a1aca04ad2a10079e958cf96d4` | 뉴스 / 산업분석 / 주간브리핑 |
| 📚 주식 공부노트 | `d1afcb876857487eb978c1a8e0952d05` | 용어 / 이론 / 지표 / 전략 / 산업지식 |
| 📖 AI 논문 | `34617b872be68060a474e18a73510f38` | 논문 리뷰 및 요약 |

---

## 🔧 MCP 도구 (server.py)

| 도구 | 설명 |
|---|---|
| `save_summary_to_notion` | 논문·주식 리서치·공부노트를 Notion DB에 새 페이지로 저장 |
| `update_notion_page` | 기존 Notion 페이지에 내용 추가(append) |

### `save_summary_to_notion` 파라미터

| 파라미터 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `title` | string | ✅ | 페이지 제목 |
| `content` | string | ✅ | 본문 내용 |
| `source_url` | string | ✅ | 원본 URL |
| `category` | `"paper"` \| `"stock_research"` \| `"stock_study"` | ✅ | 저장 DB 선택 |
| `sub_category` | string | ❌ | 카테고리 세분류 |
| `source` | string | ❌ | 출처 |
| `tags` | list[string] | ❌ | 태그 목록 |
| `difficulty` | string | ❌ | 난이도 — `stock_study` 전용 (`"기초"` \| `"중급"` \| `"고급"`) |
| `icon` | string | ❌ | 페이지 아이콘 이모지 (예: `"📰"`) |

#### sub_category 옵션

| category | sub_category 선택지 |
|---|---|
| `stock_research` | `뉴스` \| `산업분석` \| `증권사리포트` \| `주간브리핑` |
| `stock_study` | `용어` \| `이론` \| `지표` \| `전략` \| `산업지식` |

---

## ⚙️ 환경 설정

### Python 환경

```
D:\Anaconda3\envs\project\python.exe
```

### 의존성 설치

```powershell
& "D:\Anaconda3\envs\project\python.exe" -m pip install httpx python-dotenv mcp yfinance
```

### 환경변수 (.env)

```env
NOTION_API_KEY=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_PAPER_DB_ID=34617b872be68060a474e18a73510f38
NOTION_STOCK_RESEARCH_DB_ID=39bf50a1aca04ad2a10079e958cf96d4
NOTION_STOCK_STUDY_DB_ID=d1afcb876857487eb978c1a8e0952d05
```

### MCP 서버 등록 (Claude Code)

`~/.claude/settings.json`에 추가:

```json
{
  "mcpServers": {
    "notion_summary_server": {
      "command": "D:\\Anaconda3\\envs\\project\\python.exe",
      "args": ["d:/projects/python/notion_summary_server/server.py"]
    }
  }
}
```

---

## ✍️ 콘텐츠 포맷 규칙

- 마크다운 헤더(`##`, `###`) 사용 금지 → `━━━ 섹션명 ━━━` 형식 사용
- 이모지 적극 활용 (섹션 구분, 수치 강조)
- 문단 형식으로 충분히 서술 (bullet 나열 금지)

---

## ⚠️ 주의사항

- `.env`는 `.gitignore`에 포함되어 있습니다. API 키를 커밋하지 마세요.
- Notion DB의 title 프로퍼티 이름은 자동으로 감지합니다.
- 본문이 길면 자동 분할 저장합니다 (Notion 블록 제한: 2000자).
- MCP 서버 코드 변경 후에는 Claude Code를 재시작해야 합니다.
