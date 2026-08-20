package com.yiji.Chatbot.service;

import com.yiji.Chatbot.dto.*;
import com.yiji.Chatbot.entity.ChatMessage;
import com.yiji.Chatbot.entity.ChatSession;
import com.yiji.Chatbot.mapper.ChatMapper;
import com.yiji.Chatbot.repository.ChatMessageRepository;
import com.yiji.Chatbot.repository.ChatSessionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
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

        // 1. 대화방(Session) 식별 또는 신규 생성
        ChatSession session;
        if (sessionId == null || sessionId.isBlank()) {
            sessionId = UUID.randomUUID().toString();
            String initialTitle = generateTitleFromQuery(query);
            session = ChatSession.builder()
                    .id(sessionId)
                    .title(initialTitle)
                    .build();
            chatSessionRepository.save(session);
            log.info("[ChatService] 새 대화방 생성 (sessionId: {}, title: '{}')", sessionId, initialTitle);
        } else {
            String finalSessionId = sessionId;
            session = chatSessionRepository.findById(sessionId)
                    .orElseGet(() -> {
                        ChatSession newSession = ChatSession.builder()
                                .id(finalSessionId)
                                .title(generateTitleFromQuery(query))
                                .build();
                        return chatSessionRepository.save(newSession);
                    });
            session.updateTimestamp();
        }

        // 2. Redis에서 해당 대화방의 최근 멀티턴 대화 기록 조회
        List<InternalChatDto.MessageRole> history = redisSessionService.getRecentHistory(sessionId);

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
     * 전체 대화방 목록 최신순 조회 (좌측 사이드바용)
     */
    @Transactional(readOnly = true)
    public List<ChatSessionDto> getSessions() {
        return chatSessionRepository.findAllByOrderByUpdatedAtDesc().stream()
                .map(chatMapper::toSessionDto)
                .collect(Collectors.toList());
    }

    /**
     * 특정 대화방의 과거 전체 메시지 내역 조회
     */
    @Transactional(readOnly = true)
    public List<ChatMessageDto> getMessages(String sessionId) {
        return chatMessageRepository.findAllBySessionIdOrderByCreatedAtAsc(sessionId).stream()
                .map(chatMapper::toMessageDto)
                .collect(Collectors.toList());
    }

    /**
     * 대화방 삭제 (Redis 세션 및 RDB 데이터 삭제)
     */
    @Transactional
    public void deleteSession(String sessionId) {
        redisSessionService.clearSession(sessionId);
        chatSessionRepository.deleteById(sessionId);
        log.info("[ChatService] 대화방 삭제 완료 (sessionId: {})", sessionId);
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
