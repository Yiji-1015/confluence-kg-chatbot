# 1. 데이터 이해·정제

[전체 목차](README.md) · 다음: [검색 품질 설계](02-retrieval.md)

## 1. 이 축이 하는 일

Confluence의 storage HTML을 받아 검색용 본문, 문서 메타데이터, 청크로 바꾼다. 단순 태그 삭제만으로는 병합 표의 행·열 관계와 내부 문서 링크의 대상 제목을 제대로 남기기 어렵다.

```text
Confluence 본문 + 조상 페이지 목록
 → 문서 ID로 카테고리·경로 결합
 → 매크로 정제 / 내부 링크 변환 / 병합 표 펼치기
 → cleaned_text
 → 800자 크기·150자 overlap 설정으로 청킹
 → 제목 + 청크 본문 임베딩
 → ES에 본문·벡터·메타데이터 저장
```

## 2. 코드를 읽는 순서

| 파일 | 함수 / 확인할 부분 |
|---|---|
| [confluence_client.py](../../ai-server/app/core/confluence_client.py) | `fetch_confluence_pages`, `fetch_pages_with_category`, `fetch_pages_by_ids` |
| [confluence_parser.py](../../ai-server/app/parser/confluence_parser.py) | `parse_confluence_html`, `_table_to_records`, `_table_records_to_markdown`, `split_text_into_chunks` |
| [ingest.py](../../ai-server/scripts/ingest.py) | 수집 결과 결합, 파서에 넘기는 metadata, 임베딩 입력 |
| [es_client.py](../../ai-server/app/retrieval/es_client.py) | `create_confluence_index`, `index_document_chunks` |

## 3. 수집은 본문과 계층 정보로 나뉜다

`fetch_confluence_pages()`는 `/rest/api/content`에 `type=page`와 `expand=body.storage,version,history.lastUpdated,history.createdBy`를 넘긴다. 첫 요청의 limit은 100이다. 응답에 `_links.next`가 있으면 다음 URL을 호출하고 끝까지 반복한다. limit=100은 전체 100개만 가져온다는 뜻이 아니다.

반환 필드는 `id`, `title`, `space_key`, `html_body`, `author`, `version`, `last_updated`, `url`이다. `author`는 여기서 `history.createdBy`의 표시 이름이므로 최종 수정자라고 설명하면 안 된다.

계층 정보는 `fetch_pages_with_category()`가 `expand=ancestors`, limit=200으로 별도 조회한다. 조상 제목 목록에 자기 제목을 붙이고 다음을 만든다.

```text
title_parts = [루트 제목, 부서 제목, 현재 문서 제목]
path = "루트 제목 / 부서 제목 / 현재 문서 제목"
level_0, level_1, level_2 = 각 제목
```

중요한 판단은 표시 문자열인 path를 `/`로 다시 나누지 않는다는 것이다. 제목 자체가 `솔루션/개발`이면 한 제목을 두 계층으로 오해할 수 있다. 원래 리스트를 보존해 레벨을 만든다. 없는 레벨은 빈 문자열을 넣어 pandas의 NaN이 JSON으로 흘러가는 문제를 줄인다.

`ingest()`가 쓰는 category는 `level_1`이다. 보편적인 최상위 개념이 아니라 현재 계층 구조에 대한 선택이다. `--category`도 이 레벨로 필터링한다. 특정 ID 목록의 본문은 `fetch_pages_by_ids()`에서 순차 조회한다.

## 4. HTML 파싱의 실제 처리 순서

`parse_confluence_html()`은 BeautifulSoup의 `html.parser`를 쓴다.

1. `ac:parameter`, `ac:schema-version`, `ac:macro-id`, `ri:url`, `script`, `style` 태그를 내용까지 제거한다(`decompose`).
2. `ri:attachment`에서 파일명을 얻어 확장자를 뺀 이름을 `attachments`에 저장한다.
3. `ac:link` 안의 `ri:page`에서 대상 문서 제목을 읽는다. 제목이 있으면 `[표시 문구](관련문서: 대상 제목)`으로 변환한다.
4. HTML 표를 행 레코드로 만들고 Markdown 표 문자열로 교체한다.
5. 남은 `ac:*`, `ri:*` 태그는 내용은 살리고 태그만 벗긴다(`unwrap`).
6. `get_text(separator="\n", strip=True)`로 텍스트를 추출한다.

입력 HTML이 비었으면 빈 본문·빈 attachments·metadata를 반환한다.

### 보존 범위를 정확히 이해하기

내부 링크 변환은 관련 문서 제목을 남기는 것이다. 해당 문서를 자동으로 따라가거나 지식 그래프의 관계를 만드는 구현은 아니다.

일반 외부 `<a href="...">안내</a>`는 마지막 텍스트 추출에서 `안내`가 남는다. href를 Markdown URL로 바꾸는 별도 구현이 없으므로 모든 링크 주소를 보존한다고 말하면 틀린다. 첨부파일 이름은 추출하지만 PDF 내용 추출·OCR은 없고, `ingest()`는 반환된 attachments를 청크 metadata에 합치지도 않는다.

