from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Optional, Any

# 저장소 루트 (ai-server/app/config.py 기준 두 단계 위) — 실행 위치(cwd)와 무관하게
# repo 루트의 .env / elasticsearch/certs 를 항상 같은 경로로 찾기 위함
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """
    프로젝트 중앙 환경 변수 및 설정 관리 클래스 (.env 연동)
    """
    # 1. 앱기본 설정
    APP_NAME: str = "Confluence RAG Chatbot - AI Engine"
    DEBUG: bool = True

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            val = v.strip().lower()
            if val in ("1", "true", "t", "yes", "y", "debug", "dev", "development"):
                return True
            if val in ("0", "false", "f", "no", "n", "release", "prod", "production"):
                return False
        return bool(v)

    # 2. LiteLLM 게이트웨이 설정 (LLM 및 임베딩 단일 관문)
    LITELLM_BASE_URL: str = "http://127.0.0.1:4000"
    DEFAULT_LLM_MODEL: str = "deepseek-chat"
    DEFAULT_EMBEDDING_MODEL: str = "embedding-openai"  # OpenAI text-embedding-3-small, 1536차원 (ELASTICSEARCH.md 기준)

    # 3. Elasticsearch 하이브리드 검색엔진 설정 (ELASTICSEARCH.md 기준: TLS + 인증 필수)
    ELASTICSEARCH_URL: str = "https://127.0.0.1:9200"
    # 코드는 항상 별칭만 부른다. 임베딩 모델이나 차원이 바뀌어 전체 재색인이 필요할 때,
    # 새 버전 인덱스를 채워둔 뒤 별칭만 옮기면 검색을 멈추지 않고 전환할 수 있고
    # 문제가 생기면 별칭을 되돌리는 것만으로 즉시 롤백된다.
    ELASTICSEARCH_INDEX: str = "confluence-current"
    # 별칭이 가리킬 실제 인덱스. 최초 생성 때만 쓰인다.
    ELASTICSEARCH_CONCRETE_INDEX: str = "confluence-openai-v3"
    ELASTICSEARCH_USER: str = "elastic"
    ELASTICSEARCH_PASSWORD: Optional[str] = None
    ELASTICSEARCH_CA_CERT: str = str(REPO_ROOT / "elasticsearch" / "certs" / "ca" / "ca.crt")

    # 검색 튜닝 파라미터 — 코드 수정/재빌드 없이 .env로 바꿔가며 실험한다.
    # (재색인은 필요 없다. 색인 데이터는 그대로 두고 검색 단계만 달라지는 값들)
    # 멀티턴에서 검색어 앞에 붙일 직전 사용자 발화 수. 0이면 끈다.
    # 지시어("그건 며칠까지야?")에는 주제어가 없어 검색이 빗나가므로 주제어를 복원한다.
    SEARCH_HISTORY_TURNS: int = 1
    RETRIEVAL_TOP_K: int = 5              # 최종 컨텍스트에 넣을 "서로 다른 문서" 수
    RETRIEVAL_CANDIDATE_SIZE: int = 50    # BM25/kNN이 각각 가져올 재랭킹 후보 청크 수
    DOC_CONTEXT_MAX_CHARS: int = 3000     # 문서 하나를 컨텍스트에 넣을 때의 최대 글자 수

    # RRF(Reciprocal Rank Fusion) 상수. 청크가 각 리스트에서 받은 순위로 1/(K+순위)를
    # 계산해 더한다. 점수가 아니라 순위만 쓰므로 BM25(수십 단위)와 kNN(0~1)의 스케일
    # 차이를 보정할 필요가 없다.
    # K가 작을수록 상위권이 유리하고(K=10이면 1위가 50위의 5.5배), 클수록 평평해져
    # 양쪽 리스트에 고루 든 문서가 유리해진다. 60은 RRF 원 논문의 기본값이며
    # 대부분의 검색 엔진이 쓰는 값이다. 실측에서 10과 60의 결과가 같아 표준값을 따랐다.
    RRF_K: int = 60

    # 최신 문서 가산점. 후보를 updated_at 기준 5분위로 묶어 최신 그룹부터 차등 가산한다.
    # 값은 "이론상 최대 결합 점수에 대한 비율"이다 (0.04 = 최대치의 4%).
    # 절대값이 아니라 비율로 둔 이유: 결합 방식을 바꾸면 점수 범위가 통째로 달라진다.
    # min-max 정규화는 0~1이었지만 RRF(K=60)는 0~2/61(=0.0328)이다. 절대값 0.04를
    # 그대로 두면 가산점이 점수를 압도해 사실상 최신순 정렬이 된다.
    # 실측(2026-09-14): 최대치의 4%는 무해하거나 소폭 이득, 10%부터 MRR이 떨어지고
    # 25%에서는 0.908 -> 0.668로 무너졌다.
    # 0.0이면 완전히 끈다.
    RECENCY_BOOST_MAX: float = 0.04

    # 답변 생성 temperature. 미지정 시 OpenAI/DeepSeek 기본값은 0(결정적)이 아니라 1.0이라,
    # 같은 질문·같은 컨텍스트에도 답이 매번 달라져 평가 점수가 흔들린다.
    # RAG는 컨텍스트 충실도가 목적이므로 0으로 고정한다.
    LLM_TEMPERATURE: float = 0.0

    # 평가 채점(LLM-as-a-Judge) 전용 모델. 답변 생성 모델과 반드시 다른 계열로 둔다.
    # 같은 모델이 자기 출력을 채점하면 self-preference bias로 점수가 후해진다.
    JUDGE_MODEL: str = "solar-judge"

    # 4. Confluence API 연동 설정
    # 기본값에 실제 회사 주소/스페이스를 두지 않는다. 저장소를 클론한 사람이 .env 없이
    # 남의 Confluence를 찌르게 되기 때문. 실제 값은 .env가 공급한다.
    CONFLUENCE_BASE_URL: str = "https://your-company.atlassian.net/wiki"
    CONFLUENCE_SPACE_KEY: str = "SPACE_KEY"
    CONFLUENCE_EMAIL: Optional[str] = None
    CONFLUENCE_API_TOKEN: Optional[str] = None

    # 5. Langfuse LLM Observability & Tracing 설정
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_SECRET_KEY: Optional[str] = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # .env 파일 자동 인식이 가능하도록 pydantic-settings 설정 지정
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


# 전역에서 선언해 쓸 싱글톤 인스턴스
settings = Settings()
