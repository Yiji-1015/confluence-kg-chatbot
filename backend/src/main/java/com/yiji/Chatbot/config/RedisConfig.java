package com.yiji.Chatbot.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.serializer.StringRedisSerializer;

/**
 * Redis 연결 설정 클래스
 *
 * JSON 직렬화는 Boot가 자동 구성한 Jackson 3 JsonMapper를 그대로 쓴다.
 * 예전에는 여기서 Jackson 2 ObjectMapper 빈을 만들어 JavaTimeModule을 등록했는데,
 * Boot 4의 JSON 기본은 Jackson 3라 그 빈은 웹 계층에 관여하지 못했다.
 * Jackson 3는 java.time을 기본 내장하고 타임스탬프 출력도 기본 비활성이라 설정 자체가 필요 없다.
 */
@Configuration
public class RedisConfig {

    @Bean
    public RedisTemplate<String, String> redisTemplate(RedisConnectionFactory connectionFactory) {
        RedisTemplate<String, String> template = new RedisTemplate<>();
        template.setConnectionFactory(connectionFactory);

        StringRedisSerializer stringSerializer = new StringRedisSerializer();
        template.setKeySerializer(stringSerializer);
        template.setValueSerializer(stringSerializer);
        template.setHashKeySerializer(stringSerializer);
        template.setHashValueSerializer(stringSerializer);

        template.afterPropertiesSet();
        return template;
    }
}
