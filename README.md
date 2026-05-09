# notion_summary_server

AI 논문 또는 주식 기사 요약을 Notion 데이터베이스에 자동 저장하는 MCP 서버입니다.

## 파일 구조

```
notion_summary_server/
├── server.py           # MCP 서버 메인 파일
├── .env                # 환경변수 (직접 생성, git 제외)
├── .env.example        # 환경변수 템플릿
├── .gitignore
├── requirements.txt
└── README.md
```

## 도구 (Tool)

| 도구 | 설명 |
|------|------|
| `save_summary_to_notion` | 요약 내용을 Notion DB에 새 페이지로 저장 |

### 입력 파라미터

| 파라미터 | 타입 | 필수 | 설명 |
|----------|------|------|------|
| `title` | string | ✅ | 페이지 제목 |
| `content` | string | ✅ | 요약 내용 |
| `source_url` | string | ✅ | 원본 기사 또는 논문 URL |
| `category` | `"stock"` \| `"paper"` | ✅ | 저장 대상 DB 선택 |

---

## 사전 준비

### 1. Notion Integration 생성

1. [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations) 접속
2. **New integration** 클릭
3. 이름 입력 후 생성 → **Internal Integration Token** 복사

### 2. Notion DB에 Integration 연결

두 데이터베이스 각각에서:

1. 우측 상단 `...` 메뉴 → **Connections** → 생성한 Integration 추가
2. Integration이 연결되지 않으면 API 호출 시 `object_not_found` 오류 발생

---

## 설치

```bash
cd d:\python\notion_summary_server

# 가상환경 생성 (선택)
python -m venv .venv
.venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

---

## 환경변수 설정

`.env.example`을 복사해 `.env` 파일 생성:

```bash
copy .env.example .env
```

`.env` 파일을 열어 값 입력:

```env
NOTION_API_KEY=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_STOCK_DB_ID=33117b872be68187a1b4ddc51261856e
NOTION_PAPER_DB_ID=34617b872be68060a474e18a73510f38
```

> **DB ID 확인 방법**: Notion 데이터베이스 페이지 URL에서 `notion.so/` 뒤, `?` 앞의 32자리 문자열

---

## Claude Code MCP 등록

터미널에서 아래 명령어 실행:

```bash
claude mcp add notion_summary_server -- python d:/python/notion_summary_server/server.py
```

또는 `~/.claude/settings.json`에 직접 추가:

```json
{
  "mcpServers": {
    "notion_summary_server": {
      "command": "python",
      "args": ["d:/python/notion_summary_server/server.py"]
    }
  }
}
```

등록 후 Claude Code 재시작 또는 `/mcp` 명령으로 확인:

```
/mcp
```

---

## 사용 예시

Claude에게 다음과 같이 요청:

```
이 논문을 요약해서 Notion에 저장해줘:
https://arxiv.org/abs/2310.06825
```

```
오늘 삼성전자 기사 요약해서 주식 DB에 저장해줘
```

MCP 도구 직접 호출 예시:

```json
{
  "tool": "save_summary_to_notion",
  "arguments": {
    "title": "Attention Is All You Need",
    "content": "Transformer 아키텍처를 제안한 논문. Self-attention 메커니즘만으로 RNN 없이 seq2seq 모델 구현. BLEU 점수에서 SOTA 달성.",
    "source_url": "https://arxiv.org/abs/1706.03762",
    "category": "paper"
  }
}
```

---

## Notion 페이지 구조

저장되는 페이지 형태:

```
[제목]
┌─────────────────────────────────┐
│ 🔗 원본 링크: {source_url}       │
└─────────────────────────────────┘

## 요약
{content}

---
저장일시: 2025-05-09 10:30 UTC  |  분류: AI 논문
```

---

## 주의사항

- Notion DB의 title 프로퍼티 이름이 무엇이든 자동으로 감지하여 사용합니다.
- 요약 내용이 길면 자동으로 분할하여 저장합니다 (Notion 블록 제한: 2000자).
- API 키는 절대 git에 커밋하지 마세요. `.env` 파일은 `.gitignore`에 포함되어 있습니다.
