package com.yiji.Chatbot.service;

import com.yiji.Chatbot.dto.ChatMessageDto;
import com.yiji.Chatbot.dto.ChatRequestDto;
import com.yiji.Chatbot.dto.ChatResponseDto;
import com.yiji.Chatbot.dto.ChatSessionDto;
import com.yiji.Chatbot.dto.InternalChatDto;
import com.yiji.Chatbot.dto.SourceDocumentDto;
import com.yiji.Chatbot.mapper.ChatMapper;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/**
 * 채팅 흐름 오케스트레이션 서비스
 * - 세션 확인/발급 -> Redis 멀티턴 맥락 조회 -> Python AI 호출 -> 영속화
 *
 * DB 접근은 전부 ChatPersistenceService에 있다. 이 클래스는 순서만 정한다.
 */
@Slf4j
@Service
public class ChatService {

    private final ChatPersistenceService chatPersistenceService;
    private final RedisSessionService redisSessionService;
    private final AiEngineClient aiEngineClient;
    private final ChatMapper chatMapper;

    /**
     * 캐시 카운터는 시작할 때 미리 등록한다.
     *
     * Micrometer는 처음 증가하는 순간에 시계열을 만든다. 등록해두지 않으면 재시작 직후
     * 대시보드의 적중률 패널이 "No data"로 뜨고, 특히 miss 쪽은 이어쓰는 대화가 한 번
     * 나오기 전까지 아예 존재하지 않는다. "0"과 "값이 없음"은 다르게 읽힌다.
     */
    private final Counter historyCacheHit;
    private final Counter historyCacheMiss;

    public ChatService(MeterRegistry meterRegistry,
                       ChatPersistenceService chatPersistenceService,
                       RedisSessionService redisSessionService,
                       AiEngineClient aiEngineClient,
                       ChatMapper chatMapper) {
        this.chatPersistenceService = chatPersistenceService;
        this.redisSessionService = redisSessionService;
        this.aiEngineClient = aiEngineClient;
        this.chatMapper = chatMapper;
        this.historyCacheHit = historyCacheCounter(meterRegistry, "hit");
        this.historyCacheMiss = historyCacheCounter(meterRegistry, "miss");
    }

    private static Counter historyCacheCounter(MeterRegistry registry, String result) {
        return Counter.builder("chat_history_cache")
                .description("대화 이력 캐시 조회 결과")
                .tag("result", result)
                .register(registry);
    }

    /**
     * 메인 채팅 질의응답 (POST /api/chat)
     *
     * 이 메서드에 @Transactional이 없는 것은 의도한 것이다.
     * AI 호출은 최대 60초가 걸리는데, 트랜잭션 안에서 부르면 그동안 DB 커넥션 하나가
     * 아무 일도 하지 않으면서 묶여 있는다. HikariCP 기본 풀이 10개라 동시 사용자 11명이면
     * 나머지는 커넥션을 기다리다 30초 뒤 실패한다. AI 서버가 느려지는 순간 그 여파가
     * 채팅과 무관한 API까지 번진다.
     *
     * 그래서 DB를 만지는 구간만 ChatPersistenceService의 짧은 트랜잭션으로 나눠 두고,
     * AI 호출은 그 밖에 둔다.
     */
    public ChatResponseDto processChat(ChatRequestDto requestDto) {
        String query = requestDto.query().trim();
        String userId = requestDto.userId();

        // 1. 이어쓸 수 있는 대화방인지 확인한다. 아니면 서버가 새 ID를 발급한다.
        //    (예전에는 클라이언트가 준 ID로 없는 대화방을 만들어줬다. 서버가 ID 발급을 통제하지 못했다.)
        boolean continuing = chatPersistenceService.canContinue(requestDto.sessionId(), userId);
        String sessionId = continuing ? requestDto.sessionId() : issueSessionId(requestDto.sessionId());

        // 2. 이어쓰는 대화만 히스토리를 읽는다. 새 대화방은 조회할 것이 없다.
        List<InternalChatDto.MessageRole> history = continuing ? loadHistory(sessionId) : List.of();

        // 3. Python AI Engine 호출 (트랜잭션 밖). 실패하면 AiEngineException이 올라가고
        //    아무것도 저장되지 않는다.
        InternalChatDto.Response aiResponse = aiEngineClient.requestChat(sessionId, query, history);

        String answer = aiResponse.answer();
        List<SourceDocumentDto> sources = chatMapper.toSourceDtoList(aiResponse.sources());

        // 4. 질문과 답변을 한 트랜잭션에 저장한 뒤 Redis를 갱신한다.
        //    순서가 중요하다. Redis는 JPA 트랜잭션에 참여하지 않으므로 Redis를 먼저 쓰면
        //    DB가 롤백돼도 Redis에는 남아 다음 턴의 LLM 컨텍스트가 오염된다.
        chatPersistenceService.saveTurn(sessionId, userId, query, answer, chatMapper.sourcesToJson(sources));
        redisSessionService.saveTurn(sessionId, query, answer);

        return ChatResponseDto.builder()
                .sessionId(sessionId)
                .answer(answer)
                .sources(sources)
                .createdAt(LocalDateTime.now())
                .build();
    }

