package com.yiji.Chatbot.repository;

import com.yiji.Chatbot.entity.ChatMessage;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 대화 메시지 JPA Repository 인터페이스
 */
@Repository
public interface ChatMessageRepository extends JpaRepository<ChatMessage, Long> {

    /**
     * 특정 대화방(sessionId)의 모든 대화 메시지를 생성 시각(createdAt) 시간순으로 조회
     * - 과거 대화 내역 전체를 복원하는 데 사용됩니다.
     */
    List<ChatMessage> findAllBySessionIdOrderByCreatedAtAsc(String sessionId);
}
