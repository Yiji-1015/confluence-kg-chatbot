package com.yiji.Chatbot.repository;

import com.yiji.Chatbot.entity.ChatMessage;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 대화 메시지 JPA Repository
 *
 * 정렬에 `createdAt` 하나만 쓰지 않고 `id`를 함께 쓴다. `createdAt`은 애플리케이션이
 * `LocalDateTime.now()`로 넣기 때문에 같은 턴의 질문과 답변이 같은 값을 가질 수 있다.
 * 그러면 순서가 실행할 때마다 달라지고, 대화가 뒤집혀 보인다.
 */
@Repository
public interface ChatMessageRepository extends JpaRepository<ChatMessage, Long> {

    /**
     * 대화방의 전체 메시지를 시간순으로 조회 (대화 내역 화면)
     */
    List<ChatMessage> findBySessionIdOrderByCreatedAtAscIdAsc(String sessionId);

    /**
     * 대화방의 최근 메시지를 최신순으로 N개만 조회 (LLM에 넘길 맥락)
     *
     * 예전에는 전체를 읽어 메모리에서 마지막 10개를 잘랐다. 캐시가 만료될 때마다
     * 대화 전체를 스캔하는 셈이었다. 필요한 만큼만 DB에서 가져온다.
     */
    List<ChatMessage> findBySessionIdOrderByCreatedAtDescIdDesc(String sessionId, Pageable pageable);
}