    /**
     * 전체 대화방 목록 최신순 조회
     */
    public List<ChatSessionDto> getSessions(String userId) {
        return chatPersistenceService.getSessions(userId);
    }

    /**
     * 특정 대화방의 과거 전체 메시지 내역 조회
     */
    public List<ChatMessageDto> getMessages(String sessionId, String userId) {
        return chatPersistenceService.getMessages(sessionId, userId);
    }

    /**
     * 대화방 삭제
     *
     * DB 삭제가 커밋된 뒤에 Redis를 지운다. 반대로 하면 DB 삭제가 실패했을 때
     * 대화방은 남았는데 맥락만 사라진 상태가 된다. 이 순서라면 Redis 삭제가 실패해도
     * 남는 것은 갈 곳 없는 캐시뿐이고 TTL 30분이면 사라진다.
     */
    public void deleteSession(String sessionId, String userId) {
        chatPersistenceService.deleteSession(sessionId, userId);
        redisSessionService.clearSession(sessionId);
    }

    /**
     * 대화방 ID는 서버가 발급한다.
     */
    private String issueSessionId(String requested) {
        String issued = UUID.randomUUID().toString();
        if (requested != null && !requested.isBlank()) {
            log.info("[ChatService] 알 수 없는 sessionId를 받아 새 대화방을 발급한다 (요청: {}, 발급: {})",
                    requested, issued);
        }
        return issued;
    }

    /**
     * 최근 대화 맥락을 읽는다. Redis가 비었으면 DB에서 복원하고 캐시를 채운다(Cache-Aside).
     */
    private List<InternalChatDto.MessageRole> loadHistory(String sessionId) {
        List<InternalChatDto.MessageRole> cached = redisSessionService.getRecentHistory(sessionId);
        if (!cached.isEmpty()) {
            historyCacheHit.increment();
            return cached;
        }

        // 캐시 미스는 지연으로만 보면 원인을 알 수 없다. 미스 비율이 높다는 것은
        // Redis TTL(30분)이 실제 대화 간격보다 짧아 매 턴 DB를 때리고 있다는 뜻이다.
        historyCacheMiss.increment();

        List<InternalChatDto.MessageRole> recovered = chatPersistenceService.loadRecentHistory(sessionId);
        if (!recovered.isEmpty()) {
            // 트랜잭션 밖에서 채운다. 실패해도 다음 턴에 다시 시도할 뿐이다.
            redisSessionService.saveHistory(sessionId, recovered);
            log.info("[ChatService] DB 대화 기록에서 Redis 히스토리 복원 (sessionId: {}, count: {})",
                    sessionId, recovered.size());
        }
        return recovered;
    }
}