## 5. 병합 표를 어떻게 펼치는가

`_table_to_records()`의 핵심 상태는 `grid`, `rowspan_map`, `col_idx`다.

- `grid`: 최종적으로 채워지는 2차원 행렬.
- `rowspan_map`: 이전 행에서 시작해 현재 행에도 이어지는 셀의 열 위치, 남은 행 수, 텍스트.
- `col_idx`: 현재 채우는 열 번호.

각 행에서 현재 열이 rowspan_map에 있으면 이전 셀의 값을 먼저 채운다. 아니라면 다음 td/th를 읽는다. colspan=2이면 같은 값을 두 열에 넣고, rowspan=2이면 다음 행에도 한 번 더 넣도록 기록한다.

개념 예시:

```text
원래 표:      부서(2행 병합) | 이름
                           | 이름
펼친 표:      부서           | 이름
              부서           | 이름
```

행 길이는 가장 긴 행에 맞춰 빈칸으로 채운다. 첫 th가 있는 행을 헤더로 고르며, th가 없으면 첫 행을 헤더로 쓴다. 빈 헤더는 `col_i`, 중복 헤더는 접미사를 붙인다. 그 뒤 행을 `{열 이름: 값}` 레코드로 만들고 완전히 빈 행은 제외한다.

`_table_records_to_markdown()`은 `|`를 escape하고 셀 안 개행을 공백으로 바꾼다. 단, 현재 헤더를 첫 데이터 레코드의 키에서만 가져온다. 첫 데이터 행에서 빈 셀이어서 키가 빠진 열은 이후 행에 값이 있어도 출력에서 빠질 수 있다. 또한 다단 헤더와 중첩 표를 일반적으로 완벽히 복원하는 구현은 아니다.

## 6. 청킹의 단위와 overlap

`RecursiveCharacterTextSplitter` 설정은 다음과 같다.

| 설정 | 값 | 의미 |
|---|---|---|
| chunk_size | 800 | 기본 문자 길이 기준 분할 크기 |
| chunk_overlap | 150 | 경계에서 문맥을 겹치게 하는 목표 길이 |
| separators | 문단 → 줄바꿈 → 공백 → 문자 | 가능한 자연스러운 경계부터 사용 |

800토큰이 아니다. 모든 청크가 정확히 800자이고 overlap이 항상 정확히 150자라는 뜻도 아니다. 표와 문장을 의미적으로 분석해 경계를 보장하는 전용 분할기도 아니다.

청크 ID는 `{doc_id}_chunk_{idx}`다. 같은 문서를 다시 분할하면 같은 순번의 ID를 사용하지만, 분할 경계가 바뀌면 같은 ID의 내용은 달라질 수 있다.

## 7. 임베딩 입력과 스키마

임베딩은 `제목 + 개행 + 청크 본문`을 입력으로 쓴다. 제목이 없는 문단도 어느 문서에 속하는지 의미 검색에 반영하려는 선택이다. ES의 text 필드에는 제목을 합치지 않은 청크 본문을 저장한다.

| 필드 | ES 타입 | 사용하는 곳 |
|---|---|---|
| title, text | text / nori_analyzer | 키워드 검색 |
| text_vector | dense_vector, dims=1536, cosine | 의미 검색 |
| chunk_id, doc_id | keyword | 중복 식별·삭제·문서 묶기 |
| space_key, author, url, category, path | keyword | 필터 또는 메타데이터 표시 |
| updated_at | date | 증분 비교·최신성 가산 |
| chunk_index, total_chunks | integer | 청크 순서 및 개수 |

1536차원은 한 입력을 1536개 수치로 표현한다는 뜻이다. 각 차원에 사람이 읽을 수 있는 이름을 직접 부여하지 않는다. 사용 모델은 LiteLLM의 `embedding-openai` 별칭으로 매핑된 `text-embedding-3-small`이다.

Nori는 `mixed` 토큰화 뒤 품사 필터, lowercase를 적용한다. 사전이 복합명사로 인식하는 경우 분해와 원형을 함께 남기려는 설정이지, 임의의 모든 한국어 단어가 원하는 대로 분해된다는 보장은 아니다.

## 8. 설명할 수 있는 판단과 남은 한계

파싱은 데이터 손실과 검색 노이즈 사이의 선택이다. 표·내부 링크·계층을 보존하는 이유를 실제 입력/출력 사례로 설명하면 좋다. 반면 모든 Confluence 매크로·첨부파일·외부 링크를 수집했다고 설명해서는 안 된다.

본문 수집과 카테고리 수집 함수는 중간 예외를 출력하고 지금까지 모은 부분 결과를 반환할 수 있다. 삭제용 전체 ID 수집은 예외를 올리는 별도 정책이다. 이 차이는 [증분·삭제 문서](03-ingestion.md)에 이어진다.
