package com.yiji.Chatbot.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Python AI Engine 연동 설정 (application.yaml의 ai-engine.*)
 *
 * 타임아웃은 여기 없다. Boot 4의 자리는 spring.http.clients.* 이고,
 * 그 값이 자동 구성된 RestClient.Builder에 적용된다.
 */
@ConfigurationProperties(prefix = "ai-engine")
public record AiEngineProperties(String baseUrl) {
}
