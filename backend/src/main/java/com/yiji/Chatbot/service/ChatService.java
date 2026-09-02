package com.yiji.Chatbot.service;

import com.yiji.Chatbot.dto.*;
import com.yiji.Chatbot.entity.ChatMessage;
import com.yiji.Chatbot.entity.ChatSession;
import com.yiji.Chatbot.mapper.ChatMapper;
import com.yiji.Chatbot.repository.ChatMessageRepository;
import com.yiji.Chatbot.repository.ChatSessionRepository;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 전체 채팅 비즈니스 로직 오케스트레이션 서비스
 * - 세션 발급/관리 -> Redis 멀티턴 맥락 조회 -> Python AI 호출 -> Redis & JPA 영속화
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ChatService {

    private final MeterRegistry meterRegistry;

    private final ChatSessionRepository chatSessionRepository;
    private final ChatMessageRepository chatMessageRepository;
    private final RedisSessionService redisSessionService;
    private final AiEngineClient aiEngineClient;
    private final ChatMapper chatMapper;

    /**
     * 메인 채팅 질의응답 처리 메서드 (POST /api/chat)
     */
    @Transactional
    public ChatResponseDto processChat(ChatRequestDto requestDto) {
        String query = requestDto.getQuery().trim();
        String sessionId = requestDto.getSessionId();
        String userId = requestDto.getUserId();

        // 1. 대화방(Session) 식별 또는 신규 생성
        ChatSession session;
        if (sessionId == null || sessionId.isBlank()) {
            sessionId = UUID.randomUUID().toString();
            String initialTitle = generateTitleFromQuery(query);
            session = ChatSession.builder()
                    .id(sessionId)
                    .userId(userId)
                    .title(initialTitle)
                    .build();
            chatSessionRepository.save(session);
            log.info("[ChatService] 새 대화방 생성 (sessionId: {}, userId: {}, title: '{}')", sessionId, userId, initialTitle);
        } else {
            String finalSessionId = sessionId;
            session = chatSessionRepository.findById(sessionId)
                    .map(found -> requireOwner(found, userId))
                    .orElseGet(() -> {
                        ChatSession newSession = ChatSession.builder()
                                .id(finalSessionId)
                                .userId(userId)
                                .title(generateTitleFromQuery(query))
                                .build();
                        return chatSessionRepository.save(newSession);
                    });
            session.updateTimestamp();
        }

        // 2. Redis에서 해당 대화방의 최근 멀티턴 대화 기록 조회 (만료 시 DB에서 복구하는 Cache-Aside 적용)
        List<InternalChatDto.MessageRole> history = redisSessionService.getRecentHistory(sessionId);
        if (history.isEmpty()) {
            // 캐시 미스는 지연으로만 보면 원인을 알 수 없다. 미스 비율이 높다는 것은
            // Redis TTL(30분)이 실제 대화 간격보다 짧아 매 턴 DB를 때리고 있다는 뜻이다.
            meterRegistry.counter("chat_history_cache", "result", "miss").increment();
            history = recoverHistoryFromDb(sessionId);
        } else {
            meterRegistry.counter("chat_history_cache", "result", "hit").increment();
        }

        // 3. Python AI Engine 호출 (하이브리드 검색 + LiteLLM 답변 생성)
        InternalChatDto.Response aiResponse = aiEngineClient.requestChat(sessionId, query, history);

        String answer = aiResponse.getAnswer();
        List<SourceDocumentDto> sources = chatMapper.toSourceDtoList(aiResponse.getSources());
        String sourcesJson = chatMapper.sourcesToJson(sources);

        // 4. Redis에 새로운 대화 턴(사용자 질문 + AI 답변) 저장 & 30분 TTL 갱신
        redisSessionService.saveTurn(sessionId, query, answer);

        // 5. JPA로 PostgreSQL에 영구 대화 기록 저장 (질문 + 답변)
        ChatMessage userMsg = ChatMessage.builder()
                .session(session)
                .role("USER")
                .content(query)
                .build();
        chatMessageRepository.save(userMsg);

        ChatMessage assistantMsg = ChatMessage.builder()
                .session(session)
                .role("ASSISTANT")
                .content(answer)
                .sourcesJson(sourcesJson)
                .build();
        chatMessageRepository.save(assistantMsg);

        // 6. 최종 응답 DTO 반환
        return ChatResponseDto.builder()
                .sessionId(sessionId)
                .answer(answer)
                .sources(sources)
                .createdAt(LocalDateTime.now())
                .build();
    }

    /**
     * 전체 대화방 목록 최신순 조회 (사용자별 필터링 지원)
     */
    @Transactional(readOnly = true)
    public List<ChatSessionDto> getSessions(String userId) {
        return chatSessionRepository.findAllByUserIdOrderByUpdatedAtDesc(userId).stream()
                .map(chatMapper::toSessionDto)
                .collect(Collectors.toList());
    }

    /**
     * 특정 대화방의 과거 전체 메시지 내역 조회 (본인 대화방만)
     */
    @Transactional(readOnly = true)
    public List<ChatMessageDto> getMessages(String sessionId, String userId) {
        requireOwnedSession(sessionId, userId);
        return chatMessageRepository.findAllBySessionIdOrderByCreatedAtAsc(sessionId).stream()
                .map(chatMapper::toMessageDto)
                .collect(Collectors.toList());
    }

    /**
     * 대화방 삭제 (본인 대화방만, Redis 세션 및 RDB 데이터 안전 삭제)
     */
    @Transactional
    public void deleteSession(String sessionId, String userId) {
        requireOwnedSession(sessionId, userId);
        redisSessionService.clearSession(sessionId);
        chatSessionRepository.deleteById(sessionId);
        log.info("[ChatService] 대화방 삭제 완료 (sessionId: {})", sessionId);
    }

    /**
     * 대화방을 조회하면서 요청한 사용자의 것인지 확인한다.
     * userId는 브라우저 localStorage의 익명 ID라 인증은 아니지만,
     * sessionId만 알면 남의 대화를 읽고 지울 수 있던 구멍은 막는다.
     */
    private ChatSession requireOwnedSession(String sessionId, String userId) {
        return chatSessionRepository.findById(sessionId)
                .map(session -> requireOwner(session, userId))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "대화방을 찾을 수 없습니다."));
    }

    private ChatSession requireOwner(ChatSession session, String userId) {
        if (!Objects.equals(session.getUserId(), userId)) {
            // 대화방 존재 여부까지 알려주지 않도록 403 대신 404로 응답한다.
            log.warn("[ChatService] 소유자가 아닌 대화방 접근 차단 (sessionId: {}, userId: {})", session.getId(), userId);
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "대화방을 찾을 수 없습니다.");
        }
        return session;
    }

    /**
     * Redis 캐시 만료 시 PostgreSQL 영구 대화 기록에서 최근 대화 히스토리 복원 (최근 5턴 = 10개)
     */
    private List<InternalChatDto.MessageRole> recoverHistoryFromDb(String sessionId) {
        List<ChatMessage> dbMessages = chatMessageRepository.findAllBySessionIdOrderByCreatedAtAsc(sessionId);
        if (dbMessages.isEmpty()) {
            return List.of();
        }

        int maxMessages = 10;
        int startIndex = Math.max(0, dbMessages.size() - maxMessages);
        List<ChatMessage> recentMessages = dbMessages.subList(startIndex, dbMessages.size());

        List<InternalChatDto.MessageRole> history = recentMessages.stream()
                .map(msg -> InternalChatDto.MessageRole.builder()
                        .role(msg.getRole().toLowerCase()) // "user" or "assistant"
                        .content(msg.getContent())
                        .build())
                .collect(Collectors.toList());

        if (!history.isEmpty()) {
            redisSessionService.saveHistory(sessionId, history);
            log.info("[ChatService] DB 대화 기록에서 Redis 히스토리 복원 완료 (sessionId: {}, count: {})", sessionId, history.size());
        }

        return history;
    }

    /**
     * 첫 질문 내용으로 대화방 요약 제목 생성 (최대 30자)
     */
    private String generateTitleFromQuery(String query) {
        if (query == null || query.isBlank()) {
            return "새로운 대화";
        }
        return query.length() > 30 ? query.substring(0, 30) + "..." : query;
    }
}
