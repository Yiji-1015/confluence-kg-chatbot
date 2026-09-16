"""
Langfuse 연결을 한 곳에서 맡는다. 평가 스크립트 셋(`run_qa`, `push_dataset_36`,
`verify_langfuse`)이 전부 여기를 거쳐 클라이언트를 얻는다.

**왜 따로 뺐나.** 연결 확인 코드를 `run_qa`에 두면 데이터셋만 올리는 `push_dataset_36`이
`run_qa`를 import하게 되고, 그러면 Elasticsearch 클라이언트와 LLM 모듈까지 딸려 온다.
문항을 업로드하는 데 검색 엔진이 필요할 이유가 없다. 이 모듈은 `app.config`와 `langfuse`
두 개만 본다.

**키를 환경변수로 옮기는 일도 여기서 한다.** 세 스크립트가 같은 여섯 줄을 복사해 갖고
있었다. 한쪽만 고치면 그 스크립트만 다른 프로젝트에 붙는다.
"""
import os

from app.config import settings

# langfuse를 import하기 **전에** 환경변수를 채운다.
# 값은 .env가 공급하고(app.config), langfuse SDK는 환경변수만 본다.
for _name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
    _value = getattr(settings, _name, None)
    if _value:
        os.environ[_name] = _value

from langfuse import get_client  # noqa: E402  (위의 환경변수 설정이 먼저여야 한다)


# 실패 메시지마다 붙인다. 지역(region) 불일치가 제일 흔한 원인인데 증상은 그냥 401이라
# 키를 의심하며 헤매기 쉽다. 그래서 매번 같이 보여준다.
HOST_HINT = (
    "\n  확인할 것:\n"
    "  1) LANGFUSE_HOST의 지역이 맞는지. 키는 프로젝트가 있는 지역에서만 통합니다.\n"
    "     EU  https://cloud.langfuse.com\n"
    "     US  https://us.cloud.langfuse.com\n"
    "     JP  https://jp.cloud.langfuse.com\n"
    "     (이 프로젝트의 캡처 경로는 jp 입니다 — docs/presentation/CAPTURE_GUIDE.md)\n"
    "  2) 키가 그 프로젝트의 것인지 (Langfuse UI -> Settings -> API Keys).\n"
    "  3) 컨테이너에서 host로 나가지는지:\n"
    "     docker exec rag-ai-server python -m evaluation.verify_langfuse"
)


def brief(exc: Exception, limit: int = 400) -> str:
    """
    예외를 한눈에 읽을 길이로 자른다. 응답 검증 오류는 아이템마다 한 덩어리씩 붙어
    36문항이면 수십 줄이 쏟아진다. 그러면 정작 그 아래 해결 방법이 화면 밖으로 밀린다.
    """
    text = f"{type(exc).__name__}: {exc}"
    if len(text) <= limit:
        return text
    return text[:limit] + f"… (이하 생략, 총 {len(text)}자)"


def settings_problem():
    """
    호출 전에 잡을 수 있는 설정 오류만 돌려준다 (없으면 None).
    값을 추측해서 고치지는 않는다. 여기서 잡아야 401의 원인을 헤매지 않는다.
    """
    missing = [name for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
               if not (getattr(settings, name, None) or "").strip()]
    if missing:
        return (f"{', '.join(missing)}가 비어 있습니다.\n"
                "  저장소 루트의 .env에 키를 넣으세요 (.env.example 참고).\n"
                "  Langfuse UI: Settings -> API Keys 에서 발급합니다.")

    public_key = settings.LANGFUSE_PUBLIC_KEY.strip()
    secret_key = settings.LANGFUSE_SECRET_KEY.strip()
    if not public_key.startswith("pk-lf-") or not secret_key.startswith("sk-lf-"):
        return ("키 형식이 Langfuse의 것이 아닙니다 "
                f"(public={public_key[:8]}… / secret={secret_key[:8]}…).\n"
                "  public은 pk-lf-, secret은 sk-lf-로 시작해야 합니다. "
                "두 값이 뒤바뀌지 않았는지 보세요.")
    return None


def check_connection(client):
    """
    **실제로 연결되는지** 확인한다. 실패하면 SystemExit으로 멈춘다.

    데이터셋을 읽거나 쓰기 전에 부른다. 여기서 막지 않으면 첫 실패가 데이터셋 호출에서
    나는데, 그 예외만 보고는 키가 틀린 것인지 데이터셋이 없는 것인지 지역이 다른 것인지
    구분되지 않는다.
    """
    host = (settings.LANGFUSE_HOST or "").strip()

    problem = settings_problem()
    if problem:
        raise SystemExit(f"\nLangfuse 설정이 올바르지 않아 실행을 중단합니다.\n  {problem}")

    try:
        connected = client.auth_check()
    except Exception as exc:
        raise SystemExit(
            f"\nLangfuse에 연결하지 못해 실행을 중단합니다.\n"
            f"  host : {host}\n"
            f"  원인 : {brief(exc)}\n"
            f"{HOST_HINT}"
        )

    if not connected:
        raise SystemExit(
            f"\nLangfuse 인증에 실패해 실행을 중단합니다 (연결은 됐고 키가 거부됐습니다).\n"
            f"  host : {host}\n"
            f"{HOST_HINT}"
        )
    return host


def connect():
    """연결이 확인된 클라이언트를 돌려준다. 평가 스크립트의 첫 줄이 이것이다."""
    client = get_client()
    check_connection(client)
    return client


def load_dataset(client, name: str):
    """데이터셋을 읽는다. 없으면 업로드 명령을 안내하고 멈춘다."""
    try:
        return client.get_dataset(name)
    except Exception as exc:
        raise SystemExit(
            f"\n데이터셋 '{name}'을 읽지 못해 실행을 중단합니다.\n"
            f"  원인: {brief(exc)}\n"
            "  아직 올리지 않았다면 먼저 업로드하세요:\n"
            "    docker exec rag-ai-server python -m evaluation.push_dataset_36"
        )
