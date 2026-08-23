package com.yiji.Chatbot.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yiji.Chatbot.dto.InternalChatDto;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Redis 기반 실시간 대화 세션(멀티턴 히스토리) 관리 서비스
 * - 최근 5턴의 대화 내용을 Redis에 캐싱하여 AI에게 맥락을 제공하고,
 * - 30분 동안 대화가 없으면 자동으로 만료(TTL) 처리합니다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class RedisSessionService {

    private final RedisTemplate<String, String> redisTemplate;
    private final ObjectMapper objectMapper;

    private static final String KEY_PREFIX = "chat:session:";
    private static final long SESSION_TTL_MINUTES = 30; // 30분 TTL
    private static final int DEFAULT_MAX_TURNS = 5;      // 최대 5턴(질문5 + 답변5 = 총 10개 메시지)

    /**
     * 특정 대화방(sessionId)의 최근 멀티턴 대화 히스토리 목록 조회
     */
    public List<InternalChatDto.MessageRole> getRecentHistory(String sessionId) {
        if (sessionId == null || sessionId.isBlank()) {
            return Collections.emptyList();
        }

        String key = KEY_PREFIX + sessionId;
        String json = redisTemplate.opsForValue().get(key);

        if (json == null || json.isBlank()) {
            return Collections.emptyList();
        }

        try {
            List<InternalChatDto.MessageRole> history = objectMapper.readValue(
                    json,
                    new TypeReference<List<InternalChatDto.MessageRole>>() {}
            );

            // 토큰 비용 최적화를 위해 최근 maxTurns(최근 10개 메시지)만 슬라이딩 윈도우로 유지
            int maxMessages = DEFAULT_MAX_TURNS * 2;
            if (history.size() > maxMessages) {
                return history.subList(history.size() - maxMessages, history.size());
            }
            return history;

        } catch (Exception e) {
            log.warn("[RedisSession] 세션 역직렬화 실패 (sessionId: {}): {}", sessionId, e.getMessage());
            return Collections.emptyList();
        }
    }

    /**
     * 새로운 대화 턴(사용자 질문 + AI 답변)을 Redis에 추가하고 30분 TTL을 연장
     */
    public void saveTurn(String sessionId, String userMessage, String assistantAnswer) {
        if (sessionId == null || sessionId.isBlank()) {
            return;
        }

        String key = KEY_PREFIX + sessionId;
        List<InternalChatDto.MessageRole> history = new ArrayList<>(getRecentHistory(sessionId));

        // 사용자 질문 및 AI 답변 추가
        history.add(InternalChatDto.MessageRole.builder().role("user").content(userMessage).build());
        history.add(InternalChatDto.MessageRole.builder().role("assistant").content(assistantAnswer).build());

        // 최근 10개 메시지만 유지
        int maxMessages = DEFAULT_MAX_TURNS * 2;
        if (history.size() > maxMessages) {
            history = new ArrayList<>(history.subList(history.size() - maxMessages, history.size()));
        }

        try {
            String json = objectMapper.writeValueAsString(history);
            // Redis에 저장하며 30분 TTL 자동 연장 (Sliding Expiration)
            redisTemplate.opsForValue().set(key, json, SESSION_TTL_MINUTES, TimeUnit.MINUTES);
            log.debug("[RedisSession] 세션 갱신 성공 (sessionId: {}, messageCount: {})", sessionId, history.size());
        } catch (Exception e) {
            log.error("[RedisSession] 세션 저장 실패 (sessionId: {}): {}", sessionId, e.getMessage());
        }
    }

    /**
     * DB에서 복구된 히스토리 전체를 Redis에 적재(Cache-Aside 워밍업)하고 30분 TTL 설정
     */
    public void saveHistory(String sessionId, List<InternalChatDto.MessageRole> history) {
        if (sessionId == null || sessionId.isBlank() || history == null || history.isEmpty()) {
            return;
        }

        String key = KEY_PREFIX + sessionId;
        int maxMessages = DEFAULT_MAX_TURNS * 2;
        List<InternalChatDto.MessageRole> trimmed = history.size() > maxMessages
                ? new ArrayList<>(history.subList(history.size() - maxMessages, history.size()))
                : history;

        try {
            String json = objectMapper.writeValueAsString(trimmed);
            redisTemplate.opsForValue().set(key, json, SESSION_TTL_MINUTES, TimeUnit.MINUTES);
            log.info("[RedisSession] DB 히스토리로 Redis 워밍업 완료 (sessionId: {}, messageCount: {})", sessionId, trimmed.size());
        } catch (Exception e) {
            log.error("[RedisSession] 세션 워밍업 저장 실패 (sessionId: {}): {}", sessionId, e.getMessage());
        }
    }

    /**
     * 대화방 삭제 시 Redis 세션 캐시 제거
     */
    public void clearSession(String sessionId) {
        if (sessionId != null && !sessionId.isBlank()) {
            redisTemplate.delete(KEY_PREFIX + sessionId);
        }
    }
}
