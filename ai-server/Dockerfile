FROM python:3.12-slim

WORKDIR /app

# 시스템 라이브러리 및 헬스체크용 curl 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY app ./app

# 기본 포트 노출
EXPOSE 8000

# 헬스체크 정의
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=10s \
  CMD curl -f http://localhost:8000/internal/health || exit 1

# FastAPI 비동기 서버 실행
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
