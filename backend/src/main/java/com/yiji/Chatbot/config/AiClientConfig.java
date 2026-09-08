package com.yiji.Chatbot.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

/**
 * Python AI Engine (FastAPI) 통신용 RestClient 설정 클래스
 */
@Configuration
@EnableConfigurationProperties(AiEngineProperties.class)
public class AiClientConfig {

    /**
     * 자동 구성된 RestClient.Builder를 주입받는다.
     *
     * RestClient.builder()를 직접 호출하면 Micrometer 관측 설정이 붙지 않아 AI 엔진 호출
     * 지연이 지표로 남지 않는다. 그러면 요청이 느릴 때 backend가 느린 것인지 ai-server가
     * 느린 것인지 구분할 수 없다. 주입받은 빌더는 http_client_requests_seconds를 자동으로
     * 기록하고 트레이스 컨텍스트도 전파한다.
     *
     * requestFactory()도 직접 지정하지 않는다. 지정하면 Boot가 골라둔 클라이언트 구현이
     * 통째로 대체되고, spring.http.clients.* 로 준 타임아웃도 무시된다.
     */
    @Bean
    public RestClient aiEngineRestClient(RestClient.Builder builder, AiEngineProperties properties) {
        return builder
                .baseUrl(properties.baseUrl())
                .build();
    }
}
