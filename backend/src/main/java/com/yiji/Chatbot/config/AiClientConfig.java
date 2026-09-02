package com.yiji.Chatbot.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.time.Duration;

/**
 * Python AI Engine (FastAPI) 통신용 RestClient 설정 클래스
 */
@Configuration
public class AiClientConfig {

    @Value("${ai-engine.base-url:http://localhost:8000}")
    private String aiEngineBaseUrl;

    @Value("${ai-engine.timeout-seconds:60}")
    private int timeoutSeconds;

    /**
     * 자동 구성된 RestClient.Builder를 주입받는다.
     *
     * RestClient.builder()를 직접 호출하면 Micrometer 관측 설정이 붙지 않아 AI 엔진 호출
     * 지연이 지표로 남지 않는다. 그러면 요청이 느릴 때 backend가 느린 것인지 ai-server가
     * 느린 것인지 구분할 수 없다. 주입받은 빌더는 http_client_requests_seconds를 자동으로
     * 기록하고 트레이스 컨텍스트도 전파한다.
     */
    @Bean
    public RestClient aiEngineRestClient(RestClient.Builder builder) {
        // LLM 답변 생성을 기다리기 위한 타임아웃(60초) 설정
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(10));
        factory.setReadTimeout(Duration.ofSeconds(timeoutSeconds));

        return builder
                .baseUrl(aiEngineBaseUrl)
                .requestFactory(factory)
                .build();
    }
}
