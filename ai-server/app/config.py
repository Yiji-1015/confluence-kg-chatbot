from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Optional

# 저장소 루트 (ai-server/app/config.py 기준 두 단계 위) — 실행 위치(cwd)와 무관하게
# repo 루트의 .env / elasticsearch/certs 를 항상 같은 경로로 찾기 위함
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """
    프로젝트 중앙 환경 변수 및 설정 관리 클래스 (.env 연동)
    """
    # 1. 앱기본 설정
    APP_NAME: str = "Confluence KG Chatbot - AI Engine"
    DEBUG: bool = True

    # 2. LiteLLM 게이트웨이 설정 (LLM 및 임베딩 단일 관문)
    LITELLM_BASE_URL: str = "http://localhost:4000"
    DEFAULT_LLM_MODEL: str = "deepseek-chat"
    DEFAULT_EMBEDDING_MODEL: str = "embedding-openai"  # OpenAI text-embedding-3-small, 1536차원 (ELASTICSEARCH.md 기준)

    # 3. Elasticsearch 하이브리드 검색엔진 설정 (ELASTICSEARCH.md 기준: TLS + 인증 필수)
    ELASTICSEARCH_URL: str = "https://localhost:9200"
    ELASTICSEARCH_INDEX: str = "confluence-openai-v1"
    ELASTICSEARCH_USER: str = "elastic"
    ELASTICSEARCH_PASSWORD: Optional[str] = None
    ELASTICSEARCH_CA_CERT: str = str(REPO_ROOT / "elasticsearch" / "certs" / "ca" / "ca.crt")

    # 4. Neo4j Knowledge Graph 지식 그래프 DB 설정
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "kg-password"

    # 5. Redis 캐시 및 대화 세션 저장소 설정
    REDIS_URL: str = "redis://localhost:6379"

    # 6. Confluence API 연동 설정
    CONFLUENCE_BASE_URL: str = "https://lloydk.atlassian.net/wiki"
    CONFLUENCE_SPACE_KEY: str = "LLOYDK"
    CONFLUENCE_EMAIL: Optional[str] = None
    CONFLUENCE_API_TOKEN: Optional[str] = None

    # .env 파일 자동 인식이 가능하도록 pydantic-settings 설정 지정
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


# 전역에서 선언해 쓸 싱글톤 인스턴스
settings = Settings()
