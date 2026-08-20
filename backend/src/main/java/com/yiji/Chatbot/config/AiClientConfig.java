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

    @Bean
    public RestClient aiEngineRestClient() {
        // LLM 답변 생성을 기다리기 위한 타임아웃(60초) 설정
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(10));
        factory.setReadTimeout(Duration.ofSeconds(timeoutSeconds));

        return RestClient.builder()
                .baseUrl(aiEngineBaseUrl)
                .requestFactory(factory)
                .build();
    }
}
