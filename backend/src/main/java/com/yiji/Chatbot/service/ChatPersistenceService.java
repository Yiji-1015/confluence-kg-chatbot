package com.yiji.Chatbot.service;

import com.yiji.Chatbot.dto.ChatMessageDto;
import com.yiji.Chatbot.dto.ChatSessionDto;
import com.yiji.Chatbot.dto.InternalChatDto;
import com.yiji.Chatbot.entity.ChatMessage;
import com.yiji.Chatbot.entity.ChatSession;
import com.yiji.Chatbot.exception.SessionNotFoundException;
import com.yiji.Chatbot.mapper.ChatMapper;
import com.yiji.Chatbot.repository.ChatMessageRepository;
import com.yiji.Chatbot.repository.ChatSessionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Objects;

/**
 * 채팅 데이터 영속화 담당.
 *
 * ChatService(오케스트레이션)와 분리되어 있는 이유는 트랜잭션 경계 때문이다.
 * @Transactional은 프록시 기반이라 같은 클래스 안에서 this.method()로 부르면 걸리지 않는다.
 * 별도 빈으로 두면 ChatService가 주입받아 호출하므로 프록시를 지나 트랜잭션이 실제로 걸린다.
 *
 * 여기 있는 메서드는 전부 짧다. 외부 HTTP 호출은 절대 이 안에서 하지 않는다.
 * 트랜잭션이 열려 있는 동안 DB 커넥션을 붙잡기 때문이다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ChatPersistenceService {

    private final ChatSessionRepository chatSessionRepository;
    private final ChatMessageRepository chatMessageRepository;
    private final ChatMapper chatMapper;

    /** AI에게 넘길 최근 대화량. 5턴(질문 5 + 답변 5). */
    private static final int RECENT_HISTORY_SIZE = 10;
    private static final int TITLE_MAX_LENGTH = 30;

    /**
     * 요청한 sessionId를 이어써도 되는지 판단한다.
     *
     * - 없는 대화방이면 false. 호출하는 쪽이 새 ID를 발급한다.
     * - 있지만 소유자가 다르면 SessionNotFoundException.
     *
     * Redis 히스토리를 읽기 전에 반드시 이 확인을 거쳐야 한다. 순서가 뒤집히면
     * 남의 sessionId를 넣어 그 사람의 대화 맥락을 LLM 컨텍스트로 받아볼 수 있다.
     */
    @Transactional(readOnly = true)
    public boolean canContinue(String sessionId, String userId) {
        if (sessionId == null || sessionId.isBlank()) {
            return false;
        }
        return chatSessionRepository.findById(sessionId)
                .map(session -> {
                    requireOwner(session, userId);
                    return true;
                })
                .orElse(false);
    }

    /**
     * Redis 캐시가 비었을 때 DB에서 최근 대화 히스토리를 복원한다.
     * Redis 적재는 호출하는 쪽이 트랜잭션 밖에서 한다.
     */
    @Transactional(readOnly = true)
    public List<InternalChatDto.MessageRole> loadRecentHistory(String sessionId) {
        List<ChatMessage> messages = chatMessageRepository.findAllBySessionIdOrderByCreatedAtAsc(sessionId);
        if (messages.isEmpty()) {
            return List.of();
        }

        int from = Math.max(0, messages.size() - RECENT_HISTORY_SIZE);
        return messages.subList(from, messages.size()).stream()
                .map(message -> InternalChatDto.MessageRole.builder()
                        .role(message.getRole().toLowerCase()) // "user" 또는 "assistant"
                        .content(message.getContent())
                        .build())
                .toList();
    }

    /**
     * 한 턴(질문 + 답변)을 저장한다. 대화방이 아직 없으면 이때 만든다.
     *
     * 질문과 답변을 한 트랜잭션에 묶는다. AI 호출이 실패하면 여기까지 오지 않으므로
     * "질문만 있고 답변이 없는" 반쪽짜리 대화가 남지 않는다.
     */
    @Transactional
    public void saveTurn(String sessionId, String userId, String query, String answer, String sourcesJson) {
        ChatSession session = chatSessionRepository.findById(sessionId)
                .map(found -> {
                    requireOwner(found, userId);
                    found.updateTimestamp();
                    return found;
                })
                .orElseGet(() -> {
                    log.info("[ChatPersistence] 새 대화방 생성 (sessionId: {}, userId: {})", sessionId, userId);
                    return chatSessionRepository.save(ChatSession.builder()
                            .id(sessionId)
                            .userId(userId)
                            .title(titleFrom(query))
                            .build());
                });

        chatMessageRepository.save(ChatMessage.builder()
                .session(session)
                .role("USER")
                .content(query)
                .build());

        chatMessageRepository.save(ChatMessage.builder()
                .session(session)
                .role("ASSISTANT")
                .content(answer)
                .sourcesJson(sourcesJson)
                .build());
    }

    /**
     * 사용자의 대화방 목록 (최신순)
     */
    @Transactional(readOnly = true)
    public List<ChatSessionDto> getSessions(String userId) {
        return chatSessionRepository.findAllByUserIdOrderByUpdatedAtDesc(userId).stream()
                .map(chatMapper::toSessionDto)
                .toList();
    }

    /**
     * 특정 대화방의 전체 메시지 (본인 대화방만)
     */
    @Transactional(readOnly = true)
    public List<ChatMessageDto> getMessages(String sessionId, String userId) {
        requireOwnedSession(sessionId, userId);
        return chatMessageRepository.findAllBySessionIdOrderByCreatedAtAsc(sessionId).stream()
                .map(chatMapper::toMessageDto)
                .toList();
    }

    /**
     * 대화방 삭제 (본인 대화방만). Redis 캐시 제거는 커밋 이후 호출하는 쪽에서 한다.
     */
    @Transactional
    public void deleteSession(String sessionId, String userId) {
        requireOwnedSession(sessionId, userId);
        chatSessionRepository.deleteById(sessionId);
        log.info("[ChatPersistence] 대화방 삭제 완료 (sessionId: {})", sessionId);
    }

    /**
     * 대화방을 조회하면서 요청한 사용자의 것인지 확인한다.
     * userId는 브라우저 localStorage의 익명 ID라 인증은 아니지만,
     * sessionId만 알면 남의 대화를 읽고 지울 수 있던 구멍은 막는다.
     */
    private ChatSession requireOwnedSession(String sessionId, String userId) {
        return chatSessionRepository.findById(sessionId)
                .map(session -> requireOwner(session, userId))
                .orElseThrow(() -> new SessionNotFoundException(sessionId));
    }

    private ChatSession requireOwner(ChatSession session, String userId) {
        if (!Objects.equals(session.getUserId(), userId)) {
            // 소유자가 달라도 "없음"으로 취급한다. 403으로 답하면 그 대화방이 존재한다는 사실이 새어나간다.
            // 404로의 번역은 GlobalExceptionHandler가 맡는다. 서비스는 상태 코드를 모른다.
            log.warn("[ChatPersistence] 소유자가 아닌 대화방 접근 차단 (sessionId: {}, userId: {})", session.getId(), userId);
            throw new SessionNotFoundException(session.getId());
        }
        return session;
    }

    /**
     * 첫 질문 내용으로 대화방 제목 생성
     */
    private String titleFrom(String query) {
        if (query == null || query.isBlank()) {
            return "새로운 대화";
        }
        return query.length() > TITLE_MAX_LENGTH
                ? query.substring(0, TITLE_MAX_LENGTH) + "..."
                : query;
    }
}
